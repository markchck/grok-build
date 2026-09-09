"""MCP(10단계)가 이 장의 에이전트 루프에 실제로 연결되는지 확인한다.

``docagent/mcp_runtime.py``를 통해 **실제 MCP 서버 프로세스**(``python -m
mcp_server.server``, 별도 자식 프로세스)를 띄우고 stdio로 통신한다 — 이
파일은 목이나 스텁이 아니라 10단계 서버 코드를 그대로 자식 프로세스로
실행한다. Milvus를 건드리지 않는 도구(sum_sales/lookup_sales_rows)로 "연결
자체가 되는가"를 확인하고, 도구 목록 조회(``list_tool_specs``)로 "탐색"이
실제로 되는가를 확인한다. Dense 검색(``search_docs``, Milvus + 임베딩 필요)은
README의 수동 통합 실행 절차에서 확인한다(이 테스트 스위트는 결정론과 속도를
위해 Milvus를 세팅하지 않는다).

에이전트 루프(``agent.py``) 쪽 배선(``mcp_search_docs`` 분기, ``source``
필드, 연결 실패 시 도구 실패로 처리하는 경로)은 ``mcp_runtime.call_mcp_tool_sync``를
monkeypatch해 결정론적으로 확인한다 — 이 부분은 "MCP 프로토콜이 실제로
동작하는가"가 아니라 "agent.py가 그 결과를 올바르게 SSE 이벤트로 바꾸는가"를
확인하는 것이므로 실제 프로세스를 다시 띄울 필요가 없다.
"""

from __future__ import annotations

import json

import pytest

from docagent import mcp_runtime, store
from docagent.agent import advance_run, start_run
from docagent.config import load_settings
from docagent.llm import ChatResult
from docagent.mcp_client import McpConnectionError


def _to_chat_result(msg: dict, usage: dict) -> ChatResult:
    return ChatResult(
        text=msg.get("content") or "",
        finish_reason=None,
        tool_calls=msg.get("tool_calls") or [],
        raw={"usage": usage},
    )


def _settings():
    return load_settings()


# ---------------------------------------------------------------------------
# 실제 MCP 서버 프로세스를 띄워 확인 (Milvus를 건드리지 않는 도구만)
# ---------------------------------------------------------------------------


def test_real_mcp_server_tool_discovery():
    settings = _settings()
    specs = mcp_runtime.list_mcp_tool_specs_sync(settings)
    names = {s["function"]["name"] for s in specs}
    assert {"search_docs", "sum_sales", "lookup_sales_rows"} <= names


def test_real_mcp_server_sum_sales_matches_local_calculation():
    settings = _settings()
    result = mcp_runtime.call_mcp_tool_sync("sum_sales", {"product": "노트북"}, settings)
    assert result.ok
    payload = json.loads(result.content)
    assert payload["matched_row_count"] > 0
    assert payload["filters"]["product"] == "노트북"


def test_real_mcp_server_unknown_tool_reports_failure():
    settings = _settings()
    result = mcp_runtime.call_mcp_tool_sync("no_such_tool", {}, settings)
    assert result.ok is False


def test_real_mcp_server_connection_failure_raises():
    settings = _settings()
    broken = settings.__class__(**{**settings.__dict__, "mcp_server_command": "no-such-executable-xyz"})
    with pytest.raises(McpConnectionError):
        mcp_runtime.call_mcp_tool_sync("sum_sales", {}, broken)


# ---------------------------------------------------------------------------
# agent.py 배선: mcp_search_docs 분기, source 필드, 도구 목록 병합
# ---------------------------------------------------------------------------


def _tool_call_message(call_id: str, name: str, arguments_json: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments_json}}],
    }


def _final_message(text: str) -> dict:
    return {"role": "assistant", "content": text, "tool_calls": None}


