"""Dense / Sparse / Hybrid 검색.

pymilvus API는 milvus-io/pymilvus 저장소의 아래 예제로 확인했다(장 본문 하단
"참고 문서" 참고).

- ``examples/full_text_search/bm25.py`` — BM25 Function, sparse 필드 검색
- ``examples/orm_deprecated/hybrid_search/hello_hybrid_bm25.py`` —
  ``AnnSearchRequest`` + ``RRFRanker``로 하는 하이브리드 검색
- ``examples/hybrid_search.py`` — ``MilvusClient.hybrid_search`` 시그니처

검색 결과 한 건은 아래 형태의 딕셔너리로 통일해서 돌려준다(citations.py가
바로 쓸 수 있게).

    {"pk", "text", "doc_id", "doc_title", "page", "chunk_index",
     "source_url", "score"}
"""

from __future__ import annotations

from typing import Literal

from pymilvus import AnnSearchRequest, RRFRanker, WeightedRanker

from docagent.config import settings
from docagent.llm import embed_texts
from docagent.rag.store import DENSE_FIELD, SPARSE_FIELD, get_client

OUTPUT_FIELDS = ["text", "doc_id", "doc_title", "page", "chunk_index", "source_url"]

RankerName = Literal["rrf", "weighted"]


def _to_hit(raw: dict) -> dict:
    entity = raw.get("entity", {})
    return {
        "pk": raw["id"],
        "text": entity.get("text", ""),
        "doc_id": entity.get("doc_id", ""),
        "doc_title": entity.get("doc_title", ""),
        "page": entity.get("page", 0),
        "chunk_index": entity.get("chunk_index", 0),
        "source_url": entity.get("source_url", ""),
        "score": raw.get("distance", 0.0),
    }


def dense_search(
    query: str,
    *,
    limit: int = 5,
    filter_expr: str = "",
    collection_name: str | None = None,
) -> list[dict]:
    """의미 검색. 질문을 임베딩해서 코사인 유사도로 가장 가까운 청크를 찾는다."""
    name = collection_name or settings.milvus_collection
    client = get_client()
    vectors = embed_texts([query])
    results = client.search(
        name,
        data=vectors,
        anns_field=DENSE_FIELD,
        search_params={"metric_type": "COSINE", "params": {}},
        limit=limit,
        filter=filter_expr,
        output_fields=OUTPUT_FIELDS,
    )
    return [_to_hit(hit) for hit in results[0]] if results else []


def sparse_search(
    query: str,
    *,
    limit: int = 5,
    filter_expr: str = "",
    collection_name: str | None = None,
) -> list[dict]:
    """키워드 검색. BM25 Function이 질의 원문을 그대로 받아 용어 일치·빈도로
    점수를 매긴다. 임베딩을 계산하지 않는다 — 원문 문자열을 그대로 넘긴다."""
    name = collection_name or settings.milvus_collection
    client = get_client()
    results = client.search(
        name,
        data=[query],
        anns_field=SPARSE_FIELD,
        search_params={"metric_type": "BM25", "params": {}},
        limit=limit,
        filter=filter_expr,
        output_fields=OUTPUT_FIELDS,
    )
    return [_to_hit(hit) for hit in results[0]] if results else []


