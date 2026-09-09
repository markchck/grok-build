"""Milvus 컬렉션 생성·마이그레이션 (PROJECT-SPEC.md 6장 스키마).

4단계 컬렉션에는 ``dense`` 필드만 있었다. 이 장에서는 ``sparse``
(SPARSE_FLOAT_VECTOR) 필드를 추가한다. Milvus의 BM25 Function이 ``text``
필드 원문에서 sparse 벡터를 서버 쪽에서 직접 만들어 주므로, 애플리케이션은
sparse 벡터 자체를 계산해서 넣지 않는다 — insert 시 ``text``만 채우면 된다.

API는 pymilvus 저장소(milvus-io/pymilvus, examples/full_text_search/bm25.py,
examples/orm_deprecated/hybrid_search/hello_hybrid_bm25.py)의 예제로 확인했다.
정확한 URL은 장 본문 하단 "참고 문서"를 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pymilvus import DataType, Function, FunctionType, MilvusClient

from docagent.config import settings

# PROJECT-SPEC.md 6장 필드 이름. 바꾸지 않는다.
PK_FIELD = "pk"
DENSE_FIELD = "dense"
SPARSE_FIELD = "sparse"
TEXT_FIELD = "text"

_TEXT_MAX_LENGTH = 8192
_DOC_ID_MAX_LENGTH = 256
_DOC_TITLE_MAX_LENGTH = 512
_URL_MAX_LENGTH = 1024
_PK_MAX_LENGTH = 300


def make_chunk_id(doc_id: str, page: int, chunk_index: int) -> str:
    """PROJECT-SPEC.md 5장 청크 ID 형식: ``{doc_id}#p{page}-c{chunk_index}``."""
    return f"{doc_id}#p{page}-c{chunk_index}"


@dataclass(frozen=True)
class ChunkRecord:
    """Milvus에 넣을 청크 한 건. ``sparse``는 BM25 Function이 채우므로
    여기에는 없다 — insert 시 이 필드에 값을 주면 오히려 오류가 난다."""

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
    return MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token or None)


def _build_schema(client: MilvusClient, dense_dim: int):
    """PROJECT-SPEC.md 6장 스키마 + 5단계에서 추가한 sparse 필드.

    ``text`` 필드에 ``enable_analyzer=True``를 줘야 BM25 Function이 그 위에서
    동작한다(analyzer가 원문을 토큰으로 쪼갠 결과를 BM25 Function이 받는다).
    """
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(PK_FIELD, DataType.VARCHAR, is_primary=True, max_length=_PK_MAX_LENGTH)
    schema.add_field(DENSE_FIELD, DataType.FLOAT_VECTOR, dim=dense_dim)
    schema.add_field(SPARSE_FIELD, DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field(TEXT_FIELD, DataType.VARCHAR, max_length=_TEXT_MAX_LENGTH, enable_analyzer=True)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=_DOC_ID_MAX_LENGTH)
    schema.add_field("doc_title", DataType.VARCHAR, max_length=_DOC_TITLE_MAX_LENGTH)
    schema.add_field("page", DataType.INT64)
    schema.add_field("chunk_index", DataType.INT64)
    schema.add_field("source_url", DataType.VARCHAR, max_length=_URL_MAX_LENGTH)

    bm25_function = Function(
        name="text_bm25",
        function_type=FunctionType.BM25,
        input_field_names=[TEXT_FIELD],
        output_field_names=SPARSE_FIELD,
    )
    schema.add_function(bm25_function)
    return schema


def _build_index_params(client: MilvusClient):
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name=DENSE_FIELD,
        index_name="dense_index",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )
    index_params.add_index(
        field_name=SPARSE_FIELD,
        index_name="sparse_index",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="BM25",
        params={"bm25_k1": 1.2, "bm25_b": 0.75},
    )
    return index_params


def create_collection(dense_dim: int, *, recreate: bool = False, collection_name: str | None = None) -> None:
    """컬렉션을 새로 만든다. ``recreate=True``면 같은 이름이 있어도 지우고 다시 만든다.

    처음부터 sparse 필드를 포함한 스키마로 만드는 경로다. 이미 데이터가 있는
    4단계 컬렉션을 sparse 필드가 있는 스키마로 바꾸려면 이 함수 대신
    ``migrate_add_sparse``를 쓴다.
    """
    name = collection_name or settings.milvus_collection
    client = get_client()
    if client.has_collection(name):
        if not recreate:
            raise RuntimeError(
                f"컬렉션 '{name}'이(가) 이미 있다. 다시 만들려면 recreate=True를 준다."
            )
        client.drop_collection(name)

    schema = _build_schema(client, dense_dim)
    index_params = _build_index_params(client)
    client.create_collection(name, schema=schema, index_params=index_params, consistency_level="Strong")
    client.load_collection(name)


