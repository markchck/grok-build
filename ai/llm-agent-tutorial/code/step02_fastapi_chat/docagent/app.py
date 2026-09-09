"""FastAPI 앱: 대화 엔드포인트와 SSE 스트리밍.

이 장에서 다루는 범위:
- 세션별 대화 기록 유지(메모리 딕셔너리, 프로세스 하나에서만 유효)
- POST /api/chat 이 text/event-stream으로 token/status/error/done 이벤트를 보낸다
- 클라이언트 연결 종료를 감지해 모델 호출을 실제로 취소한다
- RequestContextMiddleware로 요청 ID·소요 시간을 로그에 남긴다
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .events import done_event, error_event, status_event, token_event
from .llm import LLMError, stream_chat_completion
from .middleware import RequestContextMiddleware

logger = logging.getLogger("docagent.app")

# uvicorn의 기본 로깅 설정(uvicorn.config.LOGGING_CONFIG)은 "uvicorn"/"uvicorn.access"
# 로거에만 핸들러를 붙이고 루트 로거에는 핸들러를 붙이지 않는다. docagent.* 로거는
# propagate=True로 루트까지 올라가지만 루트에 핸들러가 없으면 파이썬 로깅의
# "handler of last resort"(WARNING 이상만 stderr에 출력)로 떨어져 INFO 로그가 조용히
# 사라진다. 그래서 이 프로젝트의 요청 ID·소요 시간 로그(INFO)를 보려면 docagent 로거에
# 핸들러를 직접 붙여야 한다. 이 문제를 실제로 겪는 과정은 이 장의 "실패 상황 실습"에서 다룬다.
_docagent_logger = logging.getLogger("docagent")
_docagent_logger.setLevel(logging.INFO)
if not _docagent_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _docagent_logger.addHandler(_handler)

app = FastAPI(title="docagent — step02 fastapi-chat")
app.add_middleware(RequestContextMiddleware)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# --- 세션 저장소 -----------------------------------------------------------
# 세션 ID -> 대화 기록(OpenAI 메시지 형식의 리스트). 프로세스 메모리에만 있다.
#
# 한계: uvicorn을 --workers 2 이상으로 띄우거나 여러 인스턴스로 배포하면
# 프로세스마다 이 딕셔너리가 따로 생겨 같은 session_id라도 다른 기록을 보게 된다.
# 여러 프로세스에서 세션을 공유하려면 Redis 등 외부 저장소가 필요하다 — 이 장의 범위 밖이다.
SESSIONS: dict[str, list[dict[str, str]]] = {}

SYSTEM_PROMPT = {
    "role": "system",
    "content": "당신은 사용자의 질문에 한국어로 간결하게 답하는 도우미다.",
}


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="사용자 발화")
    session_id: str | None = Field(
        default=None, description="이어서 대화할 세션 ID. 없으면 새로 만든다."
    )


def _get_or_create_session(session_id: str | None) -> tuple[str, list[dict[str, str]]]:
    if session_id and session_id in SESSIONS:
        return session_id, SESSIONS[session_id]
    new_id = session_id or str(uuid.uuid4())
    history: list[dict[str, str]] = [SYSTEM_PROMPT]
    SESSIONS[new_id] = history
    return new_id, history


async def _chat_event_stream(
    request: Request, session_id: str, history: list[dict[str, str]]
):
    """/api/chat 응답 본문을 만드는 async generator.

    yield 되는 문자열이 그대로 text/event-stream 바디에 append된다.
    """
    request_id = request.state.request_id
    yield status_event("planning", "요청을 확인하고 대화 기록을 불러오는 중")

    collected: list[str] = []
    finish_reason: Literal["stop", "cancelled"] = "stop"

    try:
        yield status_event("writing", "모델 서버에 답변을 요청하는 중")
        async for piece in stream_chat_completion(history):
            # request.is_disconnected()는 클라이언트가 이미 끊었는지 확인하는 명시적 확인이다.
            # 이 프로젝트는 BaseHTTPMiddleware를 쓰지 않으므로(middleware.py 참고) 이 값을
            # 신뢰할 수 있다. StreamingResponse 자체도 클라이언트 종료 시 이 generator가
            # 실행되는 태스크를 취소하지만, 그 취소는 다음 await 지점(다음 청크를 기다리는
            # async for)에서만 일어난다 — is_disconnected()로 먼저 확인하면 이미 도착한
            # 조각을 굳이 더 만들지 않고 더 빨리 멈출 수 있다.
            if await request.is_disconnected():
                logger.info("request_id=%s client disconnected mid-stream", request_id)
                finish_reason = "cancelled"
                break
            collected.append(piece)
            yield token_event(piece)
    except asyncio.CancelledError:
        # StreamingResponse가 클라이언트 종료를 감지해 이 태스크를 직접 취소한 경우.
        # 이미 연결이 끊어졌으므로 더 이상 yield해도 클라이언트에 도달하지 않는다.
        # 로그만 남기고 다시 raise해 취소를 정상적으로 완료시킨다(취소를 삼키지 않는다).
        logger.info("request_id=%s stream task cancelled", request_id)
        raise
    except LLMError as exc:
        logger.warning("request_id=%s llm_error code=%s message=%s", request_id, exc.code, exc)
        yield error_event(exc.code, str(exc))
        yield done_event("stop")
        return

    if finish_reason == "stop" and collected:
        history.append({"role": "assistant", "content": "".join(collected)})

    yield done_event(finish_reason)


@app.post("/api/chat")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    session_id, history = _get_or_create_session(payload.session_id)
    history.append({"role": "user", "content": payload.message})

    return StreamingResponse(
        _chat_event_stream(request, session_id, history),
        media_type="text/event-stream",
        headers={
            # SSE는 캐시되면 안 되고, 일부 리버스 프록시(nginx)가 응답을 버퍼링하지 않도록
            # X-Accel-Buffering: no를 함께 보낸다.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
        },
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# 정적 파일(static/index.html, static/app.js)은 API 라우트를 모두 등록한 뒤 마지막에
# "/"에 마운트한다. Starlette는 등록 순서대로 경로를 매칭하므로, /api/chat처럼 앞서
# 등록된 명시적 경로가 이 마운트보다 항상 먼저 매칭된다.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
