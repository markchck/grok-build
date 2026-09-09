"""SSE(Server-Sent Events) 이벤트 포맷.

이벤트 이름과 data 필드는 ai/llm-agent-tutorial/PROJECT-SPEC.md 4절에 고정되어 있다.
이 장에서는 그중 token / status / error / done 네 가지만 실제로 내보낸다.
tool_call, tool_result, todo, sources, approval_request는 이후 장(3, 5, 8, 12, 13단계)에서
같은 스키마로 추가된다 — 이 장에서 미리 만들지 않는다.

status.stage는 PROJECT-SPEC이 고정한 다섯 값(planning, retrieving, analyzing, verifying, writing)
중 이 장에서 실제로 관측 가능한 두 값(planning, writing)만 쓴다. retrieving/analyzing/verifying은
검색·분석 단계가 생기는 4~7단계에서 쓰인다.
"""

from __future__ import annotations

import json
from typing import Any, Literal

# PROJECT-SPEC.md 4절에서 고정한 이벤트 이름 (이 장에서 다루는 것만).
EventName = Literal["token", "status", "error", "done"]

# PROJECT-SPEC.md 4절에서 고정한 status.stage 값 전체. 이 장은 planning/writing만 발생시킨다.
StatusStage = Literal["planning", "retrieving", "analyzing", "verifying", "writing"]


def sse_pack(event: EventName, data: dict[str, Any]) -> str:
    """event 이름과 JSON data를 text/event-stream 한 프레임으로 만든다.

    SSE 프레임 형식은 "event: <이름>\\ndata: <JSON 한 줄>\\n\\n"이다.
    data 안에 개행이 있으면 여러 줄 SSE 프레임이 필요하지만, 이 프로젝트의 data는
    항상 json.dumps 결과 한 줄이므로 이 단순한 형태로 충분하다.
    """
    body = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {body}\n\n"


def token_event(text: str) -> str:
    return sse_pack("token", {"text": text})


def status_event(stage: StatusStage, message: str) -> str:
    return sse_pack("status", {"stage": stage, "message": message})


def error_event(code: str, message: str) -> str:
    return sse_pack("error", {"code": code, "message": message})


def done_event(finish_reason: str) -> str:
    return sse_pack("done", {"finish_reason": finish_reason})
