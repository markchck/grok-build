"""에이전트 — 3단계 ``while`` 루프를 ``langchain.agents.create_agent``로 재구성한다.

3단계(``docagent/agent.py``)는 "모델 호출 -> tool_calls 확인 -> 도구 실행 -> 결과
추가 -> 다시 모델 호출"을 ``while`` 문으로 직접 돌리면서, 그 루프 안에서
최대 단계 수·최대 시간·반복 호출 감지를 직접 검사했다. ``create_agent``는 이
루프 자체(LangGraph 그래프: model 노드 <-> tools 노드를 도구 호출이 없을 때까지
왕복)를 대신 만들어 준다 — **주의**: ``create_agent``는 내부적으로
LangGraph 위에 구현돼 있다(``pip show langchain`` 의존성에 ``langgraph``가
실제로 잡힌다, 이 장 3절 참고). 7단계에서 배우는 "State/Node/Edge를 직접
설계하는 LangGraph"와는 다르다 — 여기서는 그 내부 그래프를 우리가 짜지
않는다.

``create_agent``가 대신 만들어주지 않는 것(이 파일에서 직접 구현하는 것):

- 도구 실행이 예외를 던졌을 때 "예외로 죽이지 않고 모델에게 실패 이유를
  문자열로 돌려준다"는 3단계의 규약(``ToolErrorMiddleware``).
- 같은 (도구 이름, 정규화한 인자) 조합이 3회 반복되면 중단한다는
  PROJECT-SPEC.md 7장의 규칙(``RepeatToolCallGuardMiddleware`` — LangChain
  내장 ``ToolCallLimitMiddleware``는 "도구 이름 하나당 호출 횟수"만 세고
  인자까지 구분하지 않으므로 이 규칙에는 맞지 않는다. 6절에서 직접 비교한다).
- 모델·도구 호출 단위의 요청 ID·소요 시간 로그(``CallLoggingMiddleware``) —
  2단계 HTTP 미들웨어가 요청 하나 단위로 하던 일을, 여기서는 모델 호출 한
  번·도구 호출 한 번 단위로 한다.

세 미들웨어 모두 ``langchain.agents.middleware.AgentMiddleware``를 상속해
``wrap_model_call``/``wrap_tool_call`` 훅을 오버라이드한다. 함수형 데코레이터
(``@wrap_model_call``)도 있지만, 이 파일의 미들웨어는 요청 사이에 상태(카운터,
로그)를 들고 있어야 하므로 클래스로 만든다.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
    ToolErrorMiddleware,
)

from docagent.config import get_settings
from docagent.models import build_chat_model
from docagent.tools import TOOLS, ToolExecutionError

logger = logging.getLogger("docagent.agent")

SYSTEM_PROMPT = (
    "너는 판매 데이터를 분석해주는 보조원이다. "
    "필요하면 제공된 도구(sum_sales, lookup_sales_rows)를 사용해서 "
    "sales.csv의 실제 수치를 근거로 답변한다. "
    "도구를 쓰지 않고 숫자를 추측해서 답하지 않는다."
)

REPEAT_LIMIT = 3  # PROJECT-SPEC.md 7장과 동일한 값


class RepeatedToolCallError(RuntimeError):
    """같은 (도구, 인자) 조합이 REPEAT_LIMIT회를 넘게 호출됐을 때 던진다.

    3단계는 이 상황을 while 루프 안에서 조용히 break하고 done 이벤트를
    만들었다. 이 장에서는 루프 자체를 create_agent가 실행하므로, "지금 멈춰야
    한다"는 판단을 루프 바깥(agent.py의 호출부)에 전달할 방법이 예외뿐이다 —
    미들웨어가 그래프 실행 중간에 상태를 바꿔 그래프를 조기 종료시키는
    방법도 있지만(``AgentMiddleware.before_model``이 ``jump_to``를 지원한다),
    "왜 멈췄는지"를 애플리케이션이 명확한 타입으로 알 수 있다는 점에서
    예외가 더 단순하다.
    """


def _normalize_args(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False)


class RepeatToolCallGuardMiddleware(AgentMiddleware):
    """같은 (도구 이름, 정규화한 인자) 조합이 REPEAT_LIMIT회 넘게 반복되면 중단한다.

    3단계 ``agent.py``의 ``call_counts`` 딕셔너리와 로직이 완전히 같다. 다른
    점은 그 딕셔너리를 담는 자리뿐이다 — 3단계는 ``while`` 루프의 지역
    변수였고, 여기서는 이 미들웨어 인스턴스의 속성이다. ``build_agent()``가
    요청마다 새 인스턴스를 만들기 때문에(3단계가 요청마다 새 ``call_counts``로
    시작하는 것과 동일하게) 요청 사이에 카운트가 섞이지 않는다.
    """

    def __init__(self, *, limit: int = REPEAT_LIMIT) -> None:
        super().__init__()
        self.limit = limit
        self.call_counts: dict[tuple[str, str], int] = {}

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        name = request.tool_call.get("name", "")
        args = request.tool_call.get("args", {}) or {}
        key = (name, _normalize_args(args))
        self.call_counts[key] = self.call_counts.get(key, 0) + 1

        if self.call_counts[key] > self.limit:
            raise RepeatedToolCallError(
                f"도구 '{name}'을(를) 같은 인자({args})로 {self.limit}회 넘게 호출했다."
            )
        return handler(request)


def _handle_tool_execution_error(exc: Exception, request: ToolCallRequest) -> str | None:
    """``ToolErrorMiddleware(on_error=...)``에 넘기는 콜백.

    3단계 ``tools.run_tool()``의 "실패해도 예외를 위로 던지지 않고
    ``ToolRunResult(ok=False, ...)``로 돌려준다"는 규약과 같은 목적이다.
    **LangChain이 이 변환을 기본으로 해주지는 않는다** — ``@tool`` 함수
    본문에서 던진 일반 예외는 ``langgraph.prebuilt.ToolNode``가 기본적으로
    그대로 다시 던진다(실제로 재현해 확인했다. pydantic 스키마 검증 실패만
    자동으로 ToolMessage로 바뀐다). 다만 그 변환 자체를 "opt-in 미들웨어
    하나 추가"로 끝낼 수 있다는 점은 LangChain이 제공하는 것 — 처음에는
    이 파일에서 ``wrap_tool_call``로 직접 구현했지만, ``langchain.agents.
    middleware``에 정확히 이 역할을 하는 ``ToolErrorMiddleware``가 이미
    있다는 것을 알고 나서 그쪽으로 바꿨다(6절에서 두 버전을 비교한다).
    ``None``을 돌려주면 그 예외는 그대로 전파된다 — 우리가 아는 예외
    (``ToolExecutionError``)만 골라서 문자열로 바꾸고, 그 외의 예외는
    프로그램 버그일 가능성이 높으므로 굳이 삼키지 않는다.
    """
    if isinstance(exc, ToolExecutionError):
        return json.dumps({"error": "tool_error", "message": str(exc)}, ensure_ascii=False)
    return None


@dataclass
class CallLog:
    kind: str  # "model" | "tool"
    name: str
    elapsed_ms: float
    ok: bool


class CallLoggingMiddleware(AgentMiddleware):
    """모델 호출 한 번·도구 호출 한 번 단위로 걸린 시간을 기록한다.

    2단계 ``RequestContextMiddleware``(HTTP 요청 하나 단위)와 이 미들웨어를
    나란히 보면 "같은 이름의 미들웨어가 서로 다른 두 층에서 서로 다른
    단위로 개입한다"는 docs/02-fastapi-chat.md 2.6절의 비교표를 코드로
    확인할 수 있다. HTTP 미들웨어는 이 에이전트가 몇 번 도구를 부르는지
    모르고, 이 미들웨어는 반대로 자신이 HTTP 요청 몇 번째 처리 중인지
    모른다 — ``request``(``ModelRequest``/``ToolCallRequest``)에는 HTTP 계층
    정보가 아예 없다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.logs: list[CallLog] = []

    def wrap_model_call(self, request: ModelRequest, handler):
        start = time.monotonic()
        response = handler(request)
        elapsed_ms = (time.monotonic() - start) * 1000
        self.logs.append(CallLog("model", request.model.__class__.__name__, elapsed_ms, True))
        logger.info("model_call elapsed_ms=%.1f messages=%d", elapsed_ms, len(request.messages))
        return response

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        start = time.monotonic()
        name = request.tool_call.get("name", "")
        try:
            response = handler(request)
        except Exception:
            elapsed_ms = (time.monotonic() - start) * 1000
            self.logs.append(CallLog("tool", name, elapsed_ms, False))
            logger.info("tool_call name=%s elapsed_ms=%.1f ok=False", name, elapsed_ms)
            raise
        elapsed_ms = (time.monotonic() - start) * 1000
        self.logs.append(CallLog("tool", name, elapsed_ms, True))
        logger.info("tool_call name=%s elapsed_ms=%.1f ok=True", name, elapsed_ms)
        return response


