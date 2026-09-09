"""OpenAI 호환 모델 서버 호출 (채팅 + 임베딩).

PROJECT-SPEC.md 9절이 고정한 공통 인터페이스(``chat`` / ``chat_stream`` /
``chat_json`` / ``embed`` / ``ChatResult`` / ``LLMError``)를 쓴다. 내부 구현은
openai 파이썬 SDK다(3·4단계와 동일한 형태).

Sparse 벡터(BM25)는 이 모듈이 만들지 않는다. 이 장에서는 Milvus 서버 안의
BM25 Function이 만든다 — 이유는 docs/05-hybrid-search-citations.md 2절과
4-2절에서 설명한다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
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
    """모델 서버 호출 실패(HTTP 오류, 타임아웃, 잘못된 응답 형식)를 감싼다."""


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
    """비-스트리밍 호출. 이 장은 도구 호출을 쓰지 않지만 시그니처는
    다른 단계와 맞춘다."""
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
    """스트리밍 호출. delta.content 조각을 순서대로 yield한다.

    이 장의 /chat은 실제로 이 함수를 쓰지 않는다 — 인용 검증을 위해 답변을
    먼저 전부 받아야 하기 때문이다(app.py 상단 설명 참고). 다른 단계와
    시그니처를 맞추기 위해 그대로 남겨 둔다.
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


async def achat(
    messages: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
) -> ChatResult:
    """``chat()``의 비동기 버전(PROJECT-SPEC.md 9절). app.py가 쓴다 —
    클라이언트가 연결을 끊었을 때 이 호출을 실제로 취소하려면 ``AsyncOpenAI``로
    연 연결을 이벤트 루프가 직접 닫을 수 있어야 하기 때문이다.
    ``OpenAIInstrumentor``는 ``AsyncOpenAI``도 계측하므로 자동 계측 스팬은
    ``achat``으로 바꿔도 그대로 남는다."""
    settings = settings or get_settings()
    client = _async_client(settings)
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
        completion = await client.chat.completions.create(**kwargs)
    except APITimeoutError as exc:
        raise LLMError(f"모델 서버 응답 시간 초과({settings.request_timeout_seconds}초)") from exc
    except APIConnectionError as exc:
        raise LLMError(f"모델 서버에 연결할 수 없다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(f"모델 서버 오류 응답: {exc.status_code} {exc.message}") from exc
    except APIError as exc:
        raise LLMError(f"모델 서버 호출 실패: {exc}") from exc
    finally:
        await client.close()

    choice = completion.choices[0]
    return ChatResult(
        text=choice.message.content or "",
        finish_reason=choice.finish_reason,
        tool_calls=[],
        raw=completion.model_dump(),
    )


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
    """``POST {OPENAI_BASE_URL}/embeddings``로 Dense 임베딩을 만든다.

    응답 data 배열의 순서가 입력 순서와 같다고 보장하지 않는 서버도 있으므로
    index로 정렬해 되돌려준다.
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
