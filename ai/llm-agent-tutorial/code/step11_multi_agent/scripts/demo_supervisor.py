"""슈퍼바이저 협업(조사->분석->검증->통합)을 스텁 서버로 실행하는 데모.

사용법:
    python code/_tools/fake_openai_server.py --port 8291 &
    python code/step11_multi_agent/docagent/a2a_min/server.py --port 8292 &
    cd code/step11_multi_agent
    OPENAI_BASE_URL=http://127.0.0.1:8291/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=stub-model \
    A2A_VERIFIER_URL=http://127.0.0.1:8292 PYTHONPATH=. python scripts/demo_supervisor.py
"""

from __future__ import annotations

from docagent.config import load_settings
from docagent.multiagent.agents import AgentDeps
from docagent.multiagent.graph_supervisor import build_supervisor_graph, initial_state


def run(request: str, *, external_verifier: bool) -> None:
    settings = load_settings()
    deps = AgentDeps(settings=settings)
    graph = build_supervisor_graph(deps, external_verifier=external_verifier)
    state = initial_state(request)
    result = graph.invoke(state, config={"recursion_limit": 50})

    label = "A2A(외부 프로세스) 검증" if external_verifier else "내부 협업(같은 프로세스)"
    print(f"=== {label}: '{request}' ===")
    for note in result["shared_notes"]:
        print(" -", note)
    print("finish_reason:", result["finish_reason"])
    print("hops_used:", result["hops_used"], " tokens_used:", result["tokens_used"], " estimated:", result["tokens_estimated"])
    print("최종 답변:", result["final_text"])
    print()


if __name__ == "__main__":
    run("노트북 매출이 왜 늘었는지 조사해줘", external_verifier=False)
    run("모니터 매출 추세를 검증해줘", external_verifier=True)
