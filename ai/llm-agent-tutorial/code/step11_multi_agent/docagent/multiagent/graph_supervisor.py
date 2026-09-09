"""슈퍼바이저 그래프 조립.

그래프 모양:

```
START --> supervisor --+--(investigator)--> investigator --+
                        |                                   |
                        +--(analyst)-------> analyst -------+--> supervisor (되돌아온다)
                        |                                   |
                        +--(verifier)------> verifier -------+
                        |
                        +--(finish)--------> finish --> END
                        |
                        +--(budget_exceeded)--> budget_exceeded --> END
```

investigator/analyst/verifier 세 노드는 전부 ``supervisor``로만 이어진다 —
"위임한 에이전트가 일을 마치면 반드시 위임한 쪽으로 돌아온다"는 슈퍼바이저
패턴의 정의를 그래프 구조 자체가 강제한다(핸드오프 그래프와 이 부분이 다르다.
``graph_handoff.py``의 모듈 docstring에서 대조한다).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from docagent.multiagent.agents import AgentDeps, make_analyst_node, make_finish_node, make_investigator_node, make_verifier_node
from docagent.multiagent.state import CollabState
from docagent.multiagent.supervisor import make_route_after_supervisor, make_supervisor_node


def _make_budget_exceeded_node(deps: AgentDeps):
    from docagent.multiagent.budget import check_budget

    def budget_exceeded(state: CollabState) -> dict:
        reason = check_budget(state, deps.settings) or "max_steps"
        return {
            "finish_reason": reason,
            "final_text": state.get("final_text", ""),
            "trace": [{"stage": "writing", "message": f"협업 예산을 넘어 중단했다: {reason}"}],
        }

    return budget_exceeded


def build_supervisor_graph(deps: AgentDeps) -> CompiledStateGraph:
    graph = StateGraph(CollabState)

    graph.add_node("supervisor", make_supervisor_node(deps))
    graph.add_node("investigator", make_investigator_node(deps))
    graph.add_node("analyst", make_analyst_node(deps))
    graph.add_node("verifier", make_verifier_node(deps))
    graph.add_node("finish", make_finish_node(deps))
    graph.add_node("budget_exceeded", _make_budget_exceeded_node(deps))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        make_route_after_supervisor(deps),
        {
            "investigator": "investigator",
            "analyst": "analyst",
            "verifier": "verifier",
            "finish": "finish",
            "budget_exceeded": "budget_exceeded",
        },
    )
    graph.add_edge("investigator", "supervisor")
    graph.add_edge("analyst", "supervisor")
    graph.add_edge("verifier", "supervisor")
    graph.add_edge("finish", END)
    graph.add_edge("budget_exceeded", END)

    return graph.compile()


def initial_state(request: str) -> CollabState:
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
