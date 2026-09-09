"""MCP 연결 관리 — 이 파일이 Host/Client 쪽의 핵심이다.

**Host 대 Client 대 Server의 책임**(장 본문 2절에서 자세히 설명한다):

- **Host**(``docagent`` 애플리케이션 전체, 특히 이 파일과 ``agent.py``)는
  "MCP 서버 프로세스를 언제 띄우고, 언제 끊고, 도구 결과를 모델 대화에
  어떻게 편입할지"를 결정한다.
- **Client**(``McpToolsClient`` 안에서 쓰는 ``mcp.ClientSession``)는 Host가
  만든 프로토콜 연결 하나를 대표한다. MCP 명세는 "하나의 Client는 하나의
  Server와 1:1로 연결된다"고 정하므로, 서버가 여러 개면 Client도 여러 개
  둔다(이 장은 서버 하나만 쓰므로 ``McpToolsClient`` 인스턴스도 하나다).
- **Server**(``../mcp_server/server.py``, 별도 프로세스)는 실제 도구 함수를
  실행한다. 이 파일은 그 함수의 이름조차 모른다 — 오직 ``list_tools()``가
  돌려주는 이름/설명/JSON 스키마와 ``call_tool()``의 결과만 안다.

3단계 로컬 함수 도구(``docagent.tools.run_tool``)와 비교하면, "함수를
실행하는" 코드는 완전히 다른 프로세스로 넘어갔고, 이 파일에 남은 것은
"그 프로세스와 통신하는 방법"뿐이다 — 직렬화(JSON), 프로세스 생명주기
관리, 타임아웃, 연결 실패 처리가 전부 이 파일이 새로 떠안은 책임이다.
3단계에는 이런 코드가 전혀 없었다(파이썬 함수 호출 한 줄이었다).
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError

from docagent.config import Settings, get_settings

# 이 서버(mcp_server/server.py)가 실제로 필요로 하는 환경 변수만 골라 넘긴다.
# **MCP 파이썬 SDK는 stdio 자식 프로세스에 기본적으로 안전한 최소 집합만
# 물려준다**(POSIX 기준 HOME/LOGNAME/PATH/SHELL/TERM/USER뿐이고, ``OPENAI_API_KEY``
# 같은 값은 저절로 전달되지 않는다 — mcp.client.stdio.get_default_environment()
# 소스로 확인, 장 본문 "참고 문서"). ``StdioServerParameters(env=...)``에 준
# 값은 이 기본 집합 위에 "추가"되는 것이지 대체가 아니다.
#
# 여기서 아래 이름만 골라 넘기는 것도 의도적이다 — 이 서버는 우리가 직접
# 작성해 신뢰하는 서버라서 필요한 값만 골라 넘기지만, 만약 서드파티가 배포한
# 서버를 붙이는 상황이었다면 이 목록에 API 키·토큰을 넣는 것 자체가 그
# 서버에게 그 비밀값을 그대로 노출하는 것과 같다(장 본문 6절 보안 참고).
_MCP_SERVER_ENV_FORWARD_KEYS = (
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "EMBEDDING_MODEL",
    "DOCAGENT_MILVUS_URI",
    "DOCAGENT_MILVUS_TOKEN",
    "DOCAGENT_MILVUS_COLLECTION",
    "DOCAGENT_MCP_SALES_CSV",
)


class McpConnectionError(RuntimeError):
    """서버 프로세스를 띄우거나, 세션을 초기화하거나, 세션이 끊긴 뒤 통신하려다
    실패했을 때 던진다. **도구 실행 자체의 실패**(예: sum_sales가 조건에 맞는
    행이 없어서 오류를 낸 경우)와는 다른 층위의 실패다 — 그건 McpToolCallResult
    (ok=False)로 정상적으로 돌아온다. 이 예외는 "MCP 세션 자체가 문제"라는 뜻이다.
    """


@dataclass(frozen=True)
class McpToolCallResult:
    """call_tool()의 결과. 도구가 실행됐지만 실패했을 수도 있으므로(ok=False)
    이 경우는 예외로 던지지 않는다 — 모델에게 도구 결과 메시지로 돌려줘서
    스스로 인자를 고치거나 사용자에게 설명할 기회를 주기 위해서다(3단계와
    같은 설계 원칙, docagent.tools.run_tool 참고).
    """

    ok: bool
    content: str


class McpToolsClient:
    """하나의 MCP 서버(문서 검색·CSV 조회 서버)에 대한 연결을 관리한다.

    이 클래스는 연결이 끊기면(서버 프로세스가 죽었거나, 파이프가 깨졌거나)
    **자동으로 계속 재시도하지 않는다.** 다음 호출 시점에 최대
    ``max_reconnect_attempts``번만 재연결을 시도하고, 그래도 안 되면
    McpConnectionError를 올린다 — 무한정 재시도하며 사용자를 기다리게 하지
    않기 위해서다(PROJECT-SPEC.md 7장의 실행 한도 정신과 같다).
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        max_reconnect_attempts: int = 1,
    ) -> None:
        self._settings = settings or get_settings()
        self._max_reconnect_attempts = max_reconnect_attempts
        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> None:
        """서버 프로세스를 새로 띄우고 MCP 세션을 초기화한다.

        ``session.initialize()``에만 ``asyncio.wait_for``로 타임아웃을 건다
        — 서버 프로세스가 뜨긴 했는데 초기화 응답을 영영 보내지 않는 경우까지
        대비해야 하기 때문이다. ``stdio_client``/``ClientSession``이 여는
        anyio 태스크 그룹(취소 스코프)은 **반드시 그것을 연 태스크 안에서
        그대로 빠져나와야 한다** — ``asyncio.wait_for``로 그 진입 과정 자체를
        감싸면 내부에 별도 태스크가 생겨, 나중에 다른 태스크(예: 이 클래스를
        쓰는 호출부)에서 종료하려 할 때
        ``RuntimeError: Attempted to exit cancel scope in a different task
        than it was entered in``으로 깨진다(직접 재현해 확인했다). 그래서
        진입 자체는 감싸지 않고 ``initialize()`` 호출 하나에만 타임아웃을
        적용한다.
        """
        stack = AsyncExitStack()
        try:
            forwarded_env = {
                key: os.environ[key] for key in _MCP_SERVER_ENV_FORWARD_KEYS if key in os.environ
            }
            params = StdioServerParameters(
                command=self._settings.mcp_server_command,
                args=self._settings.mcp_server_args,
                env=forwarded_env,
            )

            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(
                ClientSession(read, write, read_timeout_seconds=self._settings.mcp_call_timeout_seconds)
            )
            await asyncio.wait_for(
                session.initialize(), timeout=self._settings.mcp_connect_timeout_seconds
            )
        except Exception as exc:
            await stack.aclose()
            self._exit_stack = None
            self._session = None
            raise McpConnectionError(
                f"MCP 서버('{self._settings.mcp_server_command} "
                f"{' '.join(self._settings.mcp_server_args)}')에 연결할 수 없다: {exc}"
            ) from exc

        self._exit_stack = stack
        self._session = session

    async def close(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._session = None

    async def _ensure_connected(self) -> ClientSession:
        if self._session is not None:
            return self._session
        await self.connect()
        assert self._session is not None
        return self._session

    async def _drop_session(self) -> None:
        """세션이 더 이상 쓸 수 없다고 판단됐을 때 정리한다(재연결은 다음
        호출에서 한다).

        exit_stack을 **반드시 여기서 닫아야 한다** — 참조만 버리고 닫지
        않으면, 파이썬이 나중에(가비지 컬렉션 시점에) 이 스택 안의
        stdio_client 제너레이터를 대신 정리하려 하는데, 그 시점은 이 스택을
        연 태스크가 아닌 전혀 다른 태스크일 수 있다. 그러면 anyio가
        ``RuntimeError: Attempted to exit cancel scope in a different task
        than it was entered in``을 던지며, 이게 "잡히지 않는 백그라운드
        예외"로 새어 나가 다른 동시 작업(예: 재연결 시도)까지 취소시키는
        것을 직접 재현해 확인했다. 그래서 죽은 파이프에 대고 정리하다 나는
        예외는 무시하되(``ClosedResourceError``, ``BrokenPipeError`` 등 —
        이미 죽었으니 당연히 날 수 있다), 닫으려는 시도 자체는 반드시 한다.
        """
        stack, self._exit_stack = self._exit_stack, None
        self._session = None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:  # noqa: BLE001 - 이미 죽은 연결을 정리하다 나는 오류는 무시한다
                pass

    async def list_tool_specs(self) -> list[dict[str, Any]]:
        """서버의 도구 목록을 OpenAI 호환 tools 스펙으로 바꿔 돌려준다.

        이것이 "도구 탐색"이다 — docagent는 이 서버가 어떤 도구를 갖고
        있는지 코드에 미리 적어 두지 않는다. 매번 서버에게 물어서 이름·설명·
        JSON 스키마를 그대로 모델에게 전달할 tools 배열로 바꾼다. MCP의
        ``Tool.input_schema``는 이미 JSON 스키마 형태라 OpenAI의
        ``function.parameters``에 그대로 옮겨 쓸 수 있다.
        """
        for attempt in range(self._max_reconnect_attempts + 1):
            session = await self._ensure_connected()
            try:
                result = await session.list_tools()
            except (MCPError, Exception) as exc:  # noqa: BLE001 - 세션 계층 오류는 전부 재연결 대상
                await self._drop_session()
                if attempt >= self._max_reconnect_attempts:
                    raise McpConnectionError(f"도구 목록을 가져오지 못했다: {exc}") from exc
                continue
            return [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.input_schema,
                    },
                }
                for tool in result.tools
            ]
        raise McpConnectionError("도구 목록을 가져오지 못했다(재시도 소진).")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolCallResult:
        """도구 하나를 호출한다.

        - 도구가 존재하고 실행됐지만 실패한 경우(서버가 ``ToolError``를 던진
          경우 등): ``McpToolCallResult(ok=False, content=...)``를 정상
          반환한다. 모델이 이 내용을 읽고 스스로 다음 행동을 정한다.
        - 세션이 끊겼거나 요청이 타임아웃된 경우: ``McpConnectionError``를
          올린다. 이건 도구 로직의 실패가 아니라 "서버와 말이 안 통한다"는
          뜻이라 agent.py가 다른 방식(재시도 또는 중단)으로 다뤄야 한다.
        """
        for attempt in range(self._max_reconnect_attempts + 1):
            session = await self._ensure_connected()
            try:
                result = await session.call_tool(name, arguments)
            except MCPError as exc:
                # 타임아웃을 포함한 프로토콜 계층 오류. 세션을 계속 믿을 수
                # 없으므로 버리고, 남은 시도 횟수가 있으면 재연결 후 다시 시도한다.
                await self._drop_session()
                if attempt >= self._max_reconnect_attempts:
                    raise McpConnectionError(f"'{name}' 도구 호출이 실패했다(연결/타임아웃): {exc}") from exc
                continue
            except Exception as exc:  # noqa: BLE001 - 파이프 끊김 등 예상 밖 전송 오류
                await self._drop_session()
                if attempt >= self._max_reconnect_attempts:
                    raise McpConnectionError(f"'{name}' 도구 호출 중 연결이 끊겼다: {exc}") from exc
                continue

            text_parts = [c.text for c in result.content if getattr(c, "text", None)]
            content = "\n".join(text_parts) if text_parts else json.dumps(
                result.structured_content or {}, ensure_ascii=False
            )
            return McpToolCallResult(ok=not result.is_error, content=content)

        raise McpConnectionError(f"'{name}' 도구 호출에 실패했다(재시도 소진).")
