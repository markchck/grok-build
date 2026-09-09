"""SSE 이벤트 스키마 (PROJECT-SPEC.md 4장 고정 + 8단계에서의 확장).

event 이름과 data JSON 키는 PROJECT-SPEC.md 4절 표를 그대로 따른다. 이 장에서
두 곳만 "추가"한다(기존 키를 없애거나 이름을 바꾸지 않는다). PROJECT-SPEC.md의
"규약을 바꿔야 하면 해당 장 본문에 명시하고 이 파일도 함께 고친다"는 규칙에 따라
docs/08-human-in-the-loop.md 2절에 이유를 적었고 PROJECT-SPEC.md도 함께 갱신했다.

1. ``done`` 이벤트에 선택 필드 ``partial``을 추가했다.
   커리큘럼 8단계 요구사항이 "제한 도달 시 중단 사유와 부분 결과 반환"이기
   때문이다. 기존 필드 ``finish_reason``은 그대로 두고 필드를 하나 더 얹었을
   뿐이므로, 이 필드를 모르는 이전 단계 클라이언트도 ``finish_reason``만
   읽으면 그대로 동작한다.
2. ``finish_reason`` 값에 ``token_budget``을 추가했다. PROJECT-SPEC.md 7절의
   목록(``stop``, ``max_steps``, ``timeout``, ``repeated_tool_call``,
   ``no_progress``, ``rejected_by_user``, ``cancelled``)에는 토큰 예산 초과를
   나타낼 값이 없었다. 이미 있는 값(예: ``max_steps``)을 재사용하면 "왜
   멈췄는지"를 클라이언트가 구분할 수 없게 되므로, 없는 사실을 지어내는
   대신 값 하나를 새로 추가하는 쪽을 선택했다.
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
    "token_budget",  # 8단계 추가
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


def approval_request_event(approval_id: str, action: str, args: dict[str, Any], reason: str) -> str:
    """PROJECT-SPEC.md 4절에 이미 정의돼 있던 이벤트. 8단계에서 처음 실제로 쓴다."""

    return _sse("approval_request", {"id": approval_id, "action": action, "args": args, "reason": reason})


def error_event(code: str, message: str) -> str:
    return _sse("error", {"code": code, "message": message})


def done_event(finish_reason: FinishReason, *, partial: dict[str, Any] | None = None) -> str:
    """중단이든 정상 종료든 done 이벤트 하나로 끝낸다(error 이벤트로 바꾸지 않는다).

    ``partial``은 8단계 확장 필드다. 지금까지 만든 답변 텍스트 조각, 사용한
    단계 수, 누적 토큰 사용량처럼 "끝까지 못 갔어도 사용자에게 보여줄 수
    있는 것"을 담는다. 정상 종료(``finish_reason == "stop"``)에는 보통 생략한다.
    """

    data: dict[str, Any] = {"finish_reason": finish_reason}
    if partial is not None:
        data["partial"] = partial
    return _sse("done", data)
