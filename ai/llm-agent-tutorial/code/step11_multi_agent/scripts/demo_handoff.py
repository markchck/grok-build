"""핸드오프 패턴 데모: 정상적인 핸드오프 한 번 vs 상호 위임 루프 차단.

모델 서버 없이 실행한다(8단계 ``demo_infinite_loop.py``와 같은 목적). 이
스크립트의 ``decide_*`` 함수는 일부러 만든 결정 규칙이지, 실제 모델 호출이
아니다 — 핸드오프 메커니즘과 루프 차단 자체를 보여주는 것이 목적이다.
"""

from __future__ import annotations

from docagent.config import load_settings
from docagent.multiagent.handoff import build_handoff_graph, initial_handoff_state


def demo_cooperative() -> None:
    print("=== 시나리오 1: 정상적인 핸드오프 한 번 (order_agent -> shipping_agent) ===")

    def decide_order(state):
        # order_agent는 "배송" 관련 요청이면 자기 소관이 아니라고 보고 곧장 넘긴다.
        if "배송" in state["request"]:
            return "delegate", "[order_agent] 배송 문의라 shipping_agent에게 직접 넘긴다"
        return "resolve", "[order_agent] 주문 확인을 마쳤다"

    def decide_shipping(state):
        return "resolve", "[shipping_agent] 배송 조회를 마쳤다"

    settings = load_settings()
    graph = build_handoff_graph(
        agent_a_name="order_agent",
        agent_b_name="shipping_agent",
        decide_a=decide_order,
        decide_b=decide_shipping,
        settings=settings,
    )
    state = initial_handoff_state("주문한 물건 배송 상태를 알려줘")
    result = graph.invoke(state, config={"recursion_limit": 50})
    print("handoff_chain:", result["handoff_chain"])
    print("finish_reason:", result["finish_reason"])
    print("최종 노트:")
    for n in result["shared_notes"]:
        print(" -", n)
    assert result["handoff_chain"] == ["order_agent", "shipping_agent"], "order_agent에게 제어권이 돌아오지 않아야 한다"
    print("확인: order_agent로 제어권이 돌아오지 않고 shipping_agent가 곧장 마무리했다.\n")


def demo_loop() -> None:
    print("=== 시나리오 2: 상호 위임 루프 (agent_a <-> agent_b, 둘 다 서로에게 떠넘긴다) ===")

    def always_delegate_note(name):
        def decide(state):
            return "delegate", f"[{name}] 이건 내 소관이 아니다, 상대에게 넘긴다"

        return decide

    settings = load_settings()
    graph = build_handoff_graph(
        agent_a_name="agent_a",
        agent_b_name="agent_b",
        decide_a=always_delegate_note("agent_a"),
        decide_b=always_delegate_note("agent_b"),
        settings=settings,
    )
    state = initial_handoff_state("이 요청은 누가 처리해야 하는지 애매하다")
    result = graph.invoke(state, config={"recursion_limit": 50})
    print("handoff_chain:", result["handoff_chain"])
    print("finish_reason:", result["finish_reason"])
    assert result["finish_reason"] == "delegation_loop", "루프가 감지돼 delegation_loop로 멈춰야 한다"
    print(f"확인: 두 에이전트가 서로 {len(result['handoff_chain'])}번 떠넘긴 뒤 delegation_loop로 멈췄다.")
    print(f"(MAX_AGENT_HANDOFFS={settings.max_agent_handoffs}, 실제로는 그보다 먼저 핑퐁 패턴으로 걸렸을 수 있다)\n")


if __name__ == "__main__":
    demo_cooperative()
    demo_loop()
