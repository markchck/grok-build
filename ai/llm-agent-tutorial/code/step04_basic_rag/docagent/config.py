"""환경 변수 로딩. 이름은 PROJECT-SPEC.md 2절 기준으로 고정한다."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# .env가 있으면 읽어들인다. 없어도 오류를 내지 않는다(실 서비스에서는 환경 변수로 직접 주입).
load_dotenv()


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


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
    request_timeout_seconds: int

    # 이 장에서 쓰는 RAG 파라미터
    chunk_size_chars: int
    chunk_overlap_chars: int
    retrieval_top_k: int
    retrieval_score_threshold: float


def load_settings() -> Settings:
    """환경 변수를 읽어 Settings를 만든다.

    필수 값이 비어 있으면 바로 오류를 내서, 서버가 뜬 뒤 첫 요청에서야
    실패하는 상황을 피한다.
    """
    required = {
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", ""),
        "CHAT_MODEL": os.environ.get("CHAT_MODEL", ""),
        "EMBEDDING_MODEL": os.environ.get("EMBEDDING_MODEL", ""),
        "MILVUS_URI": os.environ.get("MILVUS_URI", ""),
        "MILVUS_COLLECTION": os.environ.get("MILVUS_COLLECTION", ""),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(
            "다음 환경 변수가 비어 있다: " + ", ".join(missing) + " (.env.example 참고)"
        )

    return Settings(
        openai_base_url=required["OPENAI_BASE_URL"],
        openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-local-example-key"),
        chat_model=required["CHAT_MODEL"],
        embedding_model=required["EMBEDDING_MODEL"],
        milvus_uri=required["MILVUS_URI"],
        milvus_token=os.environ.get("MILVUS_TOKEN", ""),
        milvus_collection=required["MILVUS_COLLECTION"],
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:8080"),
        request_timeout_seconds=_get_int("REQUEST_TIMEOUT_SECONDS", 60),
        chunk_size_chars=_get_int("CHUNK_SIZE_CHARS", 800),
        chunk_overlap_chars=_get_int("CHUNK_OVERLAP_CHARS", 150),
        retrieval_top_k=_get_int("RETRIEVAL_TOP_K", 4),
        retrieval_score_threshold=_get_float("RETRIEVAL_SCORE_THRESHOLD", 0.35),
    )
