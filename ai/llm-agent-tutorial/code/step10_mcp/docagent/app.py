"""FastAPI 앱 — MCP 도구를 쓰는 에이전트를 SSE로 서비스한다.

**연결 생명주기(Host의 책임)**: 이 서버 프로세스는 시작할 때 MCP 서버
프로세스를 한 번 띄우고, 그 연결을 애플리케이션 전역에서 재사용한다(요청마다
새 프로세스를 띄우지 않는다 — 프로세스 시작 비용이 매 요청마다 들면
비효율적이고, MCP 서버 쪽 상태(Milvus 클라이언트 등)도 매번 새로 만들어야
한다). 연결이 끊기면(``McpToolsClient``가 자체적으로 최대 1회 재연결을
시도하고도 실패하면) ``/chat``은 ``error``+``done(cancelled)`` 이벤트로
그 요청만 실패시킨다 — 서버 프로세스 자체를 죽이지 않는다. 다음 요청이
오면 ``McpToolsClient.call_tool()``이 다시 자동으로 재연결을 시도한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from docagent.agent import run_agent
from docagent.events import done_event, error_event, status_event, token_event, tool_call_event, tool_result_event
from docagent.mcp_client import McpConnectionError, McpToolsClient

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱 시작 시 MCP 서버에 연결하고, 종료 시 정리한다.

    시작 시점의 연결 실패는 **애플리케이션을 죽이지 않는다** — 그 대신
    ``app.state.mcp_client``를 "연결 시도는 됐지만 아직 세션이 없는" 상태로
    두고, 첫 ``/chat`` 요청이 왔을 때 다시 연결을 시도하게 한다(그때도
    실패하면 그 요청만 오류로 끝난다). MCP 서버가 아직 안 떠 있는 상태로
    호스트 애플리케이션을 먼저 띄우고 싶은 개발 흐름을 막지 않기 위해서다.
    """
    client = McpToolsClient()
    try:
        await client.connect()
    except McpConnectionError as exc:
        print(f"[startup] MCP 서버에 연결하지 못했다(요청 시 재시도한다): {exc}")
    app.state.mcp_client = client
    yield
    await client.close()


app = FastAPI(title="docagent step10: MCP 문서·CSV 도구", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    message: str


async def _chat_stream(req: ChatRequest, mcp_client: McpToolsClient) -> AsyncIterator[str]:
    async for event in run_agent(req.message, mcp_client=mcp_client):
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
async def chat_endpoint(req: ChatRequest) -> StreamingResponse:
    mcp_client: McpToolsClient = app.state.mcp_client
    return StreamingResponse(_chat_stream(req, mcp_client), media_type="text/event-stream")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/healthz/mcp")
async def mcp_health() -> dict:
    """MCP 연결 상태를 확인하는 보조 엔드포인트. 실패 상황 실습(장 본문 6절)에서
    "지금 서버가 붙어 있는지"를 빠르게 확인하는 용도다."""
    mcp_client: McpToolsClient = app.state.mcp_client
    try:
        specs = await mcp_client.list_tool_specs()
        return {"connected": True, "tools": [s["function"]["name"] for s in specs]}
    except McpConnectionError as exc:
        return {"connected": False, "error": str(exc)}
