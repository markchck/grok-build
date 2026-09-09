"""LCEL(LangChain Expression Language) RAG 체인 — 5단계 ``app.py``의 답변 생성부.

5단계 ``_chat_stream``은 "검색 -> 컨텍스트 문자열 조립 -> 메시지 리스트 조립 ->
``chat`` 호출"을 순서대로 이어 쓴 평범한 파이썬 함수였다. 이 장은
그중 "프롬프트 조립 -> 모델 호출 -> 문자열로 파싱"에 해당하는 부분을
``Runnable``(LangChain의 합성 가능한 실행 단위) 파이프라인으로 다시 쓴다.

``ChatPromptTemplate | chat_model | StrOutputParser()``처럼 ``|``(파이프)로
이어 붙이면 왼쪽 출력이 오른쪽 입력이 되는 새 ``Runnable``이 만들어진다.
이 체인은 ``invoke()``(한 번에), ``stream()``(조각 단위로), ``batch()``(여러
입력을 한 번에) 세 실행 방식을 **같은 코드로** 제공한다 — 5단계는 스트리밍과
일반 호출을 위해 ``chat``/``chat_stream`` 두 함수를
따로 만들어야 했다.

**이 체인이 대신해주지 않는 것**: 검색 결과 순서와 ``[S1]`` 라벨을 맞추는
일, 그리고 답변에 실제로 존재하지 않는 인용을 걸러내는 검증
(``docagent.rag.citations``)은 "검색 결과 몇 건이 실제로 반환됐는지"를
알아야 하는 이 애플리케이션 고유의 로직이라 체인 안에 넣지 않는다 — 체인
바깥의 ``answer_with_citations()``가 그 순서를 그대로 조립한다. 억지로
``RunnableParallel``/``RunnablePassthrough``로 전부 하나의 체인에 우겨 넣을
수도 있지만, 그러면 "이 단계에서 실제로 무슨 값이 오가는지"가 코드에서
안 보이게 된다 — 5단계 사고방식(각 단계를 순서대로 읽을 수 있게 남겨 둔다)을
그대로 유지한다.
"""

from __future__ import annotations

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from docagent.config import Settings, get_settings
from docagent.models import build_chat_model
from docagent.rag import citations
from docagent.rag.search import RankerName, hybrid_search

_SYSTEM_PROMPT = (
    "당신은 사내 문서 검색 비서다. 아래 [컨텍스트]에 주어진 조각만 근거로 답한다. "
    "컨텍스트에 없는 내용은 모른다고 답한다. "
    "각 조각에는 [S1], [S2]처럼 번호가 붙어 있다. 답변에서 어떤 조각을 근거로 썼는지 "
    "표시할 때는 반드시 그 번호를 문장 끝에 [S1]처럼 대괄호로 적는다. "
    "절대로 URL이나 파일 경로를 직접 만들어 쓰지 않는다 — 번호만 쓴다. "
    "번호가 없는 문장에는 인용 표시를 붙이지 않는다."
)

_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", _SYSTEM_PROMPT),
        ("user", "[컨텍스트]\n{context}\n\n[질문]\n{question}"),
    ]
)


def build_answer_chain(*, model=None, temperature: float = 0.2):
    """``prompt | chat_model | StrOutputParser()`` — Runnable 파이프라인.

    ``model``을 주면 그 모델을 쓰고(테스트에서 가짜 모델 주입용), 생략하면
    ``docagent.models.build_chat_model()``이 만든 실제 서버용 모델을 쓴다.
    """
    chat_model = model or build_chat_model(temperature=temperature)
    return _PROMPT | chat_model | StrOutputParser()


def _build_context_block(sources: list[citations.RetrievedSource]) -> str:
    lines = [f"[{s.label}] (문서: {s.doc_title}, 청크 ID: {s.chunk_id})\n{s.text}" for s in sources]
    return "\n\n".join(lines)


async def aanswer_with_citations(
    question: str,
    *,
    settings: Settings | None = None,
    model=None,
    top_k: int = 5,
    ranker: RankerName = "rrf",
) -> tuple[citations.CitationResult, list[citations.RetrievedSource]]:
    """``answer_with_citations()``의 비동기 버전(PROJECT-SPEC.md 9절). app.py가 쓴다.

    LCEL의 ``Runnable``은 ``invoke()``와 나란히 ``ainvoke()``를 제공한다 —
    ``langchain_openai.ChatOpenAI``는 내부적으로 ``openai.AsyncOpenAI``를 써서
    ``ainvoke()``를 처리하므로, 클라이언트가 연결을 끊었을 때(이 코루틴을
    기다리는 태스크가 취소될 때) 모델 서버로 나가는 HTTP 호출까지 실제로
    취소된다 — 2단계 llm.py의 ``achat_stream``과 같은 이유다. ``hybrid_search``
    (Milvus 호출)는 이 장에서 비동기 경로가 없으므로 동기 그대로 쓴다 —
    PROJECT-SPEC.md 9절의 취소 요구사항은 "모델 서버로 나가는 HTTP 호출"에
    관한 것이지 검색 백엔드에 관한 것이 아니다.
    """
    s = settings or get_settings()
    hits = hybrid_search(question, limit=top_k, ranker=ranker, settings=s)
    if not hits:
        empty = citations.CitationResult(
            text="등록된 문서에서 관련 내용을 찾지 못했다. 다른 질문으로 다시 시도한다.",
            sources=[],
            used_labels=[],
            dropped_labels=[],
        )
        return empty, []

    retrieved = citations.build_retrieved_sources(hits, app_base_url=s.app_base_url)
    chain = build_answer_chain(model=model)
    raw_answer = await chain.ainvoke({"context": _build_context_block(retrieved), "question": question})

    result = citations.validate_and_link_citations(raw_answer, retrieved, app_base_url=s.app_base_url)
    return result, retrieved


def answer_with_citations(
    question: str,
    *,
    settings: Settings | None = None,
    model=None,
    top_k: int = 5,
    ranker: RankerName = "rrf",
) -> tuple[citations.CitationResult, list[citations.RetrievedSource]]:
    """검색 -> LCEL 체인 호출 -> 인용 검증까지 한 번에 수행한다.

    5단계 ``_chat_stream``과 순서가 완전히 같다: 먼저 검색해서 실제로 반환된
    청크 집합을 확정하고, 그 집합을 기준으로만 인용을 검증한다.
    """
    s = settings or get_settings()
    hits = hybrid_search(question, limit=top_k, ranker=ranker, settings=s)
    if not hits:
        empty = citations.CitationResult(
            text="등록된 문서에서 관련 내용을 찾지 못했다. 다른 질문으로 다시 시도한다.",
            sources=[],
            used_labels=[],
            dropped_labels=[],
        )
        return empty, []

    retrieved = citations.build_retrieved_sources(hits, app_base_url=s.app_base_url)
    chain = build_answer_chain(model=model)
    raw_answer = chain.invoke({"context": _build_context_block(retrieved), "question": question})

    result = citations.validate_and_link_citations(raw_answer, retrieved, app_base_url=s.app_base_url)
    return result, retrieved
