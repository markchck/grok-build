"""같은 질문에 대한 Sparse / Dense / Hybrid(RRF) / Hybrid(가중치) 결과를
나란히 비교한다 (5단계 완료 기준).

사용:
    python -m scripts.compare_search "2분기 노트북 매출이 둔화된 이유는?"
    python -m scripts.compare_search "2분기 노트북 매출이 둔화된 이유는?" --top-k 3
"""

from __future__ import annotations

import argparse

from docagent.rag.search import dense_search, hybrid_search, sparse_search

_MODES = [
    ("sparse (BM25)", lambda q, k: sparse_search(q, limit=k)),
    ("dense (임베딩)", lambda q, k: dense_search(q, limit=k)),
    ("hybrid (RRF)", lambda q, k: hybrid_search(q, limit=k, ranker="rrf")),
    (
        "hybrid (가중치 0.7/0.3)",
        lambda q, k: hybrid_search(q, limit=k, ranker="weighted", dense_weight=0.7, sparse_weight=0.3),
    ),
]


def _print_hits(title: str, hits: list[dict]) -> None:
    print(f"\n=== {title} ===")
    if not hits:
        print("  (결과 없음)")
        return
    for rank, hit in enumerate(hits, start=1):
        snippet = hit["text"].replace("\n", " ")[:60]
        print(f"  {rank}. [{hit['pk']}] score={hit['score']:.4f}  {snippet}...")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="검색할 질문 또는 키워드")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    print(f"질문: {args.query!r}  (top_k={args.top_k})")
    for title, fn in _MODES:
        try:
            hits = fn(args.query, args.top_k)
        except Exception as exc:  # Milvus/임베딩 서버 미연결 등
            print(f"\n=== {title} ===\n  오류: {exc}")
            continue
        _print_hits(title, hits)


if __name__ == "__main__":
    main()
