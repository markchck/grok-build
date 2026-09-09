"""MCP 서버 프로세스가 읽는 환경 변수.

이 서버는 ``docagent`` 패키지와 별도 프로세스이므로 ``docagent.config``를
import하지 않는다(위 `__init__.py` 참고). 대신 **같은 이름**의 환경 변수를
이 파일에서 독립적으로 읽는다. PROJECT-SPEC.md 2절의 이름(``OPENAI_*``,
``DOCAGENT_MILVUS_*``)은 두 프로세스 모두에서 동일해야 하므로, 이름 자체는
그대로 가져다 쓴다 — 이름을 공유하는 것과 코드를 공유하는 것은 다르다.

이 장에서 서버 프로세스가 실제로 읽는 값:
- ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` / ``EMBEDDING_MODEL``: 문서 검색
  도구가 질의를 임베딩할 때 쓴다(4·5단계와 동일한 모델 서버).
- ``DOCAGENT_MILVUS_URI`` / ``DOCAGENT_MILVUS_TOKEN`` / ``DOCAGENT_MILVUS_COLLECTION``:
  문서 청크가 들어 있는 Milvus 컬렉션.
- ``DOCAGENT_MCP_SALES_CSV``: CSV 조회 도구가 읽을 파일 경로. 이 장에서
  새로 추가하는 이름이라 ``DOCAGENT_`` 접두사를 붙인다(PROJECT-SPEC.md 2절
  규칙). PROJECT-SPEC.md에도 이 절에서 추가했다고 표시해 둔다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - 선택 의존성
    pass

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class McpServerSettings:
    openai_base_url: str
    openai_api_key: str
    embedding_model: str
    milvus_uri: str
    milvus_token: str
    milvus_collection: str
    sales_csv_path: Path


@lru_cache(maxsize=1)
def get_mcp_server_settings() -> McpServerSettings:
    return McpServerSettings(
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-local-example-key"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "your-embedding-model-name"),
        milvus_uri=os.environ.get("DOCAGENT_MILVUS_URI", str(_DATA_DIR / "milvus_lite.db")),
        milvus_token=os.environ.get("DOCAGENT_MILVUS_TOKEN", ""),
        milvus_collection=os.environ.get("DOCAGENT_MILVUS_COLLECTION", "docagent_chunks"),
        sales_csv_path=Path(os.environ.get("DOCAGENT_MCP_SALES_CSV", str(_DATA_DIR / "sales.csv"))),
    )