@dataclass
class AgentEvent:
    """docagent.events의 SSE 이벤트 이름과 1:1로 대응한다 (3단계와 동일한 모양)."""

    kind: str
    data: dict[str, Any]


@dataclass
class AgentRunResult:
    finish_reason: str
    final_text: str
    steps_used: int
    messages: list[Any] = field(default_factory=list)


def build_agent(*, model=None):
    """요청마다 새로 부르는 팩토리. 미들웨어 인스턴스를 매 요청 새로 만들어서
    반복 호출 카운터·로그가 이전 요청과 섞이지 않게 한다."""

    chat_model = model or build_chat_model()
    call_log_mw = CallLoggingMiddleware()
    repeat_guard_mw = RepeatToolCallGuardMiddleware(limit=REPEAT_LIMIT)
    tool_error_mw = ToolErrorMiddleware(on_error=_handle_tool_execution_error)

    agent = create_agent(
        model=chat_model,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        # 실행 순서는 목록 순서대로다: 로깅(가장 바깥) -> 반복 감지 -> 오류 변환
        # (handler에 가장 가까움). tool_error_mw가 예외를 ToolMessage로 바꾸고
        # 나면 repeat_guard_mw와 call_log_mw는 "성공한 호출"만 보게 된다 —
        # 순서를 바꾸면 관찰되는 것이 달라진다는 점을 6절에서 설명한다.
        middleware=[call_log_mw, repeat_guard_mw, tool_error_mw],
    )
    return agent, call_log_mw, repeat_guard_mw


