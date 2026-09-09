"""FastAPI 엔드포인트 — Deep Agents 실행을 PROJECT-SPEC.md 4절 SSE로 스트리밍한다.

2단계(``code/step02_fastapi_chat/``)부터 이어온 ``/chat`` 계약을 그대로
따른다: ``text/event-stream``으로 ``token``/``status``/``tool_call``/
``tool_result``/``todo``/``done``을 내보낸다. 이 장에서 새로 나오는 것은
``todo`` 이벤트뿐이다(4절 표에는 2단계부터 이미 있었지만, 실제로 값을
채워 보내는 것은 이 장이 처음이다).

``docagent.llm``의 ``achat``/``achat_stream``을 쓰지 않는다 — Deep Agents가
LangGraph 그래프이기 때문이다. PROJECT-SPEC.md 9절이 "2단계 이후 서버
코드는 achat/achat_stream을 쓴다"고 정한 이유(클라이언트가 연결을 끊었을 때
모델 서버로 가는 호출까지 실제로 취소하기 위해 이벤트 루프가 스트림을 직접
닫아야 한다)는 이 장에서도 유효하다. LangGraph는 ``astream``으로 같은
역할을 한다 — 그래서 이 엔드포인트는 ``agent.astream(...)``을 쓴다.
동기 버전(``docagent.bridge.stream_agent_events``, ``agent.stream``)은
스크립트·테스트에서 쓴다.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from docagent import events
from docagent.bridge import TodoIdAssigner, is_error_result, truncate, stage_for_tool
from docagent.config import get_settings
from docagent.deep_agent import build_agent

app = FastAPI(title="docagent step12 - deep agents")
app.mount("/static", StaticFiles(directory="static"), name="static")

_RENDER = {
    "token": lambda d: events.token_event(d["text"]),
    "status": lambda d: events.status_event(d["stage"], d["message"]),
    "tool_call": lambda d: events.tool_call_event(d["id"], d["name"], d["args"]),
    "tool_result": lambda d: events.tool_result_event(d["id"], d["ok"], d["summary"]),
    "todo": lambda d: events.todo_event(d["items"]),
}


class ChatRequest(BaseModel):
    message: str


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


async def _astream_agent_events(agent: Any, user_message: str) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """``docagent.bridge.stream_agent_events``의 비동기 버전.

    로직은 동일하다(동기 버전과 두 곳에 따로 두는 이유는 ``docagent/bridge.py``
    상단에 적은 취소 관련 설명과 같다). 두 구현이 갈라지지 않도록 헬퍼
    (``TodoIdAssigner``, ``is_error_result``, ``truncate``, ``stage_for_tool``)는
    ``docagent.bridge``의 것을 그대로 가져와 쓴다.
    """

    seen = 0
    assigner = TodoIdAssigner()
    last_todo_snapshot: list[dict[str, Any]] | None = None
    final_text = ""

    # PROJECT-SPEC.md 7절의 max_agent_steps는 "모델 호출 1회"를 1단계로 센다.
    # LangGraph의 recursion_limit은 "슈퍼스텝(도구 실행 포함) 1회"를 1로 센다 —
    # 같은 숫자가 아니다. 이 장은 write_todos/task로 라운드가 늘어나는 구조라
    # 여유를 넉넉히 둔다(6배). 이 한도를 넘으면 LangGraph가
    # ``GraphRecursionError``를 던진다 — 8단계처럼 부분 결과를 담은 ``done``으로
    # 곱게 마무리하지 못하고 예외로 끝난다는 뜻이다. 이 장에서는 예외를 잡아
    # ``error``+``done(cancelled)``으로 바꿀 뿐, 8단계 수준의 부분 결과 보존
    # (``partial.steps_used`` 등)은 아직 하지 않는다 — 응용 과제로 남겨 둔다.
    settings = get_settings()
    run_config = {"recursion_limit": settings.max_agent_steps * 6}

    stream_input = {"messages": [{"role": "user", "content": user_message}]}
    async for state in agent.astream(stream_input, config=run_config, stream_mode="values"):
        messages = state.get("messages", [])
        new_messages = messages[seen:]
        seen = len(messages)

        for message in new_messages:
            kind = type(message).__name__
            if kind == "AIMessage":
                tool_calls = getattr(message, "tool_calls", None) or []
                if tool_calls:
                    for call in tool_calls:
                        name = call.get("name", "")
                        yield ("status", {"stage": stage_for_tool(name), "message": f"{name} 호출 준비"})
                        yield ("tool_call", {"id": call.get("id", ""), "name": name, "args": call.get("args", {})})
                else:
                    content = message.content or ""
                    if content:
                        final_text = content
                        yield ("token", {"text": content})
            elif kind == "ToolMessage":
                content = message.content if isinstance(message.content, str) else str(message.content)
                ok = not is_error_result(content)
                yield (
                    "tool_result",
                    {"id": getattr(message, "tool_call_id", ""), "ok": ok, "summary": truncate(content)},
                )

        todos = state.get("todos")
        if todos is not None:
            items = assigner.items_for(todos)
            if items != last_todo_snapshot:
                last_todo_snapshot = items
                yield ("todo", {"items": items})

    yield ("done", {"finish_reason": "stop", "partial": {"text": final_text}})


@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    agent = build_agent()

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for name, data in _astream_agent_events(agent, req.message):
                if name == "done":
                    yield events.done_event(data["finish_reason"], partial=data.get("partial"))
                    return
                renderer = _RENDER.get(name)
                if renderer is not None:
                    yield renderer(data)
        except Exception as exc:  # noqa: BLE001 - 스트림 중 어떤 예외가 나도 error 이벤트로 알린다
            yield events.error_event("agent_error", str(exc))
            yield events.done_event("cancelled")

    return StreamingResponse(event_stream(), media_type="text/event-stream")
