"""OpenAI 호환 모델 서버 호출 (채팅 + 임베딩).

8단계 llm.py(httpx 직접 호출, ``chat_completion_full``로 message+usage를 함께
돌려준다)에 5단계의 ``embed()``를 더했다. 이 장의 에이전트 루프는 도구
호출·토큰 예산 계산(8단계)과 하이브리드 검색을 위한 임베딩(5단계)을 모두
쓰기 때문이다. PROJECT-SPEC.md 9절의 공통 인터페이스 이름은 그대로 유지한다.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

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
    """비-스트리밍 호출. ``{"message": ..., "usage": ...}``를 돌려준다."""

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
    return chat_completion_full(
        messages, tools=tools, tool_choice=tool_choice, temperature=temperature, settings=settings
    )["message"]


def stream_chat_completion(messages: list[dict[str, Any]], *, settings=None) -> Iterator[str]:
    settings = settings or get_settings()
    payload = {"model": settings.chat_model, "messages": messages, "temperature": 0.2, "stream": True}
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


def embed(texts: list[str], *, settings=None) -> list[list[float]]:
    """``POST {OPENAI_BASE_URL}/embeddings``로 Dense 임베딩을 만든다(5단계와 동일).

    응답 순서가 입력 순서와 같다고 보장하지 않는 서버가 있으므로 index로
    정렬해 돌려준다.
    """

    if not texts:
        return []
    settings = settings or get_settings()
    payload = {"model": settings.embedding_model, "input": texts}
    url = f"{settings.openai_base_url.rstrip('/')}/embeddings"
    try:
        resp = httpx.post(
            url, headers=_headers(settings), json=payload, timeout=settings.request_timeout_seconds
        )
        resp.raise_for_status()
    except httpx.TimeoutException as exc:
        raise LLMError(f"임베딩 서버 응답 시간 초과({settings.request_timeout_seconds}초)") from exc
    except httpx.HTTPStatusError as exc:
        raise LLMError(
            f"임베딩 서버 오류 응답: {exc.response.status_code} {exc.response.text}. "
            "서버가 /embeddings 엔드포인트를 지원하는지 먼저 확인한다."
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"임베딩 서버 호출 실패: {exc}") from exc

    data = resp.json()
    try:
        items = sorted(data["data"], key=lambda item: item["index"])
    except (KeyError, TypeError) as exc:
        raise LLMError(f"예상하지 못한 임베딩 응답 형식: {data}") from exc
    return [item["embedding"] for item in items]
