"""Milvus 컬렉션 생성·적재 — 이 장에서는 dense 필드만 쓴다.

PROJECT-SPEC.md 6장 스키마 중 ``pk``/``dense``/``text``/``doc_id``/``doc_title``/
``page``/``chunk_index``/``source_url``만 그대로 쓰고 ``sparse``는 뺀다.

**왜 sparse(BM25 하이브리드)를 이 장에 옮기지 않았는가** — 5단계 하이브리드
검색을 그대로 옮기려 했으나, VERIFIED-FINDINGS.md 9절에서 이미 확인된
제약이 이 장에 그대로 걸린다: Milvus Lite에서 sparse(BM25 Function) 필드가
있는 컬렉션은 **적재한 프로세스와 다른 프로세스에서 읽으면** 다음 오류로
실패하는 경우가 있다.

    MilvusException: (code=1, message=vector column must be FixedSizeList, got binary)

MCP 서버는 정의상 적재 스크립트와 다른 프로세스이므로(별도로 띄운다) 이
문제가 정확히 걸린다. VERIFIED-FINDINGS.md는 이 오류의 정확한 원인을
"특정하지 못했다"고 기록했고, 5단계 스키마(현재 파일과 거의 동일한 필드
구성)에 한해서만 안정적으로 동작하는 것을 실험으로 확인했다 — 그런데
그 안정적인 조합조차 "왜 되는지"는 모른다. 원인을 모르는 채로 sparse를
이 장에도 그대로 옮기면, 학습자가 재현 불가능한 오류를 만났을 때 이 문서가
도움이 되지 않는다.

그래서 이 장은 **dense 검색만** MCP 도구로 노출한다. dense 전용 컬렉션이
서로 다른 프로세스에서 안정적으로 동작하는 것은 4단계에서 이미 확인했고,
이 장 집필 중에도 별도로 재확인했다(장 본문 "검증 상태" 참고). 하이브리드
검색을 실제로 MCP 서버 뒤에 두고 싶다면 VERIFIED-FINDINGS.md 9절의 권고대로
Milvus standalone(Docker)을 쓴다 — Lite가 아니라 standalone에서는 이
문제가 재현되는지 자체를 아직 확인하지 못했다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pymilvus import DataType, MilvusClient

from mcp_server.config import get_mcp_server_settings

PK_FIELD = "pk"
DENSE_FIELD = "dense"
TEXT_FIELD = "text"

_TEXT_MAX_LENGTH = 8192
_DOC_ID_MAX_LENGTH = 256
_DOC_TITLE_MAX_LENGTH = 512
_URL_MAX_LENGTH = 1024
_PK_MAX_LENGTH = 300


def make_chunk_id(doc_id: str, page: int, chunk_index: int) -> str:
    return f"{doc_id}#p{page}-c{chunk_index}"


@dataclass(frozen=True)
class ChunkRecord:
    doc_id: str
    doc_title: str
    page: int
    chunk_index: int
    text: str
    source_url: str
    dense: list[float]

    @property
    def pk(self) -> str:
        return make_chunk_id(self.doc_id, self.page, self.chunk_index)

    def to_row(self) -> dict:
        return {
            PK_FIELD: self.pk,
            TEXT_FIELD: self.text,
            DENSE_FIELD: self.dense,
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "page": self.page,
            "chunk_index": self.chunk_index,
            "source_url": self.source_url,
        }


def get_client() -> MilvusClient:
    settings = get_mcp_server_settings()
    return MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token or None)


def _build_schema(client: MilvusClient, dense_dim: int):
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(PK_FIELD, DataType.VARCHAR, is_primary=True, max_length=_PK_MAX_LENGTH)
    schema.add_field(DENSE_FIELD, DataType.FLOAT_VECTOR, dim=dense_dim)
    schema.add_field(TEXT_FIELD, DataType.VARCHAR, max_length=_TEXT_MAX_LENGTH)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=_DOC_ID_MAX_LENGTH)
    schema.add_field("doc_title", DataType.VARCHAR, max_length=_DOC_TITLE_MAX_LENGTH)
    schema.add_field("page", DataType.INT64)
    schema.add_field("chunk_index", DataType.INT64)
    schema.add_field("source_url", DataType.VARCHAR, max_length=_URL_MAX_LENGTH)
    return schema


def _build_index_params(client: MilvusClient):
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name=DENSE_FIELD,
        index_name="dense_index",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )
    return index_params


def create_collection(dense_dim: int, *, recreate: bool = False) -> None:
    name = get_mcp_server_settings().milvus_collection
    client = get_client()
    if client.has_collection(name):
        if not recreate:
            raise RuntimeError(f"컬렉션 '{name}'이(가) 이미 있다. 다시 만들려면 recreate=True를 준다.")
        client.drop_collection(name)

    schema = _build_schema(client, dense_dim)
    index_params = _build_index_params(client)
    client.create_collection(name, schema=schema, index_params=index_params, consistency_level="Strong")
    client.load_collection(name)


def ensure_loaded(client: MilvusClient, collection_name: str) -> None:
    """검색 직전에 매번 부른다. VERIFIED-FINDINGS.md 6절: load 상태는 프로세스를
    넘어 유지되지 않는다. MCP 서버는 적재 스크립트와 다른 프로세스이므로 이
    호출을 건너뛰면 첫 검색 요청에서 항상 실패한다."""
    client.load_collection(collection_name)


def insert_chunks(records: Iterable[ChunkRecord]) -> int:
    name = get_mcp_server_settings().milvus_collection
    client = get_client()
    rows = [r.to_row() for r in records]
    if not rows:
        return 0
    client.insert(name, rows)
    client.flush(name)
    return len(rows)
