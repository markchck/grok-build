"""환경 변수 로딩.

PROJECT-SPEC.md 2절에 정의된 이름은 그대로 쓰고, 이 장에서 새로 필요한 값만
"추가"한다(기존 이름은 바꾸지 않는다).

이 장에서 추가하는 두 값:
- ``MAX_AGENT_TOKENS``: 한 번의 에이전트 실행(run)이 쓸 수 있는 누적 토큰 예산.
  PROJECT-SPEC.md 7절은 "최대 단계·최대 실행 시간"만 이름을 고정했고 토큰 예산은
  이 장에서 처음 다룬다. 새 환경 변수이므로 추가만 하고 기존 이름은 그대로 둔다.
- ``DOCAGENT_STATE_DB``: 승인 대기 상태와 실행 상태를 저장하는 SQLite 파일 경로.
  프로세스가 죽어도 살아남아야 하는 상태이므로(4절) 메모리가 아니라 파일에 둔다.
  Milvus 쪽과 마찬가지로 라이브러리가 예약한 이름과 겹치지 않도록 ``DOCAGENT_``
  접두사를 붙인다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
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

    # Milvus (이 장에서는 쓰지 않지만 공통 인터페이스이므로 필드는 유지한다)
    milvus_uri: str
    milvus_token: str
    milvus_collection: str

    # 애플리케이션
    app_base_url: str
    request_timeout_seconds: int
    max_agent_steps: int
    max_agent_seconds: int

    # 8단계 추가
    max_agent_tokens: int
    state_db_path: str


def load_settings() -> Settings:
    """환경 변수를 매번 새로 읽는다. 테스트에서 쓴다."""

    return Settings(
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-local-example-key"),
        chat_model=os.environ.get("CHAT_MODEL", "your-chat-model-name"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "your-embedding-model-name"),
        milvus_uri=os.environ.get("DOCAGENT_MILVUS_URI", "http://localhost:19530"),
        milvus_token=os.environ.get("DOCAGENT_MILVUS_TOKEN", ""),
        milvus_collection=os.environ.get("DOCAGENT_MILVUS_COLLECTION", "docagent_chunks"),
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:8080"),
        request_timeout_seconds=_get_int("REQUEST_TIMEOUT_SECONDS", 60),
        max_agent_steps=_get_int("MAX_AGENT_STEPS", 8),
        max_agent_seconds=_get_int("MAX_AGENT_SECONDS", 120),
        max_agent_tokens=_get_int("MAX_AGENT_TOKENS", 4000),
        state_db_path=os.environ.get("DOCAGENT_STATE_DB", "./data/hitl_state.db"),
    )


_cached: Settings | None = None


def get_settings() -> Settings:
    """lru_cache 대신 모듈 전역 캐시를 직접 둔다(환경 변수를 한 번만 읽는다).

    PROJECT-SPEC.md 9절의 ``get_settings()``와 같은 역할이다. 테스트는
    ``load_settings()``를 직접 불러 매번 새 값을 만든다.
    """

    global _cached
    if _cached is None:
        _cached = load_settings()
    return _cached


settings = load_settings()
