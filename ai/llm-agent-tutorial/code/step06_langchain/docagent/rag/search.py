"""Dense / Sparse / Hybrid 검색 — 5단계 ``rag/search.py``를 LangChain Retriever로 재구성한다.

5단계는 ``dense_search``/``sparse_search``/``hybrid_search`` 세 함수가 각각
``client.search``/``client.hybrid_search``를 직접 호출했다. 이 장은 하나의
``langchain_milvus.Milvus`` 인스턴스(``build_vector_store()``)만 만들고,
검색 모드 차이는 ``as_retriever(search_kwargs=...)``에 넘기는 ``ranker_type``/
``ranker_params``로만 바꾼다.

**"단일 필드만 검색"을 별도 연결로 흉내 내려다 실패한 기록**: 처음에는
``vector_field="dense"``만 선언한 별도 ``Milvus`` 인스턴스로 같은 컬렉션에
다시 연결해서 "진짜 dense 전용 검색"을 만들려고 했다. dense 단독 연결은
됐지만, ``vector_field="sparse"``만 선언한 별도 인스턴스로 같은 컬렉션에
연결하면 컬렉션을 다시 불러오는 과정(``load_collection``)에서
``MilvusException: vector column must be FixedSizeList, got binary``가
**실제로 재현됐다**(Milvus Lite 3.2.1). 원인은 이 문서 작성 시점에 확정하지
못했다 — [확인 필요: langchain-milvus가 부분 필드로 재연결할 때 나머지
벡터 필드의 인덱스 파라미터를 기본값으로 다시 추론하면서 SPARSE_FLOAT_VECTOR
필드에 맞지 않는 인덱스 타입을 시도하는 것으로 보이나, Milvus Lite 서버
로그만으로는 확정할 수 없었다]. 그래서 이 모듈은 별도 연결 대신, **하나의
하이브리드 벡터 저장소에 가중치를 0으로 준 ``WeightedRanker``**로 "사실상
한쪽만 보는" 검색을 만든다 — 컬렉션을 한 번만 열기 때문에 이 문제를
피한다. 두 방법의 차이와 실패 재현은 이 장 6절 "실패 상황 실습"에서
그대로 보여준다.
"""

from __future__ import annotations

from typing import Literal

from langchain_milvus import Milvus

from docagent.config import Settings, get_settings
from docagent.rag.store import build_vector_store

RankerName = Literal["rrf", "weighted"]

_vector_store: Milvus | None = None


def get_vector_store(settings: Settings | None = None) -> Milvus:
    """모듈 전역에 벡터 저장소를 하나만 유지한다.

    5단계의 ``get_client()``는 검색마다 ``MilvusClient``를 새로 만들었다(연결
    비용이 낮은 로컬 클라이언트라 문제 없었다). ``langchain_milvus.Milvus``는
    생성 시점에 컬렉션 존재 확인·로드까지 하므로, 요청마다 새로 만들면 그
    비용이 매 검색마다 반복된다 — 이 장은 그래서 한 번만 만들어 재사용한다.
    """
    global _vector_store
    if _vector_store is None:
        _vector_store = build_vector_store(settings)
    return _vector_store


def _to_hit(doc, score: float) -> dict:
    """5단계 ``_to_hit``와 같은 모양의 딕셔너리로 맞춘다.

    citations.py(5단계에서 그대로 옮겨온 프레임워크 무관 모듈)와
    scripts/compare_with_step05.py가 두 구현을 같은 형태로 비교할 수 있게
    하기 위해서다.
    """
    meta = doc.metadata
    return {
        "pk": meta.get("pk", ""),
        "text": doc.page_content,
        "doc_id": meta.get("doc_id", ""),
        "doc_title": meta.get("doc_title", ""),
        "page": int(meta.get("page", 0)),
        "chunk_index": int(meta.get("chunk_index", 0)),
        "source_url": meta.get("source_url", ""),
        "score": float(score),
    }


def dense_search(query: str, *, limit: int = 5, settings: Settings | None = None) -> list[dict]:
    """의미 검색만 반영하도록 sparse 가중치를 0으로 준 하이브리드 검색."""
    vs = get_vector_store(settings)
    results = vs.similarity_search_with_score(
        query, k=limit, ranker_type="weighted", ranker_params={"weights": [1.0, 0.0]}
    )
    return [_to_hit(doc, score) for doc, score in results]


def sparse_search(query: str, *, limit: int = 5, settings: Settings | None = None) -> list[dict]:
    """키워드 검색만 반영하도록 dense 가중치를 0으로 준 하이브리드 검색."""
    vs = get_vector_store(settings)
    results = vs.similarity_search_with_score(
        query, k=limit, ranker_type="weighted", ranker_params={"weights": [0.0, 1.0]}
    )
    return [_to_hit(doc, score) for doc, score in results]


def hybrid_search(
    query: str,
    *,
    limit: int = 5,
    ranker: RankerName = "rrf",
    rrf_k: int = 60,
    dense_weight: float = 0.5,
    sparse_weight: float = 0.5,
    settings: Settings | None = None,
) -> list[dict]:
    """Dense·Sparse를 함께 반영하는 하이브리드 검색. 5단계와 같은 기본값(RRF, k=60)."""
    vs = get_vector_store(settings)
    if ranker == "rrf":
        results = vs.similarity_search_with_score(query, k=limit, ranker_type="rrf", ranker_params={"k": rrf_k})
    elif ranker == "weighted":
        results = vs.similarity_search_with_score(
            query, k=limit, ranker_type="weighted", ranker_params={"weights": [dense_weight, sparse_weight]}
        )
    else:
        raise ValueError(f"알 수 없는 ranker: {ranker!r}")
    return [_to_hit(doc, score) for doc, score in results]
