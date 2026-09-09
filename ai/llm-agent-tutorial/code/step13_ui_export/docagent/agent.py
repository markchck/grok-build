"""에이전트 루프 — 8단계 HITL 루프에 계획(Todo)·하이브리드 검색·CSV용 표를 더한다.

8단계와 같은 구조를 그대로 쓴다: ``advance_run``은 저장된 상태에서 이어서
진행하고, 승인이 필요한 도구를 만나면 상태를 SQLite에 저장하고 멈춘다. 이
장이 추가하는 것은 새로운 종류의 이벤트가 아니라(PROJECT-SPEC.md 4절
스키마 그대로), 기존 이벤트를 채우는 새로운 소스 두 가지다.

- ``write_plan`` 도구 호출 -> ``docagent/todo.py``로 id를 안정화해 ``todo``
  이벤트를 만든다.
- ``hybrid_search_docs`` 도구 호출 -> 5단계 ``rag/search.py``로 실제 검색을
  수행하고, 그 결과(``run.sources``)만을 답변 인용 검증의 근거로 삼는다
  (5단계 규약: 모델이 답변에 직접 쓴 URL은 신뢰하지 않는다).

지켜야 할 안전장치(PROJECT-SPEC.md 7절, 8단계와 동일하게 적용):
    1. 최대 단계 수 / 최대 실행 시간
    2. 같은 (도구 이름, 정규화한 인자) 조합 3회 반복 -> ``repeated_tool_call``
    3. 결과가 바뀌지 않는 반복 -> ``no_progress``
    4. 누적 토큰 사용량 초과 -> ``token_budget``
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from typing import Any, Callable, Iterator

from docagent import llm, mcp_runtime, skills_runtime, store, tracing
from docagent.config import Settings, get_settings
from docagent.mcp_client import McpConnectionError
from docagent.rag import citations
from docagent.rag.search import hybrid_search
from docagent.skills_runtime import (
    SKILL_TOOL_NAMES,
    SKILL_TOOL_SPECS,
    SkillRunError,
    run_csv_analysis_skill,
    run_doc_research_skill,
)
from docagent.todo import TodoIdAssigner
from docagent.tools import (
    APPROVAL_REQUIRED_TOOLS,
    STATEFUL_TOOLS,
    TOOL_SPECS,
    ToolRunResult,
    run_tool,
    validate_args,
)

# MCP(10단계) 도구 하나를 이 루프에 추가로 노출한다. mcp_server의 search_docs를
# 그대로 부르되, 로컬 hybrid_search_docs(Dense+Sparse, 이 프로세스 안에서 Milvus를
# 직접 부른다)와 이름이 겹치지 않도록 "mcp_" 접두사를 붙였다 — 도구 이름만 보고도
# 로컬 Tool과 MCP 도구를 구분할 수 있게 하기 위한 이 장의 관례다(실제 구분은
# tool_call 이벤트의 source 필드가 한다, docagent/events.py 참고).
MCP_SEARCH_TOOL_NAME = "mcp_search_docs"

_MCP_SEARCH_TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": MCP_SEARCH_TOOL_NAME,
        "description": (
            "(MCP: 별도 프로세스 mcp_server) 사내 분기 리포트 문서에서 Dense(의미) 검색으로 "
            "근거를 찾는다. hybrid_search_docs와 달리 이 도구는 완전히 별도의 프로세스에서 "
            "실행되고 stdio로 통신한다(10단계)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 질의."},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

SYSTEM_PROMPT = (
    "너는 문서와 판매 데이터를 분석하는 에이전트다. 먼저 write_plan으로 실행 계획을 "
    "선언한다. 판매 수치는 sum_sales/lookup_sales_rows로 sales.csv에서 조회하고, "
    "매출 변화의 원인은 hybrid_search_docs로 문서에서 찾는다. 문서 근거를 답변에 쓸 "
    "때는 반드시 검색 결과에 붙어 있던 [S1], [S2] 같은 번호만 문장 끝에 인용 표시로 "
    "쓴다 — URL이나 파일 경로를 직접 만들어 쓰지 않는다. archive_report는 되돌리기 "
    "어려운 작업이므로 반드시 호출해서 승인을 받는다. 계획의 각 항목을 시작할 때는 "
    "write_plan으로 상태를 in_progress로, 끝나면 completed로 갱신한다."
)

REPEAT_LIMIT = 3
NO_PROGRESS_WINDOW = 3
CHARS_PER_TOKEN_ESTIMATE = 4

_TOOL_SPECS_BY_NAME = {spec["function"]["name"]: spec for spec in TOOL_SPECS}


def _tool_source(name: str) -> str:
    """도구 이름 -> 이 도구가 어디서 오는가. tool_call 이벤트의 source 필드에 그대로
    실린다(events.py, PROJECT-SPEC.md 4절). Tool·MCP·Skill 세 층을 화면에서
    구분하는 지점이 바로 여기다."""

    if name == MCP_SEARCH_TOOL_NAME:
        return "mcp"
    if name in SKILL_TOOL_NAMES:
        return "skill"
    return "local"


def _select_tools(
    tool_names: list[str] | None,
    *,
    include_mcp: bool = False,
    include_skills: bool = False,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """8단계와 같은 데모 전용 장치. 스텁 서버가 tools의 첫 항목을 항상 고르는 것을
    이용해, 실제 모델 없이도 특정 도구 흐름(승인/검색/실패)을 재현한다.

    ``include_mcp``/``include_skills``가 참이면 로컬 Tool 목록에 MCP 도구·Skill
    도구를 **더한다**(대체하지 않는다) — 최종 통합 과제 4번이 요구하는 "같은
    에이전트 루프 안에서 공존"이 바로 이것이다. 두 값 모두 기본값이 거짓인
    이유: MCP는 별도 프로세스를 실제로 띄워야 하고(연결 실패 시 도구 목록 조회
    자체가 실패할 수 있다, ``mcp_runtime.list_mcp_tool_specs_sync`` 참고), 이
    장의 기존 테스트(반복 감지·no_progress 등)는 도구 목록이 정확히 몇 개인지에
    기대는 경우가 있어 기본 동작을 그대로 유지해야 하기 때문이다.
    """

    base = TOOL_SPECS
    if not tool_names:
        selected = list(base)
    else:
        selected = [_TOOL_SPECS_BY_NAME[name] for name in tool_names if name in _TOOL_SPECS_BY_NAME]
        if not selected:
            selected = list(base)

    if include_mcp:
        settings = settings or get_settings()
        discovered = mcp_runtime.list_mcp_tool_specs_sync(settings)
        discovered_names = {spec["function"]["name"] for spec in discovered}
        if "search_docs" in discovered_names:
            # 서버가 실제로 응답했다 — 그 스키마를 그대로 쓰되 이름만 로컬 도구와
            # 겹치지 않게 바꾼다(모델에게는 항상 mcp_search_docs로 보인다).
            selected = selected + [_MCP_SEARCH_TOOL_SPEC]
        elif not discovered:
            # 서버가 지금 응답하지 않는다 — 그래도 도구 자체는 목록에 올려 둔다.
            # 실제 호출 시점에 tool_result(ok=False)로 실패 원인이 드러나고,
            # 트레이싱을 켜 두면 그 실패가 스팬으로 남는다(9단계 배선, 6-4절 참고).
            selected = selected + [_MCP_SEARCH_TOOL_SPEC]

    if include_skills:
        selected = selected + list(SKILL_TOOL_SPECS)

    return selected


class AgentEvent:
    __slots__ = ("kind", "data")

    def __init__(self, kind: str, data: dict[str, Any]) -> None:
        self.kind = kind
        self.data = data


ChatFn = Callable[..., dict[str, Any]]


# ---------------------------------------------------------------------------
# 보조 함수
# ---------------------------------------------------------------------------


def _normalize_args(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False)


def idempotency_key(run_id: str, call_id: str, tool_name: str, args: dict[str, Any]) -> str:
    raw = f"{run_id}|{call_id}|{tool_name}|{_normalize_args(args)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _result_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _estimate_tokens(text_payload: str) -> int:
    return max(1, len(text_payload) // CHARS_PER_TOKEN_ESTIMATE)


def _accumulate_usage(run: store.RunState, usage: dict[str, Any], messages_sent: list[dict], reply: dict) -> None:
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    if prompt is None and completion is None and total is None:
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
    """PROJECT-SPEC.md 4절의 ``partial``(``text``/``steps_used``/``tokens_used``/
    ``tokens_estimated``)에 ``csv_available`` 한 필드만 더했다. 화면이 CSV 다운로드
    버튼을 보여줄지 판단하려면 "표가 있는가"를 알아야 하는데, 기존 네 필드
    어디에도 그 정보가 없다. 기존 필드는 하나도 건드리지 않았으므로 이 필드를
    모르는 클라이언트도 그대로 동작한다(PROJECT-SPEC.md 4절이 ``partial``을 추가할 때
    쓴 것과 같은 하위 호환 논리)."""

    return {
        "text": run.final_text if run.final_text is not None else _final_text_so_far(run),
        "steps_used": run.step,
        "tokens_used": run.usage_total,
        "tokens_estimated": run.usage_estimated,
        "csv_available": bool(run.table),
    }


# ---------------------------------------------------------------------------
# 계획(Todo) 처리
# ---------------------------------------------------------------------------


def _emit_todo_if_changed(run: store.RunState, assigner: TodoIdAssigner) -> AgentEvent | None:
    items = assigner.items_for(run.plan)
    if items == run.todo_state.get("last_items"):
        run.todo_state = assigner.to_state()
        run.todo_state["last_items"] = items
        return None
    run.todo_state = assigner.to_state()
    run.todo_state["last_items"] = items
    return AgentEvent("todo", {"items": items})


# ---------------------------------------------------------------------------
# 실행 시작
# ---------------------------------------------------------------------------


def start_run(conn, user_message: str, *, history: list[dict[str, Any]] | None = None) -> str:
    messages: list[dict[str, Any]] = list(history) if history else [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    messages.append({"role": "user", "content": user_message})
    run = store.create_run(conn, messages)
    return run.run_id


# ---------------------------------------------------------------------------
# 승인 대기 상태를 이어서 처리한다 (8단계와 거의 동일)
# ---------------------------------------------------------------------------


def _resolve_pending_approval(conn, run: store.RunState) -> Iterator[AgentEvent]:
    approval = store.get_approval(conn, run.pending_approval_id)
    if approval is None or approval["status"] == "pending":
        return "still_pending"

    call_id = approval["call_id"]
    tool_name = approval["tool_name"]
    original_args = json.loads(approval["args_json"])
    assigner = TodoIdAssigner.from_state(run.todo_state)

    if approval["status"] == "rejected":
        mode = approval["decision_mode"] or "alternative"
        note = json.loads(approval["decision_args_json"])["note"] if approval["decision_args_json"] else ""
        if mode == "abort":
            assigner.mark_running_failed()
            todo_evt = _emit_todo_if_changed(run, assigner)
            if todo_evt:
                yield todo_evt
            run.status = "done"
            run.finish_reason = "rejected_by_user"
            run.pending_approval_id = None
            yield AgentEvent(
                "tool_result",
                {"id": call_id, "ok": False, "summary": f"사용자가 거절해 실행을 중단했다. {note}".strip()},
            )
            return "aborted"

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

    # approved
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
    cached = store.cache_get(conn, key)
    if cached is not None:
        return ToolRunResult(ok=bool(cached["result_ok"]), content=cached["result_content"]), True

    result = run_tool(tool_name, args)
    store.cache_set(
        conn, idempotency_key=key, run_id=run.run_id, tool_name=tool_name, args=args, ok=result.ok, content=result.content
    )
    return result, False


def _register_progress(run: store.RunState, tool_name: str, args: dict[str, Any], result: ToolRunResult) -> None:
    key = (tool_name, _normalize_args(args))
    key_str = f"{key[0]}|{key[1]}"
    if run.call_counts.get(key_str, 0) != 1:
        return
    run.progress_hashes.append(_result_hash(result.content))
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
    chat_fn: ChatFn = llm.chat_completion_full,
    settings: Settings | None = None,
    tool_names: list[str] | None = None,
    include_mcp: bool = False,
    include_skills: bool = False,
) -> Iterator[AgentEvent]:
    settings = settings or get_settings()

    if not store.try_acquire(conn, run_id):
        yield AgentEvent("error", {"code": "run_busy", "message": "이 run은 이미 다른 요청이 처리하고 있다."})
        return

    try:
        run = store.load_run(conn, run_id)
        if run is None:
            yield AgentEvent("error", {"code": "not_found", "message": f"run을 찾을 수 없다: {run_id}"})
            return

        if run.status == "done":
            yield AgentEvent("done", {"finish_reason": run.finish_reason, "partial": _partial(run)})
            return

        if run.step == 0:
            yield AgentEvent("status", {"stage": "starting", "message": "실행을 시작한다"})

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

        tool_specs = _select_tools(
            tool_names, include_mcp=include_mcp, include_skills=include_skills, settings=settings
        )
        yield from _run_loop(conn, run, chat_fn, settings, tool_specs)
    finally:
        store.release(conn, run_id)


def _finish(conn, run: store.RunState, finish_reason: str) -> AgentEvent:
    run.status = "done"
    run.finish_reason = finish_reason
    if run.final_text is None:
        run.final_text = _final_text_so_far(run)
    store.save_run(conn, run)
    return AgentEvent("done", {"finish_reason": finish_reason, "partial": _partial(run)})


def _handle_write_plan(run: store.RunState, args_dict: dict[str, Any]) -> tuple[AgentEvent, AgentEvent | None]:
    items_in = args_dict.get("items", [])
    run.plan = [{"title": it["title"], "status": it["status"]} for it in items_in]
    assigner = TodoIdAssigner.from_state(run.todo_state)
    todo_evt = _emit_todo_if_changed(run, assigner)
    tool_result = AgentEvent(
        "tool_result", {"id": "", "ok": True, "summary": f"계획 {len(run.plan)}개 항목으로 갱신"}
    )
    return tool_result, todo_evt


def _handle_hybrid_search(run: store.RunState, args_dict: dict[str, Any], settings: Settings) -> tuple[bool, str]:
    """검색을 실행하고 (ok, model에게 줄 content)를 돌려준다. run.sources를 갱신한다."""

    query = args_dict.get("query", "")
    top_k = int(args_dict.get("top_k", 5))
    try:
        hits = hybrid_search(query, limit=top_k)
    except Exception as exc:  # Milvus 연결 실패 등 — 검색 실패는 도구 실패로 취급한다
        return False, json.dumps({"error": "search_failed", "message": str(exc)}, ensure_ascii=False)

    offset = len(run.sources)
    new_sources = []
    for i, hit in enumerate(hits):
        source = citations.RetrievedSource(
            label=citations.make_source_label(offset + i),
            chunk_id=hit["pk"],
            doc_id=hit["doc_id"],
            doc_title=hit.get("doc_title", hit["doc_id"]),
            page=int(hit.get("page", 0)),
            chunk_index=int(hit.get("chunk_index", 0)),
            text=hit.get("text", ""),
            source_url=hit.get("source_url", ""),
            score=float(hit.get("score", 0.0)),
        )
        new_sources.append(source)
    run.sources.extend(asdict(s) for s in new_sources)

    if not new_sources:
        return True, json.dumps({"results": [], "message": "검색 결과가 없다."}, ensure_ascii=False)

    payload = [
        {"label": s.label, "doc": s.doc_title, "snippet": s.text[:200]} for s in new_sources
    ]
    return True, json.dumps({"results": payload}, ensure_ascii=False)


def _handle_mcp_search(run: store.RunState, args_dict: dict[str, Any], settings: Settings) -> tuple[bool, str]:
    """MCP(10단계) 서버의 search_docs를 호출한다. run.sources도 갱신해 인용 검증의
    근거로 쓴다 — 로컬 hybrid_search_docs와 같은 규약(5단계: 모델이 만든 URL은
    신뢰하지 않는다)을 MCP 결과에도 그대로 적용한다."""

    query = args_dict.get("query", "")
    top_k = int(args_dict.get("top_k", 5))
    with tracing.traced_span(
        "mcp.search_docs",
        kind=tracing.SpanKind.RETRIEVER,
        input_value=query,
        attributes={"mcp.tool_name": "search_docs", "mcp.top_k": top_k},
    ) as span:
        try:
            result = mcp_runtime.call_mcp_tool_sync(
                "search_docs", {"query": query, "top_k": top_k}, settings
            )
        except McpConnectionError as exc:
            tracing.mark_error(span, "mcp_connection_error", str(exc))
            return False, json.dumps({"error": "mcp_connection_error", "message": str(exc)}, ensure_ascii=False)

        if not result.ok:
            tracing.mark_error(span, "mcp_tool_error", result.content)
            return False, result.content

        try:
            parsed = json.loads(result.content)
        except json.JSONDecodeError:
            tracing.set_output(span, result.content[:500])
            return True, result.content

        hits = parsed.get("hits", [])
        tracing.set_retrieval_documents(span, hits)
        offset = len(run.sources)
        new_sources = []
        for i, hit in enumerate(hits):
            source = citations.RetrievedSource(
                label=citations.make_source_label(offset + i),
                chunk_id=hit.get("pk", f"mcp#{i}"),
                doc_id=hit.get("doc_id", ""),
                doc_title=hit.get("doc_title", hit.get("doc_id", "")),
                page=int(hit.get("page", 0)),
                chunk_index=int(hit.get("chunk_index", 0)),
                text=hit.get("text", ""),
                source_url=hit.get("source_url", ""),
                score=float(hit.get("score", 0.0)),
            )
            new_sources.append(source)
        run.sources.extend(asdict(s) for s in new_sources)

        payload = [{"label": s.label, "doc": s.doc_title, "snippet": s.text[:200]} for s in new_sources]
        content = json.dumps({"results": payload}, ensure_ascii=False)
        tracing.set_output(span, content[:500])
        return True, content


def _handle_skill(name: str, args_dict: dict[str, Any]) -> tuple[bool, str]:
    """Skill(12단계 skills/) 실행. 스킬 스크립트 실패는 도구 실패로 취급한다(3단계와
    같은 원칙) — 스크립트가 죽었다고 애플리케이션 전체가 죽지 않는다."""

    with tracing.traced_span(
        f"skill.{name}", kind=tracing.SpanKind.CHAIN, input_value=json.dumps(args_dict, ensure_ascii=False)
    ) as span:
        try:
            if name == "csv_analysis_skill":
                output = run_csv_analysis_skill()
            elif name == "doc_research_skill":
                output = run_doc_research_skill(args_dict.get("keywords", []))
            else:
                return False, json.dumps({"error": "unknown_skill", "message": name}, ensure_ascii=False)
        except SkillRunError as exc:
            tracing.mark_error(span, "skill_run_error", str(exc))
            return False, json.dumps({"error": "skill_run_error", "message": str(exc)}, ensure_ascii=False)

        content = json.dumps(output, ensure_ascii=False)
        tracing.set_output(span, content[:500])
        return True, content


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

        with tracing.traced_span(
            "docagent.chat",
            kind=tracing.SpanKind.LLM,
            input_value=json.dumps(run.messages[-1:], ensure_ascii=False),
            attributes={"docagent.run_id": run.run_id, "docagent.step": run.step},
        ) as llm_span:
            try:
                response = chat_fn(run.messages, tools=tool_specs, tool_choice="auto", settings=settings)
            except llm.LLMError as exc:
                tracing.mark_error(llm_span, "llm_error", str(exc))
                yield AgentEvent("error", {"code": "llm_error", "message": str(exc)})
                yield _finish(conn, run, "cancelled")
                return

            usage_for_span = response.get("usage") or {}
            tracing.set_llm_usage(
                llm_span,
                model=settings.chat_model,
                prompt_tokens=usage_for_span.get("prompt_tokens"),
                completion_tokens=usage_for_span.get("completion_tokens"),
                total_tokens=usage_for_span.get("total_tokens"),
            )
            tracing.set_output(llm_span, json.dumps(response.get("message", {}), ensure_ascii=False)[:500])

        assistant_message = response["message"]
        usage = response.get("usage") or {}
        _accumulate_usage(run, usage, run.messages, assistant_message)
        run.messages.append(assistant_message)
        store.save_run(conn, run)

        if run.usage_total > settings.max_agent_tokens:
            yield _finish(conn, run, "token_budget")
            return

        tool_calls = assistant_message.get("tool_calls")
        if not tool_calls:
            raw_text = assistant_message.get("content") or ""
            yield AgentEvent("status", {"stage": "verifying", "message": "답변의 인용 표시를 검증하는 중"})
            retrieved = [citations.RetrievedSource(**s) for s in run.sources]
            result = citations.validate_and_link_citations(
                raw_text, retrieved, app_base_url=settings.app_base_url
            )
            run.final_text = result.text
            yield AgentEvent("status", {"stage": "writing", "message": "검증된 답변을 전달하는 중"})
            yield AgentEvent("token", {"text": result.text})
            yield AgentEvent("sources", {"items": result.sources})
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
                "tool_call",
                {
                    "id": call_id,
                    "name": name,
                    "args": args_dict if args_dict is not None else {},
                    "source": _tool_source(name),
                },
            )

            if args_dict is None:
                error_content = json.dumps(
                    {"error": "invalid_json", "message": "arguments가 올바른 JSON이 아니다."}, ensure_ascii=False
                )
                run.messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": error_content})
                yield AgentEvent("tool_result", {"id": call_id, "ok": False, "summary": "잘못된 JSON 인자"})
                continue

            key_str = f"{name}|{_normalize_args(args_dict)}"
            run.call_counts[key_str] = run.call_counts.get(key_str, 0) + 1
            if run.call_counts[key_str] > REPEAT_LIMIT:
                yield AgentEvent("tool_result", {"id": call_id, "ok": False, "summary": "같은 도구·인자 반복 호출 감지"})
                store.save_run(conn, run)
                yield _finish(conn, run, "repeated_tool_call")
                return

            # --- write_plan: 계획 도구 --------------------------------------
            if name == "write_plan":
                tool_result_evt, todo_evt = _handle_write_plan(run, args_dict)
                call_id_evt = AgentEvent(
                    "tool_result", {"id": call_id, "ok": True, "summary": tool_result_evt.data["summary"]}
                )
                run.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": json.dumps({"ok": True, "items": run.plan}, ensure_ascii=False),
                    }
                )
                yield call_id_evt
                if todo_evt:
                    yield todo_evt
                store.save_run(conn, run)
                continue

            # --- hybrid_search_docs: 5단계 검색 -----------------------------
            if name == "hybrid_search_docs":
                yield AgentEvent("status", {"stage": "retrieving", "message": f"'{args_dict.get('query','')}' 하이브리드 검색"})
                ok, content = _handle_hybrid_search(run, args_dict, settings)
                run.messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": content})
                summary = content if len(content) <= 200 else content[:200] + "..."
                yield AgentEvent("tool_result", {"id": call_id, "ok": ok, "summary": summary})
                if not ok:
                    assigner = TodoIdAssigner.from_state(run.todo_state)
                    assigner.mark_running_failed()
                    todo_evt = _emit_todo_if_changed(run, assigner)
                    if todo_evt:
                        yield todo_evt
                store.save_run(conn, run)
                continue

            # --- mcp_search_docs: 10단계 MCP 서버(별도 프로세스) 도구 ----------
            if name == MCP_SEARCH_TOOL_NAME:
                yield AgentEvent(
                    "status", {"stage": "retrieving", "message": f"'{args_dict.get('query','')}' MCP 문서 검색"}
                )
                ok, content = _handle_mcp_search(run, args_dict, settings)
                run.messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": content})
                summary = content if len(content) <= 200 else content[:200] + "..."
                yield AgentEvent("tool_result", {"id": call_id, "ok": ok, "summary": summary})
                if not ok:
                    assigner = TodoIdAssigner.from_state(run.todo_state)
                    assigner.mark_running_failed()
                    todo_evt = _emit_todo_if_changed(run, assigner)
                    if todo_evt:
                        yield todo_evt
                store.save_run(conn, run)
                continue

            # --- csv_analysis_skill / doc_research_skill: 12단계 Skill --------
            if name in SKILL_TOOL_NAMES:
                yield AgentEvent("status", {"stage": "analyzing", "message": f"Skill 실행: {name}"})
                ok, content = _handle_skill(name, args_dict)
                run.messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": content})
                summary = content if len(content) <= 200 else content[:200] + "..."
                yield AgentEvent("tool_result", {"id": call_id, "ok": ok, "summary": summary})
                if not ok:
                    assigner = TodoIdAssigner.from_state(run.todo_state)
                    assigner.mark_running_failed()
                    todo_evt = _emit_todo_if_changed(run, assigner)
                    if todo_evt:
                        yield todo_evt
                store.save_run(conn, run)
                continue

            # --- 승인 필요 도구 ----------------------------------------------
            if name in APPROVAL_REQUIRED_TOOLS:
                idem_key = idempotency_key(run.run_id, call_id, name, args_dict)
                cached = store.cache_get(conn, idem_key)
                if cached is not None:
                    result = ToolRunResult(ok=bool(cached["result_ok"]), content=cached["result_content"])
                    run.messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": result.content})
                    yield AgentEvent("tool_result", {"id": call_id, "ok": result.ok, "summary": "(캐시됨) 이미 승인·실행된 호출"})
                    _register_progress(run, name, args_dict, result)
                    continue

                reason = f"'{name}'은(는) 되돌리기 어려운 작업이라 실행 전 승인이 필요하다."
                approval_id = store.create_approval(
                    conn, run_id=run.run_id, call_id=call_id, tool_name=name, args=args_dict,
                    reason=reason, idempotency_key=idem_key,
                )
                run.status = "paused_approval"
                run.pending_approval_id = approval_id
                store.save_run(conn, run)
                yield AgentEvent("approval_request", {"id": approval_id, "action": name, "args": args_dict, "reason": reason})
                return

            # --- 일반 도구 (sum_sales, lookup_sales_rows) ---------------------
            idem_key = idempotency_key(run.run_id, call_id, name, args_dict)
            result, cached = _execute_with_cache(conn, run, idem_key, name, args_dict)
            run.messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": result.content})
            summary = ("(캐시됨) " if cached else "") + (
                result.content if len(result.content) <= 200 else result.content[:200] + "..."
            )
            yield AgentEvent("tool_result", {"id": call_id, "ok": result.ok, "summary": summary})
            _register_progress(run, name, args_dict, result)

            if name == "lookup_sales_rows" and result.ok:
                try:
                    parsed = json.loads(result.content)
                    run.table = parsed.get("rows")
                except json.JSONDecodeError:
                    pass

            if not result.ok:
                assigner = TodoIdAssigner.from_state(run.todo_state)
                assigner.mark_running_failed()
                todo_evt = _emit_todo_if_changed(run, assigner)
                if todo_evt:
                    yield todo_evt

            if _no_progress_triggered(run):
                store.save_run(conn, run)
                yield _finish(conn, run, "no_progress")
                return

        store.save_run(conn, run)
