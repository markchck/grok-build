"""SSE 이벤트 스키마 (PROJECT-SPEC.md 4절 고정 + 8·11단계 확장).

이 장은 8단계가 이미 추가한 ``done.partial``과 ``finish_reason=token_budget``을
그대로 물려받는다. 이 장이 새로 추가하는 것은 ``finish_reason`` 값 하나뿐이다.

- ``delegation_loop``: 에이전트끼리 제어권을 주고받다가(핸드오프) 같은
  패턴이 반복돼 중단했을 때 쓴다. 기존 값 중 ``repeated_tool_call``은 "같은
  (도구, 인자) 조합"을 가리키는 이름이라 의미가 다르고(8단계 2-4절), 그대로
  재사용하면 클라이언트가 "도구 호출이 반복된 것"과 "에이전트 위임이 반복된
  것"을 구분할 수 없다. docs/11-multi-agent.md 2-6절에서 이유를 설명하고,
  PROJECT-SPEC.md 7절도 함께 갱신했다.

``status.stage``는 PROJECT-SPEC.md 4절이 고정한 다섯 값
(``planning``/``retrieving``/``analyzing``/``verifying``/``writing``)을 그대로
쓴다 — 조사(investigator)는 ``retrieving``, 분석(analyst)은 ``analyzing``,
검증(verifier)은 ``verifying``, 슈퍼바이저의 위임 판단은 ``planning``에
대응시킨다. 새 stage 값을 만들 필요가 없었다.
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
    "token_budget",
    "delegation_loop",  # 11단계 추가
]


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def status_event(stage: StatusStage, message: str) -> str:
    return _sse("status", {"stage": stage, "message": message})


def tool_call_event(call_id: str, name: str, args: dict[str, Any]) -> str:
    return _sse("tool_call", {"id": call_id, "name": name, "args": args})


def tool_result_event(call_id: str, ok: bool, summary: str) -> str:
    return _sse("tool_result", {"id": call_id, "ok": ok, "summary": summary})


def error_event(code: str, message: str) -> str:
    return _sse("error", {"code": code, "message": message})


def done_event(finish_reason: FinishReason, *, partial: dict[str, Any] | None = None) -> str:
    data: dict[str, Any] = {"finish_reason": finish_reason}
    if partial is not None:
        data["partial"] = partial
    return _sse("done", data)
