"""OpenAI 호환 모델 서버 호출 (채팅 + 임베딩).

PROJECT-SPEC.md 9절이 고정한 공통 인터페이스(``chat`` / ``chat_json`` /
``embed`` / ``usage_tokens`` / ``ChatResult`` / ``LLMError``)를 openai SDK
하나로 구현한다. 이 장은 8단계에서 httpx 직접 호출을 물려받고 11단계에서
SDK 기반 함수를 별도로 더해, 한동안 같은 일을 하는 구현이 두 벌 있었다
(``chat_completion_full``/``chat_completion``/``stream_chat_completion`` +
``chat``/``chat_json``). 정리하면서 SDK 기반 하나로 합쳤다 — ``docagent/agent.py``
(단일 에이전트 루프)와 ``docagent/multiagent/agents.py``(협업 노드)가 모두
이 모듈의 ``chat()``이 돌려주는 ``ChatResult``를 쓴다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
)

from docagent.config import Settings, get_settings


class LLMError(RuntimeError):
    """모델 서버 호출 실패(HTTP 오류, 타임아웃, 잘못된 응답 형식)."""


def _openai_client(settings: Settings) -> OpenAI:
    return OpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        timeout=settings.request_timeout_seconds,
    )


def embed(texts: list[str], *, settings=None) -> list[list[float]]:
    """openai SDK로 Dense 임베딩을 만든다(5단계와 동일한 계약).

    응답 순서가 입력 순서와 같다고 보장하지 않는 서버가 있으므로 index로
    정렬해 돌려준다.
    """

    if not texts:
        return []
    settings = settings or get_settings()
    client = _openai_client(settings)
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

    try:
        items = sorted(resp.data, key=lambda item: item.index)
    except (AttributeError, TypeError) as exc:
        raise LLMError(f"예상하지 못한 임베딩 응답 형식: {resp}") from exc
    return [item.embedding for item in items]


@dataclass(frozen=True)
class ChatResult:
    text: str
    finish_reason: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def chat(
    messages: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
) -> ChatResult:
    """openai SDK를 쓰는 채팅 호출. ``docagent/agent.py``와 ``docagent/multiagent``
    노드들이 모두 이 함수를 쓴다."""

    settings = settings or get_settings()
    client = _openai_client(settings)
    kwargs: dict[str, Any] = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice if tool_choice is not None else "auto"
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

    data = completion.model_dump()
    try:
        choice_data = data["choices"][0]
        message_data = choice_data["message"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"예상하지 못한 응답 형식: {data}") from exc

    return ChatResult(
        text=message_data.get("content") or "",
        finish_reason=choice_data.get("finish_reason"),
        tool_calls=message_data.get("tool_calls") or [],
        raw=data,
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
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": json_schema, "strict": True},
        },
    )
    try:
        return json.loads(result.text or "{}")
    except json.JSONDecodeError as exc:
        raise LLMError(
            f"서버가 json_schema를 요청했는데도 유효한 JSON을 돌려주지 않았다: {result.text[:300]}"
        ) from exc


def usage_tokens(result: ChatResult) -> tuple[int, bool]:
    """``ChatResult``에서 이번 호출의 총 토큰 수를 뽑는다.

    서버가 ``usage``를 안 주면 글자 수 기반 추정치를 쓰고 두 번째 값을
    ``True``(추정)로 돌려준다.
    """

    usage = (result.raw or {}).get("usage") or {}
    total = usage.get("total_tokens")
    if isinstance(total, int) and total > 0:
        return total, False
    estimated = max(1, len(result.text) // 4)
    return estimated, True
