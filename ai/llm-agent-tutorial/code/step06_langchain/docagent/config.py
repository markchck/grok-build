"""환경 변수 로딩.

PROJECT-SPEC.md 2절에 정의된 이름만 읽는다. 9절이 요구하는 대로
``load_settings()``(매번 새로 읽음)와 ``get_settings()``(``lru_cache``로 한 번만
읽음)를 둘 다 둔다. 1~5단계 코드는 모듈 전역 ``settings = load_settings()``
패턴만 썼는데, 이 장부터는 스펙 9절 시그니처를 그대로 따른다 — 값과 이름은
전혀 바뀌지 않는다.

``settings`` 모듈 전역도 그대로 남겨 둔다. ``docagent.llm``(5단계에서 그대로
옮겨온 "직접 구현" 경로)이 그 이름을 쓰기 때문이다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:
    # python-dotenv가 설치돼 있으면 .env 파일을 읽는다. 없어도 동작한다.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - 선택 의존성
    pass


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"환경 변수 {name}은(는) 정수여야 한다: {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    # OpenAI 호환 모델 서버
    openai_base_url: str
    openai_api_key: str
    chat_model: str
    embedding_model: str

    # Milvus
    milvus_uri: str
    milvus_token: str
    milvus_collection: str

    # 애플리케이션
    app_base_url: str
    request_timeout_seconds: float
    max_agent_steps: int
    max_agent_seconds: float


def load_settings() -> Settings:
    """환경 변수를 매번 새로 읽는다. 테스트에서 설정을 갈아끼울 때 쓴다."""

    return Settings(
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-local-example-key"),
        chat_model=os.environ.get("CHAT_MODEL", "your-chat-model-name"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "your-embedding-model-name"),
        milvus_uri=os.environ.get("DOCAGENT_MILVUS_URI", "http://localhost:19530"),
        milvus_token=os.environ.get("DOCAGENT_MILVUS_TOKEN", ""),
        milvus_collection=os.environ.get("DOCAGENT_MILVUS_COLLECTION", "docagent_chunks"),
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:8080"),
        request_timeout_seconds=float(_get_int("REQUEST_TIMEOUT_SECONDS", 60)),
        max_agent_steps=_get_int("MAX_AGENT_STEPS", 8),
        max_agent_seconds=float(_get_int("MAX_AGENT_SECONDS", 120)),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """프로세스 안에서 한 번만 읽는다. 애플리케이션 코드는 이쪽을 쓴다."""

    return load_settings()


# 5단계까지의 코드가 쓰던 모듈 전역 패턴과의 호환을 위해 남겨 둔다.
settings = get_settings()
