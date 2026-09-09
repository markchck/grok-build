"""FastAPI 앱 — 2단계의 SSE 대화 엔드포인트에 3단계 에이전트 루프를 연결한다.

라우팅 자체는 2단계와 같은 모양(POST /chat, text/event-stream)이다.
달라진 부분은 응답을 만드는 방법뿐이다: 이제 모델을 한 번만 부르고 끝내지
않고, docagent.agent.arun_agent()가 만드는 이벤트를 그대로 SSE로 흘려보낸다.

핸들러가 ``async def``이고 ``arun_agent()``(동기 ``run_agent()``가 아니라)를
쓰는 이유는 PROJECT-SPEC.md 9절의 취소 요구사항 때문이다: 클라이언트가
연결을 끊으면 이 스트림을 만드는 태스크가 취소되고, 그 취소가
``achat`` 안의 ``finally``까지 전달되어 모델 서버로 나가는 HTTP 연결이
실제로 닫힌다(2단계 ``_stream_chat_completion_async``와 같은 이유).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from docagent import events
from docagent.agent import arun_agent

logger = logging.getLogger("docagent.app")

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
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    async def event_stream():
        try:
            async for agent_event in arun_agent(req.message):
                # request.is_disconnected()는 클라이언트가 이미 끊었는지 확인하는
                # 명시적 확인이다(2단계 app.py와 같은 이유). 다음 모델 호출을
                # 굳이 시작하지 않고 더 빨리 멈출 수 있다.
                if await request.is_disconnected():
                    logger.info("client disconnected mid-stream")
                    return
                yield _render_event(agent_event.kind, agent_event.data)
        except asyncio.CancelledError:
            # StreamingResponse가 클라이언트 종료를 감지해 이 태스크를 직접
            # 취소한 경우. 이미 연결이 끊어졌으므로 더 이상 yield해도 클라이언트에
            # 도달하지 않는다. 취소를 삼키지 않고 다시 raise한다.
            logger.info("stream task cancelled")
            raise

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}
