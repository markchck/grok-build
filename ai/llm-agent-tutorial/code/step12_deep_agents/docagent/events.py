"""SSE 이벤트 스키마 (PROJECT-SPEC.md 4절 고정).

이 장에서 처음으로 실제 데이터를 채워 쓰는 이벤트는 ``todo``다. PROJECT-SPEC.md
4절의 스키마를 그대로 따른다: ``{"items":[{"id","title","state"}]}``,
``state``는 ``pending``/``running``/``done``/``failed``.

**Deep Agents(``langchain`` ``write_todos`` 도구)의 상태 값과 이 스키마는
이름이 다르다.** ``write_todos``는 ``pending``/``in_progress``/``completed``
세 값만 쓴다(``failed`` 개념이 없다 — langchain 1.4.0 실측,
``langchain/agents/middleware/todo.py``). 이 장은 그 값을 그대로 내보내지
않고 ``docagent/bridge.py``의 ``map_todo_state()``에서 PROJECT-SPEC.md의
값으로 변환한다(``in_progress`` -> ``running``, ``completed`` -> ``done``).
``failed``는 langchain 쪽에 대응 값이 없으므로, 이 장에서는 위임한 하위
작업(``task`` 도구 호출)이 오류로 끝났을 때 애플리케이션이 직접 판단해서만
붙인다 — 모델이 스스로 "실패"라고 표시하는 경우는 없다.
"""

from __future__ import annotations

import json
from typing import Any, Literal

StatusStage = Literal["planning", "retrieving", "analyzing", "verifying", "writing"]
TodoState = Literal["pending", "running", "done", "failed"]
FinishReason = Literal[
    "stop",
    "max_steps",
    "timeout",
    "repeated_tool_call",
    "no_progress",
    "rejected_by_user",
    "cancelled",
    "token_budget",
]


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def token_event(text: str) -> str:
    return _sse("token", {"text": text})


def status_event(stage: StatusStage, message: str) -> str:
    return _sse("status", {"stage": stage, "message": message})


def tool_call_event(call_id: str, name: str, args: dict[str, Any]) -> str:
    return _sse("tool_call", {"id": call_id, "name": name, "args": args})


def tool_result_event(call_id: str, ok: bool, summary: str) -> str:
    return _sse("tool_result", {"id": call_id, "ok": ok, "summary": summary})


def todo_event(items: list[dict[str, Any]]) -> str:
    """PROJECT-SPEC.md 4절: ``items``의 각 원소는 ``{"id","title","state"}``.

    ``items``는 이번 호출 시점의 **전체 목록**이다(증분이 아니다) — 계획이
    바뀌어 항목이 사라지면 다음 ``todo`` 이벤트의 목록에서도 사라진다.
    """

    return _sse("todo", {"items": items})


def sources_event(items: list[dict[str, Any]]) -> str:
    return _sse("sources", {"items": items})


def approval_request_event(approval_id: str, action: str, args: dict[str, Any], reason: str) -> str:
    return _sse("approval_request", {"id": approval_id, "action": action, "args": args, "reason": reason})


def error_event(code: str, message: str) -> str:
    return _sse("error", {"code": code, "message": message})


def done_event(finish_reason: FinishReason, *, partial: dict[str, Any] | None = None) -> str:
    data: dict[str, Any] = {"finish_reason": finish_reason}
    if partial is not None:
        data["partial"] = partial
    return _sse("done", data)
