"""FastAPI 앱: 하이브리드 검색 RAG 채팅 + 근거 링크(/sources) + 트레이싱.

이 장은 3단계의 도구 호출 에이전트 루프를 아직 합치지 않는다(그 합치는
작업은 6단계 LangChain 재구성에서 한다). 여기서는 "질문 → 하이브리드 검색
→ 인용 검증 → 답변"만 다룬다.

스트리밍에 관한 설계 선택: 모델이 만든 [Sn] 인용이 유효한지는 답변을 끝까지
받아야 검증할 수 있다. 그래서 이 장의 /chat은 모델 응답을 먼저 전부 받고
citations.validate_and_link_citations로 검증한 뒤, 검증이 끝난 텍스트를
token 이벤트로 다시 잘라 보낸다. 진짜 토큰 단위 스트리밍(2단계)과 다르게
"검증 후 재생" 방식이라는 점을 05장 4-6절에서 설명한다.

9단계에서 추가한 것:
- ``RequestContextMiddleware``(2단계와 동일한 요청 ID·처리 시간 로그. HTTP
  요청 하나 단위의 평평한 로그 한 줄)
- 트레이싱 초기화(``init_tracing``)와 요청 전체를 감싸는 루트 스팬(요청
  하나 안에서 검색·모델 호출·인용 검증이 어떻게 계층으로 이어지는지 보여줌)
- ``OpenAIInstrumentor``로 openai SDK 호출(채팅·임베딩)을 자동 계측

요청 ID 로그와 트레이스는 같은 요청을 가리키는 서로 다른 두 기록이다.
로그는 "이 요청이 몇 ms 걸렸고 상태 코드가 뭐였는지" 한 줄로 말해 준다.
트레이스는 그 ms 안에서 검색이 몇 ms, 모델 호출이 몇 ms였는지, 그 사이에
도구 호출이 몇 번 있었는지를 계층으로 보여준다 — "얼마나 걸렸나"와 "왜
그렇게 걸렸나(또는 왜 틀렸나)"의 차이다. 이 장 2절에서 자세히 다룬다.
"""

from __future__ import annotations

import html
import logging
import re
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from docagent.config import get_settings
from docagent.events import done_event, error_event, sources_event, status_event, token_event
from docagent.llm import LLMError, achat
from docagent.middleware import RequestContextMiddleware
from docagent.rag import citations
from docagent.rag.search import dense_search, hybrid_search, sparse_search
from docagent.rag.store import get_client, make_chunk_id
from docagent.tracing import SpanKind, init_tracing, mark_error, set_llm_usage, set_output, traced_span

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DOCS_DIR = BASE_DIR / "data" / "docs"

logger = logging.getLogger("docagent.app")

# 2단계에서 겪은 문제("INFO 로그가 조용히 사라진다", docs/02-fastapi-chat.md
# 6.2절)를 여기서도 그대로 막는다. 루트 로거의 기본 레벨(WARNING)에 걸리면
# docagent.request의 요청 ID 로그가 안 보인다 — logging.basicConfig()만으로는
# 부족하고, "docagent" 로거에 직접 레벨과 핸들러를 준다.
_docagent_logger = logging.getLogger("docagent")
_docagent_logger.setLevel(logging.INFO)
if not _docagent_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _docagent_logger.addHandler(_handler)

