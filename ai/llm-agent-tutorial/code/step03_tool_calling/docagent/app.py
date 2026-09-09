"""FastAPI 앱 — 2단계의 SSE 대화 엔드포인트에 3단계 에이전트 루프를 연결한다.

라우팅 자체는 2단계와 같은 모양(POST /chat, text/event-stream)이다.
달라진 부분은 응답을 만드는 방법뿐이다: 이제 모델을 한 번만 부르고 끝내지
않고, docagent.agent.run_agent()가 만드는 이벤트를 그대로 SSE로 흘려보낸다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from docagent import events
from docagent.agent import run_agent

app = FastAPI(title="docagent step03 - tool calling")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


class ChatRequest(BaseModel):
    message: str


def _render_event(kind: str, data: dict) -> str:
    """agent.AgentEvent를 SSE 문자열로 바꾼다. events.py의 스키마만 쓴다."""

    if kind == "status":
        return events.status_event(data["stage"], data["message"])
    if kind == "tool_call":
        return events.tool_call_event(data["id"], data["name"], data["args"])
    if kind == "tool_result":
        return events.tool_result_event(data["id"], data["ok"], data["summary"])
    if kind == "token":
        return events.token_event(data["text"])
    if kind == "error":
        return events.error_event(data["code"], data["message"])
    if kind == "done":
        return events.done_event(data["finish_reason"])
    # 알 수 없는 kind는 조용히 버리지 않고 에러 이벤트로 알린다.
    return events.error_event("unknown_event", f"unhandled event kind: {kind}")


@app.post("/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    def event_stream():
        for agent_event in run_agent(req.message):
            yield _render_event(agent_event.kind, agent_event.data)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
