"""병렬 조사와 결과 통합 (4-6절).

"두 제품을 한꺼번에 비교해줘" 같은 요청은 investigator 하나가 순서대로 두
번 조사하는 것보다, 서로 관련 없는 두 조사를 **같은 슈퍼스텝에서 병렬로**
실행하는 편이 더 빠르다(서로의 결과를 기다릴 이유가 없다). 이 모듈은 그
패턴과, 병렬로 쓰는 채널에 리듀서가 없으면 결과가 사라진다는 사실(7단계
``merge_hits``와 같은 문제)을 함께 보여준다.

그래프 모양:

```
START --> fan_out --+--> investigate_product(a) --+
                     +--> investigate_product(b) --+--> combine --> END
```

``fan_out``이 조건부 간선으로 두 조사 노드 이름을 **리스트**로 돌려주면
LangGraph는 그 슈퍼스텝에서 둘을 병렬로 실행한다(7단계
``route_after_classify``의 ``["retrieve_dense", "retrieve_sparse"]``와 같은
방식).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from docagent import salesdata
from docagent.multiagent.state import CollabState


def _fan_out(state: CollabState) -> dict:
    known = salesdata.known_products()
    mentioned = [p for p in known if p in state["request"]]
    targets = mentioned[:2] if len(mentioned) >= 2 else known[:2]
    return {"trace": [{"stage": "planning", "message": f"병렬로 조사할 제품: {targets}"}]}


def _route_fan_out(state: CollabState) -> list[str]:
    known = salesdata.known_products()
    mentioned = [p for p in known if p in state["request"]]
    targets = mentioned[:2] if len(mentioned) >= 2 else known[:2]
    return [f"investigate__{p}" for p in targets]


def _make_investigate_node(product: str):
    def node(state: CollabState) -> dict:
        rows = salesdata.rows_for_product(product)
        totals = salesdata.quarterly_totals(rows)
        return {
            "investigations": {product: {"row_count": len(rows), "totals": totals}},
            "trace": [{"stage": "retrieving", "message": f"'{product}' 조사 완료: {len(rows)}건"}],
        }

    return node


def _combine(state: CollabState) -> dict:
    investigations = state.get("investigations", {})
    lines = []
    for product, data in investigations.items():
        totals = data.get("totals", {})
        q1 = totals.get("Q1", {}).get("revenue", 0)
        q2 = totals.get("Q2", {}).get("revenue", 0)
        trend = "증가" if q2 > q1 else "감소" if q2 < q1 else "보합"
        lines.append(f"{product}: Q1 {q1} -> Q2 {q2} ({trend})")
    text = "병렬 조사 결과를 통합했다.\n" + "\n".join(lines)
    return {
        "final_text": text,
        "finish_reason": "stop",
        "trace": [{"stage": "writing", "message": f"{len(investigations)}건의 병렬 조사 결과를 통합했다"}],
    }


def build_parallel_graph() -> CompiledStateGraph:
    """모든 제품을 노드로 미리 등록해 두고, 요청마다 그중 둘로 fan-out한다.

    LangGraph는 그래프 조립(컴파일) 시점에 노드가 전부 등록돼 있어야 한다 —
    "이번 요청에 필요한 노드만 그때그때 만든다"는 방식은 쓸 수 없다. 그래서
    가능한 제품을 전부 노드로 등록해 두고, 실행 시점에 조건부 간선
    (``_route_fan_out``)이 그중 둘만 골라 병렬로 보낸다.
    """

    graph = StateGraph(CollabState)
    graph.add_node("fan_out", _fan_out)
    graph.add_node("combine", _combine)

    for p in salesdata.known_products():
        graph.add_node(f"investigate__{p}", _make_investigate_node(p))
        graph.add_edge(f"investigate__{p}", "combine")

    graph.add_edge(START, "fan_out")
    graph.add_conditional_edges("fan_out", _route_fan_out)
    graph.add_edge("combine", END)
    return graph.compile()


def initial_parallel_state(request: str) -> CollabState:
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
