"""MCP 서버 연결 확인 CLI — 이 장의 완료 기준("도구 탐색과 실제 호출을
확인")을 손으로 실행해 눈으로 볼 수 있게 만든 스크립트다.

실행:
    python -m scripts.check_mcp_connection
    python -m scripts.check_mcp_connection --call search_docs '{"query": "노트북 매출"}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from docagent.config import load_settings
from docagent.mcp_client import McpConnectionError, McpToolsClient


async def main() -> int:
    parser = argparse.ArgumentParser(description="MCP 서버 연결·도구 탐색·호출 확인")
    parser.add_argument("--call", metavar="TOOL_NAME", help="탐색 후 이 도구를 호출해본다")
    parser.add_argument("args_json", nargs="?", default="{}", help="--call과 함께 쓸 JSON 인자")
    args = parser.parse_args()

    client = McpToolsClient(settings=load_settings())
    print(
        f"MCP 서버에 연결 시도: {client._settings.mcp_server_command} "  # noqa: SLF001 - 진단 출력용
        f"{' '.join(client._settings.mcp_server_args)}"
    )
    try:
        specs = await client.list_tool_specs()
    except McpConnectionError as exc:
        print(f"[실패] 연결 또는 도구 탐색 실패: {exc}", file=sys.stderr)
        return 1

    print(f"[성공] 도구 {len(specs)}개를 찾았다:")
    for spec in specs:
        fn = spec["function"]
        print(f"  - {fn['name']}: {fn['description'].splitlines()[0]}")

    if args.call:
        try:
            call_args = json.loads(args.args_json)
        except json.JSONDecodeError as exc:
            print(f"[실패] args_json이 올바른 JSON이 아니다: {exc}", file=sys.stderr)
            await client.close()
            return 1

        print(f"\n'{args.call}' 호출 중... 인자: {call_args}")
        try:
            result = await client.call_tool(args.call, call_args)
        except McpConnectionError as exc:
            print(f"[실패] 호출 중 연결 오류: {exc}", file=sys.stderr)
            await client.close()
            return 1
        print(f"[{'성공' if result.ok else '도구 오류'}] {result.content}")

    await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
