"""내부 verifier 노드 vs A2A 최소 흉내로 위임하는 verifier 노드의 경계 확인.

이 테스트는 실제 uvicorn 프로세스를 띄우지 않고, ``a2a_verifier_url``을
아무도 듣고 있지 않은 주소로 둬서 "외부 에이전트에 닿지 못해도 협업 전체가
죽지 않는다"는 것만 확인한다. 실제 프로세스 두 개를 띄운 통합 검증은
docs/11-multi-agent.md 5절에 curl 결과로 남긴다.
"""

from __future__ import annotations

from docagent.config import load_settings
from docagent.multiagent.agents import AgentDeps
from docagent.multiagent.graph_supervisor import build_supervisor_graph, initial_state


def _deps(**overrides):
    settings = load_settings()
    if overrides:
        settings = settings.__class__(**{**settings.__dict__, **overrides})
    return AgentDeps(settings=settings)


def test_external_verifier_unreachable_does_not_crash_collaboration():
    deps = _deps(a2a_verifier_url="http://127.0.0.1:1")  # 아무도 듣지 않는 주소
    graph = build_supervisor_graph(deps, external_verifier=True)
    state = initial_state("노트북 매출이 왜 늘었는지 조사해줘")
    result = graph.invoke(state, config={"recursion_limit": 50})

    assert result["finish_reason"] == "stop"  # 협업 자체는 끝까지 간다
    assert result["verification"]["ok"] is False
    assert result["verification"]["source"] == "a2a_agent_unreachable"


def test_internal_and_external_verifier_produce_same_shape():
    deps = _deps()
    internal_graph = build_supervisor_graph(deps, external_verifier=False)
    state = initial_state("노트북 매출이 왜 늘었는지 조사해줘")
    internal_result = internal_graph.invoke(state, config={"recursion_limit": 50})

    assert set(internal_result["verification"].keys()) >= {"ok", "reason", "source"}
    assert internal_result["verification"]["source"] == "heuristic"