def run_agent(
    user_message: str,
    *,
    history: list[dict[str, Any]] | None = None,
    model=None,
    max_steps: int | None = None,
) -> Iterator[AgentEvent]:
    """create_agent 그래프를 ``stream_mode="updates"``로 실행하며 진행 이벤트를 낸다.

    ``updates`` 스트림은 그래프의 각 노드(``model``, ``tools``)가 끝날 때마다
    그 노드가 만든 state 변경분만 준다 — 3단계의 while 루프가 매 반복마다
    ``yield``하던 것과 같은 지점에서 이벤트를 만들 수 있다. 도구 호출
    반복 한도 초과는 ``RepeatedToolCallError``로, 최대 단계 수 초과는
    ``max_steps``(LangGraph의 ``recursion_limit``)로 각각 감지한다.
    """

    settings = get_settings()
    max_steps = max_steps if max_steps is not None else settings.max_agent_steps

    messages: list[dict[str, Any]] = list(history) if history else []
    messages.append({"role": "user", "content": user_message})

    agent, call_log_mw, repeat_guard_mw = build_agent(model=model)

    yield AgentEvent("status", {"stage": "starting", "message": "LangChain 에이전트 실행을 시작한다"})

    steps_used = 0
    final_text = ""
    finish_reason = "stop"

    config: RunnableConfig = {"recursion_limit": max_steps * 2 + 2}

    try:
        for update in agent.stream({"messages": messages}, config=config, stream_mode="updates"):
            for node_name, node_update in update.items():
                node_messages = node_update.get("messages", []) if isinstance(node_update, dict) else []
                if node_name == "model":
                    steps_used += 1
                    yield AgentEvent(
                        "status",
                        {"stage": "planning", "message": f"{steps_used}단계: 모델에게 다음 행동을 묻는다"},
                    )
                    for msg in node_messages:
                        if isinstance(msg, AIMessage) and msg.tool_calls:
                            yield AgentEvent(
                                "status",
                                {"stage": "analyzing", "message": f"도구 {len(msg.tool_calls)}건 실행 준비"},
                            )
                            for call in msg.tool_calls:
                                yield AgentEvent(
                                    "tool_call",
                                    {"id": call.get("id", ""), "name": call.get("name", ""), "args": call.get("args", {})},
                                )
                        elif isinstance(msg, AIMessage):
                            final_text = msg.content or ""
                elif node_name == "tools":
                    for msg in node_messages:
                        if isinstance(msg, ToolMessage):
                            content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
                            ok = "\"error\"" not in content[:20]
                            summary = content if len(content) <= 200 else content[:200] + "..."
                            yield AgentEvent("tool_result", {"id": msg.tool_call_id, "ok": ok, "summary": summary})
    except RepeatedToolCallError as exc:
        logger.warning("repeated_tool_call: %s", exc)
        yield AgentEvent("done", {"finish_reason": "repeated_tool_call"})
        return
    except Exception as exc:  # LangChain/모델 서버 쪽 오류를 그대로 죽이지 않는다
        logger.warning("llm_error: %s", exc)
        yield AgentEvent("error", {"code": "llm_error", "message": str(exc)})
        yield AgentEvent("done", {"finish_reason": "cancelled"})
        return

    yield AgentEvent("token", {"text": final_text})
    yield AgentEvent("done", {"finish_reason": finish_reason})

    logger.info(
        "agent_run_complete steps=%d model_calls=%d tool_calls=%d",
        steps_used,
        sum(1 for l in call_log_mw.logs if l.kind == "model"),
        sum(1 for l in call_log_mw.logs if l.kind == "tool"),
    )


def run_agent_collect(user_message: str, *, model=None, max_steps: int | None = None) -> AgentRunResult:
    finish_reason = "stop"
    final_text = ""
    steps_used = 0
    for event in run_agent(user_message, model=model, max_steps=max_steps):
        if event.kind == "status" and event.data.get("stage") == "planning":
            steps_used += 1
        if event.kind == "token":
            final_text = event.data["text"]
        if event.kind == "done":
            finish_reason = event.data["finish_reason"]
    return AgentRunResult(finish_reason=finish_reason, final_text=final_text, steps_used=steps_used)
