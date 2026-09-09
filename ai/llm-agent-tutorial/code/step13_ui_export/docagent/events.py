"""SSE 이벤트 스키마 (PROJECT-SPEC.md 4절 고정. 이 장에서 새 이벤트를 만들지 않는다).

이 파일은 5·8·12단계 events.py를 합친 것뿐이다 — 각 단계가 도입한 이벤트
함수(``sources_event``는 5단계, ``approval_request_event``/``done_event``의
``partial``은 8단계, ``todo_event``는 12단계에서 값이 채워짐)를 그대로
가져왔다. 13단계가 새로 정의하는 event/data 필드는 없다. 화면이 이 스키마를
어떻게 소비하는지가 이 장의 주제다(docs/13-ui-and-export.md 참고).
"""

from __future__ import annotations

import json
from typing import Any, Literal

StatusStage = Literal["starting", "planning", "retrieving", "analyzing", "verifying", "writing"]
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
    """``items``는 이번 시점의 전체 목록이다(증분이 아니다)."""

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
