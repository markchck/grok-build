"""슈퍼바이저 노드와 위임 규칙.

**슈퍼바이저 위임의 정의**: 슈퍼바이저가 다음에 부를 에이전트를 정하고, 그
에이전트가 일을 마치면 **항상 슈퍼바이저에게 제어권이 돌아온다.** 그래프
모양으로 보면 investigator/analyst/verifier 세 노드 모두 슈퍼바이저 노드로만
이어지는 간선을 갖는다(``graph_supervisor.py``). 이것이 4-4절에서 다루는
핸드오프(에이전트가 다른 에이전트로 직접 건너뛰고, 슈퍼바이저는 그 사실을
다음에 다시 자신에게 제어권이 왔을 때야 안다)와의 핵심 차이다.

이 노드는 7단계 ``classify``/``verify`` 노드와 같은 패턴을 쓴다: 모델에게
구조화된 판단을 요청하고, 응답이 기대한 형태가 아니면 규칙으로 대체한다.
"""

from __future__ import annotations

from typing import Callable

from docagent.multiagent.agents import AgentDeps, _call_json, _trace
from docagent.multiagent.budget import add_usage, check_budget
from docagent.multiagent.state import CollabState

_ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "next": {"type": "string", "enum": ["investigator", "analyst", "verifier", "finish"]},
        "reason": {"type": "string"},
    },
    "required": ["next"],
}


def _heuristic_next(state: CollabState) -> str:
    """규칙: 아직 안 채워진 공유 상태를 순서대로 채운다."""

    if not state.get("investigation"):
        return "investigator"
    if not state.get("analysis"):
        return "analyst"
    if not state.get("verification"):
        return "verifier"
    return "finish"


def make_supervisor_node(deps: AgentDeps) -> Callable[[CollabState], dict]:
    def supervisor(state: CollabState) -> dict:
        summary = (
            f"investigation={'있음' if state.get('investigation') else '없음'}, "
            f"analysis={'있음' if state.get('analysis') else '없음'}, "
            f"verification={'있음' if state.get('verification') else '없음'}"
        )
        parsed, tokens, estimated = _call_json(
            deps,
            [
                {
                    "role": "system",
                    "content": (
                        "너는 조사(investigator)·분석(analyst)·검증(verifier) 세 에이전트를 "
                        "지휘하는 슈퍼바이저다. 아직 안 끝난 작업 중 다음에 시킬 에이전트를 "
                        "고른다. 셋 다 끝났으면 finish를 고른다. JSON으로만 답한다."
                    ),
                },
                {"role": "user", "content": f"요청: {state['request']}\n진행 상태: {summary}"},
            ],
            schema_name="route_next",
            schema=_ROUTE_SCHEMA,
        )
        next_agent = parsed.get("next")
        source = "model"
        if next_agent not in ("investigator", "analyst", "verifier", "finish"):
            next_agent = _heuristic_next(state)
            source = "heuristic"

        note = f"[supervisor] 다음 위임 대상: {next_agent} (판단 주체: {source})"
        budget_update = add_usage(state, tokens, estimated)
        return {
            "next_agent": next_agent,
            "route_source": source,
            "shared_notes": [note],
            "hops_used": state["hops_used"] + 1,
            "trace": [_trace("planning", note)],
            **budget_update,
        }

    return supervisor


def make_route_after_supervisor(deps: AgentDeps) -> Callable[[CollabState], str]:
    """조건 분기: 예산을 먼저 확인하고, 넘지 않았으면 supervisor가 정한 곳으로 간다."""

    def route_after_supervisor(state: CollabState) -> str:
        over = check_budget(state, deps.settings)
        if over is not None:
            return "budget_exceeded"
        return state["next_agent"]

    return route_after_supervisor
