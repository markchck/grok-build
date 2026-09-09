"""실행 한도(최대 단계·시간·토큰, 반복 감지)와 승인 거절 finish_reason 단위 테스트.

8단계 tests/test_limits.py와 같은 방식(모델 서버 없이 가짜 ``chat_fn`` 주입)이다.
이 장의 스텁 서버(fake_openai_server.py)는 "도구 결과가 하나라도 생기면 바로
최종 답변으로 끝내는" 단순한 구현이라 실제 서버로는 반복 계열 finish_reason을
재현할 수 없다 — 그래서 이 파일이 그 부분을 담당한다(PROJECT-SPEC.md의 "실제로
실행해 검증"은 여기서는 "가짜 chat_fn을 실제로 agent.py 루프에 주입해 실행"을
뜻한다. 모델 서버가 실제인지 아닌지와는 별개로 advance_run 자체는 실제로 돈다).
"""

from __future__ import annotations

import time

from docagent import store
from docagent.agent import advance_run, start_run
from docagent.config import load_settings
from docagent.llm import ChatResult


def _tool_call_message(call_id: str, name: str, arguments_json: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments_json}}],
    }


def _final_message(text: str) -> dict:
    return {"role": "assistant", "content": text, "tool_calls": None}


def _to_chat_result(msg: dict, usage: dict) -> ChatResult:
    return ChatResult(
        text=msg.get("content") or "",
        finish_reason=None,
        tool_calls=msg.get("tool_calls") or [],
        raw={"usage": usage},
    )


def _events_by_kind(evs, kind):
    return [e for e in evs if e.kind == kind]


def _run(conn, message, chat_fn, settings=None):
    run_id = start_run(conn, message)
    evs = list(advance_run(conn, run_id, chat_fn=chat_fn, settings=settings))
    return run_id, evs


def test_happy_path_tool_then_final_answer():
    conn = store.connect(":memory:")
    calls = {"n": 0}

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        calls["n"] += 1
        if calls["n"] == 1:
            msg = _tool_call_message("call_1", "sum_sales", '{"product": "노트북", "quarter": "Q1"}')
        else:
            msg = _final_message("1분기 노트북 매출 합계를 확인했다.")
        return _to_chat_result(msg, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    run_id, evs = _run(conn, "1분기 노트북 매출 알려줘", fake_chat)

    tool_results = _events_by_kind(evs, "tool_result")
    assert len(tool_results) == 1
    assert tool_results[0].data["ok"] is True

    done = _events_by_kind(evs, "done")
    assert len(done) == 1
    assert done[0].data["finish_reason"] == "stop"


def test_max_steps_limit_stops_the_loop():
    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        n = sum(1 for m in messages if m.get("role") == "assistant")
        msg = _tool_call_message(f"call_{n}", "sum_sales", f'{{"product": "노트북{n}"}}')
        return _to_chat_result(msg, {})

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "max_agent_steps": 2})

    run_id, evs = _run(conn, "계속 도구를 불러라", fake_chat, settings=s)

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "max_steps"
    assert done[0].data["partial"]["steps_used"] == 3


def test_timeout_stops_the_loop():
    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        time.sleep(0.05)
        n = sum(1 for m in messages if m.get("role") == "assistant")
        msg = _tool_call_message(f"call_{n}", "sum_sales", f'{{"product": "노트북{n}"}}')
        return _to_chat_result(msg, {})

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "max_agent_steps": 1000, "max_agent_seconds": 0})

    run_id, evs = _run(conn, "느리게 계속 불러라", fake_chat, settings=s)

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "timeout"


def test_repeated_same_tool_call_is_blocked():
    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        n = sum(1 for m in messages if m.get("role") == "assistant")
        msg = _tool_call_message(f"call_{n}", "sum_sales", '{"product": "노트북"}')
        return _to_chat_result(msg, {})

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "max_agent_steps": 10})

    run_id, evs = _run(conn, "같은 걸 계속 불러라", fake_chat, settings=s)

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "repeated_tool_call"
    assert len(_events_by_kind(evs, "tool_call")) == 4


