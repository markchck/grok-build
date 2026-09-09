"""환경 변수 로딩.

PROJECT-SPEC.md 2절에 정의된 이름만 읽는다. 이름은 절대 바꾸지 않는다.

이 장에서 **새로 추가**하는 이름은 ``DOCAGENT_MCP_*`` 네 개뿐이다
(PROJECT-SPEC.md에도 "10단계에서 추가"라고 표시해 함께 갱신했다). MCP 서버
연결 정보(어떤 명령으로 서버 프로세스를 띄울지, 연결·호출에 각각 얼마나
기다릴지)는 이전 단계에 없던 새로운 개념이라 새 이름이 필요하다 — 기존
``REQUEST_TIMEOUT_SECONDS``는 모델 서버 HTTP 호출용이지 MCP 세션용이
아니므로 재사용하지 않는다.

load_settings()/get_settings()는 PROJECT-SPEC.md 9절이 모든 단계에 고정한
이름이다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

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


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"환경 변수 {name}은(는) 숫자여야 한다: {raw!r}") from exc


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

    # MCP 서버 연결 (10단계 추가)
    mcp_server_command: str
    mcp_server_args: list[str] = field(default_factory=list)
    mcp_connect_timeout_seconds: float = 5.0
    mcp_call_timeout_seconds: float = 20.0


def load_settings() -> Settings:
    """환경 변수를 읽어 Settings를 만든다.

    ``DOCAGENT_MCP_SERVER_ARGS``는 콤마로 구분된 문자열이다(예:
    ``-m,mcp_server.server``). 셸 인용을 신경 쓰지 않도록 간단한 형식을
    골랐다 — 인자에 콤마가 들어가는 경우는 이 장의 서버에서 없다.
    """
    raw_args = os.environ.get("DOCAGENT_MCP_SERVER_ARGS", "-m,mcp_server.server")
    mcp_args = [a for a in raw_args.split(",") if a]

    return Settings(
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-local-example-key"),
        chat_model=os.environ.get("CHAT_MODEL", "your-chat-model-name"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "your-embedding-model-name"),
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:8080"),
        request_timeout_seconds=_get_int("REQUEST_TIMEOUT_SECONDS", 60),
        max_agent_steps=_get_int("MAX_AGENT_STEPS", 8),
        max_agent_seconds=_get_int("MAX_AGENT_SECONDS", 120),
        mcp_server_command=os.environ.get("DOCAGENT_MCP_SERVER_COMMAND", "python"),
        mcp_server_args=mcp_args,
        mcp_connect_timeout_seconds=_get_float("DOCAGENT_MCP_CONNECT_TIMEOUT_SECONDS", 5.0),
        mcp_call_timeout_seconds=_get_float("DOCAGENT_MCP_CALL_TIMEOUT_SECONDS", 20.0),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
