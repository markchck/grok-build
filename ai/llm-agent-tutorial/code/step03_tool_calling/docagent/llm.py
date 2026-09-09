"""OpenAI 호환 모델 서버 호출.

이 모듈은 "모델을 부르는 방법"만 안다. 도구를 실제로 실행하는 책임은
agent.py에 있다. llm.py는 모델의 응답(도구 호출 요청 포함)을 그대로 돌려줄 뿐,
그 응답 안의 tool_calls를 해석해서 함수를 실행하지 않는다.

요청/응답 형식은 OpenAI Chat Completions API를 따른다(참고 문서는 장 본문 하단 참조).
- tools: [{"type": "function", "function": {"name", "description", "parameters"}}]
- tool_choice: "auto" | "none" | "required" | {"type": "function", "function": {"name": "..."}}
- 응답 message.tool_calls: [{"id", "type": "function", "function": {"name", "arguments": "<JSON 문자열>"}}]
- 도구 실행 결과는 role="tool", tool_call_id=<해당 id>, content=<문자열> 메시지로 되돌려 보낸다.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from docagent.config import settings


class LLMError(RuntimeError):
    """모델 서버 호출 실패(HTTP 오류, 타임아웃, 잘못된 응답 형식)."""


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }


def chat_completion(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """비-스트리밍 호출. 에이전트 루프에서 "다음에 뭘 할지" 물을 때 쓴다.

    반환값은 OpenAI 호환 서버의 choices[0].message 딕셔너리다.
    (content, tool_calls, role 등을 담는다. 실행은 호출한 쪽 책임이다.)
    """

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
            url,
            headers=_headers(),
            json=payload,
            timeout=settings.request_timeout_seconds,
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
        return data["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"예상하지 못한 응답 형식: {data}") from exc


def stream_chat_completion(messages: list[dict[str, Any]]) -> Iterator[str]:
    """스트리밍 호출. 도구 호출이 더 없을 때 최종 답변을 토큰 단위로 흘려보낸다.

    2단계에서 만든 SSE 흐름과 같은 방식이다: 서버가 보낸 delta.content 조각을
    그대로 yield한다.
    """

    payload = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": 0.2,
        "stream": True,
    }
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    try:
        with httpx.stream(
            "POST",
            url,
            headers=_headers(),
            json=payload,
            timeout=settings.request_timeout_seconds,
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
