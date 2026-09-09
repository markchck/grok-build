"""OpenAI 호환 모델 서버 호출. 채팅과 임베딩 두 가지를 담당한다.

이 모듈은 "애플리케이션"이 모델 서버에 보내는 HTTP 요청을 감싼 것뿐이다.
실제로 다음 토큰을 생성하거나 텍스트를 벡터로 바꾸는 연산은 전부 모델 서버(1단계에서
다룬 OpenAI 호환 서버)가 수행한다. 이 파일은 그 요청을 만들고 응답을 파이썬 값으로
바꿔주는 역할만 한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from openai import OpenAI

from docagent.config import Settings


def make_client(settings: Settings) -> OpenAI:
    return OpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        timeout=settings.request_timeout_seconds,
    )


def embed_texts(client: OpenAI, model: str, texts: list[str]) -> list[list[float]]:
    """텍스트 목록을 임베딩 벡터 목록으로 바꾼다.

    한 번의 요청에 여러 텍스트를 담아 보낼 수 있다(배치). 응답의 data 배열
    순서는 요청한 input 순서와 같다고 OpenAI 임베딩 API 규약에 정의돼 있으므로
    그대로 매칭한다.
    """
    if not texts:
        return []
    resp = client.embeddings.create(model=model, input=texts)
    return [item.embedding for item in resp.data]


def embed_query(client: OpenAI, model: str, text: str) -> list[float]:
    return embed_texts(client, model, [text])[0]


def chat_complete(
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
) -> str:
    """스트리밍 없이 완성된 답변 문자열 하나를 받는다. (실패 상황 재현 등에 사용)"""
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


def chat_stream(
    client: OpenAI,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
) -> Iterator[str]:
    """토큰(텍스트 조각) 단위로 생성한다.

    서버가 스트리밍을 지원하지 않으면 openai 클라이언트가 예외를 던지거나
    조각이 아닌 한 덩어리로만 응답할 수 있다. 1단계에서 만든
    scripts/check_server_features.py로 스트리밍 지원 여부를 먼저 확인해두는 것을
    권장한다(이 장에서는 재구현하지 않는다).
    """
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        stream=True,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content
