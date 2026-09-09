"""FastAPI 앱 — 13단계 최종 통합.

라우팅은 8단계(승인/재개)와 5단계(``/sources/{doc_id}``)를 합치고, 이 장이
새로 더하는 것은 CSV 다운로드 라우트 하나(``/export/{run_id}.csv``)다.

    POST /chat                          새 run 시작, SSE 스트리밍.
    POST /chat/resume/{run_id}          승인 결정 이후 이어서 진행.
    GET  /approvals/{approval_id}       대기 중인 승인 요청 조회.
    POST /approvals/{approval_id}/decision   승인/거절/수정(멱등적).
    GET  /runs/{run_id}                 저장된 run 상태(디버그용).
    GET  /export/{run_id}.csv           run.table을 CSV로 다운로드.
    GET  /sources/{doc_id}              근거 원문 보기(청크 강조).
"""

from __future__ import annotations

import html
import sqlite3
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from docagent import events, store
from docagent.agent import advance_run, start_run
from docagent.config import get_settings
from docagent.csv_export import build_analysis_csv, content_disposition
from docagent.rag.store import get_client, make_chunk_id

app = FastAPI(title="docagent step13 - ui and export")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DOCS_DIR = BASE_DIR / "data" / "docs"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = store.connect(get_settings().state_db_path)
    return _conn


class ChatRequest(BaseModel):
    message: str
    # 데모 전용: 이 장의 스텁 서버(fake_openai_server.py)가 tools의 첫 항목을
    # 항상 고르는 성질을 이용해 특정 흐름을 재현할 때만 쓴다.
    tool_names: list[str] | None = None


class ResumeRequest(BaseModel):
    tool_names: list[str] | None = None


class DecisionRequest(BaseModel):
    action: Literal["approve", "reject", "edit"]
    args: dict[str, Any] | None = None
    note: str = ""
    mode: Literal["abort", "alternative"] = "alternative"


def _render_event(kind: str, data: dict) -> str:
    if kind == "status":
        return events.status_event(data["stage"], data["message"])
    if kind == "tool_call":
        return events.tool_call_event(data["id"], data["name"], data["args"])
    if kind == "tool_result":
        return events.tool_result_event(data["id"], data["ok"], data["summary"])
    if kind == "approval_request":
        return events.approval_request_event(data["id"], data["action"], data["args"], data["reason"])
    if kind == "todo":
        return events.todo_event(data["items"])
    if kind == "sources":
        return events.sources_event(data["items"])
    if kind == "token":
        return events.token_event(data["text"])
    if kind == "error":
        return events.error_event(data["code"], data["message"])
    if kind == "done":
        return events.done_event(data["finish_reason"], partial=data.get("partial"))
    return events.error_event("unknown_event", f"unhandled event kind: {kind}")


@app.post("/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    conn = get_conn()
    run_id = start_run(conn, req.message)

    def event_stream():
        for agent_event in advance_run(conn, run_id, tool_names=req.tool_names):
            yield _render_event(agent_event.kind, agent_event.data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Run-Id": run_id, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat/resume/{run_id}")
def resume(run_id: str, req: ResumeRequest = ResumeRequest()) -> StreamingResponse:
    conn = get_conn()
    if store.load_run(conn, run_id) is None:
        raise HTTPException(status_code=404, detail=f"run을 찾을 수 없다: {run_id}")

    def event_stream():
        for agent_event in advance_run(conn, run_id, tool_names=req.tool_names):
            yield _render_event(agent_event.kind, agent_event.data)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Run-Id": run_id, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    conn = get_conn()
    run = store.load_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run을 찾을 수 없다: {run_id}")
    return {
        "run_id": run.run_id,
        "status": run.status,
        "step": run.step,
        "finish_reason": run.finish_reason,
        "final_text": run.final_text,
        "pending_approval_id": run.pending_approval_id,
        "usage_total": run.usage_total,
        "usage_estimated": run.usage_estimated,
        "message_count": len(run.messages),
        "table_row_count": len(run.table) if run.table else 0,
        "source_count": len(run.sources),
        "plan": run.plan,
    }


@app.get("/approvals/{approval_id}")
def get_approval(approval_id: str) -> dict:
    conn = get_conn()
    approval = store.get_approval(conn, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"승인 요청을 찾을 수 없다: {approval_id}")
    return dict(approval)


@app.post("/approvals/{approval_id}/decision")
def decide(approval_id: str, req: DecisionRequest) -> dict:
    conn = get_conn()
    if store.get_approval(conn, approval_id) is None:
        raise HTTPException(status_code=404, detail=f"승인 요청을 찾을 수 없다: {approval_id}")

    if req.action == "approve":
        status_value, decision_args, mode = "approved", None, None
    elif req.action == "reject":
        status_value, decision_args, mode = "rejected", {"note": req.note}, req.mode
    else:
        if req.args is None:
            raise HTTPException(status_code=400, detail="action == 'edit'에는 args가 필요하다.")
        status_value, decision_args, mode = "edited", req.args, None

    approval, already_decided = store.decide_approval(
        conn, approval_id, status=status_value, decision_args=decision_args, mode=mode
    )
    return {"approval": dict(approval), "already_decided": already_decided}


# ---------------------------------------------------------------------------
# CSV 다운로드 (이 장의 핵심 산출물)
# ---------------------------------------------------------------------------


@app.get("/export/{run_id}.csv")
def export_csv(run_id: str) -> Response:
    conn = get_conn()
    run = store.load_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run을 찾을 수 없다: {run_id}")
    if not run.table:
        raise HTTPException(status_code=404, detail="이 run에는 아직 다운로드할 분석 표가 없다.")

    body = build_analysis_csv(run.table)
    filename = f"분석결과_{run_id}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition(filename)},
    )


# ---------------------------------------------------------------------------
# 근거 원문 보기 (5단계와 동일한 라우트, 그대로 가져왔다)
# ---------------------------------------------------------------------------


def _load_source_document(doc_id: str) -> str | None:
    path = DOCS_DIR / f"{doc_id}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


@app.get("/sources/{doc_id}", response_class=HTMLResponse)
async def view_source(doc_id: str, page: int = 0, chunk: int = 0) -> HTMLResponse:
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


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
