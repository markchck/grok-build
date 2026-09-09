"""OpenAI 호환 모델 서버 호출.

3·5단계 llm.py와 같은 방식(httpx로 직접 호출)을 그대로 잇는다. PROJECT-SPEC.md
9절이 고정한 공통 인터페이스(``chat`` / ``chat_stream`` / ``achat`` /
``ChatResult`` / ``LLMError``)를 쓴다.

이 장에서 바뀌는 부분은 하나다: 에이전트 루프가 토큰 예산을 계산하려면
``usage`` 값이 필요하므로, ``ChatResult.raw``에 서버 응답 전체(``usage``
포함)를 그대로 담아 둔다. ``docagent/agent.py``는 ``response.raw.get("usage")``로
이 값을 꺼내 쓴다.

**usage를 돌려주지 않는 서버가 있다.** OpenAI 호환을 표방해도 ``usage`` 필드를
아예 안 주거나 스트리밍에서는 마지막 청크에만 주는 구현이 흔하다. ``usage``가
없으면 ``raw``에 그 키가 아예 없거나 빈 값이 들어갈 뿐 예외를 던지지 않는다.
토큰 예산을 어떻게 근사하는지는 ``docagent/agent.py``의 ``_estimate_tokens``에서
다룬다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from docagent.config import get_settings


class LLMError(RuntimeError):
    """모델 서버 호출 실패(HTTP 오류, 타임아웃, 잘못된 응답 형식)."""


@dataclass(frozen=True)
class ChatResult:
    text: str
    finish_reason: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _headers(settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }


def _to_chat_result(data: dict[str, Any]) -> ChatResult:
    try:
        choice = data["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"예상하지 못한 응답 형식: {data}") from exc

    return ChatResult(
        text=message.get("content") or "",
        finish_reason=choice.get("finish_reason"),
        tool_calls=message.get("tool_calls") or [],
        raw=data,
    )


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    settings=None,
) -> ChatResult:
    """비-스트리닝 호출. ``raw``에 ``usage``를 포함한 서버 응답 전체가 담긴다."""

    settings = settings or get_settings()
    payload: dict[str, Any] = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

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

    return _to_chat_result(resp.json())


async def achat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    settings=None,
) -> ChatResult:
    """``chat()``의 비동기 버전(PROJECT-SPEC.md 9절).

    ``docagent/agent.py``의 ``aadvance_run``(비동기 에이전트 루프)이 쓴다.
    클라이언트가 연결을 끊었을 때 이 호출을 실제로 취소하려면
    ``httpx.AsyncClient``로 연 연결을 이벤트 루프가 직접 닫을 수 있어야
    하기 때문이다 — 동기 ``chat()``을 스레드에서 돌리면 그
    스레드를 밖에서 끊을 수 없어 취소가 반쪽이 된다(2단계 llm.py와 같은 이유).
    """

    settings = settings or get_settings()
    payload: dict[str, Any] = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                url, headers=_headers(settings), json=payload, timeout=settings.request_timeout_seconds
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMError(f"모델 서버 응답 시간 초과({settings.request_timeout_seconds}초)") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"모델 서버 오류 응답: {exc.response.status_code} {exc.response.text}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"모델 서버 호출 실패: {exc}") from exc

    return _to_chat_result(resp.json())


def chat_stream(messages: list[dict[str, Any]], *, settings=None) -> Iterator[str]:
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
