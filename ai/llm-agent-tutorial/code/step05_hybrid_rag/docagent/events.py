"""SSE 이벤트 스키마 (PROJECT-SPEC.md 4장 고정).

event 이름과 data JSON 키를 여기서만 만든다. app.py는 이 모듈의 함수로만
이벤트 문자열을 만들어서 event/data 이름이 흩어지지 않게 한다.

이 장(5단계)에서 새로 쓰는 것은 sources_event뿐이다. 나머지는 2·3단계에서
이미 정의된 것을 그대로 가져왔다.
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


def sources_event(items: list[dict[str, Any]]) -> str:
    """근거 목록. items의 각 원소는 {"id","doc","page","url","snippet"}.

    5단계에서 도입한다(PROJECT-SPEC.md 4장). 애플리케이션이 인용 검증을 마친
    뒤에만 이 이벤트를 내보낸다 — 검증 전의 원시 모델 인용을 그대로 넣지 않는다.
    """
    return _sse("sources", {"items": items})


def error_event(code: str, message: str) -> str:
    return _sse("error", {"code": code, "message": message})


def done_event(finish_reason: FinishReason) -> str:
    return _sse("done", {"finish_reason": finish_reason})
