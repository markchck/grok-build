"""OpenAI 호환 모델 서버 호출.

PROJECT-SPEC.md 9절이 고정한 공통 인터페이스(``chat`` / ``chat_json`` /
``ChatResult`` / ``LLMError`` / ``achat``)를 쓴다. 내부 구현은 openai 파이썬
SDK다(7단계와 같은 형태). 이 장은 스트리밍 답변을 다루지 않으므로
``chat_stream``/``achat_stream``은 만들지 않는다 — 필요해지면 그때 추가한다
(9절은 "필요한 것만 갖는다"를 허용한다).

``ChatResult.raw``에는 ``usage`` 딕셔너리가 그대로 들어 있다. 이 장의
``multiagent/budget.py``가 여기서 누적 토큰 사용량을 뽑아 쓴다.
"""

from __future__ import annotations

import json
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


class ChatResult:
    """PROJECT-SPEC.md 9절이 고정한 필드만 갖는다."""

    __slots__ = ("text", "finish_reason", "tool_calls", "raw")

    def __init__(self, *, text: str, finish_reason: str | None, tool_calls: list[dict[str, Any]], raw: dict[str, Any]):
        self.text = text
        self.finish_reason = finish_reason
        self.tool_calls = tool_calls
        self.raw = raw


def _client(settings: Settings) -> OpenAI:
    return OpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key, timeout=settings.request_timeout_seconds)


def _async_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key, timeout=settings.request_timeout_seconds)


def _wrap_errors(exc: Exception, settings: Settings) -> LLMError:
    if isinstance(exc, APITimeoutError):
        return LLMError(f"모델 서버 응답 시간 초과({settings.request_timeout_seconds}초)")
    if isinstance(exc, APIConnectionError):
        return LLMError(f"모델 서버에 연결할 수 없다: {exc}")
    if isinstance(exc, APIStatusError):
        return LLMError(f"모델 서버 오류 응답: {exc.status_code} {exc.message}")
    if isinstance(exc, APIError):
        return LLMError(f"모델 서버 호출 실패: {exc}")
    return LLMError(f"모델 서버 호출 중 알 수 없는 오류: {exc}")


def chat(
    messages: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
) -> ChatResult:
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
    except (APITimeoutError, APIConnectionError, APIStatusError, APIError) as exc:
        raise _wrap_errors(exc, settings) from exc

    choice = completion.choices[0]
    return ChatResult(
        text=choice.message.content or "",
        finish_reason=choice.finish_reason,
        tool_calls=[],
        raw=completion.model_dump(),
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
    """비동기 버전. FastAPI 엔드포인트(app.py)가 쓴다(PROJECT-SPEC.md 9절)."""

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
    except (APITimeoutError, APIConnectionError, APIStatusError, APIError) as exc:
        raise _wrap_errors(exc, settings) from exc
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
    result = chat(
        messages,
        settings=settings,
        temperature=temperature,
        response_format={"type": "json_schema", "json_schema": {"name": schema_name, "schema": json_schema, "strict": True}},
    )
    try:
        return json.loads(result.text or "{}")
    except json.JSONDecodeError as exc:
        raise LLMError(f"서버가 json_schema를 요청했는데도 유효한 JSON을 돌려주지 않았다: {result.text[:300]}") from exc


def usage_tokens(result: ChatResult) -> tuple[int, bool]:
    """``ChatResult``에서 이번 호출의 총 토큰 수를 뽑는다.

    서버가 ``usage``를 안 주면(8단계 2-5절과 같은 문제) 글자 수 기반 추정치를
    쓰고 두 번째 값을 ``True``(추정)로 돌려준다. 정확한 토큰 수가 아니라는
    사실을 항상 호출부가 알 수 있게 한다.
    """

    usage = (result.raw or {}).get("usage") or {}
    total = usage.get("total_tokens")
    if isinstance(total, int) and total > 0:
        return total, False
    # 추정치: (프롬프트로 보낸 적 없는 응답 텍스트만 여기서 볼 수 있으므로) 응답
    # 텍스트 글자 수 / 4. 매우 거친 경험칙이며 한국어에는 더 안 맞는다(8단계와
    # 같은 한계). 최소 1은 준다 — 호출 자체가 공짜는 아니라는 사실을 반영한다.
    estimated = max(1, len(result.text) // 4)
    return estimated, True
