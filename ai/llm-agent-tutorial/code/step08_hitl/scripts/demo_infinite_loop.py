"""6절 실패 상황 실습: 일부러 무한 루프를 만드는 모델을 흉내 내서 차단을 확인한다.

이 장의 스텁 서버(``code/_tools/fake_openai_server.py``)는 tools가 있으면
**딱 한 번만** 도구를 부르고 바로 최종 답을 낸다(코드 주석에 명시돼 있다).
그래서 "같은 도구를 계속 부른다" 같은 여러 라운드짜리 무한 루프는 그 스텁으로
재현할 수 없다. 이 스크립트는 실제 모델 서버 대신, **끝없이 도구만 부르도록
일부러 만든 가짜 모델(chat_fn)**을 agent.advance_run에 직접 주입해서, 애플리케이션이
정말로 멈추는지 눈으로 보여준다.

두 가지 시나리오를 순서대로 돌린다.
    1. 매번 완전히 같은 인자로 sum_sales를 부르는 모델 -> repeated_tool_call
    2. 매번 다른 질의로 flaky_lookup을 부르지만 결과가 항상 같은 상황 -> no_progress

실행:
    PYTHONPATH=. python scripts/demo_infinite_loop.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docagent import store
from docagent.agent import advance_run, start_run
from docagent.config import load_settings


def _tool_call_message(call_id: str, name: str, arguments_json: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments_json}}
        ],
    }


def run_scenario(title: str, chat_fn, message: str) -> None:
    print(f"\n=== {title} ===")
    conn = store.connect(":memory:")
    settings = load_settings()
    settings = settings.__class__(**{**settings.__dict__, "max_agent_steps": 50, "max_agent_seconds": 30})

    run_id = start_run(conn, message)
    for event in advance_run(conn, run_id, chat_fn=chat_fn, settings=settings):
        if event.kind == "tool_call":
            print(f"[tool_call] {event.data['name']}({event.data['args']})")
        elif event.kind == "tool_result":
            print(f"[tool_result] ok={event.data['ok']} {event.data['summary'][:80]}")
        elif event.kind == "done":
            print(f"[done] finish_reason={event.data['finish_reason']}")
            print(f"       partial={event.data['partial']}")


def scenario_repeated_tool_call() -> None:
    counter = {"n": 0}

    def broken_model(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        # 항상 완전히 같은 도구 + 같은 인자만 부른다(call_id는 매번 새로 발급돼서
        # 멱등성 캐시가 아니라 반복 감지 (a)가 이 상황을 잡는다는 것을 분명히 보여준다).
        counter["n"] += 1
        msg = _tool_call_message(f"call_{counter['n']}", "sum_sales", '{"product": "노트북"}')
        return {"message": msg, "usage": {}}

    run_scenario(
        "시나리오 1: 같은 (도구, 인자) 무한 반복 -> repeated_tool_call",
        broken_model,
        "노트북 매출을 계속 확인해줘",
    )


def scenario_no_progress() -> None:
    counter = {"n": 0}

    def broken_search_model(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        # 질의는 매번 다르게 만들지만(반복 감지 (a)를 피해간다), flaky_lookup은
        # 어떤 질의를 넣어도 항상 같은 고정 결과만 돌려주는 결함 있는 도구다.
        counter["n"] += 1
        msg = _tool_call_message(f"call_{counter['n']}", "flaky_lookup", f'{{"query": "질의-{counter["n"]}"}}')
        return {"message": msg, "usage": {}}

    run_scenario(
        "시나리오 2: 인자는 다른데 결과가 안 바뀌는 반복 -> no_progress",
        broken_search_model,
        "계속 다른 말로 찾아봐",
    )


if __name__ == "__main__":
    scenario_repeated_tool_call()
    scenario_no_progress()
    print("\n두 시나리오 모두 무한히 돌지 않고 애플리케이션이 스스로 멈췄다.")
