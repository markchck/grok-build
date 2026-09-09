"""그래프 노드 함수.

각 노드는 ``GraphState``의 일부를 받아 상태의 일부만 담은 dict를 돌려준다
(LangGraph 규약 — 노드가 상태 전체를 새로 만들 필요가 없다. 돌려주지 않은
키는 그대로 유지된다).

**의존성 주입**: 검색·모델 호출 함수를 노드가 직접 import해서 쓰지 않고
``NodeDeps``로 받는다. 이렇게 하면:

- 실제 서비스에서는 ``docagent.rag.search``(Milvus)와 ``docagent.llm``
  (모델 서버)을 주입한다.
- 이 장의 테스트와 데모에서는 ``docagent.rag.fake_retriever.FakeRetriever``
  (Milvus 불필요)를 주입해 그래프의 상태 전이·분기·체크포인트 재개를
  Milvus 없이도 검증할 수 있다.

**모델의 판단과 정해진 흐름의 조합** (docs/07-langgraph.md 2-4절): classify와
verify 두 노드는 먼저 모델에게 구조화된 판단(JSON)을 요청하고, 그 응답이
기대한 형태가 아니면 규칙 기반 휴리스틱으로 대체한다. 그래프의 마디(어떤
노드가 존재하고 무엇 다음에 무엇이 오는지)는 애플리케이션이 고정하고,
그 마디 중 일부의 "어느 쪽으로 갈지"만 모델이 돕는다 — 이것이 "정해진
워크플로와 모델 판단의 조합"이다.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from docagent.llm import ChatResult
from docagent.rag import citations


class ChatFn(Protocol):
    def __call__(self, messages: list[dict[str, Any]], *, temperature: float = 0.2) -> ChatResult: ...


class JsonChatFn(Protocol):
    def __call__(
        self,
        messages: list[dict[str, Any]],
        *,
        schema_name: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> dict[str, Any]: ...


class SearchFn(Protocol):
    def __call__(self, query: str, *, limit: int = 5, filter_expr: str = "") -> list[dict]: ...


@dataclass(frozen=True)
class NodeDeps:
    """노드가 쓰는 외부 호출을 한데 묶은 의존성 컨테이너."""

    dense_search: SearchFn
    sparse_search: SearchFn
    chat: ChatFn
    json_chat: JsonChatFn
    app_base_url: str
    top_k: int = 5
    min_sufficient_hits: int = 2
    max_retrieval_rounds: int = 2


_CHITCHAT_MARKERS = ("안녕", "고마워", "감사", "잘 지내", "날씨", "누구야", "심심")


def _trace(stage: str, message: str) -> dict:
    """PROJECT-SPEC.md 4장 status 이벤트와 같은 모양의 로그 한 줄.

    이 값은 모델의 사고 과정이 아니라 "어느 노드가 실행됐는지"를 애플리케이션이
    관측한 사실이다(WRITING-GUIDE.md 용어 해석).
    """

    return {"stage": stage, "message": message}


# ---------------------------------------------------------------------------
# 1. classify: 질문을 factual/chitchat으로 나눈다. stage="planning"
# ---------------------------------------------------------------------------


def make_classify_node(deps: NodeDeps) -> Callable[[dict], dict]:
    schema = {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": ["factual", "chitchat"]},
            "reason": {"type": "string"},
        },
        "required": ["category"],
    }

    def classify(state: dict) -> dict:
        question = state["question"]

        category: str | None = None
        source = "heuristic"
        try:
            result = deps.json_chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "사용자 질문이 문서·매출 데이터에 관한 사실 확인 질문(factual)인지 "
                            "인사말 같은 잡담(chitchat)인지 JSON으로만 답하라."
                        ),
                    },
                    {"role": "user", "content": question},
                ],
                schema_name="classify_question",
                json_schema=schema,
            )
            if isinstance(result.get("category"), str) and result["category"] in ("factual", "chitchat"):
                category = result["category"]
                source = "model"
        except Exception:  # noqa: BLE001 - LLMError 및 파싱 실패를 모두 휴리스틱으로 우회
            category = None

        if category is None:
            # 모델 응답이 기대한 스키마를 채우지 않았을 때(예: 이 저장소의 학습용
            # 스텁 서버는 요청한 스키마와 무관하게 고정된 JSON을 돌려준다) 규칙
            # 기반으로 대체한다.
            stripped = question.strip()
            is_short_greeting = len(stripped) <= 12 and any(m in stripped for m in _CHITCHAT_MARKERS)
            category = "chitchat" if is_short_greeting else "factual"
            source = "heuristic"

        return {
            "category": category,
            "category_source": source,
            "current_query": question,
            "retrieval_round": 0,
            "trace": [_trace("planning", f"질문을 '{category}'(으)로 분류했다 (판단 주체: {source})")],
        }

    return classify


def route_after_classify(state: dict) -> str | list[str]:
    """조건 분기: chitchat이면 바로 답변, factual이면 dense/sparse를 병렬로 fan-out."""

    if state["category"] == "chitchat":
        return "chitchat_answer"
    return ["retrieve_dense", "retrieve_sparse"]


# ---------------------------------------------------------------------------
# 2. retrieve_dense / retrieve_sparse: 병렬 검색. stage="retrieving"
# ---------------------------------------------------------------------------
#
# 오류 처리를 LangGraph의 노드 수준 ``retry_policy``/``error_handler``가
# 아니라 **노드 함수 안에서 직접** 한다. 이유는 langgraph 1.2.11에서 실제로
# 재현한 동작 때문이다: 같은 슈퍼스텝에서 형제 노드(예: retrieve_dense와
# retrieve_sparse처럼 나란히 실행되는 노드)가 있을 때, 그중 하나가
# ``error_handler``로 복구되더라도(핸들러 함수는 정상 호출되고 값도
# 만든다) 그 슈퍼스텝을 실행한 스레드 풀 실행기가 원래 예외를 다시 던져
# 그래프 실행 전체가 실패로 끝난다 — 형제 노드가 없는 단독 노드에서는
# ``error_handler``가 정상 동작한다(``answer``/``chitchat_answer``처럼
# 병렬로 실행되는 다른 노드가 없는 경우). 이 장 하단 "검증 상태"에 재현
# 스크립트 요약을 남긴다. 그래서 병렬로 실행되는 이 두 노드는 재시도와
# 대체(fallback)를 함수 안에서 직접 처리해 이 문제를 피한다.


def _search_with_retry(
    search_fn: SearchFn,
    query: str,
    *,
    limit: int,
    max_attempts: int = 3,
    initial_delay: float = 0.2,
) -> tuple[list[dict], Exception | None]:
    """검색을 최대 ``max_attempts``번 시도하고, (결과, 마지막 예외)를 돌려준다.

    성공하면 예외는 ``None``이다. 모두 실패하면 빈 리스트와 마지막 예외를
    돌려준다 — 호출부(노드 함수)가 이 실패를 "다른 쪽 검색 결과만으로 계속
    진행" 여부를 결정하는 데 쓴다.
    """

    delay = initial_delay
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return search_fn(query, limit=limit), None
        except Exception as exc:  # noqa: BLE001 - 검색 백엔드 예외 타입에 의존하지 않는다
            last_exc = exc
            if attempt < max_attempts:
                time.sleep(delay)
                delay *= 2
    return [], last_exc


def make_retrieve_dense_node(deps: NodeDeps) -> Callable[[dict], dict]:
    def retrieve_dense(state: dict) -> dict:
        query = state["current_query"]
        hits, exc = _search_with_retry(deps.dense_search, query, limit=deps.top_k)
        if exc is not None:
            return {
                "retrieved": [],
                "trace": [_trace("retrieving", f"dense 검색이 재시도 후에도 실패해 이번 라운드는 건너뛴다: {exc}")],
            }
        return {
            "retrieved": hits,
            "queries_tried": [f"dense:{query}"],
            "trace": [_trace("retrieving", f"dense 검색으로 '{query}' 관련 청크 {len(hits)}건을 찾았다")],
        }

    return retrieve_dense


def make_retrieve_sparse_node(deps: NodeDeps) -> Callable[[dict], dict]:
    def retrieve_sparse(state: dict) -> dict:
        query = state["current_query"]
        hits, exc = _search_with_retry(deps.sparse_search, query, limit=deps.top_k)
        if exc is not None:
            return {
                "retrieved": [],
                "trace": [_trace("retrieving", f"sparse 검색이 재시도 후에도 실패해 이번 라운드는 건너뛴다: {exc}")],
            }
        return {
            "retrieved": hits,
            "queries_tried": [f"sparse:{query}"],
            "trace": [_trace("retrieving", f"sparse 검색으로 '{query}' 관련 청크 {len(hits)}건을 찾았다")],
        }

    return retrieve_sparse


# ---------------------------------------------------------------------------
# 3. verify: 모은 근거가 답하기에 충분한지 판단. stage="analyzing"
# ---------------------------------------------------------------------------


def make_verify_node(deps: NodeDeps) -> Callable[[dict], dict]:
    schema = {
        "type": "object",
        "properties": {
            "sufficient": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["sufficient", "reason"],
    }

    def verify(state: dict) -> dict:
        retrieved = state.get("retrieved", [])
        source = "heuristic"
        sufficient: bool | None = None
        reason = ""

        try:
            context = "\n".join(f"- {h['text'][:200]}" for h in retrieved[: deps.top_k])
            result = deps.json_chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "아래 검색된 조각들이 질문에 답하기에 충분한 근거인지 JSON으로만 판단하라."
                        ),
                    },
                    {"role": "user", "content": f"질문: {state['question']}\n\n검색된 조각:\n{context or '(없음)'}"},
                ],
                schema_name="verify_evidence",
                json_schema=schema,
            )
            if isinstance(result.get("sufficient"), bool):
                sufficient = result["sufficient"]
                reason = str(result.get("reason", ""))
                source = "model"
        except Exception:  # noqa: BLE001
            sufficient = None

        if sufficient is None:
            # 규칙: 서로 다른 청크(pk)가 최소 min_sufficient_hits건 이상 모였으면 충분하다고 본다.
            distinct = {h["pk"] for h in retrieved}
            sufficient = len(distinct) >= deps.min_sufficient_hits
            reason = (
                f"검색된 서로 다른 청크가 {len(distinct)}건으로 기준({deps.min_sufficient_hits}건) 이상이다"
                if sufficient
                else f"검색된 서로 다른 청크가 {len(distinct)}건뿐이다(기준 {deps.min_sufficient_hits}건)"
            )

        return {
            "sufficient": sufficient,
            "insufficiency_reason": reason,
            "verify_source": source,
            "trace": [
                _trace(
                    "analyzing",
                    f"근거 {len(retrieved)}건을 확인했다: {'충분' if sufficient else '부족'} "
                    f"({reason}, 판단 주체: {source})",
                )
            ],
        }

    return verify


def make_route_after_verify(deps: NodeDeps) -> Callable[[dict], str]:
    def route_after_verify(state: dict) -> str:
        if state["sufficient"]:
            return "answer"
        if state["retrieval_round"] >= deps.max_retrieval_rounds:
            # 근거 부족이 계속돼도 무한히 검색을 반복하지 않는다. 라운드 상한에
            # 도달하면 지금까지 모은 근거로 답변을 시도한다(그래프 자체의
            # recursion_limit과는 별개의, 이 워크플로가 스스로 두는 안전장치다).
            return "answer"
        return "expand_query"

    return route_after_verify


# ---------------------------------------------------------------------------
# 4. expand_query: 근거 부족 시 질의를 넓혀 재검색을 준비한다. stage="retrieving"
# ---------------------------------------------------------------------------

_STOPWORD_SUFFIXES = ("은", "는", "이", "가", "을", "를", "의", "이유는", "이유는?", "왜")
_TOKEN_RE = re.compile(r"[\w가-힣]+")


def expand_query(state: dict) -> dict:
    """질문을 조금 더 넓은 질의로 바꾼다.

    이 장에서는 아주 단순한 규칙을 쓴다: 질문에서 핵심 명사로 보이는 토큰
    (2글자 이상, 조사로 끝나지 않는 토큰) 위주로 다시 조합해 더 짧고 넓은
    질의를 만든다. 실무에서는 이 자리에 모델 호출(질의 재작성, query
    rewriting)을 넣을 수도 있다 — 이 장은 그래프의 반복·분기 구조를 보이는
    것이 목적이라 규칙 기반으로 남겨 둔다.
    """

    tokens = _TOKEN_RE.findall(state["question"])
    core = [t for t in tokens if len(t) >= 2][:4]
    broadened = " ".join(core) if core else state["question"]
    next_round = state["retrieval_round"] + 1

    return {
        "current_query": broadened,
        "retrieval_round": next_round,
        "trace": [
            _trace(
                "retrieving",
                f"근거가 부족해 질의를 넓힌다 ({next_round}차 재검색): '{broadened}'",
            )
        ],
    }


# ---------------------------------------------------------------------------
# 5. answer: 최종 답변 생성 + 인용 검증. stage="verifying" -> "writing"
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "당신은 사내 문서 검색 비서다. 아래 [컨텍스트]에 주어진 조각만 근거로 답한다. "
    "컨텍스트에 없는 내용은 모른다고 답한다. "
    "각 조각에는 [S1], [S2]처럼 번호가 붙어 있다. 어떤 조각을 근거로 썼는지 "
    "표시할 때는 반드시 그 번호를 문장 끝에 [S1]처럼 대괄호로 적는다. "
    "URL이나 파일 경로를 직접 만들어 쓰지 않는다 — 번호만 쓴다."
)


def make_answer_node(deps: NodeDeps) -> Callable[[dict], dict]:
    def answer(state: dict) -> dict:
        retrieved = state.get("retrieved", [])[: deps.top_k]
        sources = citations.build_retrieved_sources(retrieved, app_base_url=deps.app_base_url)

        if not sources:
            text = "이 질문에 답할 근거를 문서에서 찾지 못했다."
            return {
                "answer_text": text,
                "citations": [],
                "dropped_citations": [],
                "trace": [
                    _trace("verifying", "근거가 없어 인용 검증을 생략했다"),
                    _trace("writing", "근거 없음 답변을 전달한다"),
                ],
            }

        context_block = "\n\n".join(f"[{s.label}] (문서: {s.doc_title})\n{s.text}" for s in sources)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"[컨텍스트]\n{context_block}\n\n[질문]\n{state['question']}",
            },
        ]
        result_chat = deps.chat(messages, temperature=0.2)
        raw_answer = result_chat.text or ""

        result = citations.validate_and_link_citations(raw_answer, sources, app_base_url=deps.app_base_url)

        return {
            "answer_text": result.text,
            "citations": result.sources,
            "dropped_citations": result.dropped_labels,
            "trace": [
                _trace(
                    "verifying",
                    f"인용 표시를 검증했다: 사용 {len(result.used_labels)}건, 제거 {len(result.dropped_labels)}건",
                ),
                _trace("writing", "검증된 답변을 전달한다"),
            ],
        }

    return answer


def make_chitchat_answer_node(deps: NodeDeps) -> Callable[[dict], dict]:
    def chitchat_answer(state: dict) -> dict:
        messages = [
            {
                "role": "system",
                "content": "짧고 자연스럽게 인사에 응답한다. 문서 검색은 하지 않는다.",
            },
            {"role": "user", "content": state["question"]},
        ]
        result_chat = deps.chat(messages, temperature=0.4)
        text = result_chat.text or ""
        return {
            "answer_text": text,
            "citations": [],
            "dropped_citations": [],
            "trace": [_trace("writing", "잡담 질문이라 검색 없이 바로 답했다")],
        }

    return chitchat_answer
