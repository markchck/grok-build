"""HTTP 요청 단위 미들웨어: 요청 ID 부여, 처리 시간 측정, 로그 기록.

**중요**: 이 미들웨어는 FastAPI/Starlette가 제공하는 두 가지 방법 중
`BaseHTTPMiddleware`(또는 `@app.middleware("http")`)가 아니라 순수 ASGI 미들웨어로 만든다.

이유: `BaseHTTPMiddleware`로 감싼 요청에서는 `Request.is_disconnected()`가
클라이언트가 실제로 연결을 끊어도 항상 False를 반환하는 알려진 문제가 있다
(Starlette 0.21.0부터 보고, 이 문서 작성 시점까지 미해결 — 참고 문서 참조).
`BaseHTTPMiddleware`는 요청을 감쌀 때 스코프를 새로 구성하고 응답 바디를 다시 읽는
방식으로 동작하는데, 이 과정이 연결 종료 감지에 쓰는 anyio CancelScope의 타이밍과
충돌한다. 이 프로젝트의 /api/chat은 연결 종료 감지가 핵심 기능이므로 그 위에
BaseHTTPMiddleware를 얹지 않는다. 실제로 겪는 과정은 이 장의 "실패 상황 실습"에서 재현한다.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("docagent.request")


class RequestContextMiddleware:
    """요청마다 request_id를 만들어 scope["state"]에 넣고, 종료 시 소요 시간을 로그로 남긴다.

    순수 ASGI 미들웨어는 (app) 하나만 받는 생성자와 __call__(scope, receive, send)만
    가지면 된다. Starlette의 Request.state는 scope["state"] 딕셔너리를 그대로 노출하므로,
    여기서 넣은 값을 라우트 핸들러에서 request.state.request_id로 그대로 읽을 수 있다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # 웹소켓·lifespan 이벤트는 요청 로그 대상이 아니므로 그대로 통과시킨다.
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        state: dict[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id

        method = scope.get("method", "")
        path = scope.get("path", "")
        start = time.perf_counter()
        status_code_holder: dict[str, int] = {}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code_holder["status"] = message["status"]
                headers = MutableHeaders(scope=message)
                headers.append("x-request-id", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            status = status_code_holder.get("status", 0)
            # 요청 ID로 같은 요청의 로그를 나중에 묶어 검색할 수 있게 구조화된 필드로 남긴다.
            logger.info(
                "request_id=%s method=%s path=%s status=%s elapsed_ms=%.1f",
                request_id,
                method,
                path,
                status,
                elapsed_ms,
            )
