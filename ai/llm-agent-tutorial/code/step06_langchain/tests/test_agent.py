"""에이전트(docagent.agent.run_agent) 단위 테스트 — 3단계 tests/test_agent.py와 같은 시나리오를
LangChain ``create_agent`` + 커스텀 미들웨어 조합으로 재현해 확인한다.

실제 모델 서버 대신 ``ScriptedChatModel``(``BaseChatModel``을 상속한 가짜 모델)을
``run_agent(..., model=...)``에 주입한다 — 3단계가 ``chat_fn``을 주입했던 것과
같은 목적이다.
"""

from __future__ import annotations

from typing import Any, Callable, List

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from docagent.agent import run_agent


class ScriptedChatModel(BaseChatModel):
    """호출될 때마다 ``script(messages)``가 만든 ``AIMessage``를 그대로 돌려준다."""

    script: Callable[[List[BaseMessage]], AIMessage]

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self.script(messages))])

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # create_agent가 부른다. 도구 바인딩은 이 테스트에서 필요 없다.
        return self


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def _events_by_kind(events, kind):
    return [e for e in events if e.kind == kind]


def test_happy_path_tool_then_final_answer():
    calls = {"n": 0}

    def script(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_call("c1", "sum_sales", {"product": "노트북", "quarter": "Q1"})
        return AIMessage(content="1분기 노트북 매출 합계를 확인했다.")

    model = ScriptedChatModel(script=script)
    events = list(run_agent("1분기 노트북 매출 합계 알려줘", model=model, max_steps=5))

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


def test_tool_execution_error_is_fed_back_not_raised():
    """존재하지 않는 제품 -> ToolExecutionError -> ToolErrorMiddleware가 ToolMessage로 변환."""

    calls = {"n": 0}

    def script(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool_call("c1", "sum_sales", {"product": "없는제품"})
        return AIMessage(content="조건에 맞는 데이터가 없다고 답했다.")

    model = ScriptedChatModel(script=script)
    events = list(run_agent("없는제품 매출은?", model=model, max_steps=5))

    tool_results = _events_by_kind(events, "tool_result")
    assert len(tool_results) == 1
    assert tool_results[0].data["ok"] is False

    done = _events_by_kind(events, "done")
    assert done[0].data["finish_reason"] == "stop"  # 예외로 전체가 죽지 않는다


def test_repeated_same_tool_call_is_blocked():
    def script(messages):
        return _tool_call("c", "sum_sales", {"product": "노트북"})

    model = ScriptedChatModel(script=script)
    events = list(run_agent("같은 걸 계속 불러라", model=model, max_steps=10))

    done = _events_by_kind(events, "done")
    assert len(done) == 1
    assert done[0].data["finish_reason"] == "repeated_tool_call"

    tool_calls = _events_by_kind(events, "tool_call")
    # 3번 성공적으로 실행되고 4번째 시도에서 차단돼야 한다 (REPEAT_LIMIT=3).
    assert len(tool_calls) == 4


def test_different_args_do_not_trigger_repeat_guard():
    calls = {"n": 0}

    def script(messages):
        calls["n"] += 1
        if calls["n"] <= 3:
            return _tool_call(f"c{calls['n']}", "sum_sales", {"product": f"제품{calls['n']}"})
        return AIMessage(content="여러 제품을 확인했다.")

    model = ScriptedChatModel(script=script)
    events = list(run_agent("여러 제품을 조회해줘", model=model, max_steps=10))

    done = _events_by_kind(events, "done")
    assert done[0].data["finish_reason"] == "stop"
