"""에이전트 루프 — 프레임워크 없이 while 문으로 직접 구현한다.

이 장(3단계)의 핵심 파일이다. 흐름은 다음과 같다.

    1. 모델을 부른다 (messages + tools 스펙을 함께 전달).
    2. 모델 응답에 tool_calls가 있으면:
       a. 각 tool_call의 인자(JSON 문자열)를 파싱한다.
       b. tools.run_tool()로 **애플리케이션이** 실제 함수를 실행한다.
       c. 실행 결과를 role="tool" 메시지로 대화에 추가한다.
       d. 1번으로 돌아간다.
    3. tool_calls가 없으면 모델의 content가 최종 답변이다. 종료한다.

이 과정에서 지켜야 할 두 가지 안전장치(PROJECT-SPEC.md 7장)를 while 루프
안에서 직접 검사한다:
    - 최대 단계 수(MAX_AGENT_STEPS), 최대 실행 시간(MAX_AGENT_SECONDS)
    - 같은 (도구 이름, 정규화한 인자) 조합이 3회 반복되면 중단

**중요**: 이 while 루프를 실행하는 것은 모델이 아니라 이 파이썬 프로세스다.
모델은 "다음에 무엇을 하고 싶은지"를 한 번의 응답으로만 말할 뿐, 반복을
스스로 제어하지 않는다. 반복·중단·한도 판단은 전부 애플리케이션 코드다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from docagent import llm
from docagent.config import settings
from docagent.tools import TOOL_SPECS, run_tool

SYSTEM_PROMPT = (
    "너는 판매 데이터를 분석해주는 보조원이다. "
    "필요하면 제공된 도구(sum_sales, lookup_sales_rows)를 사용해서 "
    "sales.csv의 실제 수치를 근거로 답변한다. "
    "도구를 쓰지 않고 숫자를 추측해서 답하지 않는다."
)


@dataclass
class AgentEvent:
    """agent.py가 밖으로 내보내는 사건 하나.

    kind는 docagent.events의 이벤트 이름과 1:1로 대응한다
    (status / tool_call / tool_result / token / done).
    """

    kind: str
    data: dict[str, Any]


@dataclass
class AgentRunResult:
    """루프가 끝난 뒤의 최종 상태. 테스트와 app.py에서 함께 쓴다."""

    finish_reason: str
    final_text: str
    steps_used: int
    messages: list[dict[str, Any]] = field(default_factory=list)


ChatFn = Callable[..., dict[str, Any]]

REPEAT_LIMIT = 3


def _normalize_args(args: dict[str, Any]) -> str:
    """반복 호출 감지용으로 인자를 정규화한 문자열로 만든다.

    키 순서를 정렬해서 같은 값이 다른 순서로 와도 같은 것으로 취급한다.
    """

    return json.dumps(args, sort_keys=True, ensure_ascii=False)


def run_agent(
    user_message: str,
    *,
    history: list[dict[str, Any]] | None = None,
    chat_fn: ChatFn = llm.chat_completion,
    max_steps: int | None = None,
    max_seconds: int | None = None,
) -> Iterator[AgentEvent]:
    """에이전트 루프를 실행하며 진행 이벤트를 하나씩 내보낸다(제너레이터).

    Args:
        user_message: 이번 턴의 사용자 입력.
        history: 이전 턴의 messages 리스트(without system prompt 반복 추가 방지용).
            None이면 새 대화로 취급한다.
        chat_fn: 모델 호출 함수. 기본은 llm.chat_completion이며,
            테스트에서는 실제 서버 없이 가짜 함수로 바꿔 끼운다.
        max_steps, max_seconds: 생략하면 config.settings 값을 쓴다.

    Yields:
        AgentEvent — status / tool_call / tool_result / token / done.
        마지막 이벤트는 항상 kind == "done"이다.
    """

    max_steps = max_steps if max_steps is not None else settings.max_agent_steps
    max_seconds = max_seconds if max_seconds is not None else settings.max_agent_seconds

    messages: list[dict[str, Any]] = list(history) if history else [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    messages.append({"role": "user", "content": user_message})

    call_counts: dict[tuple[str, str], int] = {}
    start = time.monotonic()
    step = 0

    while True:
        step += 1

        if step > max_steps:
            yield AgentEvent("done", {"finish_reason": "max_steps"})
            return

        elapsed = time.monotonic() - start
        if elapsed > max_seconds:
            yield AgentEvent("done", {"finish_reason": "timeout"})
            return

        yield AgentEvent("status", {"stage": "planning", "message": f"{step}단계: 모델에게 다음 행동을 묻는다"})

        try:
            assistant_message = chat_fn(messages, tools=TOOL_SPECS, tool_choice="auto")
        except llm.LLMError as exc:
            yield AgentEvent("error", {"code": "llm_error", "message": str(exc)})
            yield AgentEvent("done", {"finish_reason": "cancelled"})
            return

        messages.append(assistant_message)
        tool_calls = assistant_message.get("tool_calls")

        if not tool_calls:
            final_text = assistant_message.get("content") or ""
            yield AgentEvent("token", {"text": final_text})
            yield AgentEvent("done", {"finish_reason": "stop"})
            return

        yield AgentEvent(
            "status",
            {"stage": "analyzing", "message": f"도구 {len(tool_calls)}건 실행 준비"},
        )

        for call in tool_calls:
            call_id = call.get("id", "")
            fn = call.get("function", {})
            name = fn.get("name", "")
            raw_arguments = fn.get("arguments", "{}")

            try:
                args_dict = json.loads(raw_arguments) if raw_arguments else {}
            except json.JSONDecodeError:
                # 모델이 문법적으로 잘못된 JSON을 만든 경우도 예외로 죽이지 않고
                # 도구 결과 메시지로 되돌려 모델이 스스로 고치게 한다.
                args_dict = None

            yield AgentEvent(
                "tool_call",
                {"id": call_id, "name": name, "args": args_dict if args_dict is not None else {}},
            )

            if args_dict is None:
                error_content = json.dumps(
                    {
                        "error": "invalid_json",
                        "message": "arguments가 올바른 JSON이 아니다. 다시 만들어서 호출해달라.",
                    },
                    ensure_ascii=False,
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call_id, "name": name, "content": error_content}
                )
                yield AgentEvent("tool_result", {"id": call_id, "ok": False, "summary": "잘못된 JSON 인자"})
                continue

            key = (name, _normalize_args(args_dict))
            call_counts[key] = call_counts.get(key, 0) + 1

            if call_counts[key] > REPEAT_LIMIT:
                yield AgentEvent(
                    "tool_result",
                    {"id": call_id, "ok": False, "summary": "같은 도구·인자 반복 호출 감지"},
                )
                yield AgentEvent("done", {"finish_reason": "repeated_tool_call"})
                return

            result = run_tool(name, args_dict)
            messages.append(
                {"role": "tool", "tool_call_id": call_id, "name": name, "content": result.content}
            )

            summary = result.content if len(result.content) <= 200 else result.content[:200] + "..."
            yield AgentEvent("tool_result", {"id": call_id, "ok": result.ok, "summary": summary})

        # 도구 결과를 반영해서 다음 단계에서 모델을 다시 부른다.


def run_agent_collect(
    user_message: str,
    *,
    chat_fn: ChatFn = llm.chat_completion,
    max_steps: int | None = None,
    max_seconds: int | None = None,
) -> AgentRunResult:
    """제너레이터를 끝까지 소비해서 최종 결과만 필요할 때 쓰는 편의 함수.

    테스트와 비-스트리밍 호출 경로(예: 배치 스크립트)에서 쓴다.
    app.py의 SSE 엔드포인트는 run_agent()를 직접 순회한다.
    """

    finish_reason = "stop"
    final_text = ""
    steps_used = 0
    messages: list[dict[str, Any]] = []

    for event in run_agent(user_message, chat_fn=chat_fn, max_steps=max_steps, max_seconds=max_seconds):
        if event.kind == "status" and event.data.get("stage") == "planning":
            steps_used += 1
        if event.kind == "token":
            final_text = event.data["text"]
        if event.kind == "done":
            finish_reason = event.data["finish_reason"]

    return AgentRunResult(
        finish_reason=finish_reason,
        final_text=final_text,
        steps_used=steps_used,
        messages=messages,
    )
