"""FastAPI 앱 — 5단계 하이브리드 RAG(``/chat``)와 3단계 도구 호출 에이전트
(``/agent/chat``)를 LangChain 구현으로 나란히 제공한다.

SSE 배관(이벤트 이름, ``StreamingResponse``, ``media_type``) 자체는 2단계
이후로 바뀐 적이 없다 — 이 장도 그대로다. 바뀌는 것은 각 엔드포인트 **안**에서
답변을 만드는 방법뿐이다: ``/chat``은 ``docagent.chains.answer_with_citations``
(LCEL 체인 + 인용 검증)를, ``/agent/chat``은 ``docagent.agent.run_agent``
(``create_agent`` 기반 에이전트 루프)를 부른다.

**두 기능을 하나의 에이전트로 완전히 합치지 않은 이유**: LangChain의
``create_agent``에 검색을 도구(``create_retriever_tool``)로 얹으면 "도구
호출과 RAG를 한 에이전트가 같이 쓰게" 만드는 것 자체는 어렵지 않다(6절에
``search_docs`` 도구를 실제로 추가해서 보여준다). 하지만 PROJECT-SPEC.md
5장의 인용 검증 규칙("이번 검색에서 실제로 반환된 청크 ID 집합에 없는
인용은 버린다")은 검색이 **한 번, 답변 직전에** 일어난다는 전제로 설계돼
있다. 에이전트가 검색 도구를 여러 번, 다른 시점에 부를 수 있게 되면 "이번
답변의 근거가 정확히 어느 검색 결과 집합인지"가 더 이상 하나로 고정되지
않는다 — 이 문제를 제대로 풀려면 검색 도구 호출마다 별도의 라벨 네임스페이스를
두거나(예: 도구 호출 순번을 라벨에 포함) 상태 추적이 필요하고, 이는 7단계
(LangGraph로 상태를 명시적으로 설계)의 주제에 더 가깝다. 이 장은 범위를
분명히 하기 위해 두 엔드포인트를 분리해 둔다.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from docagent.agent import arun_agent
from docagent.chains import aanswer_with_citations
from docagent.config import get_settings
from docagent.events import done_event, error_event, sources_event, status_event, token_event, tool_call_event, tool_result_event
from docagent.rag.store import make_chunk_id

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DOCS_DIR = BASE_DIR / "data" / "docs"

app = FastAPI(title="docagent step06: LangChain으로 재구성한 RAG + 도구 호출")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def _ingest_once_at_startup() -> None:
    """서버 프로세스가 시작할 때 문서가 없는 컬렉션이면 한 번 적재한다.

    **이 순서가 실제로 중요하다.** Milvus Lite + ``langchain-milvus``(BM25
    하이브리드 스키마) 조합에서, 문서를 적재한 프로세스와 검색하는 프로세스가
    다르면(예: 별도 스크립트로 적재한 뒤 서버를 새로 띄우면)
    ``MilvusException: vector column must be FixedSizeList, got binary`` 오류가
    **실제로 재현됐다** — 컬렉션을 ``flush``하거나 새 프로세스에서 다시 연결할
    때 Milvus Lite가 sparse 벡터 컬럼을 잘못 읽는다. 같은 스키마를 5단계처럼
    ``pymilvus``로 직접 선언했을 때는 이 문제가 재현되지 않았다 — 원인을 이
    문서 작성 시점에 langchain-milvus/milvus-lite 어느 쪽 문제인지 확정하지
    못했다(6절 "실패 상황 실습" 참고). 이 우회책은 "적재와 서비스를 같은
    프로세스 안에서, 재연결 없이" 유지하는 것이다 — 운영 환경에서는 실제
    Milvus 서버(Milvus Lite가 아닌)로 이 문제가 재현되는지 반드시 별도로
    확인해야 한다.
    """
    from docagent.rag.ingest import ingest_directory
    from docagent.rag.search import get_vector_store
    import docagent.rag.search as search_module

    settings = get_settings()
    vs = get_vector_store(settings)
    if vs.client.has_collection(settings.milvus_collection):
        return
    count, vs = ingest_directory(DOCS_DIR, drop_old=False)
    search_module._vector_store = vs
    print(f"[startup] {count}개 청크를 적재했다.")


class ChatRequest(BaseModel):
    message: str
    top_k: int = 5


class AgentChatRequest(BaseModel):
    message: str


def _chunk_for_stream(text: str, size: int = 40) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


async def _rag_chat_stream(req: ChatRequest) -> AsyncIterator[str]:
    """``/chat`` — 5단계와 같은 순서(검색 -> 답변 -> 검증)를 LangChain으로 만든다.

    5단계와 마찬가지로 "답변을 전부 받은 뒤 인용을 검증하고 나서야 token으로
    잘라 보낸다"는 방식을 그대로 쓴다 — LCEL 체인의 ``.stream()``을 그대로
    쓰면 모델이 만드는 조각을 실시간으로 보낼 수는 있지만, 인용 검증은 문장이
    끝까지 나와야 할 수 있는 작업이라 5단계와 같은 이유로 "검증 후 재생"
    방식을 유지한다(docs/05 4-6절).
    """
    settings = get_settings()
    yield status_event("retrieving", f"'{req.message}' 관련 청크를 하이브리드 검색으로 찾는 중")
    try:
        result, retrieved = await aanswer_with_citations(req.message, settings=settings, top_k=req.top_k)
    except Exception as exc:  # Milvus/모델 서버 오류
        yield error_event("retrieval_failed", f"검색 또는 답변 생성 중 오류가 발생했다: {exc}")
        yield done_event("stop")
        return

    if not retrieved:
        yield status_event("writing", "검색 결과가 없어 답변을 만들 수 없다")
        yield token_event(result.text)
        yield sources_event([])
        yield done_event("stop")
        return

    yield status_event("analyzing", f"{len(retrieved)}개 청크를 찾았다. 답변을 준비하는 중")
    yield status_event("verifying", "답변의 인용 표시가 실제 검색 결과를 가리키는지 확인하는 중")
    yield status_event("writing", "검증된 답변을 전달하는 중")
    for piece in _chunk_for_stream(result.text):
        yield token_event(piece)
    yield sources_event(result.sources)
    yield done_event("stop")


async def _agent_chat_stream(req: AgentChatRequest) -> AsyncIterator[str]:
    """``/agent/chat`` — 3단계와 같은 이벤트 순서를 create_agent로 만든다."""
    async for event in arun_agent(req.message):
        if event.kind == "status":
            yield status_event(event.data["stage"], event.data["message"])
        elif event.kind == "tool_call":
            yield tool_call_event(event.data["id"], event.data["name"], event.data["args"])
        elif event.kind == "tool_result":
            yield tool_result_event(event.data["id"], event.data["ok"], event.data["summary"])
        elif event.kind == "token":
            yield token_event(event.data["text"])
        elif event.kind == "error":
            yield error_event(event.data["code"], event.data["message"])
        elif event.kind == "done":
            yield done_event(event.data["finish_reason"])


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(_rag_chat_stream(req), media_type="text/event-stream")


@app.post("/agent/chat")
async def agent_chat(req: AgentChatRequest) -> StreamingResponse:
    return StreamingResponse(_agent_chat_stream(req), media_type="text/event-stream")


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
    """근거 링크의 도착지. 5단계와 같은 URL 형식을 그대로 쓴다."""
    from docagent.rag.search import get_vector_store

    chunk_id = make_chunk_id(doc_id, page, chunk)
    chunk_text = ""
    try:
        vs = get_vector_store()
        docs = vs.similarity_search(chunk_id, k=1, expr=f'pk == "{chunk_id}"')
        if docs:
            chunk_text = docs[0].page_content
    except Exception:
        chunk_text = ""

    doc_text = _load_source_document(doc_id)
    if doc_text is None:
        body = f"<p>문서 '{html.escape(doc_id)}'를 찾을 수 없다.</p>"
    else:
        escaped_doc = html.escape(doc_text)
        if chunk_text:
            escaped_chunk = html.escape(chunk_text)
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
</body></html>"""
    return HTMLResponse(html_page)
