"""체크포인트·재개 실습 2/2: 새 프로세스에서 이어서 재개.

``demo_checkpoint_run1.py``와 **완전히 별도의 파이썬 프로세스**로 실행한다.
같은 ``thread_id``, 같은 SQLite 체크포인트 파일을 가리키면 LangGraph는
``get_state()``로 "이 스레드가 어디까지 실행됐는지"를 그대로 읽어 오고,
``invoke(None, config=...)``(입력을 새로 주지 않고 ``None``을 준다)로
멈췄던 지점부터 이어서 실행한다.

이 스크립트는 ``DOCAGENT_FORCE_CRASH``를 설정하지 않는다 — "장애가 있던
모델 서버가 복구된 뒤 애플리케이션을 다시 켰다"는 상황을 흉내낸다.
retrieve_dense/retrieve_sparse/verify는 **다시 실행되지 않는다**(콘솔에
그 노드들의 print가 다시 찍히지 않는 것으로 확인한다) — answer 노드부터
이어서 실행된다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.checkpoint.sqlite import SqliteSaver

from docagent.config import get_settings
from docagent.graph.build import build_graph
from docagent.graph.nodes import NodeDeps
from docagent.llm import chat, chat_json
from docagent.rag.fake_retriever import FakeRetriever

THREAD_ID = "demo-checkpoint-1"  # run1과 반드시 같아야 같은 실행을 이어받는다


def main() -> None:
    settings = get_settings()
    docs_dir = Path(__file__).resolve().parent.parent / "data" / "docs"
    retriever = FakeRetriever(docs_dir)

    deps = NodeDeps(
        dense_search=retriever.dense_search,
        sparse_search=retriever.sparse_search,
        chat=chat,  # 이번엔 정상 함수. "장애 복구 후 재시작"을 흉내낸다.
        json_chat=chat_json,
        app_base_url=settings.app_base_url,
    )

    with SqliteSaver.from_conn_string(settings.checkpoint_db_path) as saver:
        app = build_graph(deps, checkpointer=saver)
        config = {"configurable": {"thread_id": THREAD_ID}}

        snapshot_before = app.get_state(config)
        if not snapshot_before.values:
            print("[run2] 저장된 상태가 없다 — 먼저 demo_checkpoint_run1.py를 실행한다.")
            return

        print("[run2] 재개 전 상태: 다음에 실행할 노드 =", snapshot_before.next)
        print("[run2] 재개 전까지 쌓인 trace 항목 수:", len(snapshot_before.values.get("trace", [])))

        # None을 입력으로 주면 새 실행이 아니라 중단된 지점부터 재개한다.
        result = app.invoke(None, config=config)

        print("\n[run2] 재개 완료. 최종 답변:")
        print(" ", result["answer_text"])
        print("[run2] 전체 trace (run1에서 쌓인 것 + run2에서 새로 쌓인 것):")
        for t in result["trace"]:
            print("   ", t)

        snapshot_after = app.get_state(config)
        print("[run2] 재개 후 다음 노드 (완료됐으면 빈 튜플):", snapshot_after.next)


if __name__ == "__main__":
    main()
