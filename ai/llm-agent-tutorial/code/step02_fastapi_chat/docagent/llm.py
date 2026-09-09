"""모델 서버 호출.

OpenAI 호환 Chat Completions API를 openai 파이썬 SDK로 스트리밍 호출한다.
1단계(step01_raw_api)에서 다룬 것과 같은 SDK·같은 환경 변수를 그대로 쓴다.
이 모듈은 HTTP·세션·SSE를 전혀 모른다 — app.py가 그 책임을 진다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI

from .config import get_settings


class LLMError(Exception):
    """모델 서버 호출 실패를 app.py의 error 이벤트로 옮기기 위한 공통 예외.

    code는 PROJECT-SPEC.md 4절 error 이벤트의 data.code에 그대로 들어간다.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _build_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        timeout=settings.request_timeout_seconds,
    )


# 프로세스당 커넥션 풀을 하나만 재사용한다. 요청마다 새로 만들면 TCP 연결이 매번 새로 생긴다.
_client = _build_client()


async def stream_chat_completion(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    """messages를 모델 서버에 보내고 답변 텍스트 조각을 순서대로 내보낸다.

    호출한 쪽(app.py)의 asyncio 태스크가 취소되면(클라이언트 연결 종료 등)
    `async for` 지점에서 asyncio.CancelledError가 발생하고, finally에서
    stream.close()가 실행돼 모델 서버로 가는 HTTP 연결도 함께 끊긴다.
    openai 파이썬 SDK의 AsyncStream.close()는 "응답을 닫고 연결을 반환한다.
    응답 바디를 끝까지 읽으면 자동으로 호출된다"고 문서화돼 있다 — 즉 정상 종료 시에는
    호출하지 않아도 되지만, 취소처럼 끝까지 읽지 못하고 끊는 경로에서는 직접 불러야 한다.
    """
    try:
        stream = await _client.chat.completions.create(
            model=get_settings().chat_model,
            messages=messages,
            stream=True,
        )
    except APIConnectionError as exc:
        raise LLMError("connection_error", f"모델 서버에 연결할 수 없다: {exc}") from exc
    except APITimeoutError as exc:
        raise LLMError("timeout", f"모델 서버 응답이 시간 안에 오지 않았다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(
            "api_status_error",
            f"모델 서버가 오류 상태를 반환했다 (status={exc.status_code}): {exc.message}",
        ) from exc
    except APIError as exc:
        raise LLMError("api_error", f"모델 서버 호출 중 오류가 발생했다: {exc}") from exc

    try:
        async for chunk in stream:
            if not chunk.choices:
                # 일부 서버는 사용량 정보만 담은 마지막 청크를 choices 없이 보낸다.
                continue
            delta = chunk.choices[0].delta
            if delta is not None and delta.content:
                yield delta.content
    finally:
        await stream.close()
