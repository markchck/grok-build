"""에이전트 루프 — 3단계의 while 루프에 Human in the Loop와 예산 관리를 더한다.

3단계와 가장 크게 다른 점: 이 장의 루프는 **한 번의 파이썬 함수 호출로 끝까지
돌지 않는다.** 승인이 필요한 도구를 만나면 상태를 SQLite에 저장하고 제너레이터를
끝낸다(다음 이벤트를 만들지 않고 return). 승인 결정이 내려진 뒤 ``advance_run``을
다시 부르면, 메모리가 아니라 저장된 상태에서 이어서 진행한다 — 그 사이에 서버
프로세스가 재시작됐어도 상관없다. 이 파일 안에서 "지금 메모리에 들고 있는 것"에
의존하는 상태는 하나도 없다. 전부 ``store.RunState``를 통해 SQLite로 오간다.

지켜야 할 안전장치 네 가지(PROJECT-SPEC.md 7절 + 이 장에서 추가):
    1. 최대 단계 수(``max_agent_steps``), 최대 실행 시간(``max_agent_seconds``)
    2. 같은 (도구 이름, 정규화한 인자) 조합이 3회 반복 -> ``repeated_tool_call``
    3. **다른 인자인데 결과가 바뀌지 않는 반복** -> ``no_progress``
    4. 누적 토큰 사용량이 ``max_agent_tokens``를 넘음 -> ``token_budget``
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator

from docagent import llm, store
from docagent.config import Settings, get_settings
from docagent.tools import (
    APPROVAL_REQUIRED_TOOLS,
    TOOL_SPECS,
    ToolRunResult,
    run_tool,
    validate_args,
)

SYSTEM_PROMPT = (
    "너는 문서 분석 에이전트다. 필요하면 제공된 도구를 사용해서 sales.csv의 실제 "
    "수치를 근거로 답한다. archive_report는 되돌리기 어려운 작업이므로 반드시 "
    "호출해서 승인을 받는다. 도구를 쓰지 않고 숫자를 추측해서 답하지 않는다."
)

REPEAT_LIMIT = 3
NO_PROGRESS_WINDOW = 3
CHARS_PER_TOKEN_ESTIMATE = 4  # 토큰 추정치 계산에 쓰는 대략적인 나눗셈 값(3절에서 근거 설명)

_TOOL_SPECS_BY_NAME = {spec["function"]["name"]: spec for spec in TOOL_SPECS}


def _select_tools(tool_names: list[str] | None) -> list[dict[str, Any]]:
    """도구 목록을 고른다. 평소에는 항상 TOOL_SPECS 전체를 그대로 쓴다.

    ``tool_names``는 이 장의 스텁 서버(``code/_tools/fake_openai_server.py``)를
    상대로 승인 흐름을 시연할 때만 쓰는 값이다. 그 스텁은 모델이 아니어서
    "사용자가 뭘 물었는지"를 보지 않고 **항상 tools 목록의 첫 번째 도구**를
    호출한다(5단계 실측 사실, 스텁 코드 주석에도 있다). 실제 모델은 설명과
    사용자 의도로 도구를 고르므로 운영 코드에는 영향이 없다 — 이 값을 생략하면
    (기본값 ``None``) 항상 원래 순서(TOOL_SPECS)를 그대로 쓴다.
    """

    if not tool_names:
        return TOOL_SPECS
    selected = [_TOOL_SPECS_BY_NAME[name] for name in tool_names if name in _TOOL_SPECS_BY_NAME]
    return selected or TOOL_SPECS


@dataclass
class AgentEvent:
    kind: str
    data: dict[str, Any]


ChatFn = Callable[..., llm.ChatResult]
AsyncChatFn = Callable[..., Awaitable[llm.ChatResult]]


# ---------------------------------------------------------------------------
# 보조 함수
# ---------------------------------------------------------------------------


def _normalize_args(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False)


def idempotency_key(run_id: str, call_id: str, tool_name: str, args: dict[str, Any]) -> str:
    """멱등성 키. 같은 run 안에서 같은 call_id + 같은 도구 + 같은 인자만 같은 키를 만든다.

    ``call_id``를 포함하는 이유: 모델이 같은 도구를 같은 인자로 "정당하게" 여러
    단계에 걸쳐 다시 부르는 경우(예: 사용자가 같은 질문을 다시 함)와, 이 장이
    막으려는 "같은 실행이 중복 처리되는 경우"(승인 더블 클릭, 재개 재시도)를
    구분해야 하기 때문이다. 후자는 항상 같은 call_id를 가진 같은 tool_call을
    다시 처리하는 상황이고, 전자는 매번 새 call_id를 받는 새 tool_call이다.
    """

    raw = f"{run_id}|{call_id}|{tool_name}|{_normalize_args(args)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _result_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _estimate_tokens(text_payload: str) -> int:
    """usage를 안 주는 서버를 위한 대략적인 토큰 추정.

    실제 토크나이저를 쓰지 않는다(모델마다 다르고, 이 장에서 새 의존성을
    추가할 이유가 없다). "글자 수 / 4"는 영어 기준의 매우 거친 경험칙이고
    한국어에는 더 안 맞는다 — 정확한 값이 아니라 "그래도 예산을 완전히
    무시하는 것보다는 낫다"는 하한선으로만 쓴다. 이 값을 쓴 실행은
    ``usage_estimated=True``로 표시해서 화면에 "추정치"라고 알린다.
    """

    return max(1, len(text_payload) // CHARS_PER_TOKEN_ESTIMATE)


def _accumulate_usage(run: store.RunState, usage: dict[str, Any], messages_sent: list[dict], reply: dict) -> None:
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    if prompt is None and completion is None and total is None:
        # usage를 안 주는 서버 -> 문자 수 기반으로 근사한다.
        prompt = _estimate_tokens(json.dumps(messages_sent, ensure_ascii=False))
        completion = _estimate_tokens(json.dumps(reply, ensure_ascii=False))
        total = prompt + completion
        run.usage_estimated = True
    run.usage_prompt += int(prompt or 0)
    run.usage_completion += int(completion or 0)
    run.usage_total += int(total or (int(prompt or 0) + int(completion or 0)))


def _final_text_so_far(run: store.RunState) -> str:
    for message in reversed(run.messages):
        if message.get("role") == "assistant" and isinstance(message.get("content"), str) and message["content"]:
            return message["content"]
    return ""


def _partial(run: store.RunState) -> dict[str, Any]:
    return {
        "text": run.final_text if run.final_text is not None else _final_text_so_far(run),
        "steps_used": run.step,
        "tokens_used": run.usage_total,
        "tokens_estimated": run.usage_estimated,
    }


# ---------------------------------------------------------------------------
# 실행 시작
# ---------------------------------------------------------------------------


def start_run(conn, user_message: str, *, history: list[dict[str, Any]] | None = None) -> str:
    """새 run을 만들어 저장하고 run_id를 돌려준다. 실행은 advance_run이 한다."""

    messages: list[dict[str, Any]] = list(history) if history else [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    messages.append({"role": "user", "content": user_message})
    run = store.create_run(conn, messages)
    return run.run_id


# ---------------------------------------------------------------------------
# 승인 대기 상태를 이어서 처리한다
# ---------------------------------------------------------------------------


def _resolve_pending_approval(conn, run: store.RunState) -> Iterator[AgentEvent]:
    """대기 중인 승인 요청의 결정을 읽어 messages에 반영한다.

    반환값(제너레이터의 ``return``)은 다음 중 하나다.
        "still_pending" — 아직 아무도 결정하지 않았다. 루프를 이어갈 수 없다.
        "aborted"       — 거절(중단 모드)로 실행 자체를 끝낸다.
        "continue"      — 승인/거절(대안 모드)/수정 처리를 끝냈다. 메인 루프를 계속한다.
    """

    approval = store.get_approval(conn, run.pending_approval_id)
    if approval is None or approval["status"] == "pending":
        return "still_pending"

    call_id = approval["call_id"]
    tool_name = approval["tool_name"]
    original_args = json.loads(approval["args_json"])

    if approval["status"] == "rejected":
        mode = approval["decision_mode"] or "alternative"
        note = json.loads(approval["decision_args_json"])["note"] if approval["decision_args_json"] else ""
        if mode == "abort":
            run.status = "done"
            run.finish_reason = "rejected_by_user"
            run.pending_approval_id = None
            yield AgentEvent(
                "tool_result",
                {"id": call_id, "ok": False, "summary": f"사용자가 거절해 실행을 중단했다. {note}".strip()},
            )
            return "aborted"

        # 대안 모드: 실행하지 않은 채 "거절됐다"는 사실을 도구 결과로 모델에 돌려주고
        # 계속 진행시킨다(모델이 다른 방법을 찾거나, 그냥 이 작업 없이 답하게 한다).
        content = json.dumps(
            {"error": "rejected_by_user", "message": f"사용자가 이 작업을 거절했다. {note}".strip()},
            ensure_ascii=False,
        )
        run.messages.append({"role": "tool", "tool_call_id": call_id, "name": tool_name, "content": content})
        yield AgentEvent("tool_result", {"id": call_id, "ok": False, "summary": "사용자가 거절함(대안 진행)"})
        run.status = "running"
        run.pending_approval_id = None
        return "continue"

    if approval["status"] == "edited":
        edited_args = json.loads(approval["decision_args_json"])
        parsed, error_result = validate_args(tool_name, edited_args)
        if error_result is not None:
            run.messages.append(
                {"role": "tool", "tool_call_id": call_id, "name": tool_name, "content": error_result.content}
            )
            yield AgentEvent("tool_result", {"id": call_id, "ok": False, "summary": "수정된 인자도 유효하지 않음"})
            run.status = "running"
            run.pending_approval_id = None
            return "continue"

        key = idempotency_key(run.run_id, call_id, tool_name, edited_args)
        yield AgentEvent("tool_call", {"id": call_id, "name": tool_name, "args": edited_args})
        result, cached = _execute_with_cache(conn, run, key, tool_name, edited_args)
        summary = ("(캐시됨) " if cached else "") + "사용자가 인자를 수정해 실행함: " + (
            result.content if len(result.content) <= 200 else result.content[:200] + "..."
        )
        run.messages.append(
            {"role": "tool", "tool_call_id": call_id, "name": tool_name, "content": result.content}
        )
        yield AgentEvent("tool_result", {"id": call_id, "ok": result.ok, "summary": summary})
        _register_progress(run, tool_name, edited_args, result)
        run.status = "running"
        run.pending_approval_id = None
        return "continue"

    # approved: 원래 인자 그대로 실행한다. 이미 실행됐다면(캐시 적중) 다시 실행하지 않는다.
    key = idempotency_key(run.run_id, call_id, tool_name, original_args)
    result, cached = _execute_with_cache(conn, run, key, tool_name, original_args)
    summary = ("(캐시됨, 재실행하지 않음) " if cached else "승인됨: ") + (
        result.content if len(result.content) <= 200 else result.content[:200] + "..."
    )
    run.messages.append({"role": "tool", "tool_call_id": call_id, "name": tool_name, "content": result.content})
    yield AgentEvent("tool_result", {"id": call_id, "ok": result.ok, "summary": summary})
    _register_progress(run, tool_name, original_args, result)
    run.status = "running"
    run.pending_approval_id = None
    return "continue"


def _execute_with_cache(
    conn, run: store.RunState, key: str, tool_name: str, args: dict[str, Any]
) -> tuple[ToolRunResult, bool]:
    """멱등성 키로 캐시를 먼저 확인한 뒤에만 실제로 도구를 실행한다.

    이 함수가 **중복 실행 방지**의 실제 구현이다. 재개된 run이 이미 실행된
    단계를 다시 밟거나, 승인 결정 엔드포인트가 두 번 불려도(락으로 대부분
    막히지만 방어적으로), 실제 부작용이 있는 함수는 최초 1회만 호출된다.
    """

    cached = store.cache_get(conn, key)
    if cached is not None:
        return ToolRunResult(ok=bool(cached["result_ok"]), content=cached["result_content"]), True

    result = run_tool(tool_name, args)
    store.cache_set(
        conn, idempotency_key=key, run_id=run.run_id, tool_name=tool_name, args=args, ok=result.ok, content=result.content
    )
    return result, False


def _register_progress(run: store.RunState, tool_name: str, args: dict[str, Any], result: ToolRunResult) -> None:
    """결과-무변화 반복 감지(no_progress)용 상태를 갱신한다.

    이 호출의 (도구, 정규화한 인자) 조합이 **이번 run에서 처음** 등장했을
    때만 결과 해시를 기록한다. 완전히 같은 인자의 반복은 이미 3절의
    ``call_counts``(반복 감지 (a))가 따로 처리하므로, 여기서는 "인자는
    다른데 실질적으로 같은 결과만 돌아오는" 경우만 보고 싶어서다.
    """

    key = (tool_name, _normalize_args(args))
    key_str = f"{key[0]}|{key[1]}"
    if run.call_counts.get(key_str, 0) != 1:
        return
    run.progress_hashes.append(_result_hash(result.content))
    # 최근 창(window)만 유지한다. 계속 쌓아두면 파일이 무한히 커진다.
    if len(run.progress_hashes) > NO_PROGRESS_WINDOW:
        run.progress_hashes = run.progress_hashes[-NO_PROGRESS_WINDOW:]


def _no_progress_triggered(run: store.RunState) -> bool:
    if len(run.progress_hashes) < NO_PROGRESS_WINDOW:
        return False
    window = run.progress_hashes[-NO_PROGRESS_WINDOW:]
    return len(set(window)) == 1


# ---------------------------------------------------------------------------
# 메인 진행 함수
# ---------------------------------------------------------------------------


def advance_run(
    conn,
    run_id: str,
    *,
    chat_fn: ChatFn = llm.chat,
    settings: Settings | None = None,
    tool_names: list[str] | None = None,
) -> Iterator[AgentEvent]:
    """저장된 상태에서 run을 이어서(또는 새로) 진행한다.

    한 번의 호출은 다음 중 하나로 끝난다.
        - 승인이 필요한 도구를 만나 ``approval_request``를 내보내고 멈춘다(아직 done 아님).
        - 모델이 최종 답을 냈거나 한도를 넘어 ``done``을 내보낸다.
        - 다른 요청이 이미 이 run을 처리 중이어서 즉시 ``error``만 내보낸다.
    """

    settings = settings or get_settings()

    if not store.try_acquire(conn, run_id):
        yield AgentEvent(
            "error", {"code": "run_busy", "message": "이 run은 이미 다른 요청이 처리하고 있다."}
        )
        return

    try:
        run = store.load_run(conn, run_id)
        if run is None:
            yield AgentEvent("error", {"code": "not_found", "message": f"run을 찾을 수 없다: {run_id}"})
            return

        if run.status == "done":
            yield AgentEvent("done", {"finish_reason": run.finish_reason, "partial": _partial(run)})
            return

        if run.status == "paused_approval":
            resolution = yield from _resolve_pending_approval(conn, run)
            if resolution == "still_pending":
                yield AgentEvent(
                    "status", {"stage": "planning", "message": "아직 승인 결정이 내려지지 않았다. 대기한다."}
                )
                store.save_run(conn, run)
                return
            store.save_run(conn, run)
            if resolution == "aborted":
                yield AgentEvent("done", {"finish_reason": run.finish_reason, "partial": _partial(run)})
                return
            # resolution == "continue" -> 아래 while 루프로 이어진다.

        yield from _run_loop(conn, run, chat_fn, settings, _select_tools(tool_names))
    finally:
        store.release(conn, run_id)


def _finish(conn, run: store.RunState, finish_reason: str) -> AgentEvent:
    run.status = "done"
    run.finish_reason = finish_reason
    if run.final_text is None:
        run.final_text = _final_text_so_far(run)
    store.save_run(conn, run)
    return AgentEvent("done", {"finish_reason": finish_reason, "partial": _partial(run)})


def _run_loop(
    conn, run: store.RunState, chat_fn: ChatFn, settings: Settings, tool_specs: list[dict[str, Any]]
) -> Iterator[AgentEvent]:
    while True:
        run.step += 1

        if run.step > settings.max_agent_steps:
            yield _finish(conn, run, "max_steps")
            return

        if time.time() - run.start_time > settings.max_agent_seconds:
            yield _finish(conn, run, "timeout")
            return

        if run.usage_total > settings.max_agent_tokens:
            yield _finish(conn, run, "token_budget")
            return

        yield AgentEvent("status", {"stage": "planning", "message": f"{run.step}단계: 모델에게 다음 행동을 묻는다"})

        try:
            response = chat_fn(run.messages, tools=tool_specs, settings=settings)
        except llm.LLMError as exc:
            yield AgentEvent("error", {"code": "llm_error", "message": str(exc)})
            yield _finish(conn, run, "cancelled")
            return

        assistant_message = {
            "role": "assistant",
            "content": response.text or None,
            "tool_calls": response.tool_calls or None,
        }
        usage = (response.raw or {}).get("usage") or {}
        _accumulate_usage(run, usage, run.messages, assistant_message)
        run.messages.append(assistant_message)
        store.save_run(conn, run)

        if run.usage_total > settings.max_agent_tokens:
            yield _finish(conn, run, "token_budget")
            return

        tool_calls = assistant_message.get("tool_calls")
        if not tool_calls:
            final_text = assistant_message.get("content") or ""
            run.final_text = final_text
            yield AgentEvent("token", {"text": final_text})
            yield _finish(conn, run, "stop")
            return

        yield AgentEvent("status", {"stage": "analyzing", "message": f"도구 {len(tool_calls)}건 실행 준비"})

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
                    {"error": "invalid_json", "message": "arguments가 올바른 JSON이 아니다."}, ensure_ascii=False
                )
                run.messages.append(
                    {"role": "tool", "tool_call_id": call_id, "name": name, "content": error_content}
                )
                yield AgentEvent("tool_result", {"id": call_id, "ok": False, "summary": "잘못된 JSON 인자"})
                continue

            key_str = f"{name}|{_normalize_args(args_dict)}"
            run.call_counts[key_str] = run.call_counts.get(key_str, 0) + 1

            if run.call_counts[key_str] > REPEAT_LIMIT:
                yield AgentEvent(
                    "tool_result", {"id": call_id, "ok": False, "summary": "같은 도구·인자 반복 호출 감지"}
                )
                store.save_run(conn, run)
                yield _finish(conn, run, "repeated_tool_call")
                return

            if name in APPROVAL_REQUIRED_TOOLS:
                idem_key = idempotency_key(run.run_id, call_id, name, args_dict)
                cached = store.cache_get(conn, idem_key)
                if cached is not None:
                    # 이미 (과거 승인으로) 실행된 호출이 재개 과정에서 다시 나타난 경우.
                    result = ToolRunResult(ok=bool(cached["result_ok"]), content=cached["result_content"])
                    run.messages.append(
                        {"role": "tool", "tool_call_id": call_id, "name": name, "content": result.content}
                    )
                    yield AgentEvent(
                        "tool_result", {"id": call_id, "ok": result.ok, "summary": "(캐시됨) 이미 승인·실행된 호출"}
                    )
                    _register_progress(run, name, args_dict, result)
                    continue

                approval_id = store.create_approval(
                    conn,
                    run_id=run.run_id,
                    call_id=call_id,
                    tool_name=name,
                    args=args_dict,
                    reason=f"'{name}'은(는) 되돌리기 어려운 작업이라 실행 전 승인이 필요하다.",
                    idempotency_key=idem_key,
                )
                run.status = "paused_approval"
                run.pending_approval_id = approval_id
                store.save_run(conn, run)
                yield AgentEvent(
                    "approval_request",
                    {
                        "id": approval_id,
                        "action": name,
                        "args": args_dict,
                        "reason": f"'{name}'은(는) 되돌리기 어려운 작업이라 실행 전 승인이 필요하다.",
                    },
                )
                return  # 여기서 스트림을 끝낸다. done 이벤트는 아직 내보내지 않는다.

            idem_key = idempotency_key(run.run_id, call_id, name, args_dict)
            result, cached = _execute_with_cache(conn, run, idem_key, name, args_dict)
            run.messages.append(
                {"role": "tool", "tool_call_id": call_id, "name": name, "content": result.content}
            )
            summary = ("(캐시됨) " if cached else "") + (
                result.content if len(result.content) <= 200 else result.content[:200] + "..."
            )
            yield AgentEvent("tool_result", {"id": call_id, "ok": result.ok, "summary": summary})
            _register_progress(run, name, args_dict, result)

            if _no_progress_triggered(run):
                store.save_run(conn, run)
                yield _finish(conn, run, "no_progress")
                return

        store.save_run(conn, run)
        # 도구 결과를 반영해서 다음 단계에서 모델을 다시 부른다.


# ---------------------------------------------------------------------------
# 비동기 버전 (PROJECT-SPEC.md 9절) — app.py가 쓴다
# ---------------------------------------------------------------------------
#
# 아래 ``aadvance_run``/``_arun_loop``는 위 ``advance_run``/``_run_loop``와
# 로직이 완전히 같다. 다른 점은 모델 호출을 ``await chat_fn(...)``으로 하는
# 것뿐이다 — 이 ``await`` 지점이 있어야 클라이언트가 연결을 끊었을 때(이
# 제너레이터를 도는 태스크가 취소될 때) 그 취소가 ``achat``
# 안의 취소 처리까지 전달되어 모델 서버로 나가는 HTTP 연결이 실제로 닫힌다.
# ``_resolve_pending_approval``은 순수 상태 전이 로직(SQLite 읽기/쓰기)이라
# 모델을 부르지 않는다 — 동기 버전을 그대로 수동으로 순회해서 쓴다(async
# generator 안에서는 ``yield from``을 쓸 수 없다).


async def aadvance_run(
    conn,
    run_id: str,
    *,
    chat_fn: AsyncChatFn = llm.achat,
    settings: Settings | None = None,
    tool_names: list[str] | None = None,
) -> AsyncIterator[AgentEvent]:
    """``advance_run()``의 비동기 버전. app.py의 ``/chat``, ``/chat/resume``이 쓴다."""

    settings = settings or get_settings()

    if not store.try_acquire(conn, run_id):
        yield AgentEvent(
            "error", {"code": "run_busy", "message": "이 run은 이미 다른 요청이 처리하고 있다."}
        )
        return

    try:
        run = store.load_run(conn, run_id)
        if run is None:
            yield AgentEvent("error", {"code": "not_found", "message": f"run을 찾을 수 없다: {run_id}"})
            return

        if run.status == "done":
            yield AgentEvent("done", {"finish_reason": run.finish_reason, "partial": _partial(run)})
            return

        if run.status == "paused_approval":
            resolution_gen = _resolve_pending_approval(conn, run)
            resolution = "continue"
            try:
                while True:
                    event = next(resolution_gen)
                    yield event
            except StopIteration as stop:
                resolution = stop.value
            if resolution == "still_pending":
                yield AgentEvent(
                    "status", {"stage": "planning", "message": "아직 승인 결정이 내려지지 않았다. 대기한다."}
                )
                store.save_run(conn, run)
                return
            store.save_run(conn, run)
            if resolution == "aborted":
                yield AgentEvent("done", {"finish_reason": run.finish_reason, "partial": _partial(run)})
                return
            # resolution == "continue" -> 아래 루프로 이어진다.

        async for event in _arun_loop(conn, run, chat_fn, settings, _select_tools(tool_names)):
            yield event
    finally:
        store.release(conn, run_id)


async def _arun_loop(
    conn, run: store.RunState, chat_fn: AsyncChatFn, settings: Settings, tool_specs: list[dict[str, Any]]
) -> AsyncIterator[AgentEvent]:
    while True:
        run.step += 1

        if run.step > settings.max_agent_steps:
            yield _finish(conn, run, "max_steps")
            return

        if time.time() - run.start_time > settings.max_agent_seconds:
            yield _finish(conn, run, "timeout")
            return

        if run.usage_total > settings.max_agent_tokens:
            yield _finish(conn, run, "token_budget")
            return

        yield AgentEvent("status", {"stage": "planning", "message": f"{run.step}단계: 모델에게 다음 행동을 묻는다"})

        try:
            response = await chat_fn(run.messages, tools=tool_specs, settings=settings)
        except llm.LLMError as exc:
            yield AgentEvent("error", {"code": "llm_error", "message": str(exc)})
            yield _finish(conn, run, "cancelled")
            return

        assistant_message = {
            "role": "assistant",
            "content": response.text or None,
            "tool_calls": response.tool_calls or None,
        }
        usage = (response.raw or {}).get("usage") or {}
        _accumulate_usage(run, usage, run.messages, assistant_message)
        run.messages.append(assistant_message)
        store.save_run(conn, run)

        if run.usage_total > settings.max_agent_tokens:
            yield _finish(conn, run, "token_budget")
            return

        tool_calls = assistant_message.get("tool_calls")
        if not tool_calls:
            final_text = assistant_message.get("content") or ""
            run.final_text = final_text
            yield AgentEvent("token", {"text": final_text})
            yield _finish(conn, run, "stop")
            return

        yield AgentEvent("status", {"stage": "analyzing", "message": f"도구 {len(tool_calls)}건 실행 준비"})

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
                    {"error": "invalid_json", "message": "arguments가 올바른 JSON이 아니다."}, ensure_ascii=False
                )
                run.messages.append(
                    {"role": "tool", "tool_call_id": call_id, "name": name, "content": error_content}
                )
                yield AgentEvent("tool_result", {"id": call_id, "ok": False, "summary": "잘못된 JSON 인자"})
                continue

            key_str = f"{name}|{_normalize_args(args_dict)}"
            run.call_counts[key_str] = run.call_counts.get(key_str, 0) + 1

            if run.call_counts[key_str] > REPEAT_LIMIT:
                yield AgentEvent(
                    "tool_result", {"id": call_id, "ok": False, "summary": "같은 도구·인자 반복 호출 감지"}
                )
                store.save_run(conn, run)
                yield _finish(conn, run, "repeated_tool_call")
                return

            if name in APPROVAL_REQUIRED_TOOLS:
                idem_key = idempotency_key(run.run_id, call_id, name, args_dict)
                cached = store.cache_get(conn, idem_key)
                if cached is not None:
                    result = ToolRunResult(ok=bool(cached["result_ok"]), content=cached["result_content"])
                    run.messages.append(
                        {"role": "tool", "tool_call_id": call_id, "name": name, "content": result.content}
                    )
                    yield AgentEvent(
                        "tool_result", {"id": call_id, "ok": result.ok, "summary": "(캐시됨) 이미 승인·실행된 호출"}
                    )
                    _register_progress(run, name, args_dict, result)
                    continue

                approval_id = store.create_approval(
                    conn,
                    run_id=run.run_id,
                    call_id=call_id,
                    tool_name=name,
                    args=args_dict,
                    reason=f"'{name}'은(는) 되돌리기 어려운 작업이라 실행 전 승인이 필요하다.",
                    idempotency_key=idem_key,
                )
                run.status = "paused_approval"
                run.pending_approval_id = approval_id
                store.save_run(conn, run)
                yield AgentEvent(
                    "approval_request",
                    {
                        "id": approval_id,
                        "action": name,
                        "args": args_dict,
                        "reason": f"'{name}'은(는) 되돌리기 어려운 작업이라 실행 전 승인이 필요하다.",
                    },
                )
                return

            idem_key = idempotency_key(run.run_id, call_id, name, args_dict)
            result, cached = _execute_with_cache(conn, run, idem_key, name, args_dict)
            run.messages.append(
                {"role": "tool", "tool_call_id": call_id, "name": name, "content": result.content}
            )
            summary = ("(캐시됨) " if cached else "") + (
                result.content if len(result.content) <= 200 else result.content[:200] + "..."
            )
            yield AgentEvent("tool_result", {"id": call_id, "ok": result.ok, "summary": summary})
            _register_progress(run, name, args_dict, result)

            if _no_progress_triggered(run):
                store.save_run(conn, run)
                yield _finish(conn, run, "no_progress")
                return

        store.save_run(conn, run)
        # 도구 결과를 반영해서 다음 단계에서 모델을 다시 부른다.
