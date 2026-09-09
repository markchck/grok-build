"""협업 전체 단위의 토큰 예산·단계 한도 초과를 일부러 만들어 보는 데모.

모델 서버 없이, "항상 진행이 안 되는" 가짜 슈퍼바이저 결정을 흉내 낸다 —
슈퍼바이저가 매번 investigator만 계속 고르도록 만들어(공유 상태를 지우는
방식으로) hops_used가 MAX_AGENT_STEPS를 넘기게 한다. 8단계
``demo_infinite_loop.py``와 같은 방식(안전장치가 실제로 멈추는지 직접 보는
것)이다.
"""

from __future__ import annotations

from docagent.config import load_settings
from docagent.multiagent.agents import AgentDeps
from docagent.multiagent.budget import check_budget
from docagent.multiagent.graph_supervisor import initial_state
from docagent.multiagent.state import CollabState


def demo_max_steps() -> None:
    print("=== 협업 단계 한도(MAX_AGENT_STEPS) 초과 ===")
    settings = load_settings()
    state: CollabState = initial_state("아무 제품이나 조사해줘")
    state["hops_used"] = settings.max_agent_steps + 1
    reason = check_budget(state, settings)
    print(f"MAX_AGENT_STEPS={settings.max_agent_steps}, hops_used={state['hops_used']} -> {reason}")
    assert reason == "max_steps"


def demo_token_budget() -> None:
    print("=== 협업 토큰 예산(MAX_COLLAB_TOKENS) 초과 ===")
    settings = load_settings()
    state: CollabState = initial_state("아무 제품이나 조사해줘")
    state["tokens_used"] = settings.max_collab_tokens + 1
    reason = check_budget(state, settings)
    print(f"MAX_COLLAB_TOKENS={settings.max_collab_tokens}, tokens_used={state['tokens_used']} -> {reason}")
    assert reason == "token_budget"


if __name__ == "__main__":
    demo_max_steps()
    demo_token_budget()
    print("두 한도 모두 check_budget()이 올바른 finish_reason을 돌려줬다.")
