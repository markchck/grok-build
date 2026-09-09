"""환경 변수 로딩.

PROJECT-SPEC.md 2절·9절의 이름과 시그니처를 그대로 잇는다. 이 장에서 새로
추가하는 두 값(둘 다 "추가"만 한다 — 기존 이름은 바꾸지 않는다):

- ``MAX_AGENT_HANDOFFS``: 4-4절(핸드오프)에서 도입한 한도. 슈퍼바이저처럼
  매번 제어권이 돌아오는 구조가 아니라, 에이전트가 다른 에이전트에게 직접
  제어권을 넘기는(``Command(goto=...)``) 횟수를 센다. 이 값이 필요한 이유는
  PROJECT-SPEC.md 7절의 ``max_agent_steps``만으로는 부족하기 때문이다 —
  ``max_agent_steps``는 그래프 노드 실행 횟수 전체를 세지만, "상호 위임"이
  문제가 되는 것은 그중에서도 "제어권이 바로 옆 에이전트로 계속 넘어가는"
  패턴이므로 별도로 추적하고 별도 사유(``finish_reason=delegation_loop``)로
  구분해 보고한다. 2-6·4-5절에서 이유를 자세히 설명한다.
- ``MAX_COLLAB_TOKENS``: 협업 전체(슈퍼바이저 + 하위 에이전트 전부)가 쓸 수
  있는 누적 토큰 예산. 8단계의 ``MAX_AGENT_TOKENS``는 "에이전트 하나의 실행
  (run)"을 단위로 삼았다. 이 장은 여러 에이전트가 관여하므로 예산의 단위를
  "협업 전체"로 새로 정의해야 한다 — 이름이 다른 이유다(9절 "이미 있는
  필드의 이름은 바꾸지 않고 필요한 필드를 추가만 한다").
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
    # OpenAI 호환 모델 서버 (PROJECT-SPEC.md 9절 고정)
    openai_base_url: str
    openai_api_key: str
    chat_model: str
    embedding_model: str

    # Milvus (이 장은 검색을 쓰지 않지만 공통 인터페이스라 필드는 유지한다)
    milvus_uri: str
    milvus_token: str
    milvus_collection: str

    # 애플리케이션 (PROJECT-SPEC.md 7절)
    app_base_url: str
    request_timeout_seconds: float
    max_agent_steps: int
    max_agent_seconds: float

    # 8단계에서 추가된 값. 이 장에서도 협업 "run" 개념에 재사용한다.
    max_agent_tokens: int

    # 11단계에서 새로 추가.
    max_agent_handoffs: int
    max_collab_tokens: int

    # 11단계: 이 장의 A2A 최소 예제가 바라볼 외부 검증 에이전트 주소.
    a2a_verifier_url: str


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
        max_agent_handoffs=_get_int("MAX_AGENT_HANDOFFS", 4),
        max_collab_tokens=_get_int("MAX_COLLAB_TOKENS", 6000),
        a2a_verifier_url=os.environ.get("A2A_VERIFIER_URL", "http://127.0.0.1:8291"),
    )


_cached: Settings | None = None


def get_settings() -> Settings:
    """모듈 전역 캐시(8단계와 같은 방식). 환경 변수를 한 번만 읽는다.

    테스트는 ``load_settings()``를 직접 불러 매번 새 값을 만든다.
    """

    global _cached
    if _cached is None:
        _cached = load_settings()
    return _cached


def reset_settings_cache() -> None:
    """테스트에서 환경 변수를 바꾼 뒤 ``get_settings()``가 새 값을 읽게 한다."""

    global _cached
    _cached = None
