"""환경 변수 로딩.

모든 단계가 이 모듈을 공통으로 쓴다(PROJECT-SPEC.md 1절).
환경 변수 이름은 PROJECT-SPEC.md 2절에 고정되어 있으므로 이름을 바꾸지 않는다.
값이 없는 항목은 이후 단계(4단계 Milvus, 2단계 APP_BASE_URL 등)에서 쓰이므로
1단계 실행에는 필요하지 않으면 기본값을 비워 두거나 None으로 둔다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# .env 파일이 있으면 읽어서 프로세스 환경 변수에 채운다.
# 이미 설정된 환경 변수(예: 쉘에서 export한 값)는 덮어쓰지 않는다.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    """1단계에서 쓰는 설정 값 모음."""

    openai_base_url: str
    openai_api_key: str
    chat_model: str
    request_timeout_seconds: float


def _get_env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(
            f"환경 변수 {name}이(가) 설정되지 않았다. "
            f".env 파일을 .env.example을 참고해 만들었는지 확인한다."
        )
    return value or ""


def load_settings() -> Settings:
    """환경 변수를 읽어 Settings로 반환한다.

    필수 값이 비어 있으면 RuntimeError를 던진다. 이 오류는 프로그램을
    시작하자마자 즉시 발생하므로, 모델 서버에 요청을 보낸 뒤에야
    설정 실수를 알게 되는 상황을 막는다.
    """
    return Settings(
        openai_base_url=_get_env(
            "OPENAI_BASE_URL", default="http://localhost:8000/v1", required=True
        ),
        openai_api_key=_get_env(
            "OPENAI_API_KEY", default="sk-local-example-key", required=True
        ),
        chat_model=_get_env("CHAT_MODEL", required=True),
        request_timeout_seconds=float(_get_env("REQUEST_TIMEOUT_SECONDS", default="60")),
    )
