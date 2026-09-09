"""SSE 이벤트 스키마. PROJECT-SPEC.md 4절에서 이름과 필드를 고정했다.

이 장에서는 아래 이벤트만 실제로 내보낸다: status, sources, token, error, done.
tool_call / tool_result / todo / approval_request는 3·8·12단계에서 쓴다.
스키마 자체는 프로젝트 전체에서 동일하므로 상수만 여기 미리 적어둔다.
"""

from __future__ import annotations

import json
from typing import Any

# status.stage 값 (고정)
STAGE_PLANNING = "planning"
STAGE_RETRIEVING = "retrieving"
STAGE_ANALYZING = "analyzing"
STAGE_VERIFYING = "verifying"
STAGE_WRITING = "writing"

# done.finish_reason 값 중 이 장에서 쓰는 것
FINISH_STOP = "stop"
FINISH_ERROR = "error"


def sse(event: str, data: dict[str, Any]) -> str:
    """SSE 한 프레임을 만든다. event 이름과 JSON data를 그대로 고정 스키마에 맞춰 보낸다."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def status(stage: str, message: str) -> str:
    return sse("status", {"stage": stage, "message": message})


def token(text: str) -> str:
    return sse("token", {"text": text})


def sources(items: list[dict[str, Any]]) -> str:
    return sse("sources", {"items": items})


def error(code: str, message: str) -> str:
    return sse("error", {"code": code, "message": message})


def done(finish_reason: str) -> str:
    return sse("done", {"finish_reason": finish_reason})
