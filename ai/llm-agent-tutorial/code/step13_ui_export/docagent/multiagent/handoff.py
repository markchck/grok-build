"""핸드오프(handoff) 패턴: 제어권이 자동으로 돌아오지 않는 위임.

**슈퍼바이저 위임과의 차이(2-5절 참고)**: ``graph_supervisor.py``에서는 모든
하위 에이전트가 일을 마치면 반드시 ``supervisor``로 돌아온다 — 그래프의
고정 간선이 그렇게 강제한다. 이 모듈의 에이전트는 다르다. 각 에이전트 노드가
``langgraph.types.Command(goto=다른_에이전트_이름)``을 직접 돌려주면, 그
순간 실행이 **그 에이전트로 곧장 넘어간다.** 넘긴 쪽은 그 뒤에 자신에게
제어권이 돌아온다는 보장이 없다 — LangGraph가 에이전트 이름이 붙은 목적지로
그래프를 옮겨갈 뿐, "위임한 곳으로 돌아온다"는 규칙은 코드 어디에도 없다.

**이 파일이 실제로 보여주는 것 두 가지**:

1. ``build_cooperative_handoff_graph()`` — 정상적인 핸드오프 한 번. 요청을
   먼저 받은 에이전트가 자기 소관이 아니라고 판단해 다른 에이전트에게 직접
   넘기고, 넘겨받은 에이전트가 마무리한다. 처음 에이전트에게는 제어권이
   돌아오지 않는다(END로 바로 간다) — 이것이 "돌아오는가"의 차이를 눈으로
   보여준다.
2. ``build_looping_handoff_graph()`` — **일부러 고장 낸** 두 에이전트가
   서로 "이건 내 일이 아니다"라며 계속 상대에게 떠넘긴다. 8단계의 반복
   감지(같은 (도구,인자) 3회 반복, 결과-무변화 반복)를 이 상황에 맞게
   확장한 ``detect_delegation_loop()``가 A→B→A→B 패턴이 일정 횟수 이상
   반복되면 멈춘다. ``MAX_AGENT_HANDOFFS``가 그 백스톱이다(8단계의
   ``MAX_AGENT_STEPS``가 반복 감지의 백스톱 역할을 한 것과 같은 구조).
"""

from __future__ import annotations

from typing import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from docagent.config import Settings
from docagent.multiagent.state import CollabState

DecideFn = Callable[[CollabState], tuple[str, str]]  # state -> (action, note); action: "resolve"|"delegate"


def detect_delegation_loop(chain: list[str], max_handoffs: int) -> str | None:
    """핸드오프 사슬에서 상호 위임 루프를 판정한다.

    두 가지 조건 중 하나라도 걸리면 사유 문자열을 준다(둘 다 아니면 ``None``).

    1. **하드 캡**: 사슬 길이가 ``max_handoffs``를 넘었다. 패턴을 못 알아봐도
       무한정 넘어가지 않게 하는 최종 백스톱이다(8단계 ``MAX_AGENT_STEPS``와
       같은 역할).
    2. **핑퐁 패턴**: 최근 6번의 핸드오프가 "A, B, A, B, A, B"처럼 정확히
       두 이름이 번갈아 나타난다 — 세 번 연속 같은 자리를 서로 되돌려주고
       있다는 뜻이다. 8단계 2-4절의 "결과가 안 바뀌는 반복" 감지와 같은
       발상을 핸드오프 사슬에 맞게 옮긴 것이다.
    """

    if len(chain) > max_handoffs:
        return "delegation_loop"
    if len(chain) >= 6:
        window = chain[-6:]
        a, b = window[0], window[1]
        if a != b and window == [a, b, a, b, a, b]:
            return "delegation_loop"
    return None


def make_handoff_agent_node(name: str, other: str, decide: DecideFn, settings: Settings):
    """핸드오프 에이전트 노드를 만든다. 반환값은 dict가 아니라 ``Command``다.

    ``Command(goto=...)``를 쓰면 이 그래프에 ``add_edge(name, other)`` 같은
    고정 간선을 선언할 필요가 없다 — 노드 스스로 다음 목적지를 정한다. 이
    유연함이 핸드오프의 본질이자, 안전장치 없이 쓰면 위험한 이유이기도 하다.
    """

    def node(state: CollabState) -> Command:
        chain_so_far = state["handoff_chain"]
        loop_reason = detect_delegation_loop(chain_so_far, settings.max_agent_handoffs)
        if loop_reason is not None:
            return Command(
                goto="loop_stopped",
                update={
                    "finish_reason": loop_reason,
                    "trace": [{"stage": "writing", "message": f"핸드오프 루프를 감지해 중단했다: {chain_so_far}"}],
                },
            )

        action, note = decide(state)
        new_chain = chain_so_far + [name]
        base_update = {
            "handoff_chain": [name],
            "shared_notes": [note],
            "trace": [{"stage": "planning", "message": note}],
            "hops_used": state["hops_used"] + 1,
        }
        if action == "resolve":
            return Command(
                goto="finish",
                update={**base_update, "final_text": note, "finish_reason": "stop"},
            )
        return Command(goto=other, update=base_update)

    return node


def _finish_node(state: CollabState) -> dict:
    return {"trace": [{"stage": "writing", "message": "핸드오프가 마무리됐다"}]}


def _loop_stopped_node(state: CollabState) -> dict:
    return {"final_text": "", "trace": [{"stage": "writing", "message": "핸드오프 루프 차단으로 중단했다"}]}


def build_handoff_graph(
    *,
    agent_a_name: str,
    agent_b_name: str,
    decide_a: DecideFn,
    decide_b: DecideFn,
    settings: Settings,
) -> CompiledStateGraph:
    """두 에이전트 사이의 핸드오프 그래프를 조립한다.

    ``decide_a``/``decide_b``를 다르게 주면 4-4절의 "정상 핸드오프 한 번"과
    4-5절의 "상호 위임 루프" 데모를 같은 그래프 구성 함수로 만들 수 있다.
    """

    graph = StateGraph(CollabState)
    graph.add_node(agent_a_name, make_handoff_agent_node(agent_a_name, agent_b_name, decide_a, settings))
    graph.add_node(agent_b_name, make_handoff_agent_node(agent_b_name, agent_a_name, decide_b, settings))
    graph.add_node("finish", _finish_node)
    graph.add_node("loop_stopped", _loop_stopped_node)

    graph.add_edge(START, agent_a_name)
    graph.add_edge("finish", END)
    graph.add_edge("loop_stopped", END)

    return graph.compile()


def initial_handoff_state(request: str) -> CollabState:
    return CollabState(
        request=request,
        investigation={},
        investigations={},
        analysis={},
        verification={},
        shared_notes=[],
        investigator_context=[],
        analyst_context=[],
        verifier_context=[],
        next_agent="",
        route_source="",
        handoff_chain=[],
        trace=[],
        hops_used=0,
        tokens_used=0,
        tokens_estimated=False,
        final_text="",
        finish_reason=None,
    )
