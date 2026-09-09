"""OpenAI 호환 모델 서버 호출. 채팅과 임베딩 두 가지를 담당한다.

PROJECT-SPEC.md 9절이 고정한 공통 인터페이스(``chat`` / ``chat_stream`` /
``chat_json`` / ``embed`` / ``ChatResult`` / ``LLMError``)를 쓴다. 이 장부터
``embed()``를 실제로 쓴다(EMBEDDING_MODEL이 Settings에 추가된 장이다).

이 모듈은 "애플리케이션"이 모델 서버에 보내는 HTTP 요청을 감싼 것뿐이다.
실제로 다음 토큰을 생성하거나 텍스트를 벡터로 바꾸는 연산은 전부 모델 서버(1단계에서
다룬 OpenAI 호환 서버)가 수행한다. 이 파일은 그 요청을 만들고 응답을 파이썬 값으로
바꿔주는 역할만 한다.
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
    APITimeoutError,
    AsyncOpenAI,
    OpenAI,
)

from docagent.config import Settings, get_settings


class LLMError(RuntimeError):
    """모델 서버 호출 실패(연결/타임아웃/상태 오류/응답 형식 오류)를 감싼다."""


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
    """비스트리밍 호출. (실패 상황 재현 등에서 스트리밍 없이 답을 받을 때 쓴다.)"""
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
        raise LLMError(f"모델 서버 응답 시간 초과({settings.request_timeout_seconds}초)") from exc
    except APIConnectionError as exc:
        raise LLMError(f"모델 서버에 연결할 수 없다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(f"모델 서버 오류 응답: {exc.status_code} {exc.message}") from exc
    except APIError as exc:
        raise LLMError(f"모델 서버 호출 실패: {exc}") from exc

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
    """토큰(텍스트 조각) 단위로 생성한다.

    서버가 스트리밍을 지원하지 않으면 openai 클라이언트가 예외를 던지거나
    조각이 아닌 한 덩어리로만 응답할 수 있다. 1단계에서 만든
    scripts/check_server_features.py로 스트리밍 지원 여부를 먼저 확인해두는 것을
    권장한다(이 장에서는 재구현하지 않는다).
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
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
    except APITimeoutError as exc:
        raise LLMError(f"모델 서버 응답 시간 초과({settings.request_timeout_seconds}초)") from exc
    except APIConnectionError as exc:
        raise LLMError(f"모델 서버에 연결할 수 없다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(f"모델 서버 오류 응답: {exc.status_code} {exc.message}") from exc
    except APIError as exc:
        raise LLMError(f"모델 서버 호출 실패: {exc}") from exc


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
    """``chat_stream()``의 비동기 버전(PROJECT-SPEC.md 9절). app.py가 쓴다.

    이 코루틴을 소비하는 태스크가 취소되면(클라이언트가 연결을 끊어
    ``asyncio.CancelledError``가 올라오면) ``finally``에서 스트림과 클라이언트를
    닫아 모델 서버로 나가는 HTTP 연결까지 함께 끊는다. 동기 ``chat_stream()``을
    스레드에서 돌리는 방식으로는 그 스레드를 밖에서 끊을 수 없어 취소가 반쪽이
    된다(2단계 llm.py의 같은 설명 참고).
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
    except APITimeoutError as exc:
        raise LLMError(f"모델 서버 응답 시간 초과({settings.request_timeout_seconds}초)") from exc
    except APIConnectionError as exc:
        raise LLMError(f"모델 서버에 연결할 수 없다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(f"모델 서버 오류 응답: {exc.status_code} {exc.message}") from exc
    except APIError as exc:
        raise LLMError(f"모델 서버 호출 실패: {exc}") from exc

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


def chat_json(
    messages: list[dict[str, Any]],
    *,
    schema_name: str,
    json_schema: dict[str, Any],
    settings: Settings | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """구조화된 JSON 출력을 요청한다. 서버 지원 여부는
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


def embed(texts: list[str], *, settings: Settings | None = None) -> list[list[float]]:
    """텍스트 목록을 임베딩 벡터 목록으로 바꾼다. (4단계부터 쓴다)

    한 번의 요청에 여러 텍스트를 담아 보낼 수 있다(배치). 응답의 data 배열
    순서는 요청한 input 순서와 같다고 OpenAI 임베딩 API 규약에 정의돼 있지만,
    이를 보장하지 않는 서버도 있으므로 index로 정렬해 되돌려준다.
    """
    if not texts:
        return []
    settings = settings or get_settings()
    client = _client(settings)
    try:
        resp = client.embeddings.create(model=settings.embedding_model, input=texts)
    except APITimeoutError as exc:
        raise LLMError(f"임베딩 서버 응답 시간 초과({settings.request_timeout_seconds}초)") from exc
    except APIConnectionError as exc:
        raise LLMError(f"임베딩 서버에 연결할 수 없다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(
            f"임베딩 서버 오류 응답: {exc.status_code} {exc.message}. "
            "서버가 /embeddings 엔드포인트를 지원하는지 먼저 확인한다."
        ) from exc
    except APIError as exc:
        raise LLMError(f"임베딩 서버 호출 실패: {exc}") from exc

    items = sorted(resp.data, key=lambda item: item.index)
    return [item.embedding for item in items]
