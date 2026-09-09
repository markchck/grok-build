"""OpenAI 호환 모델 서버 호출.

3·5단계 llm.py와 같은 방식(httpx로 직접 호출)을 그대로 잇는다. 이 장에서
바뀌는 부분은 하나다: 에이전트 루프가 토큰 예산을 계산하려면 ``usage`` 값이
필요하므로, 응답 ``message``만 돌려주던 기존 ``chat_completion`` 대신
``chat_completion_full``을 새로 두어 ``message``와 ``usage``를 함께 돌려준다.
``chat_completion``은 이전 단계와의 호환을 위해 남겨두고 내부에서
``chat_completion_full``을 호출하도록 바꿨다.

**usage를 돌려주지 않는 서버가 있다.** OpenAI 호환을 표방해도 ``usage`` 필드를
아예 안 주거나 스트리밍에서는 마지막 청크에만 주는 구현이 흔하다. 이 모듈은
``usage``가 없으면 빈 딕셔너리를 돌려줄 뿐 예외를 던지지 않는다. 토큰 예산을
어떻게 근사하는지는 ``docagent/agent.py``의 ``_estimate_tokens``에서 다룬다.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator

import httpx

from docagent.config import get_settings


class LLMError(RuntimeError):
    """모델 서버 호출 실패(HTTP 오류, 타임아웃, 잘못된 응답 형식)."""


def _headers(settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }


def chat_completion_full(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    temperature: float = 0.2,
    settings=None,
) -> dict[str, Any]:
    """비-스트리밍 호출. ``{"message": ..., "usage": ...}``를 돌려준다.

    ``usage``는 서버가 안 주면 빈 딕셔너리다(``{}``).
    """

    settings = settings or get_settings()
    payload: dict[str, Any] = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    try:
        resp = httpx.post(
            url, headers=_headers(settings), json=payload, timeout=settings.request_timeout_seconds
        )
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise LLMError(f"모델 서버 응답 시간 초과({settings.request_timeout_seconds}초)") from exc
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"모델 서버 오류 응답: {exc.response.status_code} {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"모델 서버 호출 실패: {exc}") from exc

    data = resp.json()
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"예상하지 못한 응답 형식: {data}") from exc

    usage = data.get("usage") or {}
    return {"message": message, "usage": usage}


def chat_completion(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    temperature: float = 0.2,
    settings=None,
) -> dict[str, Any]:
    """이전 단계와 같은 시그니처: message 딕셔너리만 돌려준다."""

    return chat_completion_full(
        messages, tools=tools, tool_choice=tool_choice, temperature=temperature, settings=settings
    )["message"]


def stream_chat_completion(messages: list[dict[str, Any]], *, settings=None) -> Iterator[str]:
    settings = settings or get_settings()
    payload = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": 0.2,
        "stream": True,
    }
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    try:
        with httpx.stream(
            "POST", url, headers=_headers(settings), json=payload, timeout=settings.request_timeout_seconds
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if chunk == "[DONE]":
                    break
                event = json.loads(chunk)
                delta = event["choices"][0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield text
    except httpx.TimeoutException as exc:
        raise LLMError(f"모델 서버 응답 시간 초과({settings.request_timeout_seconds}초)") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"모델 서버 호출 실패: {exc}") from exc
