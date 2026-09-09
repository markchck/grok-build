"""FastAPI 앱 — 승인 대기·재개 엔드포인트를 추가한 8단계 버전.

라우팅이 3단계보다 늘었다. 핵심은 "실행 하나(run)"가 여러 번의 HTTP 요청에
걸쳐 이어질 수 있다는 것이다.

    POST /chat                          새 run 시작, SSE로 진행 이벤트 스트리밍.
                                         approval_request가 나오면 done 없이 스트림이 끝난다.
    GET  /approvals/{approval_id}       대기 중인 승인 요청 조회.
    POST /approvals/{approval_id}/decision   승인/거절/수정 결정 제출(멱등적).
    POST /chat/resume/{run_id}          결정이 내려진 뒤 run을 이어서 진행. 다시 SSE.
    GET  /runs/{run_id}                 저장된 run 상태를 그대로 보여준다(디버그·복구 확인용).

모든 상태는 ``docagent.store``를 거쳐 SQLite 파일에 있다. 이 프로세스가
재시작돼도 ``/runs/{run_id}``와 ``/chat/resume/{run_id}``는 재시작 전과 같은
결과를 낸다 — 5절에서 실제로 서버를 껐다 켜서 이것을 확인한다.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from docagent import events, store
from docagent.agent import aadvance_run, start_run
from docagent.config import get_settings

logger = logging.getLogger("docagent.app")

app = FastAPI(title="docagent step08 - human in the loop")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    """요청마다 새로 열지 않고 프로세스 안에서 하나의 연결을 재사용한다.

    주의: 재사용하는 것은 "이 프로세스 안에서의 커넥션 객체"일 뿐, 상태 자체는
    파일에 있다. 이 함수가 프로세스마다 매번 파일을 여는 것이 핵심이다 —
    그래서 프로세스를 껐다 켜도 같은 파일을 열면 같은 상태를 이어서 쓴다.
    """

    global _conn
    if _conn is None:
        _conn = store.connect(get_settings().state_db_path)
    return _conn


class ChatRequest(BaseModel):
    message: str
    # 데모 전용: 이 장의 스텁 서버는 tools 목록의 첫 번째 도구를 항상 호출한다
    # (agent.py의 _select_tools 설명 참조). 생략하면 평소 순서(TOOL_SPECS)를 쓴다.
    tool_names: list[str] | None = None


class ResumeRequest(BaseModel):
    tool_names: list[str] | None = None


class DecisionRequest(BaseModel):
    action: Literal["approve", "reject", "edit"]
    args: dict[str, Any] | None = None  # action == "edit"일 때 필수
    note: str = ""
    mode: Literal["abort", "alternative"] = "alternative"  # action == "reject"일 때만 쓴다


def _render_event(kind: str, data: dict) -> str:
    if kind == "status":
        return events.status_event(data["stage"], data["message"])
    if kind == "tool_call":
        return events.tool_call_event(data["id"], data["name"], data["args"])
    if kind == "tool_result":
        return events.tool_result_event(data["id"], data["ok"], data["summary"])
    if kind == "approval_request":
        return events.approval_request_event(data["id"], data["action"], data["args"], data["reason"])
    if kind == "token":
        return events.token_event(data["text"])
    if kind == "error":
        return events.error_event(data["code"], data["message"])
    if kind == "done":
        return events.done_event(data["finish_reason"], partial=data.get("partial"))
    return events.error_event("unknown_event", f"unhandled event kind: {kind}")


@app.post("/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    conn = get_conn()
    run_id = start_run(conn, req.message)

    async def event_stream():
        try:
            async for agent_event in aadvance_run(conn, run_id, tool_names=req.tool_names):
                if await request.is_disconnected():
                    logger.info("client disconnected mid-stream run_id=%s", run_id)
                    return
                yield _render_event(agent_event.kind, agent_event.data)
        except asyncio.CancelledError:
            logger.info("stream task cancelled run_id=%s", run_id)
            raise

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"X-Run-Id": run_id})


@app.post("/chat/resume/{run_id}")
async def resume(run_id: str, request: Request, req: ResumeRequest = ResumeRequest()) -> StreamingResponse:
    conn = get_conn()
    if store.load_run(conn, run_id) is None:
        raise HTTPException(status_code=404, detail=f"run을 찾을 수 없다: {run_id}")

    async def event_stream():
        try:
            async for agent_event in aadvance_run(conn, run_id, tool_names=req.tool_names):
                if await request.is_disconnected():
                    logger.info("client disconnected mid-stream run_id=%s", run_id)
                    return
                yield _render_event(agent_event.kind, agent_event.data)
        except asyncio.CancelledError:
            logger.info("stream task cancelled run_id=%s", run_id)
            raise

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"X-Run-Id": run_id})


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
    else:  # edit
        if req.args is None:
            raise HTTPException(status_code=400, detail="action == 'edit'에는 args가 필요하다.")
        status_value, decision_args, mode = "edited", req.args, None

    approval, already_decided = store.decide_approval(
        conn, approval_id, status=status_value, decision_args=decision_args, mode=mode
    )
    return {"approval": dict(approval), "already_decided": already_decided}


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
