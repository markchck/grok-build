"""MCP(10단계)를 이 장의 동기 에이전트 루프(``docagent/agent.py``)에 배선하는 얇은 층.

``docagent/mcp_client.py``(10단계에서 그대로 가져온 파일, 한 글자도 고치지
않았다)의 ``McpToolsClient``는 전부 ``async def``다. 이 장의 ``agent.py``는
동기 제너레이터(``advance_run``)이고, FastAPI의 동기 라우트(스레드풀에서
실행됨, PROJECT-SPEC.md 9절 참고)에서 호출된다 — 그 스레드에는 실행 중인
이벤트 루프가 없으므로 ``asyncio.run()``으로 새 이벤트 루프를 하나씩 열어도
안전하다.

**연결을 요청마다 새로 연다**(재사용하지 않는다). ``mcp_client.py``
``connect()``의 docstring이 설명하듯 stdio 세션이 여는 anyio 취소 스코프는
그것을 연 태스크 안에서만 빠져나올 수 있다 — ``asyncio.run()``을 여러 번
부르면 매번 다른 이벤트 루프(다른 태스크)가 생기므로, 커넥션을 전역에 캐싱해
재사용하면 두 번째 호출에서 그 오류가 그대로 재현된다(직접 만들어 확인했다,
장 본문 6절 "실패 상황 실습" 참고). 그래서 이 모듈은 매 도구 호출마다 자식
프로세스를 새로 띄우고 끝나면 바로 닫는다 — 매 호출 지연(프로세스 기동
비용)이 있다는 대가를 그대로 감수한다. 학습용 데모의 범위에서는 이 대가가
"연결을 재사용하려다 크로스-태스크 취소 스코프 오류를 만나는 것"보다 낫다.
"""

from __future__ import annotations

import asyncio
from typing import Any

from docagent.config import Settings
from docagent.mcp_client import McpConnectionError, McpToolCallResult, McpToolsClient


async def _with_fresh_client(settings: Settings, coro_fn):
    client = McpToolsClient(settings=settings)
    try:
        await client.connect()
        return await coro_fn(client)
    finally:
        await client.close()


def list_mcp_tool_specs_sync(settings: Settings) -> list[dict[str, Any]]:
    """서버에 실제로 물어서 도구 목록을 가져온다(10단계 "도구 탐색"과 동일한 호출).

    서버가 지금 뜨지 않았거나 응답하지 않으면 빈 목록을 준다 — MCP 도구가
    이번 실행에서 그냥 빠지는 것이지 애플리케이션 전체가 죽지 않는다(3·8단계와
    같은 원칙: 외부 자원 실패가 곧 애플리케이션 실패는 아니다).
    """

    async def _list(client: McpToolsClient):
        return await client.list_tool_specs()

    try:
        return asyncio.run(_with_fresh_client(settings, _list))
    except McpConnectionError:
        return []


def call_mcp_tool_sync(name: str, arguments: dict[str, Any], settings: Settings) -> McpToolCallResult:
    """MCP 도구 하나를 호출한다. 연결 자체가 실패하면 ``McpConnectionError``를 그대로 올린다
    — 호출부(``agent.py``)가 이것을 ``tool_result(ok=False)``로 바꾼다(트레이싱 대상,
    최종 통합 과제 10번 "실패 원인을 트레이스로 확인" 시나리오로도 쓴다)."""

    async def _call(client: McpToolsClient):
        return await client.call_tool(name, arguments)

    return asyncio.run(_with_fresh_client(settings, _call))
