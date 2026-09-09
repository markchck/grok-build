"""실행 한도(최대 단계·시간·토큰, 반복 감지 두 종류) 단위 테스트.

모델 서버 없이 가짜 ``chat_fn``을 주입해서 확인한다. PROJECT-SPEC.md 7절이
요구하는 "모델 서버 없이 단위 테스트로 검증 가능"을 이 파일이 채운다.
"""

from __future__ import annotations

import time

from docagent import store
from docagent.agent import advance_run, start_run


def _tool_call_message(call_id: str, name: str, arguments_json: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments_json}}
        ],
    }


def _final_message(text: str) -> dict:
    return {"role": "assistant", "content": text, "tool_calls": None}


def _events_by_kind(events, kind):
    return [e for e in events if e.kind == kind]


def _run(conn, message, chat_fn, settings=None):
    run_id = start_run(conn, message)
    events = list(advance_run(conn, run_id, chat_fn=chat_fn, settings=settings))
    return run_id, events


def test_happy_path_tool_then_final_answer():
    conn = store.connect(":memory:")
    calls = {"n": 0}

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        calls["n"] += 1
        if calls["n"] == 1:
            msg = _tool_call_message("call_1", "sum_sales", '{"product": "노트북", "quarter": "Q1"}')
        else:
            msg = _final_message("1분기 노트북 매출 합계를 확인했다.")
        return {"message": msg, "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

    run_id, evs = _run(conn, "1분기 노트북 매출 알려줘", fake_chat)

    tool_results = _events_by_kind(evs, "tool_result")
    assert len(tool_results) == 1
    assert tool_results[0].data["ok"] is True

    done = _events_by_kind(evs, "done")
    assert len(done) == 1
    assert done[0].data["finish_reason"] == "stop"

    run = store.load_run(conn, run_id)
    assert run.status == "done"
    assert run.usage_total == 30  # 두 번의 모델 호출 * 15


def test_max_steps_limit_stops_the_loop():
    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        n = sum(1 for m in messages if m.get("role") == "assistant")
        msg = _tool_call_message(f"call_{n}", "sum_sales", f'{{"product": "노트북{n}"}}')
        return {"message": msg, "usage": {}}

    from docagent.config import load_settings

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "max_agent_steps": 2})

    run_id, evs = _run(conn, "계속 도구를 불러라", fake_chat, settings=s)

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "max_steps"
    assert done[0].data["partial"]["steps_used"] == 3  # 3번째 진입에서 한도를 넘겨 멈춘다


def test_timeout_stops_the_loop():
    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        time.sleep(0.05)
        n = sum(1 for m in messages if m.get("role") == "assistant")
        msg = _tool_call_message(f"call_{n}", "sum_sales", f'{{"product": "노트북{n}"}}')
        return {"message": msg, "usage": {}}

    from docagent.config import load_settings

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "max_agent_steps": 1000, "max_agent_seconds": 0})

    run_id, evs = _run(conn, "느리게 계속 불러라", fake_chat, settings=s)

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "timeout"


def test_repeated_same_tool_call_is_blocked():
    """(a) 같은 (도구, 정규화한 인자)가 3회 넘게 반복되면 repeated_tool_call."""

    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        n = sum(1 for m in messages if m.get("role") == "assistant")
        msg = _tool_call_message(f"call_{n}", "sum_sales", '{"product": "노트북"}')
        return {"message": msg, "usage": {}}

    from docagent.config import load_settings

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "max_agent_steps": 10})

    run_id, evs = _run(conn, "같은 걸 계속 불러라", fake_chat, settings=s)

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "repeated_tool_call"

    tool_calls = _events_by_kind(evs, "tool_call")
    assert len(tool_calls) == 4  # 3번 실행되고 4번째 시도에서 차단


def test_no_progress_detected_when_args_differ_but_result_never_changes():
    """(b) 인자는 매번 다른데 flaky_lookup의 결과가 계속 똑같으면 no_progress."""

    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        n = sum(1 for m in messages if m.get("role") == "assistant")
        msg = _tool_call_message(f"call_{n}", "flaky_lookup", f'{{"query": "질의-{n}"}}')
        return {"message": msg, "usage": {}}

    from docagent.config import load_settings

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "max_agent_steps": 20})

    run_id, evs = _run(conn, "계속 다르게 검색해봐", fake_chat, settings=s)

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "no_progress"

    tool_calls = _events_by_kind(evs, "tool_call")
    # 서로 다른 질의를 3번 실행한 뒤(창 크기 3) 진전이 없다고 판단해 멈춘다.
    assert len(tool_calls) == 3


def test_token_budget_exceeded_with_real_usage():
    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        n = sum(1 for m in messages if m.get("role") == "assistant")
        msg = _tool_call_message(f"call_{n}", "sum_sales", f'{{"product": "노트북{n}"}}')
        return {"message": msg, "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200}}

    from docagent.config import load_settings

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "max_agent_steps": 100, "max_agent_tokens": 250})

    run_id, evs = _run(conn, "토큰을 많이 써라", fake_chat, settings=s)

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "token_budget"
    run = store.load_run(conn, run_id)
    assert run.usage_estimated is False


def test_token_budget_estimated_when_server_omits_usage():
    """usage를 안 주는 서버 -> 글자 수 기반 추정치로 예산을 판단한다."""

    conn = store.connect(":memory:")

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        n = sum(1 for m in messages if m.get("role") == "assistant")
        # 아주 긴 인자를 넣어서 추정 토큰 수가 빠르게 예산을 넘도록 만든다.
        long_query = "질의" * 500
        msg = _tool_call_message(f"call_{n}", "flaky_lookup", f'{{"query": "{long_query}{n}"}}')
        return {"message": msg, "usage": {}}  # usage 없음

    from docagent.config import load_settings

    s = load_settings()
    s = s.__class__(**{**s.__dict__, "max_agent_steps": 100, "max_agent_tokens": 50})

    run_id, evs = _run(conn, "usage 없는 서버", fake_chat, settings=s)

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "token_budget"
    assert done[0].data["partial"]["tokens_estimated"] is True
