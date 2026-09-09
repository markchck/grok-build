"""FastAPI 앱: LangGraph로 만든 질문 분류→검색→근거 확인→답변 그래프를 SSE로 노출한다.

**이 파일이 하는 일과 하지 않는 일** (docs/07-langgraph.md 7절 "FastAPI·작업
큐·LangGraph의 역할 구분"):

- FastAPI는 HTTP 요청을 받고 SSE로 응답을 스트리밍하는 일**만** 한다.
  "다음에 뭘 실행할지"는 결정하지 않는다.
- 상태·분기·반복·재시도는 전부 ``docagent.graph``(LangGraph)가 결정한다.
  이 파일은 그래프를 만들고(``build_graph``), 실행하고(``astream``), 그
  결과를 SSE 이벤트로 옮기는 어댑터에 가깝다.
- 별도의 작업 큐(Celery, RQ 등)는 아직 두지 않는다 — 이유는 7절 본문에
  있다: 이 그래프 한 번 실행은 ``MAX_AGENT_SECONDS``(기본 120초) 안에
  끝나는 요청-응답 범위의 작업이고, 이미 그래프 자체의 체크포인터가 "중단된
  지점부터 재개"를 제공한다. 큐가 꼭 필요해지는 경우(문서 대량 색인처럼
  HTTP 요청보다 훨씬 오래 걸리는 작업, 또는 여러 사용자의 실행을 동시에
  아주 많이 큐잉해야 하는 경우)는 아직 이 프로젝트에 없다.

체크포인터는 ``AsyncSqliteSaver``를 쓴다(FastAPI가 비동기이므로). 요청마다
``thread_id``를 새로 발급한다 — 그래야 매 채팅이 독립된 실행이 된다.
``session_id``를 요청에 실어 보내면 그 값을 ``thread_id``로 그대로 써서,
클라이언트가 원하면 같은 스레드로 이어서 실행할 수도 있다(예: 이전 요청이
네트워크 문제로 끊겼을 때 같은 session_id로 다시 요청하면 그래프가 중단된
지점부터 이어서 실행된다 — 5절의 체크포인트 재개와 같은 메커니즘이다).
"""

from __future__ import annotations

import html
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from docagent.config import get_settings
from docagent.events import done_event, error_event, sources_event, status_event, token_event
from docagent.graph.build import build_graph
from docagent.graph.nodes import NodeDeps
from docagent.llm import LLMError, chat, chat_json
from docagent.rag.search import dense_search, sparse_search
from docagent.rag.store import get_client, make_chunk_id

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DOCS_DIR = BASE_DIR / "data" / "docs"


def _build_deps() -> NodeDeps:
    """실제 서비스용 의존성: Milvus 검색(5단계) + 모델 서버(9절 공통 인터페이스)."""
    settings = get_settings()
    return NodeDeps(
        dense_search=lambda q, *, limit=5, filter_expr="": dense_search(q, limit=limit, filter_expr=filter_expr),
        sparse_search=lambda q, *, limit=5, filter_expr="": sparse_search(q, limit=limit, filter_expr=filter_expr),
        chat=chat,
        json_chat=chat_json,
        app_base_url=settings.app_base_url,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱 시작 시 체크포인터를 한 번 열어 그래프를 컴파일해 둔다.

    ``AsyncSqliteSaver.from_conn_string``은 비동기 컨텍스트 매니저다
    (langgraph-checkpoint-sqlite 3.1.1에서 실제로 확인). 앱이 떠 있는 동안
    같은 커넥션을 재사용하고, 종료될 때 정리한다.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    settings = get_settings()
    Path(settings.checkpoint_db_path).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(settings.checkpoint_db_path) as saver:
        app.state.graph = build_graph(_build_deps(), checkpointer=saver)
        yield


app = FastAPI(title="docagent step07: LangGraph 오케스트레이션", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None  # 주면 그 thread_id로 이어서 실행(재개)한다


def _chunk_for_stream(text: str, size: int = 40) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


async def _chat_stream(req: ChatRequest, graph: Any) -> AsyncIterator[str]:
    thread_id = req.session_id or uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "question": req.message,
        "retrieved": [],
        "queries_tried": [],
        "trace": [],
    }

    final_state: dict[str, Any] = {}
    try:
        # stream_mode="updates": 노드가 완료될 때마다 {노드 이름: 부분 상태}를 내보낸다.
        # 각 노드가 trace에 채운 {"stage", "message"}를 그대로 status 이벤트로 옮긴다 —
        # 이것이 PROJECT-SPEC.md 4장이 말하는 "애플리케이션이 관측한 실제 실행 사실"이다.
        async for update in graph.astream(initial_state, config=config, stream_mode="updates"):
            for _node_name, partial in update.items():
                for item in partial.get("trace", []):
                    yield status_event(item["stage"], item["message"])
        snapshot = await graph.aget_state(config)
        final_state = snapshot.values
    except LLMError as exc:
        yield error_event("llm_call_failed", str(exc))
        yield done_event("stop")
        return
    except Exception as exc:  # noqa: BLE001 - 예상 못 한 노드 실패도 done으로 마무리한다
        yield error_event("graph_execution_failed", str(exc))
        yield done_event("stop")
        return

    answer_text = final_state.get("answer_text", "")
    for piece in _chunk_for_stream(answer_text):
        yield token_event(piece)
    yield sources_event(final_state.get("citations", []))
    yield done_event("stop")


@app.post("/chat")
async def chat_endpoint(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(_chat_stream(req, app.state.graph), media_type="text/event-stream")


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
    """근거 링크의 도착지. 5단계와 동일하다(PROJECT-SPEC.md 5장 URL 형식)."""

    chunk_id = make_chunk_id(doc_id, page, chunk)
    chunk_text = ""
    try:
        rows = get_client().query(
            get_settings().milvus_collection,
            filter=f'pk == "{chunk_id}"',
            output_fields=["text", "doc_title"],
        )
        if rows:
            chunk_text = rows[0].get("text", "")
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
  <script>
    const el = document.getElementById('highlight');
    if (el) el.scrollIntoView({{block: 'center'}});
  </script>
</body></html>"""
    return HTMLResponse(html_page)
