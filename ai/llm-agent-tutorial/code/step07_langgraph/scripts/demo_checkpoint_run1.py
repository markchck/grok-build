"""체크포인트·재개 실습 1/2: 도중에 "죽는" 실행.

이 스크립트를 한 번 실행하면 그래프가 classify → retrieve_dense/
retrieve_sparse(병렬) → verify까지 정상적으로 마치고, **answer 노드에서
모델 호출이 실패해 프로세스가 예외로 종료**된다(모델 서버가 잠깐 죽어 있는
상황을 흉내낸다 — ``DOCAGENT_FORCE_CRASH=1``로 켠다). SqliteSaver가
``data/checkpoints.sqlite``에 지금까지의 상태를 저장해 두었으므로,
``demo_checkpoint_run2.py``(완전히 새로운 파이썬 프로세스)를 실행하면
retrieve_dense/retrieve_sparse/verify를 **다시 실행하지 않고** answer
노드부터 이어서 끝낸다.

실행 순서(README.md도 같은 내용)::

    python code/_tools/fake_openai_server.py --port 8131 &
    cd code/step07_langgraph
    rm -f data/checkpoints.sqlite
    python -m scripts.demo_checkpoint_run1   # 여기서 일부러 실패한다
    python -m scripts.demo_checkpoint_run2   # 새 프로세스. 이어서 재개한다
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.checkpoint.sqlite import SqliteSaver

from docagent.config import get_settings
from docagent.graph.build import build_graph
from docagent.graph.nodes import NodeDeps
from docagent.llm import LLMError, chat, chat_json
from docagent.rag.fake_retriever import FakeRetriever

THREAD_ID = "demo-checkpoint-1"
QUESTION = "2분기 노트북 매출이 둔화된 이유는?"


def _flaky_chat(messages, *, temperature: float = 0.2):
    """모델 서버가 잠깐 죽어 있는 상황을 흉내낸다.

    ``DOCAGENT_FORCE_CRASH=1``이면 항상 실패한다 — answer 노드의 재시도
    정책(``_LLM_RETRY``, 최대 3회)이 세 번 다 실패로 끝나고 예외가 노드
    밖으로 올라가 그래프 실행 자체가 실패한다.
    """
    if os.environ.get("DOCAGENT_FORCE_CRASH") == "1":
        raise LLMError("모델 서버에 연결할 수 없다 (데모용 강제 실패)")
    return chat(messages, temperature=temperature)


def main() -> None:
    settings = get_settings()
    docs_dir = Path(__file__).resolve().parent.parent / "data" / "docs"
    retriever = FakeRetriever(docs_dir)

    deps = NodeDeps(
        dense_search=retriever.dense_search,
        sparse_search=retriever.sparse_search,
        chat=_flaky_chat,
        json_chat=chat_json,
        app_base_url=settings.app_base_url,
    )

    os.environ["DOCAGENT_FORCE_CRASH"] = "1"

    Path(settings.checkpoint_db_path).parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(settings.checkpoint_db_path) as saver:
        app = build_graph(deps, checkpointer=saver)
        config = {"configurable": {"thread_id": THREAD_ID}}

        print(f"[run1] 질문: {QUESTION!r}")
        try:
            app.invoke(
                {
                    "question": QUESTION,
                    "retrieved": [],
                    "queries_tried": [],
                    "trace": [],
                },
                config=config,
            )
            print("[run1] 예상과 다르게 성공했다 — DOCAGENT_FORCE_CRASH가 적용되지 않았다.")
        except Exception as exc:  # noqa: BLE001 - 의도된 데모용 크래시를 프로세스 종료로 보여준다
            print(f"[run1] 예외로 중단됨(=모델 서버 장애로 프로세스가 죽었다고 가정): {exc!r}")

        snapshot = app.get_state(config)
        print("[run1] 체크포인트에 저장된 state 키:", sorted(snapshot.values.keys()))
        print("[run1] 이미 끝난 trace 항목 수:", len(snapshot.values.get("trace", [])))
        for t in snapshot.values.get("trace", []):
            print("   ", t)
        print("[run1] 다음에 실행할 노드:", snapshot.next)


if __name__ == "__main__":
    main()
