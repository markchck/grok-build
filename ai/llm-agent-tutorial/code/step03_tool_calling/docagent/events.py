"""SSE 이벤트 스키마 (PROJECT-SPEC.md 4장 고정).

event 이름과 data JSON 키를 여기서만 만든다. app.py와 agent.py는 이 모듈의
함수로만 이벤트 문자열을 만들어서 event/data 이름이 흩어지지 않게 한다.

이 장(3단계)에서는 token, status, tool_call, tool_result, error, done을 쓴다.
todo, sources, approval_request는 뒤 단계(8, 12, 13단계)에서 쓴다.
"""

from __future__ import annotations

import json
from typing import Any, Literal

StatusStage = Literal["planning", "retrieving", "analyzing", "verifying", "writing"]
FinishReason = Literal[
    "stop",
    "max_steps",
    "timeout",
    "repeated_tool_call",
    "no_progress",
    "rejected_by_user",
    "cancelled",
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


def error_event(code: str, message: str) -> str:
    return _sse("error", {"code": code, "message": message})


def done_event(finish_reason: FinishReason) -> str:
    return _sse("done", {"finish_reason": finish_reason})
