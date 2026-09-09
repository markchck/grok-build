"""연결 실패 처리 테스트 — 이 장의 완료 기준이다.

모델 서버나 Milvus 없이도 실행할 수 있다. 두 시나리오 모두 MCP 파이썬 SDK
(``mcp`` 패키지)만 있으면 되고, 실제로 뜨는 "서버"는 이 테스트 파일 안에서
``python -c``로 즉석에서 만드는 최소 스텁이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from docagent.config import load_settings
from docagent.mcp_client import McpConnectionError, McpToolsClient

PROJECT_DIR = str(Path(__file__).resolve().parent.parent)

_SLOW_SERVER_SNIPPET = (
    "import time\n"
    "from mcp.server.mcpserver import MCPServer\n"
    "m = MCPServer(name='slow-test')\n"
    "@m.tool()\n"
    "def slow(x: int) -> int:\n"
    "    time.sleep(5)\n"
    "    return x\n"
    "m.run(transport='stdio')\n"
)

_FLAKY_SERVER_SNIPPET = (
    "import os\n"
    "from mcp.server.mcpserver import MCPServer\n"
    "m = MCPServer(name='flaky-test')\n"
    "_count = {'n': 0}\n"
    "@m.tool()\n"
    "def flaky(x: int) -> int:\n"
    "    _count['n'] += 1\n"
    "    if _count['n'] >= 2:\n"
    "        os._exit(1)\n"
    "    return x\n"
    "m.run(transport='stdio')\n"
)


def _settings_with(**overrides):
    base = load_settings()
    return type(base)(**{**base.__dict__, **overrides})


@pytest.mark.asyncio
async def test_connect_to_nonexistent_command_raises_mcp_connection_error():
    settings = _settings_with(mcp_server_command="/no/such/binary-xyz", mcp_server_args=[])
    client = McpToolsClient(settings=settings, max_reconnect_attempts=0)
    with pytest.raises(McpConnectionError):
        await client.list_tool_specs()


@pytest.mark.asyncio
async def test_slow_tool_call_times_out():
    settings = _settings_with(
        mcp_server_command=sys.executable,
        mcp_server_args=["-c", _SLOW_SERVER_SNIPPET],
        mcp_connect_timeout_seconds=5.0,
        mcp_call_timeout_seconds=1.0,
    )
    client = McpToolsClient(settings=settings, max_reconnect_attempts=0)
    await client.connect()
    with pytest.raises(McpConnectionError):
        await client.call_tool("slow", {"x": 1})
    await client.close()


@pytest.mark.asyncio
async def test_reconnect_after_server_crash():
    settings = _settings_with(
        mcp_server_command=sys.executable,
        mcp_server_args=["-c", _FLAKY_SERVER_SNIPPET],
        mcp_connect_timeout_seconds=5.0,
        mcp_call_timeout_seconds=5.0,
    )
    client = McpToolsClient(settings=settings, max_reconnect_attempts=1)
    await client.connect()

    r1 = await client.call_tool("flaky", {"x": 1})
    assert r1.ok

    # 두 번째 호출에서 서버 프로세스가 스스로 죽는다. max_reconnect_attempts=1이므로
    # McpToolsClient가 자동으로 재연결해서 성공해야 한다.
    r2 = await client.call_tool("flaky", {"x": 2})
    assert r2.ok

    await client.close()
