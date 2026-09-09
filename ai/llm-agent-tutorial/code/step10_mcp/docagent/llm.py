"""OpenAI 호환 모델 서버 호출.

PROJECT-SPEC.md 9절이 고정한 공통 인터페이스(``chat`` / ``chat_stream`` /
``chat_json`` / ``ChatResult`` / ``LLMError``)를 쓴다. 내부 구현은 openai
파이썬 SDK다(2단계부터 이 경로만 남는다 — 1단계의 raw httpx 구현은 HTTP
계층을 가르치기 위한 예외였다).

이 모듈은 "모델을 부르는 방법"만 안다. 도구를 실제로 실행하는 책임은
agent.py에 있다. chat()이 반환하는 ChatResult.tool_calls는 모델이 요청한
도구 호출 목록을 그대로 옮긴 것일 뿐, 이 모듈이 함수를 실행하지 않는다.

요청/응답 형식은 OpenAI Chat Completions API를 따른다(참고 문서는 장 본문 하단 참조).
- tools: [{"type": "function", "function": {"name", "description", "parameters"}}]
- 모델이 도구를 쓰기로 하면 tool_calls가 채워진다(ChatResult.tool_calls).
- 도구 실행 결과는 role="tool", tool_call_id=<해당 id>, content=<문자열> 메시지로 되돌려 보낸다.
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


def _tool_calls_to_dicts(tool_calls: Any) -> list[dict[str, Any]]:
    """SDK의 ChatCompletionMessageToolCall 객체 목록을 순수 dict 목록으로 바꾼다.

    agent.py는 OpenAI SDK 타입을 몰라도 되게, 아래 형태만 다룬다.
    [{"id", "type": "function", "function": {"name", "arguments": "<JSON 문자열>"}}]
    """
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
    """비-스트리밍 호출. 에이전트 루프에서 "다음에 뭘 할지" 물을 때 쓴다.

    tools를 주면 tool_choice="auto"로 호출한다 — 모델이 도구를 쓸지 말지
    스스로 판단하게 하는 기본값이다. 특정 도구를 강제로 부르게 하려면
    이 함수를 감싸는 상위 코드에서 messages/tools 구성으로 유도한다.
    """
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
    """스트리밍 호출. 도구 호출이 더 없을 때 최종 답변을 조각 단위로 흘려보낸다.

    2단계에서 만든 SSE 흐름과 같은 방식이다: 서버가 보낸 delta.content 조각을
    그대로 yield한다.
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
    """``chat()``의 비동기 버전(PROJECT-SPEC.md 9절). ``docagent/agent.py``의
    ``_default_chat_fn``이 쓴다 — 클라이언트가 연결을 끊었을 때 이 호출을
    실제로 취소하려면 ``AsyncOpenAI``로 연 연결을 이벤트 루프가 직접 닫을 수
    있어야 하기 때문이다. 이전에는 이 함수가 없어 ``run_in_executor``로 동기
    ``chat()``을 스레드에서 돌렸다 — 조각을 받아오는 것은 됐지만 그 스레드를
    밖에서 끊을 수 없어 취소가 반쪽이었다(agent.py의 예전 주석 참고)."""
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
        tool_calls=_tool_calls_to_dicts(choice.message.tool_calls),
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
