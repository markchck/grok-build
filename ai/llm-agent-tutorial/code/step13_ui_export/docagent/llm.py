"""OpenAI 호환 모델 서버 호출 (채팅 + 임베딩).

8단계 llm.py(httpx 직접 호출, ``chat_completion_full``로 message+usage를 함께
돌려준다)에 5단계의 ``embed()``를 더했다. 이 장의 단일 에이전트 루프는 도구
호출·토큰 예산 계산(8단계)과 하이브리드 검색을 위한 임베딩(5단계)을 모두
쓰기 때문이다.

**추가(11단계 멀티에이전트 협업을 이 장에 배선하면서)**: ``chat()``/
``chat_json()``/``ChatResult``/``usage_tokens()``를 아래에 "추가"했다.
``docagent/multiagent/agents.py``(11단계에서 그대로 가져온 코드)가 이
이름들을 그대로 부른다 — PROJECT-SPEC.md 9절이 고정한 공통 인터페이스
이름이므로, 11단계 코드를 한 글자도 고치지 않고 이 장에 옮기려면 이 장의
``llm.py``에도 같은 이름의 함수가 있어야 한다. 기존 ``chat_completion_full``/
``chat_completion``/``stream_chat_completion``/``embed``(httpx 직접 호출)는
그대로 남겨 뒀다 — 이 장의 단일 에이전트 루프(``docagent/agent.py``)가
계속 그 함수들을 쓴다. 새 함수는 openai SDK를 쓴다(PROJECT-SPEC.md 9절:
"내부 구현은 openai SDK를 쓴다. 예외는 1단계뿐이다"를 이제야 이 장에서도
지킨다 — 이전까지 이 장의 llm.py는 8단계에서 물려받은 httpx 직접 호출
방식을 그대로 썼다는 점을 여기 정직하게 남긴다).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx
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


# ---------------------------------------------------------------------------
# 11단계 멀티에이전트 협업 배선을 위해 추가한 공통 인터페이스(PROJECT-SPEC.md 9절).
# 위 함수들(httpx 직접 호출)과는 별개 경로다 — 모듈 docstring 참고.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatResult:
    text: str
    finish_reason: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _openai_client(settings: Settings) -> OpenAI:
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
    """openai SDK를 쓰는 채팅 호출. ``docagent.multiagent`` 노드들이 이 함수를 쓴다."""

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
