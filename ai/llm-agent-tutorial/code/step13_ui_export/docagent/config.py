"""환경 변수 로딩.

PROJECT-SPEC.md 2절·9절에 정의된 이름과 시그니처(``Settings``, ``load_settings``,
``get_settings``)를 그대로 쓴다. 8단계의 ``DOCAGENT_STATE_DB``와 4·5단계의
Milvus 값을 그대로 가져다 쓴다.

이번 통합(최종 통합 과제 4·5·6·10번 항목을 이 장에 배선)에서 새로 "추가"하는
필드는 세 그룹이다 — 이름은 전부 PROJECT-SPEC.md 2절에 이미 있던 값을 그대로
재사용한다(9단계·10단계·11단계가 각각의 장에서 이미 정의해 둔 이름이다):

- 10단계(MCP): ``mcp_server_command``/``mcp_server_args``/
  ``mcp_connect_timeout_seconds``/``mcp_call_timeout_seconds``.
- 9단계(트레이싱): ``tracing_enabled``/``phoenix_collector_endpoint``/
  ``trace_project_name``.
- 11단계(멀티에이전트 협업·A2A): ``max_agent_handoffs``/``max_collab_tokens``/
  ``a2a_verifier_url``.

기존 필드는 하나도 이름을 바꾸지 않았다(PROJECT-SPEC.md 9절 원칙).
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

    # Milvus (4단계부터)
    milvus_uri: str
    milvus_token: str
    milvus_collection: str

    # 애플리케이션
    app_base_url: str
    request_timeout_seconds: float
    max_agent_steps: int
    max_agent_seconds: float

    # 8단계부터
    max_agent_tokens: int
    state_db_path: str

    # 10단계부터 (MCP 서버 연결)
    mcp_server_command: str
    mcp_server_args: list[str]
    mcp_connect_timeout_seconds: float
    mcp_call_timeout_seconds: float

    # 9단계부터 (트레이싱)
    tracing_enabled: bool
    phoenix_collector_endpoint: str
    trace_project_name: str

    # 11단계부터 (멀티에이전트 협업 한도 · A2A 최소 흉내 서버 주소)
    max_agent_handoffs: int
    max_collab_tokens: int
    a2a_verifier_url: str


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


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
        request_timeout_seconds=_get_float("REQUEST_TIMEOUT_SECONDS", 60),
        max_agent_steps=_get_int("MAX_AGENT_STEPS", 8),
        max_agent_seconds=_get_float("MAX_AGENT_SECONDS", 120),
        max_agent_tokens=_get_int("MAX_AGENT_TOKENS", 4000),
        state_db_path=os.environ.get("DOCAGENT_STATE_DB", "./data/hitl_state.db"),
        mcp_server_command=os.environ.get("DOCAGENT_MCP_SERVER_COMMAND", "python"),
        mcp_server_args=[
            a for a in os.environ.get("DOCAGENT_MCP_SERVER_ARGS", "-m,mcp_server.server").split(",") if a
        ],
        mcp_connect_timeout_seconds=_get_float("DOCAGENT_MCP_CONNECT_TIMEOUT_SECONDS", 5),
        mcp_call_timeout_seconds=_get_float("DOCAGENT_MCP_CALL_TIMEOUT_SECONDS", 20),
        tracing_enabled=_get_bool("DOCAGENT_TRACING_ENABLED", False),
        phoenix_collector_endpoint=os.environ.get(
            "DOCAGENT_PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces"
        ),
        trace_project_name=os.environ.get("DOCAGENT_TRACE_PROJECT_NAME", "docagent"),
        max_agent_handoffs=_get_int("MAX_AGENT_HANDOFFS", 4),
        max_collab_tokens=_get_int("MAX_COLLAB_TOKENS", 6000),
        a2a_verifier_url=os.environ.get("A2A_VERIFIER_URL", "http://127.0.0.1:8292"),
    )


_cached: Settings | None = None


def get_settings() -> Settings:
    """모듈 전역 캐시. 테스트는 ``load_settings()``를 직접 불러 매번 새 값을 만든다."""

    global _cached
    if _cached is None:
        _cached = load_settings()
    return _cached


def reset_settings_cache() -> None:
    """테스트 전용: 캐시를 지워 다음 ``get_settings()`` 호출이 환경 변수를 다시 읽게 한다."""

    global _cached
    _cached = None
