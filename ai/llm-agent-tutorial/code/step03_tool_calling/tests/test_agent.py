"""에이전트 루프(agent.run_agent) 단위 테스트.

실제 모델 서버 대신 가짜 chat_fn을 주입해서, 모델 서버 없이도
- 모델 호출 -> 도구 실행 -> 결과 전달 -> 최종 답변 흐름
- 인자 검증 실패 후 모델이 스스로 고치는 흐름
- 최대 단계 수 / 최대 시간 / 반복 호출 감지에 의한 중단
을 확인한다.
"""

import time

from docagent.agent import run_agent


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


def test_happy_path_tool_then_final_answer():
    calls = {"n": 0}

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_call_message("call_1", "sum_sales", '{"product": "노트북", "quarter": "Q1"}')
        return _final_message("1분기 노트북 매출 합계는 확인한 값과 같다.")

    events = list(run_agent("1분기 노트북 매출 합계 알려줘", chat_fn=fake_chat, max_steps=5, max_seconds=30))

    tool_calls = _events_by_kind(events, "tool_call")
    tool_results = _events_by_kind(events, "tool_result")
    tokens = _events_by_kind(events, "token")
    done = _events_by_kind(events, "done")

    assert len(tool_calls) == 1
    assert tool_calls[0].data["name"] == "sum_sales"
    assert tool_calls[0].data["args"] == {"product": "노트북", "quarter": "Q1"}

    assert len(tool_results) == 1
    assert tool_results[0].data["ok"] is True

    assert len(tokens) == 1
    assert "매출" in tokens[0].data["text"]

    assert len(done) == 1
    assert done[0].data["finish_reason"] == "stop"


def test_invalid_args_are_fed_back_and_model_fixes_them():
    calls = {"n": 0}

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2):
        calls["n"] += 1
        if calls["n"] == 1:
            # quarter에 스키마에 없는 값을 줘서 검증 실패를 유도한다.
            return _tool_call_message("call_1", "sum_sales", '{"quarter": "Q9"}')
        if calls["n"] == 2:
            # 도구 결과(오류)를 보고 모델이 값을 고쳐서 다시 호출했다고 가정한다.
            return _tool_call_message("call_2", "sum_sales", '{"quarter": "Q1"}')
        return _final_message("고친 값으로 계산했다.")

    events = list(run_agent("이번 분기 매출 알려줘", chat_fn=fake_chat, max_steps=5, max_seconds=30))

    tool_results = _events_by_kind(events, "tool_result")
    assert len(tool_results) == 2
    assert tool_results[0].data["ok"] is False  # 첫 번째는 검증 실패
    assert tool_results[1].data["ok"] is True  # 두 번째는 고쳐서 성공

    done = _events_by_kind(events, "done")
    assert done[0].data["finish_reason"] == "stop"


def test_max_steps_limit_stops_the_loop():
    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2):
        # 매번 다른 인자로 도구를 불러서 반복 호출 감지에는 걸리지 않게 한다.
        n = sum(1 for m in messages if m.get("role") == "assistant")
        return _tool_call_message(f"call_{n}", "sum_sales", f'{{"product": "노트북{n}"}}')

    events = list(run_agent("계속 도구를 불러라", chat_fn=fake_chat, max_steps=2, max_seconds=30))

    done = _events_by_kind(events, "done")
    assert len(done) == 1
    assert done[0].data["finish_reason"] == "max_steps"

    planning = [e for e in events if e.kind == "status" and e.data.get("stage") == "planning"]
    assert len(planning) == 2  # max_steps=2를 넘기 직전까지만 모델을 부른다


def test_timeout_stops_the_loop():
    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2):
        time.sleep(0.05)
        n = sum(1 for m in messages if m.get("role") == "assistant")
        return _tool_call_message(f"call_{n}", "sum_sales", f'{{"product": "노트북{n}"}}')

    events = list(run_agent("느린 도구를 계속 불러라", chat_fn=fake_chat, max_steps=100, max_seconds=0))

    done = _events_by_kind(events, "done")
    assert len(done) == 1
    assert done[0].data["finish_reason"] == "timeout"


def test_repeated_same_tool_call_is_blocked():
    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2):
        # 항상 완전히 같은 이름 + 같은 인자로 도구를 부른다.
        n = sum(1 for m in messages if m.get("role") == "assistant")
        return _tool_call_message(f"call_{n}", "sum_sales", '{"product": "노트북"}')

    events = list(run_agent("같은 걸 계속 불러라", chat_fn=fake_chat, max_steps=10, max_seconds=30))

    done = _events_by_kind(events, "done")
    assert len(done) == 1
    assert done[0].data["finish_reason"] == "repeated_tool_call"

    tool_calls = _events_by_kind(events, "tool_call")
    # 3번 성공적으로 실행되고 4번째 시도에서 차단돼야 한다.
    assert len(tool_calls) == 4


def test_invalid_json_arguments_do_not_crash_the_loop():
    calls = {"n": 0}

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_call_message("call_1", "sum_sales", "이건 JSON이 아니다")
        return _final_message("복구해서 답했다.")

    events = list(run_agent("이상한 인자를 만들어봐", chat_fn=fake_chat, max_steps=5, max_seconds=30))

    tool_results = _events_by_kind(events, "tool_result")
    assert tool_results[0].data["ok"] is False

    done = _events_by_kind(events, "done")
    assert done[0].data["finish_reason"] == "stop"
