"""핸드오프: 제어권이 자동으로 돌아오지 않는 것, 상호 위임 루프 차단."""

from __future__ import annotations

from docagent.config import load_settings
from docagent.multiagent.handoff import build_handoff_graph, detect_delegation_loop, initial_handoff_state


def test_cooperative_handoff_transfers_control_without_returning():
    def decide_a(state):
        return "delegate", "a에서 b로"

    def decide_b(state):
        return "resolve", "b가 마무리"

    settings = load_settings()
    graph = build_handoff_graph(
        agent_a_name="a", agent_b_name="b", decide_a=decide_a, decide_b=decide_b, settings=settings
    )
    result = graph.invoke(initial_handoff_state("req"), config={"recursion_limit": 50})

    assert result["handoff_chain"] == ["a", "b"]
    assert result["finish_reason"] == "stop"


def test_mutual_delegation_loop_is_blocked():
    def always_delegate(state):
        return "delegate", "떠넘긴다"

    settings = load_settings()
    graph = build_handoff_graph(
        agent_a_name="a", agent_b_name="b", decide_a=always_delegate, decide_b=always_delegate, settings=settings
    )
    result = graph.invoke(initial_handoff_state("req"), config={"recursion_limit": 100})

    assert result["finish_reason"] == "delegation_loop"
    # 하드 캡(max_agent_handoffs)보다 사슬이 크게 자라지 않아야 한다.
    assert len(result["handoff_chain"]) <= settings.max_agent_handoffs + 2


def test_detect_delegation_loop_hard_cap():
    chain = ["a", "b"] * 10
    assert detect_delegation_loop(chain, max_handoffs=5) == "delegation_loop"


def test_detect_delegation_loop_ping_pong_pattern():
    chain = ["a", "b", "a", "b", "a", "b"]
    assert detect_delegation_loop(chain, max_handoffs=100) == "delegation_loop"


def test_detect_delegation_loop_allows_normal_short_chain():
    chain = ["a", "b"]
    assert detect_delegation_loop(chain, max_handoffs=10) is None


def test_detect_delegation_loop_does_not_flag_non_alternating_chain():
    # a,b,c가 번갈아 나오는 것은 "핑퐁"이 아니다(참여자가 둘이 아니다).
    chain = ["a", "b", "c", "a", "b", "c"]
    assert detect_delegation_loop(chain, max_handoffs=100) is None
