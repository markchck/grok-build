"""MCP 서버(``mcp_server/server.py``)를 실제 별도 프로세스로 띄워 도구 탐색과
호출을 검증하는 통합 테스트.

``tests/test_mcp_client_failures.py``와 달리 이 테스트는 OpenAI 호환 모델
서버(임베딩 엔드포인트)와, 문서가 이미 적재된 Milvus 컬렉션이 필요하다.
README의 "3. 문서 적재"까지 마친 뒤, 아래 환경 변수를 채우고 실행한다.

    OPENAI_BASE_URL=http://127.0.0.1:8171/v1 OPENAI_API_KEY=sk-test \\
    EMBEDDING_MODEL=stub-embed DOCAGENT_MILVUS_URI=./data/milvus_lite.db \\
    DOCAGENT_MCP_SALES_CSV=./data/sales.csv \\
    DOCAGENT_MCP_SERVER_COMMAND=python DOCAGENT_MCP_SERVER_ARGS=-m,mcp_server.server \\
    python -m pytest tests/test_mcp_server_tools.py -v

필요한 값이 갖춰지지 않았거나 서버에 연결할 수 없으면, 이 테스트는 실패
대신 **건너뛴다**(pytest.skip) — 이 파일 하나 때문에 이 장의 다른 테스트가
CI/로컬에서 항상 외부 상태를 요구하게 만들고 싶지 않아서다.
"""

from __future__ import annotations

import json

import pytest

from docagent.config import load_settings
from docagent.mcp_client import McpConnectionError, McpToolsClient


async def _make_connected_client() -> McpToolsClient:
    client = McpToolsClient(settings=load_settings(), max_reconnect_attempts=0)
    try:
        await client.connect()
    except McpConnectionError as exc:
        pytest.skip(f"MCP 서버(문서/CSV)에 연결할 수 없어 건너뛴다: {exc}")
    return client


@pytest.mark.asyncio
async def test_discover_tools():
    client = await _make_connected_client()
    try:
        specs = await client.list_tool_specs()
        names = {s["function"]["name"] for s in specs}
        assert names == {"search_docs", "sum_sales", "lookup_sales_rows"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_call_sum_sales():
    client = await _make_connected_client()
    try:
        result = await client.call_tool("sum_sales", {"product": "노트북", "quarter": "Q2"})
        assert result.ok
        payload = json.loads(result.content)
        assert payload["total_units"] > 0
        assert payload["filters"]["product"] == "노트북"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_call_search_docs_returns_hits_or_skip():
    client = await _make_connected_client()
    try:
        result = await client.call_tool("search_docs", {"query": "노트북 매출 감소 원인", "top_k": 3})
        if not result.ok:
            pytest.skip(f"search_docs 실패(Milvus 미적재 가능성): {result.content[:200]}")
        payload = json.loads(result.content)
        assert payload["matched_count"] >= 1
        assert payload["hits"][0]["doc_id"]
    finally:
        await client.close()
