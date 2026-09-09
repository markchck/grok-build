"""환경 변수 로딩.

PROJECT-SPEC.md 2절에 이름을 고정해 둔 환경 변수만 읽는다.
새 변수가 필요한 장은 이름을 추가만 하고 기존 이름을 바꾸지 않는다.

load_settings()는 호출할 때마다 환경 변수를 새로 읽는다(테스트용).
get_settings()는 lru_cache로 한 번만 읽어 캐시한다(애플리케이션 코드용).
두 함수 모두 PROJECT-SPEC.md 9절이 모든 단계에 고정한 이름이다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

# 프로세스 시작 시 .env 파일을 읽어 os.environ에 채워 넣는다.
# 이미 설정된 환경 변수(예: 배포 환경의 실제 값)는 덮어쓰지 않는다.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_base_url: str
    openai_api_key: str
    chat_model: str
    app_base_url: str
    request_timeout_seconds: float


def load_settings() -> Settings:
    """환경 변수를 읽어 Settings를 만든다. 호출할 때마다 새로 읽는다."""
    return Settings(
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-local-example-key"),
        chat_model=os.environ.get("CHAT_MODEL", "your-chat-model-name"),
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:8080"),
        request_timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60")),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """환경 변수를 한 번만 읽어 캐시한다.

    FastAPI 요청마다 os.environ을 다시 읽지 않도록 lru_cache로 고정한다.
    테스트에서 값을 바꾸려면 get_settings.cache_clear()를 먼저 호출한다.
    """
    return load_settings()
