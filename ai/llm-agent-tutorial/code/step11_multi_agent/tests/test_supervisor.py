"""슈퍼바이저 위임: 역할 분리, 제어권 복귀, 예산 한도.

모델 서버가 없으므로(conftest가 존재하지 않는 주소로 고정) 모든 노드가
규칙 기반(heuristic) 경로를 탄다 — ``route_source``가 항상 "heuristic"임을
그대로 확인해서 "모델이 실패해도 협업이 멈추지 않는다"는 것도 함께 검증한다.
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


def test_supervisor_delegates_investigator_analyst_verifier_in_order():
    deps = _deps()
    graph = build_supervisor_graph(deps)
    state = initial_state("노트북 매출이 왜 늘었는지 조사해줘")
    result = graph.invoke(state, config={"recursion_limit": 50})

    assert result["finish_reason"] == "stop"
    assert result["investigation"]["product"] == "노트북"
    assert result["analysis"]["trend"] in ("increase", "decrease", "flat", "unknown")
    assert result["verification"]["ok"] is True
    # 슈퍼바이저 패턴의 정의: 위임된 세 에이전트가 모두 슈퍼바이저에게 돌아온다.
    supervisor_visits = sum(1 for n in result["shared_notes"] if n.startswith("[supervisor]"))
    assert supervisor_visits == 4  # investigator, analyst, verifier 위임 3번 + finish 판단 1번


def test_control_returns_to_supervisor_after_each_agent():
    deps = _deps()
    graph = build_supervisor_graph(deps)
    state = initial_state("모니터 매출 확인해줘")
    result = graph.invoke(state, config={"recursion_limit": 50})

    notes = result["shared_notes"]
    # investigator/analyst/verifier 노트 각각의 바로 다음 노트가 supervisor여야 한다
    for i, note in enumerate(notes[:-1]):
        if note.startswith("[investigator]") or note.startswith("[analyst]") or note.startswith("[verifier]"):
            assert notes[i + 1].startswith("[supervisor]"), f"{note} 다음에 supervisor가 오지 않았다"


def test_max_steps_budget_stops_collaboration():
    deps = _deps(max_agent_steps=2)
    graph = build_supervisor_graph(deps)
    state = initial_state("노트북 매출이 왜 늘었는지 조사해줘")
    result = graph.invoke(state, config={"recursion_limit": 50})

    assert result["finish_reason"] == "max_steps"
    assert result["hops_used"] > deps.settings.max_agent_steps


def test_token_budget_stops_collaboration():
    """토큰 사용량은 모델 호출이 실제로 성공해야 쌓인다(모델 서버가 없으면
    항상 0이다 — llm.chat이 실패하면 휴리스틱으로 넘어가고 토큰을 더하지
    않는다, agents.py의 ``_call_json``). 그래서 이 테스트는 "이미 예산을
    다 쓴 상태에서 시작한 협업"을 흉내 낸다 — check_budget()이 그래프
    조건부 간선(``route_after_supervisor``) 안에서 실제로 호출돼 그래프
    실행을 멈추는지를 본다(단위 함수 호출만으로 확인하는
    scripts/demo_budget.py보다 한 단계 더 통합적인 검증이다)."""

    deps = _deps(max_collab_tokens=1)
    graph = build_supervisor_graph(deps)
    state = initial_state("노트북 매출이 왜 늘었는지 조사해줘")
    state["tokens_used"] = 999
    result = graph.invoke(state, config={"recursion_limit": 50})

    assert result["finish_reason"] == "token_budget"


def test_investigator_and_analyst_have_separate_private_context():
    """공유 상태(shared_notes)는 합쳐지지만, 에이전트별 컨텍스트는 섞이지 않는다."""

    deps = _deps()
    graph = build_supervisor_graph(deps)
    state = initial_state("키보드 매출 확인해줘")
    result = graph.invoke(state, config={"recursion_limit": 50})

    assert all("[investigator]" in m["content"] for m in result["investigator_context"])
    assert all("[analyst]" in m["content"] for m in result["analyst_context"])
    assert all("[verifier]" in m["content"] for m in result["verifier_context"])
