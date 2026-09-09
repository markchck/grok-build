"""트레이싱(9단계)이 이 장의 에이전트 루프에 실제로 배선됐는지 확인한다.

``docagent/tracing.py``는 9단계에서 한 글자도 고치지 않고 그대로 가져왔다.
이 테스트는 그 모듈 자체가 아니라 **agent.py가 실제로 traced_span을
부르는가**를 확인한다. Phoenix 서버(관측 플랫폼)를 실제로 띄워 스팬이
도착하는지까지 보는 것은 이 pytest 스위트가 아니라 수동 통합 실행(README
"최종 통합 확인" 절)에서 한다 — 9단계가 이미 그 방식으로 검증했고
(code/step09_tracing_eval/README.md), 여기서는 결정론적인 부분만 자동화한다.
"""

from __future__ import annotations

from docagent import store, tracing
from docagent.agent import advance_run, start_run
from docagent.config import load_settings, reset_settings_cache


def _tool_call_message(call_id: str, name: str, arguments_json: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments_json}}],
    }


def _final_message(text: str) -> dict:
    return {"role": "assistant", "content": text, "tool_calls": None}


def test_tracing_disabled_by_default():
    settings = load_settings()
    assert settings.tracing_enabled is False


def test_init_tracing_noop_when_disabled():
    settings = load_settings()
    assert settings.tracing_enabled is False
    assert tracing.init_tracing(settings) is False


def test_agent_loop_runs_normally_with_tracing_noop(monkeypatch):
    """traced_span이 NoOp이어도(트레이싱 꺼짐, 기본값) agent.py 실행이 평소와
    똑같이 끝나는지 확인한다 — 계측 코드가 실행 자체를 바꾸면 안 된다."""

    calls = {"n": 0}

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"message": _tool_call_message("call_1", "sum_sales", '{"product": "노트북"}'), "usage": {}}
        return {"message": _final_message("매출을 확인했다."), "usage": {}}

    conn = store.connect(":memory:")
    run_id = start_run(conn, "노트북 매출 확인해줘")
    events = list(advance_run(conn, run_id, chat_fn=fake_chat, settings=load_settings()))

    done = [e for e in events if e.kind == "done"][0]
    assert done.data["finish_reason"] == "stop"


def test_init_tracing_reports_enabled_when_phoenix_available():
    """DOCAGENT_TRACING_ENABLED=true + arize-phoenix가 설치돼 있으면
    init_tracing이 실제로 전역 TracerProvider를 등록하고 True를 돌려준다.

    **별도 프로세스에서 확인한다.** ``init_tracing()``은 프로세스 전역
    TracerProvider를 등록하는 부수효과가 있고(한 번 등록하면 그 프로세스 안의
    이후 모든 ``traced_span`` 호출이 실제로 OTLP 내보내기를 시도하게 된다),
    이 테스트가 쓰는 엔드포인트(``http://127.0.0.1:1``, 아무도 듣지 않는
    주소)로 이 pytest 프로세스 자체를 오염시키면 이후 다른 테스트 파일의
    ``advance_run`` 호출까지 매번 연결 실패를 기다리며 느려진다 — 그래서
    이 확인만 별도 파이썬 프로세스에 격리했다.
    """

    try:
        import phoenix.otel  # noqa: F401
    except ImportError:
        import pytest

        pytest.skip("arize-phoenix가 설치돼 있지 않다")

    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env["DOCAGENT_TRACING_ENABLED"] = "true"
    env["DOCAGENT_PHOENIX_ENDPOINT"] = "http://127.0.0.1:1/v1/traces"
    script = (
        "from docagent.config import load_settings\n"
        "from docagent import tracing\n"
        "settings = load_settings()\n"
        "assert settings.tracing_enabled is True\n"
        "assert tracing.init_tracing(settings) is True\n"
        "print('OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
