"""Milvus 컬렉션 생성, 저장, 검색.

pymilvus 2.5.x/2.6.x의 MilvusClient(고수준 클라이언트)를 쓴다. 필드 이름은
PROJECT-SPEC.md 6절 표를 그대로 따른다. `sparse` 필드는 5단계(하이브리드 검색)에서
추가하므로 이 장의 스키마에는 없다.

컬렉션 스키마 (이 장 기준):

| 필드          | 타입                 | 비고                              |
|---------------|----------------------|-----------------------------------|
| pk            | VARCHAR, primary     | 청크 ID                            |
| dense         | FLOAT_VECTOR         | 임베딩. 차원은 실제 모델 호출로 확인 |
| text          | VARCHAR              | 청크 원문                          |
| doc_id        | VARCHAR              | 문서 식별자                        |
| doc_title     | VARCHAR              | 문서명                             |
| page          | INT64                | 페이지(없으면 0)                   |
| chunk_index   | INT64                | 문서 내 청크 순번                  |
| source_url    | VARCHAR              | 원본 URL 또는 로컬 경로            |
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymilvus import DataType, MilvusClient

from docagent.rag.ingest import Chunk

# 임베딩 차원은 하드코딩하지 않는다. 모델마다(그리고 같은 모델도 설정에 따라)
# 차원이 다를 수 있기 때문이다(예: 384, 768, 1024, 1536, 3072 등). 스키마의 dim이
# 실제 임베딩 벡터 길이와 다르면 삽입 시점에 Milvus가 오류를 낸다. 그래서 컬렉션을
# 만들기 전에 임베딩을 한 번 호출해 len(vector)로 실제 차원을 확인한다.


def make_client(uri: str, token: str) -> MilvusClient:
    """MilvusClient를 만든다.

    uri가 http(s):// 로 시작하면 Docker standalone 등 네트워크 서버에 연결한다.
    uri가 로컬 파일 경로(예: "./data/milvus_demo.db")면 pymilvus가 자동으로
    Milvus Lite(같은 프로세스 안에서 도는 내장 모드)로 동작한다. 두 경우 모두
    이 함수의 코드는 같다 — 어느 모드인지는 uri 값 하나로 결정된다.
    """
    return MilvusClient(uri=uri, token=token or None)


def collection_exists(client: MilvusClient, collection_name: str) -> bool:
    return client.has_collection(collection_name)


def create_chunks_collection(
    client: MilvusClient, collection_name: str, dense_dim: int
) -> None:
    """4단계 스키마로 컬렉션을 만든다. 이미 있으면 아무것도 하지 않는다."""
    if client.has_collection(collection_name):
        return

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("pk", DataType.VARCHAR, is_primary=True, max_length=512)
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=dense_dim)
    schema.add_field("text", DataType.VARCHAR, max_length=8192)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=256)
    schema.add_field("doc_title", DataType.VARCHAR, max_length=512)
    schema.add_field("page", DataType.INT64)
    schema.add_field("chunk_index", DataType.INT64)
    schema.add_field("source_url", DataType.VARCHAR, max_length=1024)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="dense",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )

    client.create_collection(
        collection_name=collection_name,
        schema=schema,
        index_params=index_params,
    )


def drop_collection(client: MilvusClient, collection_name: str) -> None:
    if client.has_collection(collection_name):
        client.drop_collection(collection_name)


def insert_chunks(
    client: MilvusClient,
    collection_name: str,
    chunks: list[Chunk],
    dense_vectors: list[list[float]],
) -> int:
    """청크와 그에 대응하는 임베딩 벡터를 저장한다.

    같은 pk로 다시 insert하면 중복 행이 생길 수 있으므로, 재적재 시에는
    scripts/make_sample_data.py 실행 후 컬렉션을 새로 만들거나
    upsert(client.upsert)를 쓰는 방법을 5단계 이후 응용 과제로 남겨둔다.
    """
    if len(chunks) != len(dense_vectors):
        raise ValueError("chunks와 dense_vectors의 길이가 다르다.")

    rows: list[dict[str, Any]] = []
    for chunk, vector in zip(chunks, dense_vectors):
        rows.append(
            {
                "pk": chunk.pk,
                "dense": vector,
                "text": chunk.text,
                "doc_id": chunk.doc_id,
                "doc_title": chunk.doc_title,
                "page": chunk.page,
                "chunk_index": chunk.chunk_index,
                "source_url": chunk.source_url,
            }
        )
    result = client.insert(collection_name=collection_name, data=rows)
    return int(result.get("insert_count", len(rows)))


@dataclass(frozen=True)
class SearchHit:
    pk: str
    score: float
    text: str
    doc_id: str
    doc_title: str
    page: int
    chunk_index: int
    source_url: str


_OUTPUT_FIELDS = ["text", "doc_id", "doc_title", "page", "chunk_index", "source_url"]


def search_dense(
    client: MilvusClient,
    collection_name: str,
    query_vector: list[float],
    top_k: int,
) -> list[SearchHit]:
    """질의 벡터로 dense 검색을 한다. 컬렉션 생성 시 지정한 metric(COSINE)을 그대로 쓴다.

    COSINE 거리에서 Milvus가 돌려주는 score는 코사인 유사도 자체다(1에 가까울수록
    유사, -1에 가까울수록 반대). 근거 부족 판단에 이 score를 그대로 쓴다.
    """
    results = client.search(
        collection_name=collection_name,
        data=[query_vector],
        anns_field="dense",
        limit=top_k,
        output_fields=_OUTPUT_FIELDS,
    )
    hits: list[SearchHit] = []
    for hit in results[0]:
        entity = hit.get("entity", {})
        hits.append(
            SearchHit(
                pk=hit["id"],
                score=float(hit["distance"]),
                text=entity.get("text", ""),
                doc_id=entity.get("doc_id", ""),
                doc_title=entity.get("doc_title", ""),
                page=int(entity.get("page", 0)),
                chunk_index=int(entity.get("chunk_index", 0)),
                source_url=entity.get("source_url", ""),
            )
        )
    return hits