def test_no_progress_detected_when_args_differ_but_result_never_changes(monkeypatch):
    """(b) 인자는 매번 다른데 도구 결과가 계속 똑같으면 no_progress로 멈춘다.

    이 장은 8단계의 ``flaky_lookup``(일부러 결함을 넣은 도구)을 그대로 가져오지
    않았으므로, ``run_tool``을 몽키패치해 "인자와 무관하게 같은 결과"를 흉내 낸다.
    감지 로직 자체(``docagent.agent._register_progress``/``_no_progress_triggered``)는
    8단계에서 그대로 가져왔다.
    """

    import docagent.agent as agent_module
    from docagent.tools import ToolRunResult

    def fake_run_tool(name, args):
        return ToolRunResult(ok=True, content='{"matches": [], "match_count": 0}')

    monkeypatch.setattr(agent_module, "run_tool", fake_run_tool)

    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        n = sum(1 for m in messages if m.get("role") == "assistant")
        msg = _tool_call_message(f"call_{n}", "sum_sales", f'{{"product": "질의-{n}"}}')
        return _to_chat_result(msg, {})

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "max_agent_steps": 20})

    run_id, evs = _run(conn, "계속 다르게 검색해봐", fake_chat, settings=s)

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "no_progress"
    assert len(_events_by_kind(evs, "tool_call")) == 3


def test_token_budget_exceeded_with_real_usage():
    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        n = sum(1 for m in messages if m.get("role") == "assistant")
        msg = _tool_call_message(f"call_{n}", "sum_sales", f'{{"product": "노트북{n}"}}')
        return _to_chat_result(msg, {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200})

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "max_agent_steps": 100, "max_agent_tokens": 250})

    run_id, evs = _run(conn, "토큰을 많이 써라", fake_chat, settings=s)

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "token_budget"


def test_rejected_by_user_abort_via_full_flow():
    """approval_request -> 거절(중단) -> resume 이후 finish_reason이 그대로 rejected_by_user다."""

    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        msg = _tool_call_message("call_1", "archive_report", '{"report_id": "r1", "reason": "test"}')
        return _to_chat_result(msg, {})

    run_id = start_run(conn, "r1 보관해줘")
    evs = list(advance_run(conn, run_id, chat_fn=fake_chat))
    approvals = _events_by_kind(evs, "approval_request")
    assert len(approvals) == 1
    approval_id = approvals[0].data["id"]

    approval, already = store.decide_approval(conn, approval_id, status="rejected", decision_args={"note": "안 돼"}, mode="abort")
    assert already is False

    evs2 = list(advance_run(conn, run_id, chat_fn=fake_chat))
    done = _events_by_kind(evs2, "done")
    assert done[0].data["finish_reason"] == "rejected_by_user"


def test_double_decision_is_idempotent():
    """같은 승인에 두 번 응답해도(더블 클릭) 첫 결정만 유지된다."""

    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        msg = _tool_call_message("call_1", "archive_report", '{"report_id": "r1", "reason": "test"}')
        return _to_chat_result(msg, {})

    run_id = start_run(conn, "r1 보관해줘")
    evs = list(advance_run(conn, run_id, chat_fn=fake_chat))
    approval_id = _events_by_kind(evs, "approval_request")[0].data["id"]

    first, already1 = store.decide_approval(conn, approval_id, status="approved", decision_args=None, mode=None)
    assert already1 is False
    assert first["status"] == "approved"

    second, already2 = store.decide_approval(
        conn, approval_id, status="rejected", decision_args={"note": "너무 늦음"}, mode="abort"
    )
    assert already2 is True
    assert second["status"] == "approved"  # 두 번째 호출은 아무것도 바꾸지 못했다