app = FastAPI(title="docagent step09: tracing + eval")
app.add_middleware(RequestContextMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_settings = get_settings()
if init_tracing(_settings):
    # OpenAIInstrumentor는 openai.OpenAI 클라이언트가 만드는 모든 호출(채팅,
    # 임베딩)을 자동으로 스팬으로 감싼다 — llm.py를 한 줄도 고치지 않아도
    # 된다. docagent.rag.search의 dense_search 등은 이 자동 계측 대상이
    # 아니므로(Milvus 호출은 openai SDK가 아니다) tracing.py의
    # traced_span으로 수동 계측했다. "표준 SDK 호출은 자동, 그 외
    # 애플리케이션 고유 로직은 수동"이 이 장에서 쓰는 원칙이다.
    try:
        from openinference.instrumentation.openai import OpenAIInstrumentor

        OpenAIInstrumentor().instrument()
        logger.info("OpenAIInstrumentor enabled: openai SDK 호출이 자동으로 스팬이 된다")
    except ImportError:
        logger.warning(
            "openinference-instrumentation-openai가 설치돼 있지 않다. "
            "모델 호출 자동 계측 없이 계속 진행한다(수동 스팬만 남는다)."
        )

_SYSTEM_PROMPT = (
    "당신은 사내 문서 검색 비서다. 아래 [컨텍스트]에 주어진 조각만 근거로 답한다. "
    "컨텍스트에 없는 내용은 모른다고 답한다. "
    "각 조각에는 [S1], [S2]처럼 번호가 붙어 있다. 답변에서 어떤 조각을 근거로 썼는지 "
    "표시할 때는 반드시 그 번호를 문장 끝에 [S1]처럼 대괄호로 적는다. "
    "절대로 URL이나 파일 경로를 직접 만들어 쓰지 않는다 — 번호만 쓴다. "
    "번호가 없는 문장에는 인용 표시를 붙이지 않는다."
)


class ChatRequest(BaseModel):
    message: str
    search_mode: str = "hybrid"  # "dense" | "sparse" | "hybrid" (응용 과제에서 바꿔볼 수 있게)
    top_k: int = 5


def _build_context_block(sources: list[citations.RetrievedSource]) -> str:
    lines = []
    for s in sources:
        lines.append(f"[{s.label}] (문서: {s.doc_title}, 청크 ID: {s.chunk_id})\n{s.text}")
    return "\n\n".join(lines)


def _chunk_for_stream(text: str, size: int = 40) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


async def _chat_stream(req: ChatRequest, request_id: str) -> AsyncIterator[str]:
    # 이 요청 전체를 감싸는 루트 스팬. 아래에서 만드는 retrieval.*, citations.validate,
    # (자동 계측된) openai 스팬이 모두 이 스팬의 자식으로 붙어 하나의 트레이스를
    # 이룬다. request_id를 스팬 속성에 남겨 두면, 미들웨어가 남긴 로그 한 줄과
    # 이 트레이스를 request_id로 서로 찾아 연결할 수 있다.
    with traced_span(
        "docagent.chat",
        kind=SpanKind.CHAIN,
        input_value=req.message,
        attributes={
            "http.request_id": request_id,
            "retrieval.mode": req.search_mode,
            "retrieval.top_k": req.top_k,
        },
    ) as root_span:
        async for event in _chat_stream_body(req, root_span):
            yield event


async def _chat_stream_body(req: ChatRequest, root_span) -> AsyncIterator[str]:
    yield status_event("retrieving", f"'{req.message}' 관련 청크를 하이브리드 검색으로 찾는 중")

    try:
        if req.search_mode == "dense":
            hits = dense_search(req.message, limit=req.top_k)
        elif req.search_mode == "sparse":
            hits = sparse_search(req.message, limit=req.top_k)
        else:
            hits = hybrid_search(req.message, limit=req.top_k)
    except Exception as exc:  # Milvus 연결 실패 등
        # 주의: 이 except가 예외를 삼키고 SSE error 이벤트로 바꾸므로, 예외가
        # traced_span의 with 블록 밖으로 올라가지 않는다 — 그래서 root_span을
        # 명시적으로 오류로 표시해야 한다(mark_error가 없으면 트레이스에는
        # 이 요청이 "정상 종료"로 보인다. 9장 6절에서 실제로 재현한다).
        mark_error(root_span, "retrieval_failed", str(exc))
        yield error_event("retrieval_failed", f"검색 중 오류가 발생했다: {exc}")
        yield done_event("stop")
        return

    if not hits:
        yield status_event("writing", "검색 결과가 없어 답변을 만들 수 없다")
        yield token_event("등록된 문서에서 관련 내용을 찾지 못했다. 다른 질문으로 다시 시도한다.")
        yield sources_event([])
        yield done_event("stop")
        return

    retrieved = citations.build_retrieved_sources(hits, app_base_url=get_settings().app_base_url)
    context_block = _build_context_block(retrieved)

    yield status_event("analyzing", f"{len(retrieved)}개 청크를 찾았다. 답변을 준비하는 중")

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"[컨텍스트]\n{context_block}\n\n[질문]\n{req.message}",
        },
    ]

    try:
        # achat()(동기 chat()이 아니라)을 쓰는 이유는 PROJECT-SPEC.md 9절의
        # 취소 요구사항이다: 클라이언트가 연결을 끊으면 이 코루틴을 기다리는
        # 태스크가 취소되고, 그 취소가 achat 안의 finally까지 전달되어 모델
        # 서버로 나가는 HTTP 연결이 실제로 닫힌다.
        result = await achat(messages, settings=get_settings())
    except LLMError as exc:
        mark_error(root_span, "llm_call_failed", str(exc))
        yield error_event("llm_call_failed", str(exc))
        yield done_event("stop")
        return

    # 모델 응답의 usage를 루트 스팬에도 남긴다. OpenAIInstrumentor가 켜져
    # 있으면 openai 호출 스팬에도 같은 정보가 자동으로 남지만, 여기서는
    # "이 요청 전체가 총 몇 토큰을 썼는지"를 한 곳에서 보기 위해 별도로 남긴다.
    # 모든 OpenAI 호환 서버가 usage를 채워 준다고 가정하지 않는다 — 없으면
    # 조용히 건너뛴다(set_llm_usage는 None 값을 기록하지 않는다).
    usage = (result.raw or {}).get("usage") or {}
    set_llm_usage(
        root_span,
        model=get_settings().chat_model,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )

    raw_answer = result.text

    yield status_event("verifying", "답변의 인용 표시가 실제 검색 결과를 가리키는지 확인하는 중")
    result = citations.validate_and_link_citations(
        raw_answer, retrieved, app_base_url=get_settings().app_base_url
    )

    yield status_event("writing", "검증된 답변을 전달하는 중")
    for piece in _chunk_for_stream(result.text):
        yield token_event(piece)

    set_output(root_span, result.text[:1000])
    yield sources_event(result.sources)
    yield done_event("stop")


