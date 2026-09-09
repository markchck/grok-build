"""OpenAI 호환 모델 서버 호출 (채팅 + 임베딩) — "직접 구현" 경로.

5단계 파일을 그대로 옮겨왔다. 6단계(``docagent/models.py``, ``docagent/agent.py``,
``docagent/rag/``)는 이 모듈을 쓰지 않는다 — 그 자리를 ``langchain_openai``의
``ChatOpenAI``/``OpenAIEmbeddings``가 대신한다. 이 파일은 두 가지 이유로 남긴다.

1. ``scripts/compare_with_step05.py``가 "직접 구현으로 호출했을 때"와
   "LangChain으로 호출했을 때"를 같은 서버·같은 입력으로 비교하려면 직접
   구현 경로가 필요하다.
2. ``docs/06-langchain.md`` 7절(직접 구현 vs LangChain 비교)이 코드를 나란히
   놓고 설명한다.

PROJECT-SPEC.md 9절은 이 인터페이스의 표준 이름을 ``chat()``/``chat_stream()``/
``chat_json()``/``embed()``로 정하고 있지만, 1~5단계가 이미
``chat_completion``/``stream_chat_completion``/``embed_texts``로 구현해 두었다.
이 장은 5단계까지의 실제 코드와 이어지는 것을 우선해 이름을 그대로 둔다 —
이름을 스펙에 맞추는 것은 이 장의 범위가 아니다.
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
    temperature: float = 0.2,
) -> dict[str, Any]:
    """비-스트리밍 호출. choices[0].message 딕셔너리를 반환한다."""

    payload: dict[str, Any] = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": temperature,
    }
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    try:
        resp = httpx.post(url, headers=_headers(), json=payload, timeout=settings.request_timeout_seconds)
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
    """스트리밍 호출. delta.content 조각을 순서대로 yield한다."""

    payload = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": 0.2,
        "stream": True,
    }
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    try:
        with httpx.stream(
            "POST", url, headers=_headers(), json=payload, timeout=settings.request_timeout_seconds
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


def embed_texts(texts: list[str]) -> list[list[float]]:
    """``POST {OPENAI_BASE_URL}/embeddings``로 Dense 임베딩을 만든다.

    OpenAI 호환 서버는 대부분 아래 형태로 응답한다.

        {"data": [{"index": 0, "embedding": [...]}, ...], "model": "..."}

    ``data``의 순서가 입력 순서와 같다고 보장하지 않는 서버도 있으므로
    ``index``로 정렬해 되돌려준다.
    """

    if not texts:
        return []

    payload = {"model": settings.embedding_model, "input": texts}
    url = f"{settings.openai_base_url.rstrip('/')}/embeddings"
    try:
        resp = httpx.post(url, headers=_headers(), json=payload, timeout=settings.request_timeout_seconds)
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
        return [item["embedding"] for item in items]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"예상하지 못한 임베딩 응답 형식: {data}") from exc
