"""환경 변수 로딩.

PROJECT-SPEC.md 2절·9절에 정의된 이름과 시그니처(``Settings``, ``load_settings``,
``get_settings``)를 그대로 쓰고, 이 장에서 필요한 값만 "추가"한다(기존 이름은
바꾸지 않는다).

이 장에서 추가하는 값:
- ``DOCAGENT_AGENT_WORKDIR``: Deep Agents의 파일시스템 백엔드(``deepagents.
  backends.LocalShellBackend``)가 파일 읽기/쓰기·스크립트 실행의 기준으로 삼는
  루트 디렉터리. 이 값 아래(`skills/`, `data/`, `results/`)만 에이전트가 건드릴
  수 있다 — 8단계의 ``DOCAGENT_STATE_DB``와 같은 이유로, 실행 위치와 무관하게
  항상 이 단계 폴더를 가리키도록 명시적인 환경 변수를 둔다.
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

    # 12단계 추가
    agent_workdir: str


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
        agent_workdir=os.environ.get("DOCAGENT_AGENT_WORKDIR", "."),
    )


_cached: Settings | None = None


def get_settings() -> Settings:
    """모듈 전역 캐시. PROJECT-SPEC.md 9절과 같은 역할이다.

    테스트는 ``load_settings()``를 직접 불러 매번 새 값을 만든다.
    """

    global _cached
    if _cached is None:
        _cached = load_settings()
    return _cached
