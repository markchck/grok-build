"""에이전트 루프 — 3단계와 같은 while 루프 모양이지만, 도구를 실행하는 자리에
로컬 함수 대신 MCP 세션 호출이 들어간다.

3단계(``code/step03_tool_calling/docagent/agent.py``)와 나란히 비교하면:

    3단계: result = run_tool(name, args_dict)          # 같은 프로세스, 함수 호출
    10단계: result = await mcp_client.call_tool(name, args_dict)  # 다른 프로세스, 프로토콜 요청

바깥쪽 루프 구조("모델 호출 → tool_calls 있으면 실행 → 결과를 메시지로
추가 → 반복", 최대 단계·시간·반복 호출 감지)는 **바뀌지 않는다** — 이
루프를 도는 것이 여전히 이 파이썬 프로세스라는 사실 자체는 3단계와
같기 때문이다(PROJECT-SPEC.md 7장 실행 한도도 동일하게 적용한다). 바뀐
것은 도구 "탐색"(TOOL_SPECS를 파일에 미리 적어 두는 대신 매 세션 시작 시
``list_tool_specs()``로 물어본다)과 "실행"(파이썬 함수 호출 대신 비동기
프로토콜 요청, 그래서 이 파일은 3단계와 달리 async def다) 두 가지뿐이다.

이 장의 완료 기준(연결 실패 처리)에 맞춰, MCP 세션 자체가 끊긴 경우
(``McpConnectionError``)는 도구 실행 실패와 다르게 다룬다 — 도구 실행
실패는 모델에게 돌려주고 계속 진행하지만, 세션이 끊기면 이 턴을 더 진행할
수 없으므로 ``done`` 이벤트로 즉시 종료한다(finish_reason="cancelled").
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from docagent import llm
from docagent.config import get_settings
from docagent.mcp_client import McpConnectionError, McpToolsClient

SYSTEM_PROMPT = (
    "너는 판매 데이터와 사내 분기 리포트를 분석해주는 보조원이다. "
    "매출 수치는 반드시 도구(sum_sales, lookup_sales_rows)로 확인하고, "
    "매출 변화의 원인은 search_docs로 찾은 문서 내용을 근거로 답한다. "
    "도구를 쓰지 않고 숫자나 원인을 추측해서 답하지 않는다."
)

REPEAT_LIMIT = 3


@dataclass
class AgentEvent:
    kind: str
    data: dict[str, Any]


@dataclass
class AgentRunResult:
    finish_reason: str
    final_text: str
    steps_used: int
    messages: list[dict[str, Any]] = field(default_factory=list)


def _normalize_args(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False)


async def _default_chat_fn(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """``docagent/llm.py``는 3단계 버전을 그대로 가져온 동기 구현이라
    ``achat``이 없다. ``run_in_executor``로 동기 ``chat()``을 이벤트 루프
    밖(스레드)에서 돌려, 모델 서버 응답을 기다리는 동안 이 프로세스의
    다른 비동기 작업(다른 요청의 MCP 호출 등)을 막지 않게 한다. PROJECT-SPEC.md
    9절이 요구하는 "취소 가능한 진짜 비동기"는 아니다 — 그 요구사항을
    만족하려면 ``docagent/llm.py``에 ``achat``을 추가해야 하는데, 이 장은
    MCP 자체가 핵심이라 llm.py는 3단계 버전을 그대로 재사용했다. 이 절충을
    장 본문 "검증 상태"에도 명시한다.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: llm.chat(messages, settings=get_settings(), tools=tools)
    )
    return {
        "role": "assistant",
        "content": result.text or None,
        "tool_calls": result.tool_calls or None,
    }


