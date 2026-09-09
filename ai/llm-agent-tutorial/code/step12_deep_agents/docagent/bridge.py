"""LangGraph 스트림 -> PROJECT-SPEC.md 4절 SSE 이벤트로 바꾼다.

Deep Agents가 만든 그래프는 ``agent.stream(..., stream_mode="values")``로
"이번 슈퍼스텝이 끝난 뒤의 전체 상태"를 순서대로 내보낸다. 이 모듈은 그
상태에서 **새로 추가된 메시지**와 **``todos`` 필드의 변화**만 골라
``events.py``의 이벤트로 옮긴다.

## 위임(``task`` 도구)이 보이는 단위

``task`` 도구 호출은 하위 에이전트(csv-analyst/doc-researcher) 하나를 통째로
실행한다. 하위 에이전트 내부의 낱개 도구 호출(예: ``execute``로 스크립트를
돌리는 것)은 이 스트림에 **별도 이벤트로 나타나지 않는다** — LangGraph
``stream_mode="values"``가 최상위 그래프의 상태만 보여주기 때문이다(하위
에이전트는 별도 서브그래프에서 실행된다). 그래서 화면에는 "task 호출 →
(위임한 동안 잠깐 조용함) → task 결과" 한 쌍만 보인다. 이것은 이 장이
의도한 관측 단위이기도 하다 — 위임은 "안에서 무엇을 몇 번 했는지"가 아니라
"무엇을 시켰고 무엇을 받았는지"로 보여주는 편이 상위 대화의 컨텍스트를
아낀다(2절 "긴 컨텍스트 관리" 참고). 하위 에이전트 내부까지 이벤트로
보고 싶다면 ``agent.stream(..., subgraphs=True)``로 바꿔야 한다 —
``[확인 필요: subgraphs=True일 때 이벤트 형태가 이 모듈의 가정과 얼마나
달라지는지는 이 장에서 실험하지 않았다]``.

## `todo` 이벤트로의 상태 변환

PROJECT-SPEC.md 4절의 ``todo`` 스키마는 ``{"id","title","state"}``이고
``state``는 ``pending``/``running``/``done``/``failed``다. langchain의
``write_todos`` 도구(``langchain`` 1.4.0 실측, ``Todo`` TypedDict)는
``content``/``status``만 가지고 ``status``는 ``pending``/``in_progress``/
``completed`` 세 값뿐이다 — ``id``도 ``failed``도 없다. 이 모듈이 그 차이를
메운다: ``id``는 ``content`` 문자열을 이번 run 안에서 처음 보는 순서대로
발급해 이후 상태 변화(같은 항목이 pending -> running -> done으로 바뀌는
것)에서도 같은 id를 유지한다. 계획이 바뀌어 새 ``content``가 나타나면 새
id를 받고, 목록에서 사라진 항목은 다음 ``todo`` 이벤트부터 보이지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

_STATE_MAP = {"pending": "pending", "in_progress": "running", "completed": "done"}

_TOOL_STAGE = {
    "write_todos": "planning",
    "task": "analyzing",
    "read_file": "retrieving",
    "write_file": "writing",
    "execute": "analyzing",
    "ls": "retrieving",
    "glob": "retrieving",
    "grep": "retrieving",
    "edit_file": "writing",
}


def map_todo_state(langchain_status: str) -> str:
    """langchain ``write_todos``의 status -> PROJECT-SPEC.md의 state.

    매핑에 없는 값(향후 langchain이 상태를 추가하는 경우)은 ``pending``으로
    낮춰 잡는다 — 알 수 없는 상태를 임의로 "완료"나 "실패"로 단정하지 않는다.
    """

    return _STATE_MAP.get(langchain_status, "pending")


def stage_for_tool(tool_name: str) -> str:
    """PROJECT-SPEC.md 4절 ``status.stage``(고정값 다섯 개) 중 하나로 매핑한다."""

    return _TOOL_STAGE.get(tool_name, "analyzing")


@dataclass
class TodoIdAssigner:
    """``content`` 문자열 -> 안정적인 id. 같은 run 동안 상태만 유지한다."""

    _ids: dict[str, str] = field(default_factory=dict)
    _next: int = 1
    _failed: set[str] = field(default_factory=set)
    _running: set[str] = field(default_factory=set)

    def items_for(self, todos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items = []
        running: set[str] = set()
        for todo in todos:
            content = todo["content"]
            if content not in self._ids:
                self._ids[content] = f"t{self._next}"
                self._next += 1
            todo_id = self._ids[content]
            state = map_todo_state(todo["status"])
            if state == "running":
                running.add(todo_id)
                # 이 항목을 실행하는 동안 도구가 실패했다면 failed로 올려 보낸다.
                if todo_id in self._failed:
                    state = "failed"
            else:
                # 모델이 상태를 바꿨으면(예: 다시 시도해 completed) 실패 표시를 푼다.
                self._failed.discard(todo_id)
            items.append({"id": todo_id, "title": content, "state": state})
        self._running = running
        return items

    def mark_running_failed(self) -> None:
        """지금 ``running`` 상태인 항목을 실패로 기록한다.

        ``write_todos``의 status에는 실패에 해당하는 값이 없다(pending /
        in_progress / completed 뿐). 그래서 "무엇이 실패했는가"는 모델이
        말해주지 않는다 — 도구 호출이 실패한 것을 **애플리케이션이 직접 관측**해서
        그때 진행 중이던 항목에 표시한다. 이것은 모델의 내부 판단을 추측한 것이
        아니라 실제 실행 사실이다(PROJECT-SPEC.md 4절).

        한계: 한 항목을 처리하는 동안 도구를 여러 번 부르고 그중 하나만 실패한
        뒤 다른 방법으로 성공하는 경우, 모델이 그 항목을 completed로 바꾸는
        시점에 표시가 풀린다. 실패한 채로 남는 것은 모델이 끝까지 진행 중으로
        두거나 실행이 거기서 멈춘 경우다.
        """

        self._failed |= self._running


def truncate(text: str, limit: int = 240) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "..."


def is_error_result(content: str) -> bool:
    # langchain의 도구 오류 메시지는 "Error"로 시작한다(filesystem.py,
    # subagents.py의 오류 분기를 직접 읽어 확인). 이 휴리스틱은 그 관례에
    # 기대므로, 다른 도구가 이 접두어 없이 오류를 표현하면 놓칠 수 있다.
    return content.startswith("Error")


def stream_agent_events(
    agent: Any,
    user_message: str,
    *,
    config: dict[str, Any] | None = None,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Deep Agent를 실행하며 ``(event_name, data)`` 쌍을 순서대로 만든다.

    ``event_name``/``data``는 ``docagent.events``의 각 ``*_event()`` 함수가
    받는 인자와 그대로 대응한다 — 이 함수는 렌더링(SSE 문자열 생성)을 하지
    않는다. app.py가 이 제너레이터를 감싸 ``_sse()``로 바꾼다(관심사 분리:
    이 함수는 테스트에서 문자열 파싱 없이 데이터로 바로 검증할 수 있다).
    """

    seen = 0
    assigner = TodoIdAssigner()
    final_text = ""
    last_todo_snapshot: list[dict[str, Any]] | None = None

    stream_input = {"messages": [{"role": "user", "content": user_message}]}
    for state in agent.stream(stream_input, config=config, stream_mode="values"):
        messages = state.get("messages", [])
        new_messages = messages[seen:]
        seen = len(messages)

        for message in new_messages:
            kind = type(message).__name__
            if kind == "AIMessage":
                tool_calls = getattr(message, "tool_calls", None) or []
                if tool_calls:
                    for call in tool_calls:
                        name = call.get("name", "")
                        yield ("status", {"stage": stage_for_tool(name), "message": f"{name} 호출 준비"})
                        yield ("tool_call", {"id": call.get("id", ""), "name": name, "args": call.get("args", {})})
                else:
                    content = message.content or ""
                    if content:
                        final_text = content
                        yield ("token", {"text": content})
            elif kind == "ToolMessage":
                content = message.content if isinstance(message.content, str) else str(message.content)
                ok = not is_error_result(content)
                if not ok:
                    # 실패를 관측한 시점에 진행 중이던 작업 항목을 failed로 표시한다.
                    assigner.mark_running_failed()
                    last_todo_snapshot = None  # 다음 스냅샷을 강제로 다시 내보낸다
                yield (
                    "tool_result",
                    {"id": getattr(message, "tool_call_id", ""), "ok": ok, "summary": truncate(content)},
                )

        todos = state.get("todos")
        if todos is not None:
            items = assigner.items_for(todos)
            if items != last_todo_snapshot:
                last_todo_snapshot = items
                yield ("todo", {"items": items})

    yield ("done", {"finish_reason": "stop", "partial": {"text": final_text}})