def hybrid_search(
    query: str,
    *,
    limit: int = 5,
    filter_expr: str = "",
    ranker: RankerName = "rrf",
    dense_weight: float = 0.5,
    sparse_weight: float = 0.5,
    rrf_k: int = 60,
    candidate_limit: int | None = None,
    collection_name: str | None = None,
) -> list[dict]:
    """Dense·Sparse 검색을 각각 수행하고 서버 쪽에서 랭커로 결합한다.

    ``candidate_limit``은 각 개별 검색에서 몇 건씩 후보로 가져올지다.
    최종적으로 반환하는 건수(``limit``)보다 넉넉히 가져와야 결합 후 순위가
    안정적이다. 기본값은 ``limit``의 4배(최소 20)로 잡는다.
    """
    name = collection_name or settings.milvus_collection
    client = get_client()
    candidate_limit = candidate_limit or max(limit * 4, 20)

    dense_vectors = embed_texts([query])
    dense_req = AnnSearchRequest(
        data=dense_vectors,
        anns_field=DENSE_FIELD,
        param={"metric_type": "COSINE", "params": {}},
        limit=candidate_limit,
        expr=filter_expr,
    )
    sparse_req = AnnSearchRequest(
        data=[query],
        anns_field=SPARSE_FIELD,
        param={"metric_type": "BM25", "params": {}},
        limit=candidate_limit,
        expr=filter_expr,
    )

    if ranker == "rrf":
        rerank = RRFRanker(k=rrf_k)
    elif ranker == "weighted":
        # WeightedRanker는 요청을 만든 순서(dense, sparse)에 맞춰 가중치를 준다.
        rerank = WeightedRanker(dense_weight, sparse_weight)
    else:
        raise ValueError(f"알 수 없는 ranker: {ranker!r}")

    results = client.hybrid_search(
        name,
        [dense_req, sparse_req],
        rerank,
        limit=limit,
        output_fields=OUTPUT_FIELDS,
    )
    return [_to_hit(hit) for hit in results[0]] if results else []


# ---------------------------------------------------------------------------
# RRF 결합을 직접 구현한 참고용 함수 (외부 서버 없이 단위 테스트 가능).
#
# Milvus의 RRFRanker()는 이 계산을 서버 안에서 해 준다. 아래 함수는
# "그 서버가 실제로 하는 계산이 무엇인지"를 독자가 순수 파이썬으로 눈으로
# 확인하고, scripts/compare_search.py에서 클라이언트 쪽 비교용으로 쓰기
# 위한 것이다. hybrid_search()가 이 함수를 호출하지는 않는다.
# ---------------------------------------------------------------------------


def reciprocal_rank_fusion(
    rank_lists: list[list[str]],
    *,
    k: int = 60,
    limit: int | None = None,
) -> list[tuple[str, float]]:
    """여러 순위 목록(각 목록은 pk를 순위대로 나열한 리스트)을 RRF로 결합한다.

    RRF 점수 공식: ``score(d) = sum( 1 / (k + rank_i(d)) )``
    (문서 d가 등장하는 각 순위 목록 i에서, rank_i(d)는 1부터 시작하는 순위)

    점수 자체의 절대값이 아니라 "여러 목록에서 상위에 있었는가"만 반영한다.
    한 목록에만 있어도 순위가 높으면 점수가 크게 오르고, 두 목록 모두에서
    중간 정도인 문서도 누적으로 꽤 높은 점수를 받을 수 있다.
    """
    scores: dict[str, float] = {}
    for ranked_ids in rank_lists:
        for position, doc_id in enumerate(ranked_ids, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + position)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return ordered[:limit] if limit else ordered


def weighted_fusion(
    scored_lists: list[list[tuple[str, float]]],
    weights: list[float],
    *,
    limit: int | None = None,
) -> list[tuple[str, float]]:
    """여러 (pk, 정규화된 점수) 목록을 가중합으로 결합한다.

    RRF와 달리 원래 점수(유사도·BM25 점수)의 크기를 그대로 반영한다. 대신
    Dense 코사인 유사도(0~1 부근)와 BM25 점수(스케일이 다름)를 같은 잣대로
    더하려면 두 점수를 미리 같은 범위로 정규화해야 한다 — 정규화하지 않으면
    점수 스케일이 큰 쪽이 사실상 결과를 독점한다.
    """
    if len(scored_lists) != len(weights):
        raise ValueError("scored_lists와 weights의 길이가 같아야 한다.")
    scores: dict[str, float] = {}
    for items, weight in zip(scored_lists, weights):
        for doc_id, score in items:
            scores[doc_id] = scores.get(doc_id, 0.0) + weight * score
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return ordered[:limit] if limit else ordered