async def run_agent(
    user_message: str,
    *,
    mcp_client: McpToolsClient,
    history: list[dict[str, Any]] | None = None,
    chat_fn=_default_chat_fn,
    max_steps: int | None = None,
    max_seconds: int | None = None,
) -> AsyncIterator[AgentEvent]:
    """에이전트 루프. MCP 서버 연결은 호출부가 만들어서 ``mcp_client``로
    넘긴다 — 이 함수는 연결의 생명주기(언제 열고 닫을지)를 책임지지 않는다
    (그건 app.py, Host의 몫이다). 이 함수는 "이미 연결된(또는 연결 시도할
    수 있는) 세션 하나로 도구를 어떻게 쓰는지"만 안다.
    """

    max_steps = max_steps if max_steps is not None else get_settings().max_agent_steps
    max_seconds = max_seconds if max_seconds is not None else get_settings().max_agent_seconds

    yield AgentEvent("status", {"stage": "planning", "message": "MCP 서버의 도구 목록을 확인하는 중"})
    try:
        tool_specs = await mcp_client.list_tool_specs()
    except McpConnectionError as exc:
        yield AgentEvent("error", {"code": "mcp_connection_failed", "message": str(exc)})
        yield AgentEvent("done", {"finish_reason": "cancelled"})
        return

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
            assistant_message = await chat_fn(messages, tools=tool_specs)
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
            "status", {"stage": "analyzing", "message": f"MCP 도구 {len(tool_calls)}건 호출 준비"}
        )

        for call in tool_calls:
            call_id = call.get("id", "")
            fn = call.get("function", {})
            name = fn.get("name", "")
            raw_arguments = fn.get("arguments", "{}")

            try:
                args_dict = json.loads(raw_arguments) if raw_arguments else {}
            except json.JSONDecodeError:
                args_dict = None

            yield AgentEvent(
                "tool_call", {"id": call_id, "name": name, "args": args_dict if args_dict is not None else {}}
            )

            if args_dict is None:
                error_content = json.dumps(
                    {"error": "invalid_json", "message": "arguments가 올바른 JSON이 아니다. 다시 만들어서 호출해달라."},
                    ensure_ascii=False,
                )
                messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": error_content})
                yield AgentEvent("tool_result", {"id": call_id, "ok": False, "summary": "잘못된 JSON 인자"})
                continue

            key = (name, _normalize_args(args_dict))
            call_counts[key] = call_counts.get(key, 0) + 1
            if call_counts[key] > REPEAT_LIMIT:
                yield AgentEvent("tool_result", {"id": call_id, "ok": False, "summary": "같은 도구·인자 반복 호출 감지"})
                yield AgentEvent("done", {"finish_reason": "repeated_tool_call"})
                return

            # --- 여기가 3단계와 근본적으로 다른 지점이다 -----------------
            # 3단계: result = run_tool(name, args_dict)  (같은 프로세스, 동기 함수 호출)
            # 여기: MCP 세션을 통한 요청/응답. McpConnectionError는 "도구가
            # 실패했다"가 아니라 "서버와 통신 자체가 안 된다"는 뜻이므로
            # 도구 결과 메시지로 모델에게 돌려주지 않고 이 턴을 중단한다 —
            # 모델에게 "다시 시도해라"라고 알려줘 봤자 같은 세션이 끊긴
            # 상태라 재시도도 똑같이 실패할 뿐이다.
            try:
                result = await mcp_client.call_tool(name, args_dict)
            except McpConnectionError as exc:
                yield AgentEvent("tool_result", {"id": call_id, "ok": False, "summary": f"MCP 연결 오류: {exc}"})
                yield AgentEvent("error", {"code": "mcp_connection_failed", "message": str(exc)})
                yield AgentEvent("done", {"finish_reason": "cancelled"})
                return

            messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": result.content})
            summary = result.content if len(result.content) <= 200 else result.content[:200] + "..."
            yield AgentEvent("tool_result", {"id": call_id, "ok": result.ok, "summary": summary})


async def run_agent_collect(
    user_message: str,
    *,
    mcp_client: McpToolsClient,
    chat_fn=_default_chat_fn,
    max_steps: int | None = None,
    max_seconds: int | None = None,
) -> AgentRunResult:
    """제너레이터를 끝까지 소비해서 최종 결과만 필요할 때 쓰는 편의 함수(테스트용)."""

    finish_reason = "stop"
    final_text = ""
    steps_used = 0

    async for event in run_agent(
        user_message, mcp_client=mcp_client, chat_fn=chat_fn, max_steps=max_steps, max_seconds=max_seconds
    ):
        if event.kind == "status" and event.data.get("stage") == "planning":
            steps_used += 1
        if event.kind == "token":
            final_text = event.data["text"]
        if event.kind == "done":
            finish_reason = event.data["finish_reason"]

    return AgentRunResult(finish_reason=finish_reason, final_text=final_text, steps_used=steps_used)
