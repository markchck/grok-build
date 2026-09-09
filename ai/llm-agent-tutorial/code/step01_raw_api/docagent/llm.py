"""모델 서버 호출 (1단계에서 도입).

PROJECT-SPEC.md 9절이 고정한 공통 인터페이스(``chat`` / ``chat_stream`` /
``chat_json`` / ``ChatResult`` / ``LLMError``)를 이 장에서부터 제공한다.
1단계는 예외적으로 그 옆에 HTTP 계층을 직접 가르치기 위한 구현을
나란히 둔다.

- ``raw_*``: httpx로 ``POST {OPENAI_BASE_URL}/chat/completions``를 직접 호출한다.
  OpenAI 호환 API가 실제로 HTTP 위에서 어떻게 오가는지 보여주기 위한 것이다.
- ``chat`` / ``chat_stream`` / ``chat_json``: openai 파이썬 SDK(``OpenAI`` 클라이언트)로
  같은 요청을 보낸다. 2단계부터는 이 SDK 경로만 남고, 이후 모든 단계가
  이 이름을 그대로 쓴다(PROJECT-SPEC.md 9절).

두 방식 모두 최종적으로는 같은 HTTP 요청을 만든다. SDK가 무엇을 대신해 주는지는
docs/01-llm-api-direct.md 4절에서 설명한다. 1단계는 아직 embed()를 쓰지 않는다
(EMBEDDING_MODEL은 4단계부터 Settings에 추가된다).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
)

from docagent.config import Settings, get_settings

# 일시적 오류로 보고 재시도할 HTTP 상태 코드.
# 429(요청 과다), 5xx(서버 오류)는 잠깐 뒤 같은 요청을 다시 보내면 성공할 수 있다.
# 4xx(429 제외)는 요청 자체가 잘못된 것이므로 재시도해도 결과가 같다 → 재시도하지 않는다.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """모델 서버 호출 실패(재시도까지 모두 실패했거나 재시도 대상이 아닌 오류)를 감싼다.

    PROJECT-SPEC.md 9절: 모든 오류는 이 타입으로 감싸서 올린다. 호출부가
    httpx나 openai SDK의 예외 타입을 직접 알 필요가 없게 하기 위해서다.
    """


@dataclass(frozen=True)
class ChatResult:
    text: str
    finish_reason: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# raw httpx 구현: /v1/chat/completions를 직접 호출한다 (1단계 전용, 교육 목적).
# ---------------------------------------------------------------------------


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }


def raw_chat_completion(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 512,
    max_retries: int = 2,
) -> ChatResult:
    """httpx로 일반(비스트리밍) 응답을 한 번 요청한다.

    OpenAI 호환 서버는 대부분 ``max_tokens``를 받는다. OpenAI 공식 API는
    최신 모델에서 ``max_completion_tokens``를 쓰도록 안내하지만, 이 저장소가
    대상으로 하는 로컬/자체 호스팅 OpenAI 호환 서버는 아직 ``max_tokens``만
    지원하는 경우가 많다. 서버가 어떤 이름을 쓰는지는 서버 문서로 확인한다.
    """
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    body = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=settings.request_timeout_seconds) as client:
                response = client.post(url, headers=_headers(settings), json=body)
            if response.status_code in _RETRYABLE_STATUS and attempt < max_retries:
                last_error = LLMError(
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )
                continue
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            return ChatResult(
                text=choice["message"]["content"] or "",
                finish_reason=choice.get("finish_reason"),
                raw=data,
            )
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt >= max_retries:
                raise LLMError(
                    f"{settings.request_timeout_seconds}초 안에 응답이 오지 않았다 "
                    f"(재시도 {attempt}회 모두 실패)."
                ) from exc
        except httpx.HTTPStatusError as exc:
            # raise_for_status()가 던진 4xx/5xx. 재시도 대상이 아니면 즉시 던진다.
            raise LLMError(
                f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc

    # 여기 도달하면 재시도 로직에 버그가 있는 것이다.
    raise LLMError(f"알 수 없는 오류로 요청에 실패했다: {last_error}")


def raw_chat_completion_stream(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 512,
) -> Iterator[str]:
    """httpx로 스트리밍(Server-Sent Events) 응답을 직접 파싱한다.

    서버는 아래 형태의 텍스트 줄을 순서대로 보낸다.

        data: {"id":"...","choices":[{"delta":{"content":"안"},"finish_reason":null}]}
        data: {"id":"...","choices":[{"delta":{"content":"녕"},"finish_reason":null}]}
        data: [DONE]

    각 줄은 ``data: ``로 시작하고 그 뒤에 JSON 조각(청크, chunk) 하나가 온다.
    마지막 줄은 JSON이 아니라 문자열 ``[DONE]``이며, 스트림이 끝났다는 신호다.
    이 함수는 매 청크에서 ``delta.content``만 꺼내 순서대로 넘겨준다(yield).
    """
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    body = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    try:
        with httpx.Client(timeout=settings.request_timeout_seconds) as client, client.stream(
            "POST", url, headers=_headers(settings), json=body
        ) as response:
            if response.status_code >= 400:
                response.read()
                raise LLMError(
                    f"HTTP {response.status_code}: {response.text[:300]}"
                )
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                delta = chunk["choices"][0].get("delta", {})
                content = delta.get("content")
                if content:
                    yield content
    except httpx.TimeoutException as exc:
        raise LLMError(
            f"스트리밍 중 {settings.request_timeout_seconds}초 동안 응답이 없었다."
        ) from exc


# ---------------------------------------------------------------------------
# openai SDK 구현: PROJECT-SPEC.md 9절의 공통 인터페이스.
# 2단계부터는 이 경로만 남는다 — 이후 장의 llm.py도 같은 이름을 그대로 쓴다.
# ---------------------------------------------------------------------------


def _make_client(settings: Settings, max_retries: int = 2) -> OpenAI:
    """SDK 클라이언트를 만든다.

    SDK가 대신 해 주는 일: 요청/응답 스키마를 파이썬 타입으로 감싸기,
    429·5xx에 대한 지수 백오프(exponential backoff) 재시도, 타임아웃 처리,
    스트리밍 청크를 이터레이터로 감싸기. 이 함수 이후의 코드는 이런 세부
    사항을 직접 다루지 않는다.
    """
    return OpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        timeout=settings.request_timeout_seconds,
        max_retries=max_retries,
    )


def _tool_calls_to_dicts(tool_calls: Any) -> list[dict[str, Any]]:
    if not tool_calls:
        return []
    return [
        {
            "id": call.id,
            "type": call.type,
            "function": {
                "name": call.function.name,
                "arguments": call.function.arguments,
            },
        }
        for call in tool_calls
    ]


def chat(
    messages: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
) -> ChatResult:
    """비스트리밍 호출. 1단계에서는 도구 호출을 아직 쓰지 않지만(3단계부터),
    이후 장과 시그니처를 맞추기 위해 tools/response_format을 미리 받아 둔다."""
    settings = settings or get_settings()
    client = _make_client(settings)
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
        raise LLMError(
            f"{settings.request_timeout_seconds}초 안에 응답이 오지 않았다."
        ) from exc
    except APIConnectionError as exc:
        raise LLMError(f"모델 서버에 연결할 수 없다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(f"HTTP {exc.status_code}: {exc.message}") from exc
    except APIError as exc:
        raise LLMError(f"모델 서버 호출 중 오류가 발생했다: {exc}") from exc

    choice = completion.choices[0]
    return ChatResult(
        text=choice.message.content or "",
        finish_reason=choice.finish_reason,
        tool_calls=_tool_calls_to_dicts(choice.message.tool_calls),
        raw=completion.model_dump(),
    )


def chat_stream(
    messages: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> Iterator[str]:
    """SDK로 스트리밍한다. SSE 파싱은 SDK 내부에서 처리되고,
    ``for piece in chat_stream(...)`` 만으로 텍스트 조각을 순서대로 받는다."""
    settings = settings or get_settings()
    client = _make_client(settings)
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
            content = chunk.choices[0].delta.content
            if content:
                yield content
    except APITimeoutError as exc:
        raise LLMError(
            f"스트리밍 중 {settings.request_timeout_seconds}초 동안 응답이 없었다."
        ) from exc
    except APIConnectionError as exc:
        raise LLMError(f"모델 서버에 연결할 수 없다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(f"HTTP {exc.status_code}: {exc.message}") from exc


def chat_json(
    messages: list[dict[str, Any]],
    *,
    schema_name: str,
    json_schema: dict[str, Any],
    settings: Settings | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """구조화된 JSON 출력을 요청한다 (``response_format={"type": "json_schema", ...}``).

    이 기능은 서버마다 지원 여부가 다르다. 미지원 서버는 대부분
    ``response_format`` 자체를 모르는 파라미터로 취급해 400 오류를 내거나,
    파라미터를 무시하고 일반 텍스트를 그대로 돌려준다. 어느 쪽인지는
    scripts/check_server_features.py로 먼저 확인한다.
    """
    result = chat(
        messages,
        settings=settings,
        temperature=temperature,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": json_schema,
                "strict": True,
            },
        },
    )
    try:
        return json.loads(result.text or "{}")
    except json.JSONDecodeError as exc:
        raise LLMError(
            f"서버가 json_schema를 요청했는데도 유효한 JSON을 돌려주지 않았다: "
            f"{result.text[:300]}"
        ) from exc
