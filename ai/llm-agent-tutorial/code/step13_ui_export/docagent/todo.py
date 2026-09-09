"""계획(Todo) 상태를 id가 안정된 ``todo`` 이벤트 목록으로 바꾼다.

이 장은 LangChain Deep Agents의 ``write_todos``를 쓰지 않는다(그 도구는
12단계가 다룬다). 대신 이 장의 에이전트는 로컬 함수 도구 ``write_plan``으로
계획을 선언·수정한다(``tools.py``). 하지만 "모델이 주는 상태 값과
PROJECT-SPEC.md의 ``todo`` 스키마가 다르다", "id를 어떻게 안정적으로
유지할 것인가", "실패는 누가 판단하는가"라는 문제는 12단계와 완전히
동일하다. 그래서 이 모듈은 ``code/step12_deep_agents/docagent/bridge.py``의
``TodoIdAssigner``/``map_todo_state`` 로직을 그대로 가져와, langchain
``Todo`` TypedDict 대신 이 장의 ``write_plan`` 인자 형태(``{"title","status"}``,
``status`` ∈ ``pending``/``in_progress``/``completed``)에 맞춰 이름만 바꿨다.

핵심 규칙(12단계와 동일):
1. ``title`` 문자열이 이번 run에서 처음 등장하는 순서대로 ``id``를 발급하고,
   같은 title이 다시 나오면 같은 id를 재사용한다 — 계획이 pending -> running
   -> done으로 바뀌어도 화면의 항목이 다른 행으로 보이지 않는다.
2. 계획이 바뀌어 새 title이 나타나면 새 id를 받고, 목록에서 사라진 title은
   다음 ``todo`` 이벤트부터 보이지 않는다(증분이 아니라 매번 전체 목록).
3. ``failed``는 모델이 알려주지 않는다. 도구 호출이 실패한 것을
   애플리케이션이 관측한 시점에, 그때 ``running``이던 항목에만 표시한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_STATE_MAP = {"pending": "pending", "in_progress": "running", "completed": "done"}


def map_todo_state(plan_status: str) -> str:
    """``write_plan``의 status -> PROJECT-SPEC.md의 ``todo.state``.

    매핑에 없는 값은 ``pending``으로 낮춰 잡는다 — 알 수 없는 상태를 임의로
    "완료"나 "실패"로 단정하지 않는다.
    """

    return _STATE_MAP.get(plan_status, "pending")


@dataclass
class TodoIdAssigner:
    """``title`` 문자열 -> 안정적인 id. 같은 run 동안 상태만 유지한다."""

    ids: dict[str, str] = field(default_factory=dict)
    next_seq: int = 1
    failed: set[str] = field(default_factory=set)
    running: set[str] = field(default_factory=set)

    @classmethod
    def from_state(cls, state: dict[str, Any] | None) -> "TodoIdAssigner":
        """RunState에 저장된 JSON 딕셔너리에서 복원한다(재개 시 id를 계속 유지하기 위해)."""

        if not state:
            return cls()
        return cls(
            ids=dict(state.get("ids", {})),
            next_seq=int(state.get("next_seq", 1)),
            failed=set(state.get("failed", [])),
            running=set(state.get("running", [])),
        )

    def to_state(self) -> dict[str, Any]:
        return {
            "ids": self.ids,
            "next_seq": self.next_seq,
            "failed": sorted(self.failed),
            "running": sorted(self.running),
        }

    def items_for(self, plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = []
        running: set[str] = set()
        for entry in plan:
            title = entry["title"]
            if title not in self.ids:
                self.ids[title] = f"t{self.next_seq}"
                self.next_seq += 1
            todo_id = self.ids[title]
            state = map_todo_state(entry["status"])
            if state == "running":
                running.add(todo_id)
                if todo_id in self.failed:
                    state = "failed"
            else:
                # 모델이 상태를 바꿨으면(예: 다시 시도해 completed) 실패 표시를 푼다.
                self.failed.discard(todo_id)
            items.append({"id": todo_id, "title": title, "state": state})
        self.running = running
        return items

    def mark_running_failed(self) -> None:
        """지금 ``running``인 항목을 실패로 기록한다(12단계와 같은 이유·같은 한계).

        도구 호출 실패를 애플리케이션이 직접 관측해서 표시하는 것이지, 모델의
        내부 판단을 추측한 것이 아니다.
        """

        self.failed |= self.running
