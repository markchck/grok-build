"""모델 서버 호출.

PROJECT-SPEC.md 9절이 고정한 공통 인터페이스(``chat`` / ``chat_stream`` /
``chat_json`` / ``ChatResult`` / ``LLMError``)를 쓴다. 1단계(step01_raw_api)에서
정한 이름과 시그니처를 그대로 잇는다. 내부 구현은 openai 파이썬 SDK이고
동기(sync)와 비동기(async) 두 벌을 제공한다.

- CLI·스크립트·단위 테스트는 읽기 쉬운 동기 함수(``chat``, ``chat_stream``)를 쓴다.
- FastAPI 엔드포인트는 비동기 함수(``achat``, ``achat_stream``)를 쓴다.
  클라이언트가 연결을 끊었을 때 **모델 서버로 나가는 호출까지 실제로 취소**하려면
  이벤트 루프가 열린 스트림을 직접 닫을 수 있어야 하기 때문이다. 동기 제너레이터를
  별도 스레드에서 돌리면 조각을 받아오는 것까지는 되지만 그 스레드를 밖에서
  끊을 수 없어 취소가 반쪽이 된다.

이 모듈은 "모델을 어떻게 부르는가"만 안다. HTTP·세션·SSE는 전혀 모른다 — app.py의 책임이다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    AsyncOpenAI,
    APITimeoutError,
    OpenAI,
)

from .config import Settings, get_settings


class LLMError(RuntimeError):
    """모델 서버 호출 실패를 감싸는 공통 예외.

    PROJECT-SPEC.md 9절: 호출부가 openai SDK의 예외 타입을 직접 알 필요가
    없게, 모든 오류를 이 타입으로 감싸서 올린다.
    """


@dataclass(frozen=True)
class ChatResult:
    text: str
    finish_reason: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _client(settings: Settings) -> OpenAI:
    return OpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        timeout=settings.request_timeout_seconds,
    )


def chat(
    messages: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
) -> ChatResult:
    """비스트리밍 호출. 이 장은 아직 tools를 쓰지 않지만(3단계부터),
    이후 장과 시그니처를 맞추기 위해 받아 둔다."""
    settings = settings or get_settings()
    client = _client(settings)
    kwargs: dict[str, Any] = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if response_format is not None:
        kwargs["response_format"] = response_format

    try:
        completion = client.chat.completions.create(**kwargs)
    except APITimeoutError as exc:
        raise LLMError(f"모델 서버 응답이 시간 안에 오지 않았다: {exc}") from exc
    except APIConnectionError as exc:
        raise LLMError(f"모델 서버에 연결할 수 없다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(
            f"모델 서버가 오류 상태를 반환했다 (status={exc.status_code}): {exc.message}"
        ) from exc
    except APIError as exc:
        raise LLMError(f"모델 서버 호출 중 오류가 발생했다: {exc}") from exc

    choice = completion.choices[0]
    return ChatResult(
        text=choice.message.content or "",
        finish_reason=choice.finish_reason,
        tool_calls=[],
        raw=completion.model_dump(),
    )


def chat_stream(
    messages: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> Iterator[str]:
    """동기 스트리밍 호출. delta.content 조각을 순서대로 yield한다.

    app.py는 이 제너레이터를 별도 스레드에서 소비해 비동기 SSE 응답으로
    연결한다(``_stream_chat_completion_async`` 참고).
    """
    settings = settings or get_settings()
    client = _client(settings)
    try:
        stream = client.chat.completions.create(
            model=settings.chat_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
    except APIConnectionError as exc:
        raise LLMError(f"모델 서버에 연결할 수 없다: {exc}") from exc
    except APITimeoutError as exc:
        raise LLMError(f"모델 서버 응답이 시간 안에 오지 않았다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(
            f"모델 서버가 오류 상태를 반환했다 (status={exc.status_code}): {exc.message}"
        ) from exc
    except APIError as exc:
        raise LLMError(f"모델 서버 호출 중 오류가 발생했다: {exc}") from exc

    try:
        for chunk in stream:
            if not chunk.choices:
                # 일부 서버는 사용량 정보만 담은 마지막 청크를 choices 없이 보낸다.
                continue
            delta = chunk.choices[0].delta
            if delta is not None and delta.content:
                yield delta.content
    except APIError as exc:
        raise LLMError(f"스트리밍 중 오류가 발생했다: {exc}") from exc
    finally:
        stream.close()


def chat_json(
    messages: list[dict[str, Any]],
    *,
    schema_name: str,
    json_schema: dict[str, Any],
    settings: Settings | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """구조화된 JSON 출력을 요청한다. 서버가 json_schema를 지원하는지는
    scripts/check_server_features.py(1단계)로 먼저 확인한다."""
    result = chat(
        messages,
        settings=settings,
        temperature=temperature,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": json_schema, "strict": True},
        },
    )
    try:
        return json.loads(result.text or "{}")
    except json.JSONDecodeError as exc:
        raise LLMError(
            f"서버가 json_schema를 요청했는데도 유효한 JSON을 돌려주지 않았다: "
            f"{result.text[:300]}"
        ) from exc


def _async_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        timeout=settings.request_timeout_seconds,
    )


async def achat_stream(
    messages: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> AsyncIterator[str]:
    """비동기 스트리밍 호출. ``chat_stream``과 같은 조각을 같은 순서로 준다.

    이 코루틴을 소비하는 태스크가 취소되면(클라이언트가 연결을 끊어
    ``asyncio.CancelledError``가 올라오면) ``finally``에서 스트림을 닫아
    모델 서버로 가는 HTTP 연결까지 함께 끊는다. 서버가 계속 토큰을 생성하며
    자원을 쓰는 것을 막는 것이 목적이다.
    """
    settings = settings or get_settings()
    client = _async_client(settings)
    try:
        stream = await client.chat.completions.create(
            model=settings.chat_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
    except APIConnectionError as exc:
        raise LLMError(f"모델 서버에 연결할 수 없다: {exc}") from exc
    except APITimeoutError as exc:
        raise LLMError(f"모델 서버 응답이 시간 안에 오지 않았다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(
            f"모델 서버가 오류 상태를 반환했다 (status={exc.status_code}): {exc.message}"
        ) from exc
    except APIError as exc:
        raise LLMError(f"모델 서버 호출 중 오류가 발생했다: {exc}") from exc

    try:
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is not None and delta.content:
                yield delta.content
    except APIError as exc:
        raise LLMError(f"스트리밍 중 오류가 발생했다: {exc}") from exc
    finally:
        # 정상 종료·오류·취소 어느 경우에도 스트림과 연결을 닫는다.
        await stream.close()
        await client.close()
