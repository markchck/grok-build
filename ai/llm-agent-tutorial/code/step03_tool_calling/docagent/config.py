"""환경 변수 로딩.

PROJECT-SPEC.md 2절에 정의된 이름만 읽는다. 이름은 절대 바꾸지 않는다.
값이 없으면 합리적인 기본값을 쓰거나(로컬 개발용) 필수 항목은 에러로 알린다.

load_settings()/get_settings() 두 이름은 PROJECT-SPEC.md 9절이 모든 단계에
고정한 것이다. load_settings()는 매번 새로 읽고(테스트용), get_settings()는
lru_cache로 한 번만 읽는다(애플리케이션 코드용).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

try:
    # python-dotenv가 설치돼 있으면 .env 파일을 읽는다. 없어도 동작은 한다.
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

    # 애플리케이션
    app_base_url: str
    request_timeout_seconds: int
    max_agent_steps: int
    max_agent_seconds: int


def load_settings() -> Settings:
    """환경 변수를 읽어 Settings를 만든다.

    이 단계(3단계)에서 실제로 쓰는 값은 openai_* 4개와 request_timeout_seconds,
    max_agent_steps, max_agent_seconds다. app_base_url은 5단계 근거 링크에서
    쓰이지만 이름은 미리 고정돼 있으므로 여기서도 읽어 둔다.
    """

    return Settings(
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-local-example-key"),
        chat_model=os.environ.get("CHAT_MODEL", "your-chat-model-name"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "your-embedding-model-name"),
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:8080"),
        request_timeout_seconds=_get_int("REQUEST_TIMEOUT_SECONDS", 60),
        max_agent_steps=_get_int("MAX_AGENT_STEPS", 8),
        max_agent_seconds=_get_int("MAX_AGENT_SECONDS", 120),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """환경 변수를 한 번만 읽어 캐시한다. 애플리케이션 코드는 이쪽을 쓴다.

    테스트에서 환경 변수를 바꾼 뒤 새 값을 읽고 싶으면
    get_settings.cache_clear()를 먼저 호출한다.
    """
    return load_settings()