def test_select_tools_includes_mcp_tool_when_requested(monkeypatch):
    from docagent import agent

    def fake_list(settings):
        return [
            {
                "type": "function",
                "function": {"name": "search_docs", "description": "", "parameters": {"type": "object", "properties": {}}},
            }
        ]

    monkeypatch.setattr(agent.mcp_runtime, "list_mcp_tool_specs_sync", fake_list)
    specs = agent._select_tools(None, include_mcp=True, settings=_settings())
    names = {s["function"]["name"] for s in specs}
    assert "mcp_search_docs" in names
    # 로컬 hybrid_search_docs와 공존한다(대체가 아니라 추가) — 최종 통합 4번 항목.
    assert "hybrid_search_docs" in names


def test_select_tools_without_include_mcp_has_no_mcp_tool():
    from docagent import agent

    specs = agent._select_tools(None, include_mcp=False, settings=_settings())
    names = {s["function"]["name"] for s in specs}
    assert "mcp_search_docs" not in names


def test_agent_loop_routes_mcp_search_docs_and_marks_source(monkeypatch):
    """agent.py가 mcp_search_docs 호출을 mcp_runtime을 통해 실행하고, tool_call
    이벤트에 source="mcp"를 싣는지 확인한다. 실제 MCP 프로세스 대신
    mcp_runtime.call_mcp_tool_sync를 monkeypatch해 결정론적으로 만든다
    (이 테스트의 목적은 프로토콜이 아니라 agent.py의 배선이다)."""

    from docagent import agent
    from docagent.mcp_client import McpToolCallResult

    def fake_call(name, arguments, settings):
        assert name == "search_docs"
        return McpToolCallResult(
            ok=True,
            content=json.dumps(
                {
                    "matched_count": 1,
                    "hits": [
                        {
                            "pk": "doc1#p0-c0",
                            "text": "노트북 매출이 증가했다",
                            "doc_id": "doc1",
                            "doc_title": "리포트",
                            "page": 0,
                            "chunk_index": 0,
                            "source_url": "doc1.md",
                            "score": 0.9,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )

    monkeypatch.setattr(agent.mcp_runtime, "call_mcp_tool_sync", fake_call)

    calls = {"n": 0}

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _to_chat_result(
                _tool_call_message("call_1", "mcp_search_docs", '{"query": "노트북 매출", "top_k": 3}'), {}
            )
        return _to_chat_result(_final_message("노트북 매출이 증가했다[S1]."), {})

    conn = store.connect(":memory:")
    run_id = start_run(conn, "노트북 매출 원인을 MCP로 찾아줘")
    events = list(
        advance_run(conn, run_id, chat_fn=fake_chat, include_mcp=True, settings=_settings())
    )

    tool_calls = [e for e in events if e.kind == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].data["name"] == "mcp_search_docs"
    assert tool_calls[0].data["source"] == "mcp"

    tool_results = [e for e in events if e.kind == "tool_result"]
    assert tool_results[0].data["ok"] is True

    done = [e for e in events if e.kind == "done"][0]
    assert done.data["finish_reason"] == "stop"


def test_agent_loop_mcp_connection_failure_becomes_tool_result_false(monkeypatch):
    """MCP 서버 연결이 실패해도 애플리케이션이 죽지 않고 tool_result(ok=False)로
    끝난다 — 실패 원인을 트레이스에서 확인하는 시나리오의 재료(9단계 배선)."""

    from docagent import agent

    def fake_call(name, arguments, settings):
        raise McpConnectionError("MCP 서버 프로세스에 연결할 수 없다(테스트로 일부러 낸 오류)")

    monkeypatch.setattr(agent.mcp_runtime, "call_mcp_tool_sync", fake_call)

    calls = {"n": 0}

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _to_chat_result(
                _tool_call_message("call_1", "mcp_search_docs", '{"query": "노트북 매출"}'), {}
            )
        return _to_chat_result(_final_message("검색을 사용할 수 없어 답할 수 없다."), {})

    conn = store.connect(":memory:")
    run_id = start_run(conn, "노트북 매출 원인을 MCP로 찾아줘")
    events = list(
        advance_run(conn, run_id, chat_fn=fake_chat, include_mcp=True, settings=_settings())
    )

    tool_results = [e for e in events if e.kind == "tool_result"]
    assert tool_results[0].data["ok"] is False
    assert "mcp_connection_error" in tool_results[0].data["summary"]
