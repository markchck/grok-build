"""LangChain Milvus 벡터 저장소 — 5단계 ``rag/store.py``를 재구성한다.

5단계는 ``pymilvus.MilvusClient``로 스키마(``create_schema``/``add_field``),
인덱스(``prepare_index_params``), BM25 ``Function``을 전부 손으로 선언했다.
이 장은 ``langchain-milvus`` 패키지(``langchain_milvus.Milvus``,
``BM25BuiltInFunction``)가 그 선언을 대신하게 한다. 클래스 이름과
생성자 인자는 실제로 설치해서 확인했다(장 하단 "참고 문서"의
``langchain-milvus`` 버전 참고) — 기억으로 적지 않았다.

**통제력을 잃는 지점** (6절에서 표로 다시 정리한다):

- 5단계는 ``pk``/``dense``/``sparse``/``text``/``doc_id``/``doc_title``/``page``/
  ``chunk_index``/``source_url`` 9개 필드를 스키마에 명시적으로 선언했다
  (PROJECT-SPEC.md 6장). ``langchain_milvus.Milvus``는 ``primary_field``,
  ``text_field``, ``vector_field``(dense/sparse 두 개) 이름만 우리가 정할 수
  있고, 나머지 메타데이터(``doc_id``, ``doc_title``, ``page``, ``chunk_index``,
  ``source_url``)는 ``enable_dynamic_field=True``를 켠 동적 필드(JSON 컬럼)
  하나에 다 들어간다 — 5단계처럼 각각을 독립된 스칼라 컬럼으로 두고 그
  컬럼에 별도 인덱스를 거는 것은 이 통합의 기본 경로가 아니다.
- 인덱스 파라미터(``AUTOINDEX``/``COSINE``, ``SPARSE_INVERTED_INDEX``/``BM25``)의
  기본값도 ``langchain_milvus``가 정한 것을 그대로 받는다. 5단계처럼
  ``bm25_k1``/``bm25_b``를 직접 정하려면 ``index_params``를 우리가 다시 만들어
  넘겨야 한다(아래 ``_build_index_params`` 참고) — "기본값을 받아들이거나,
  프레임워크가 정해 둔 자리에 우리 값을 끼워 넣거나" 둘 중 하나다.
"""

from __future__ import annotations

from langchain_milvus import BM25BuiltInFunction, Milvus

from docagent.config import Settings, get_settings
from docagent.models import build_embeddings

PK_FIELD = "pk"
DENSE_FIELD = "dense"
SPARSE_FIELD = "sparse"
TEXT_FIELD = "text"


def make_chunk_id(doc_id: str, page: int, chunk_index: int) -> str:
    """PROJECT-SPEC.md 5장과 동일한 청크 ID 형식. 5단계 store.py와 같다."""
    return f"{doc_id}#p{page}-c{chunk_index}"


def _build_index_params() -> list[dict]:
    """5단계와 같은 BM25 파라미터(k1=1.2, b=0.75)를 유지하고 싶을 때 쓰는 예시.

    ``Milvus(...)``의 ``index_params``는 ``vector_field``와 같은 순서의
    리스트를 받는다(``["dense", "sparse"]``이므로 dense 인덱스, sparse
    인덱스 순서). 기본값만으로 충분하면 이 함수를 쓰지 않고
    ``build_vector_store()``에서 ``index_params=None``으로 둬도 된다.
    """
    return [
        {"index_type": "AUTOINDEX", "metric_type": "COSINE"},
        {
            "index_type": "SPARSE_INVERTED_INDEX",
            "metric_type": "BM25",
            "params": {"bm25_k1": 1.2, "bm25_b": 0.75},
        },
    ]


def build_vector_store(
    settings: Settings | None = None,
    *,
    drop_old: bool = False,
    use_custom_index_params: bool = False,
) -> Milvus:
    """Dense + Sparse(BM25) 하이브리드가 가능한 ``Milvus`` 벡터 저장소를 만든다.

    ``embedding_function``에 리스트를 주고 ``builtin_function``에
    ``BM25BuiltInFunction``을 주면, ``langchain_milvus``가 컬렉션에
    ``dense``(우리가 만든 임베딩)와 ``sparse``(Milvus 서버의 BM25 Function이
    ``text`` 필드에서 직접 만드는 값) 두 벡터 필드를 함께 선언한다 — 5단계에서
    ``Function(function_type=FunctionType.BM25, ...)``로 손수 선언한 것과
    같은 일이다.

    Milvus Lite의 BM25 tokenizer 제약(``standard``/``jieba``만 지원, 한국어는
    공백 단위로만 잘림)은 이 장에서도 그대로 적용된다 — VERIFIED-FINDINGS.md
    7절 참고. ``BM25BuiltInFunction()``을 인자 없이 쓰면 기본 ``standard``
    analyzer를 쓴다.
    """
    s = settings or get_settings()
    embeddings = build_embeddings(s)

    return Milvus(
        embedding_function=[embeddings],
        builtin_function=[BM25BuiltInFunction(input_field_names=TEXT_FIELD, output_field_names=SPARSE_FIELD)],
        vector_field=[DENSE_FIELD, SPARSE_FIELD],
        primary_field=PK_FIELD,
        text_field=TEXT_FIELD,
        collection_name=s.milvus_collection,
        connection_args={"uri": s.milvus_uri, "token": s.milvus_token or None},
        consistency_level="Strong",
        auto_id=False,
        enable_dynamic_field=True,
        drop_old=drop_old,
        index_params=_build_index_params() if use_custom_index_params else None,
    )
