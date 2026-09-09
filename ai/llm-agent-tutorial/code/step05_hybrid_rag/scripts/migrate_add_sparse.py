"""4단계 컬렉션(dense만 있음)에 sparse(BM25) 필드를 추가하는 마이그레이션.

실행 전에 반드시 Milvus 서버가 떠 있고 MILVUS_COLLECTION에 4단계에서 넣은
데이터가 들어 있어야 한다. docagent.rag.store.migrate_add_sparse의 동작
방식(내보내기 → 새 컬렉션 생성 → 다시 넣기 → 이름 바꾸기)은 그 함수의
docstring에 설명되어 있다.

사용:
    python -m scripts.migrate_add_sparse --dense-dim 1024
"""

from __future__ import annotations

import argparse

from docagent.rag.store import migrate_add_sparse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dense-dim",
        type=int,
        required=True,
        help="기존 컬렉션의 dense 필드 차원. Milvus 콘솔이나 "
        "MilvusClient.describe_collection() 결과에서 확인한다.",
    )
    parser.add_argument("--batch-size", type=int, default=200, help="내보내기/재적재 배치 크기")
    args = parser.parse_args()

    moved = migrate_add_sparse(args.dense_dim, batch_size=args.batch_size)
    print(f"마이그레이션 완료: {moved}건을 새 스키마(sparse 필드 포함)로 옮겼다.")


if __name__ == "__main__":
    main()
