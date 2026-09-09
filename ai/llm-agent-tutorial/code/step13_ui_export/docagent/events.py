"""SSE 이벤트 스키마 (PROJECT-SPEC.md 4절 고정).

이 파일은 5·8·12단계 events.py를 합친 것뿐이다 — 각 단계가 도입한 이벤트
함수(``sources_event``는 5단계, ``approval_request_event``/``done_event``의
``partial``은 8단계, ``todo_event``는 12단계에서 값이 채워짐)를 그대로
가져왔다. 새 이벤트(``event:`` 이름)는 이 장에서도 만들지 않는다.

**필드 하나만 추가한다**: ``tool_call``의 ``data``에 ``source`` 필드를
더했다(기본값 ``"local"``). 최종 통합에서 로컬 함수 Tool·MCP 도구·Skill이
같은 에이전트 루프 안에서 함께 실행되는데(``docagent/agent.py``), 화면이
이 셋을 구분해서 보여주려면 "이 도구 호출이 어디서 왔는가"를 애플리케이션이
이미 아는 사실을 그대로 실어 보내는 방법이 필요했다 — 기존 네 필드
(``id``/``name``/``args``) 중 어디에도 그 정보가 없었다. ``done.partial``에
``csv_available``을 추가할 때와 같은 논리(PROJECT-SPEC.md 4절)로, 이 필드를
모르는 이전 단계 클라이언트도 그대로 동작한다 — 몰라도 되는 추가 필드일
뿐이다. PROJECT-SPEC.md 4절 표에도 이 사실을 반영해 뒀다.
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


def tool_call_event(call_id: str, name: str, args: dict[str, Any], source: str = "local") -> str:
    """``source``: ``"local"``(로컬 함수 Tool, 기본값) | ``"mcp"``(10단계 MCP 서버) |
    ``"skill"``(12단계 Skill 스크립트)."""

    return _sse("tool_call", {"id": call_id, "name": name, "args": args, "source": source})


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