def insert_chunks(records: Iterable[ChunkRecord], *, collection_name: str | None = None) -> int:
    name = collection_name or settings.milvus_collection
    client = get_client()
    rows = [r.to_row() for r in records]
    if not rows:
        return 0
    client.insert(name, rows)
    client.flush(name)
    return len(rows)


# ---------------------------------------------------------------------------
# 마이그레이션: dense만 있던 4단계 컬렉션에 sparse(BM25) 필드를 추가한다.
# ---------------------------------------------------------------------------

# 4단계 컬렉션에서 내보낼 필드. sparse/dense는 여기서 다루지 않는다 —
# dense는 그대로 복사하고, sparse는 새 컬렉션의 BM25 Function이 text로부터
# 다시 만들어 준다.
_EXPORT_FIELDS = [
    PK_FIELD,
    TEXT_FIELD,
    DENSE_FIELD,
    "doc_id",
    "doc_title",
    "page",
    "chunk_index",
    "source_url",
]


def migrate_add_sparse(dense_dim: int, *, batch_size: int = 200) -> int:
    """기존 컬렉션(sparse 없음)을 sparse 필드가 있는 스키마로 재구성한다.

    Milvus는 이미 있는 컬렉션에 "BM25 Function이 만드는 벡터 필드"를 그
    자리에서 덧붙이는 것을 표준 경로로 보장하지 않는다 — Function이 만드는
    출력 필드는 컬렉션을 새로 만들 때 스키마에 포함해야 하는 것이 안전하게
    보장되는 방식이다. 그래서 이 함수는 아래 순서로 "내보내기 → 새로 만들기
    → 다시 넣기 → 이름 바꾸기" 방식을 쓴다.

    1. 기존 컬렉션의 모든 행을 ``query_iterator``로 순서대로 내보낸다.
    2. 임시 이름(``{collection}__migrating``)으로 sparse 필드가 있는 새
       컬렉션을 만든다.
    3. 내보낸 행을 배치 단위로 새 컬렉션에 넣는다(``text``만 채우면
       sparse는 BM25 Function이 채운다).
    4. 기존 컬렉션을 지우고, 임시 컬렉션을 원래 이름으로 바꾼다.

    [확인 필요: pymilvus 최신 개발 버전에는 ``MilvusClient.add_function_field``
    라는, 기존 컬렉션에 Function 출력 필드를 그 자리에서 추가하는 API가
    보인다. 이 API가 실제로 몇 버전의 Milvus 서버부터 동작하는지, 기존 행에
    대한 sparse 값을 자동으로 채워 주는지(백필)는 이 문서 작성 시점에
    공식 문서로 확인하지 못했다. 그 API를 쓰려는 경우 반드시 최신 공식
    문서와 서버 버전을 먼저 확인한다. 아래 구현은 서버 버전에 관계없이
    동작하는 재구성 경로만 다룬다.]
    """
    old_name = settings.milvus_collection
    tmp_name = f"{old_name}__migrating"

    client = get_client()
    if not client.has_collection(old_name):
        raise RuntimeError(f"마이그레이션할 컬렉션 '{old_name}'이(가) 없다.")

    if client.has_collection(tmp_name):
        client.drop_collection(tmp_name)

    schema = _build_schema(client, dense_dim)
    index_params = _build_index_params(client)
    client.create_collection(tmp_name, schema=schema, index_params=index_params, consistency_level="Strong")

    iterator = client.query_iterator(
        old_name, batch_size=batch_size, output_fields=_EXPORT_FIELDS
    )
    moved = 0
    try:
        while True:
            batch = iterator.next()
            if not batch:
                break
            client.insert(tmp_name, batch)
            moved += len(batch)
    finally:
        iterator.close()

    client.flush(tmp_name)
    client.load_collection(tmp_name)

    client.drop_collection(old_name)
    client.rename_collection(tmp_name, old_name)
    return moved