@app.post("/chat")
async def chat_endpoint(req: ChatRequest, request: Request) -> StreamingResponse:
    # 주의: 이 함수 이름을 "chat"으로 지으면 위에서 가져온 docagent.llm.chat과
    # 모듈 전역 이름이 겹쳐, _chat_stream() 안의 chat(...) 호출이 이 라우트
    # 핸들러를 가리키게 되는 버그가 생긴다. 그래서 라우트 핸들러는 chat_endpoint로 둔다.
    request_id = getattr(request.state, "request_id", "")
    return StreamingResponse(_chat_stream(req, request_id), media_type="text/event-stream")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


def _load_source_document(doc_id: str) -> str | None:
    path = DOCS_DIR / f"{doc_id}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


@app.get("/sources/{doc_id}", response_class=HTMLResponse)
async def view_source(doc_id: str, page: int = 0, chunk: int = 0) -> HTMLResponse:
    """근거 링크의 도착지. 해당 청크 원문을 Milvus에서 찾아 문서 전체 안에서
    강조 표시한다. PROJECT-SPEC.md 5장의 URL 형식
    (``/sources/{doc_id}?page={page}&chunk={chunk_index}``)과 맞춘다."""

    chunk_id = make_chunk_id(doc_id, page, chunk)
    client = get_client()
    chunk_text = ""
    try:
        rows = client.query(
            get_settings().milvus_collection,
            filter=f'pk == "{chunk_id}"',
            output_fields=["text", "doc_title"],
        )
        if rows:
            chunk_text = rows[0].get("text", "")
    except Exception:
        # Milvus에 연결할 수 없어도 원문 파일만으로 페이지를 보여줄 수 있게 한다.
        chunk_text = ""

    doc_text = _load_source_document(doc_id)
    if doc_text is None:
        body = f"<p>문서 '{html.escape(doc_id)}'를 찾을 수 없다.</p>"
    else:
        escaped_doc = html.escape(doc_text)
        if chunk_text:
            escaped_chunk = html.escape(chunk_text)
            # 청크 원문이 등장하는 자리를 <mark>로 감싼다. Milvus에 없으면(예: 마이그레이션
            # 전이거나 서버 미연결) 강조 없이 전체 문서만 보여준다.
            highlighted = escaped_doc.replace(escaped_chunk, f'<mark id="highlight">{escaped_chunk}</mark>', 1)
        else:
            highlighted = escaped_doc
        body = f"<pre style='white-space:pre-wrap;font-family:inherit;'>{highlighted}</pre>"

    html_page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<title>{html.escape(doc_id)} - 근거 원문</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 760px; margin: 40px auto; padding: 0 16px; }}
  mark#highlight {{ background: #fff3a3; padding: 2px 0; }}
  .meta {{ color: #666; font-size: 13px; margin-bottom: 16px; }}
</style>
</head>
<body>
  <div class="meta">문서 ID: {html.escape(doc_id)} · 청크 ID: {html.escape(chunk_id)}</div>
  {body}
  <script>
    const el = document.getElementById('highlight');
    if (el) el.scrollIntoView({{block: 'center'}});
  </script>
</body></html>"""
    return HTMLResponse(html_page)
