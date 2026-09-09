# 10단계. MCP로 외부 도구 연결하기

## 1. 학습 목표와 완성 모습

3단계에서 만든 도구(`sum_sales`, `lookup_sales_rows`)는 에이전트와 같은 파이썬
프로세스 안에 있었다. `agent.py`가 `tools.py`의 함수를 직접 호출했다 — 프로세스
경계도, 네트워크도, 직렬화도 없었다. 5단계에서 만든 하이브리드 검색도 마찬가지로
`docagent` 패키지 안의 함수였다.

이 장은 그 함수들을 **별도 프로세스**로 옮긴다. Anthropic이 만들고 지금은
Model Context Protocol(MCP)이라는 이름의 개방형 표준으로 공개된 프로토콜[^mcp-what]을
써서, "문서 검색·CSV 조회 도구를 제공하는 서버"와 "그 서버에 접속해 도구를 쓰는
에이전트"를 완전히 다른 두 파이썬 패키지, 다른 두 프로세스로 분리한다.

완성하면 이렇게 동작한다.

```
$ python -m scripts.check_mcp_connection
MCP 서버에 연결 시도: python -m mcp_server.server
[성공] 도구 3개를 찾았다:
  - search_docs: 사내 분기 리포트 문서에서 질의와 의미적으로 가까운 조각을 찾는다.
  - sum_sales: sales.csv에서 조건에 맞는 매출·판매수량 합계를 계산한다.
  - lookup_sales_rows: sales.csv에서 조건에 맞는 판매 기록 원본 행을 최대 limit개까지 반환한다.
```

에이전트 쪽에서는 이 도구들이 원래부터 로컬 함수였던 것처럼 모델에게 전달된다.

```
사용자: 2분기 노트북 매출 합계와 감소 원인을 알려줘

[status]     planning   MCP 서버의 도구 목록을 확인하는 중
[status]     planning   1단계: 모델에게 다음 행동을 묻는다
[tool_call]  id=call_1  name=sum_sales      args={"product":"노트북","quarter":"Q2"}
[tool_result] id=call_1 ok=true  summary={"total_units":4588,"total_revenue":2752800000,...}
[status]     planning   2단계: 모델에게 다음 행동을 묻는다
[tool_call]  id=call_2  name=search_docs    args={"query":"노트북 2분기 매출 둔화 원인"}
[tool_result] id=call_2 ok=true  summary={"matched_count":2,"hits":[...]}
[token]      2분기 노트북 매출 합계는 2,752,800,000원이다. 매출 둔화의 주된 원인은...
[done]       finish_reason=stop
```

겉모습(SSE 이벤트, 에이전트 루프 모양)은 3단계와 거의 같다. 달라진 것은 `tool_call`
에서 `tool_result`로 넘어가는 그 한 화살표 안쪽이다 — 3단계에서는 파이썬 함수 호출
한 줄이었던 자리가, 이 장에서는 별도 프로세스와 주고받는 프로토콜 요청/응답으로
바뀐다. 이 장은 정확히 그 안쪽을 연다.

이 장이 다루는 범위: MCP의 Host·Client·Server 역할 분담, Tools·Resources·Prompts
세 원시 개념의 차이, 로컬 함수 Tool과 MCP Tool의 차이, 최소 MCP 서버 구현, 문서
검색·CSV 조회 도구를 MCP 서버로 옮기기, 전송 방식(stdio vs HTTP)의 비교와 선택,
도구 탐색, 인증·권한·보안, 타임아웃과 연결 실패 처리.

실습 결과: 별도 MCP 서버(`code/step10_mcp/mcp_server/`)의 검색·조회 도구를 쓰는
에이전트(`code/step10_mcp/docagent/`). 완료 기준: 도구 탐색과 실제 호출을 확인하고,
서버 연결 실패(죽어 있음·도중에 끊김·응답이 느림)를 각각 처리한다.

## 2. 핵심 개념

### 2.1 왜 도구를 별도 프로세스로 빼는가

3단계 방식(로컬 함수 도구)은 한 팀이 에이전트도 도구도 모두 만들 때는 충분하다.
문제는 다음과 같은 상황에서 생긴다.

- 문서 검색 도구는 검색팀이 관리하는 Milvus 컬렉션에 접근해야 하는데, 에이전트를
  만드는 팀과 검색팀이 다르다.
- 같은 CSV 조회 도구를 여러 에이전트(이 문서 분석 에이전트, 다른 사내 챗봇 등)가
  함께 쓰고 싶다.
- 도구 쪽 배포 주기(예: CSV 스키마 변경, Milvus 컬렉션 마이그레이션)를 에이전트
  코드 배포와 분리하고 싶다.

MCP는 이런 상황을 위해 "도구를 가진 쪽"과 "도구를 쓰는 쪽" 사이에 표준화된
클라이언트-서버 프로토콜을 둔다. 비유하자면 REST API와 비슷한 위치에 있다 —
다만 REST API는 사람이 설계한 임의의 엔드포인트 모음인 반면, MCP는 "도구 목록을
물어본다(discovery)", "도구를 호출한다", "정적 데이터를 읽는다(리소스)", "미리
만든 프롬프트 템플릿을 가져온다" 같은 상호작용 자체를 표준 메서드로 고정해서,
클라이언트 쪽 코드를 서버마다 새로 짜지 않아도 되게 한다.

### 2.2 Host, Client, Server — 누가 무엇을 하는가

MCP 명세는 세 가지 역할을 구분한다[^mcp-python-sdk-readme].

| 역할 | 이 장에서의 실체 | 하는 일 |
| --- | --- | --- |
| **Host** | `code/step10_mcp/docagent/` 애플리케이션 전체(특히 `app.py`) | MCP 서버 **프로세스를 언제 띄우고 언제 끊을지** 결정한다. 여러 서버에 붙을 수 있고, 그 연결들을 조율한다. 사용자를 대신해 "이 도구를 실행해도 되는지"를 최종 판단하는 것도 Host의 자리다(6절 보안 참고). |
| **Client** | `docagent/mcp_client.py`의 `McpToolsClient`가 감싸는 `mcp.ClientSession` | Host가 만든 프로토콜 연결 **하나**를 대표한다. MCP는 "하나의 Client는 하나의 Server와 1:1로 연결된다"고 정한다 — 서버가 여러 개면 Client도 여러 개(이 장은 서버가 하나뿐이라 `McpToolsClient` 인스턴스도 하나다). |
| **Server** | `mcp_server/server.py`, 별도 프로세스 | 실제 도구 함수를 실행하고, 리소스를 읽고, 프롬프트를 만들어 돌려준다. Client가 보낸 요청 하나하나에 응답할 뿐, 대화 흐름이나 모델 호출은 전혀 모른다. |

3단계 로컬 함수 도구와 나란히 놓고 "무엇이 프로세스 경계를 넘어갔는가"를 보면
이렇다.

```
3단계 (로컬 함수 도구)
┌─────────────────────────────────────┐
│ docagent 프로세스                     │
│                                       │
│  agent.py --함수 호출--> tools.py     │
│  (모델이 만든 이름/인자를 그대로       │
│   파이썬 함수 인자로 넘긴다)           │
└─────────────────────────────────────┘

10단계 (MCP 도구)
┌───────────────────────┐  MCP 프로토콜   ┌───────────────────────┐
│ docagent 프로세스(Host) │  (stdio 또는   │ mcp_server 프로세스     │
│                        │   HTTP)        │ (Server)               │
│  agent.py              │◄──────────────►│  server.py             │
│    --Client(세션)-->   │  JSON-RPC 형태  │    --함수 호출-->       │
│    call_tool(name,args) │  요청/응답     │    sales_data.py 등     │
└───────────────────────┘                └───────────────────────┘
```

3단계에서는 "함수 호출"이었던 것이, 이 장에서는 "직렬화(파이썬 dict를 JSON으로) →
전송(파이프 또는 HTTP) → 역직렬화 → 함수 호출 → 결과 직렬화 → 전송 → 역직렬화"로
바뀐다. `docagent/mcp_client.py`가 이 왕복 전체를 감싸고 있다 — 3단계에는 이만한
분량의 코드가 아예 없었다.

### 2.3 Tools, Resources, Prompts — 셋 다 만들어야 배울 수 있는 차이

MCP가 서버에 정의하는 원시 개념(Primitive)은 세 가지다[^mcp-python-sdk-readme].
**흔한 오해는 셋 다 Tool로 만드는 것**이다 — Tool 하나로도 "동작"은 만들 수 있지만,
그러면 모델이 매번 "이 정적인 정보를 가져올지 말지"까지 판단해야 하고, 호스트
애플리케이션은 "모델이 부르지 않으면 이 데이터가 컨텍스트에 아예 안 들어간다"는
제약을 떠안는다.

| 원시 개념 | 누가 "쓸지 결정"하는가 | 성격 | 이 장의 예 |
| --- | --- | --- | --- |
| **Tool** | 모델(대화 맥락을 보고 호출 여부·인자를 스스로 판단) | 부수효과가 있을 수 있는 동작. 입력 스키마가 있다 | `search_docs`, `sum_sales`, `lookup_sales_rows` |
| **Resource** | 호스트/클라이언트(또는 그 뒤의 사용자)가 URI로 직접 읽는다. 모델의 "호출 판단"이 끼지 않는다 | 부수효과 없는 데이터. 파일 첨부와 비슷하다 | `data://sales-schema`(정적 리소스), `docs://{doc_id}`(URI 템플릿으로 문서 원문 읽기) |
| **Prompt** | 클라이언트(또는 사용자)가 미리 정의된 템플릿 중 하나를 고른다 | 재사용 가능한 대화 시작 지시문. 서버가 매개변수를 받아 텍스트를 만들어 준다 | `analyze_sales(product, quarter)` — "이 제품·분기 매출을 분석해라"는 지시문을 만들어 준다 |

`mcp_server/server.py`는 세 가지를 각각 실제로 등록한다.

```python
# mcp_server/server.py (발췌)
@mcp.tool()
def search_docs(query: str, top_k: int = 5) -> dict:
    """사내 분기 리포트 문서에서 질의와 의미적으로 가까운 조각을 찾는다."""
    ...

@mcp.resource("data://sales-schema", mime_type="text/plain")
def sales_schema_resource() -> str:
    """정적 리소스: sales.csv의 컬럼 구성 설명."""
    return csv_schema_description()

@mcp.resource("docs://{doc_id}", mime_type="text/markdown")
def doc_resource(doc_id: str) -> str:
    """리소스 템플릿: 문서 원문 전체를 doc_id로 직접 읽는다."""
    ...

@mcp.prompt()
def analyze_sales(product: str, quarter: str) -> str:
    """제품·분기를 지정해 매출 변화 원인 분석을 요청하는 프롬프트 템플릿을 만든다."""
    ...
```

`sales_schema_resource`를 Tool로 만들 수도 있었다(`get_csv_schema()`라는 이름의
도구). 그렇게 하지 않은 이유: 이 정보는 인자도 없고 부수효과도 없고, 대화가
시작될 때 호스트가 그냥 컨텍스트에 붙여도 되는 정보다. 매번 모델이 "이 도구를
호출할지" 판단하게 만드는 것은 낭비다 — 판단에 걸리는 토큰과 지연 시간, 그리고
모델이 호출을 깜빡할 가능성까지 떠안는다. 반대로 `search_docs`는 "무엇을 찾을지"가
질문마다 다르고 모델의 판단이 꼭 필요하므로 Tool이 맞다.

`doc_resource`와 `search_docs`의 차이도 마찬가지 논리다. `docs://{doc_id}`는
"이미 `doc_id`를 아는 상태에서 원문 전체를 그대로 가져오기"(5단계 근거 링크를
클릭해 원문을 여는 상황과 같은 모양)라 정해진 대상을 그대로 가져오는 동작
(Resource)이고, `search_docs`는 "무엇을 찾을지 모르는 상태에서 질의로 후보를
찾기"라 모델의 판단이 필요한 동작(Tool)이다.

### 2.4 로컬 함수 Tool과 MCP Tool의 차이

모델 입장에서는 둘이 구분되지 않는다 — 어느 쪽이든 `tools` 배열에 담긴
이름/설명/JSON 스키마만 보고 "이 이름을, 이런 인자로 부르고 싶다"는 텍스트를
만들 뿐이다(3단계 2.2절과 동일). 차이는 전부 **애플리케이션 쪽**에 있다.

| | 3단계: 로컬 함수 Tool | 10단계: MCP Tool |
| --- | --- | --- |
| 도구 명세는 어디 있나 | `tools.py`의 `TOOL_SPECS`(코드에 직접 작성) | 서버가 `list_tools()` 응답으로 알려준다(코드에 미리 적지 않는다) |
| 실행 주체 | 같은 프로세스의 파이썬 함수 | 별도 프로세스. 프로토콜 요청/응답 |
| 실행 실패의 의미 | 파이썬 예외 하나만 신경 쓰면 된다 | "도구가 실행됐지만 실패"와 "서버와 통신 자체가 안 됨"을 구분해야 한다(6절) |
| 배포 단위 | 에이전트와 함께 배포 | 독립적으로 배포·재시작·확장 가능 |
| 신뢰 경계 | 없음(같은 코드베이스) | 있음 — 서버가 우리가 안 만든 코드일 수 있다(6절 보안) |

### 2.5 이 장에서 처음 겪는 실패 유형

3단계의 실패 유형(잘못된 인자, 반복 호출, 실행 시간 초과)은 여전히 유효하지만,
이 장은 그 위에 **연결 계층의 실패**를 새로 추가한다 — 파이썬 함수 호출은 "성공
아니면 예외"뿐이지만, 프로세스 간 통신은 "상대가 아예 없음", "있었는데 없어짐",
"있긴 한데 대답이 없음"까지 구분해야 한다. 이 장의 완료 기준이기도 하다(6절에서
세 가지 모두 실제로 재현하고 처리한다).

## 3. 실습 준비

### 3-1. MCP 파이썬 SDK 버전 확인

이 장을 쓰는 시점(2026-09-09)에 `pip install mcp`가 설치하는 버전을 직접 확인했다.

```bash
python3 -m venv /tmp/mcp-check && /tmp/mcp-check/bin/pip install "mcp[cli]"
/tmp/mcp-check/bin/pip show mcp mcp-types
# Name: mcp          Version: 2.2.0
# Name: mcp-types    Version: 2.2.0
```

**중요한 사실 하나**: `mcp` 2.x부터 `FastMCP`가 `MCPServer`로 이름이 바뀌었다.
과거(1.x) 코드에서 흔히 보이는 `from mcp.server.fastmcp import FastMCP`를 이
버전으로 그대로 실행하면 다음 오류가 난다(실제로 재현했다).

```
ModuleNotFoundError: No module named 'mcp.server.fastmcp'. This is mcp 2.x,
where FastMCP was renamed to MCPServer (from mcp.server.mcpserver import
MCPServer) and other APIs changed; see the migration guide at
https://py.sdk.modelcontextprotocol.io/v2/migration/#fastmcp-renamed-to-mcpserver
or pin 'mcp<2' to keep running v1 code.
```

이 튜토리얼은 지금 `pip install mcp`가 실제로 설치하는 버전(2.x)을 기준으로
썼다. `from mcp.server import MCPServer` 또는 `from mcp.server.mcpserver import
MCPServer` 둘 다 동작하는 것을 확인했다 — 이 장의 코드는 후자를 쓴다. 이전
버전(1.x)의 `FastMCP` 기반 예제를 인터넷에서 보게 되면, 그 예제가 몇 버전
기준인지 먼저 확인한다. `mcp<2`로 고정하면 1.x API를 계속 쓸 수 있다고 SDK
자신도 안내한다[^mcp-v2-note].

이 SDK가 구현하는 MCP 프로토콜 버전(스펙 날짜)도 확인했다.

```python
>>> import mcp.types as t
>>> t.LATEST_PROTOCOL_VERSION
'2026-07-28'
```

패키지 안에는 `2025-11-25`, `2026-07-28` 두 스펙 버전에 대응하는 모듈
(`mcp_types/_v2025_11_25/`, `mcp_types/_v2026_07_28/`)이 함께 들어 있다 — 하위
호환을 위해 이전 스펙 버전과도 대화할 수 있다는 뜻이다.

### 3-2. 폴더 구조

이 장은 PROJECT-SPEC.md의 표준 배치를 그대로 따르되, **패키지를 하나 더
추가한다.**

```
code/step10_mcp/
├── mcp_server/          # MCP 서버 — 별도 프로세스로 띄운다. docagent를 import하지 않는다
│   ├── config.py
│   ├── store.py          # Milvus dense 전용 스키마(4-2절에서 이유 설명)
│   ├── search.py
│   ├── sales_data.py      # 3단계 tools.py와 같은 계산 로직
│   └── server.py            # MCPServer 정의: tools/resources/prompts
├── docagent/            # 에이전트(Host + Client) — 이전 단계와 같은 이름
│   ├── config.py
│   ├── llm.py
│   ├── events.py
│   ├── mcp_client.py       # 이 장에서 새로 생기는 연결 관리 코드
│   ├── agent.py
│   └── app.py
├── static/
├── data/{docs,sales.csv}
├── scripts/{make_sample_data.py, ingest_docs.py, check_mcp_connection.py}
└── tests/
```

`mcp_server`와 `docagent`는 서로의 이름조차 모른다 — 어느 쪽 코드에도
`import mcp_server` 또는 `import docagent`가 반대쪽 패키지를 향해 나타나지
않는다. 통신 통로는 오직 MCP 프로토콜뿐이다.

### 3-3. 환경 변수

PROJECT-SPEC.md 2절의 기존 이름은 그대로 쓴다. 이 장에서 새로 추가하는 이름
넷은 `DOCAGENT_` 접두사를 붙였다(PROJECT-SPEC.md 2절 규칙, 그리고 이 규칙에
따라 PROJECT-SPEC.md 본문에도 추가해 두었다).

```bash
# MCP 서버 연결 (docagent 쪽이 읽는다 — 어떤 명령으로 mcp_server를 띄울지)
DOCAGENT_MCP_SERVER_COMMAND=python
DOCAGENT_MCP_SERVER_ARGS=-m,mcp_server.server
DOCAGENT_MCP_CONNECT_TIMEOUT_SECONDS=5
DOCAGENT_MCP_CALL_TIMEOUT_SECONDS=20

# mcp_server 쪽이 읽는다 — CSV 조회 도구가 읽을 파일 경로
DOCAGENT_MCP_SALES_CSV=./data/sales.csv
```

`DOCAGENT_MILVUS_URI`, `OPENAI_BASE_URL` 등 기존 이름은 **두 프로세스 모두**
읽는다 — 이름은 같지만(PROJECT-SPEC.md가 정한 이름이므로), 각 프로세스가
독립적으로 자기 환경 변수에서 읽는다(`mcp_server/config.py`와
`docagent/config.py`는 서로 다른 파일이고 서로를 import하지 않는다). 이 값을
자식 프로세스(mcp_server)에 실제로 전달하는 방법은 3-4절에서 다룬다 — 기본적으로
는 전달되지 않는다.

### 3-4. 패키지 설치

```bash
cd code/step10_mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt`에 `mcp[cli]>=2.0,<3`를 추가했다. 이전 단계 패키지
(`pymilvus[milvus-lite]`, `openai`, `fastapi` 등)는 PROJECT-SPEC.md 10절의
동일한 범위를 그대로 쓴다. MCP 클라이언트 코드가 `async def`이므로 이 장부터
테스트에 `pytest-asyncio`도 추가했다(이전 단계에는 없었다).

## 4. 단계별 구현

### 4-1. 최소 MCP 서버

먼저 이 장의 개념을 익히기 위한 독립 예제로, 도구 하나·리소스 하나·프롬프트
하나짜리 서버를 만들어 본다(누적 프로젝트에 적용하기 전에).

```python
# 독립 예제 (개념 확인용, 누적 프로젝트 코드 아님)
from mcp.server.mcpserver import MCPServer

mcp = MCPServer(name="hello-mcp")

@mcp.tool()
def add(a: int, b: int) -> int:
    """두 정수를 더한다."""
    return a + b

@mcp.resource("greeting://{name}")
def greeting(name: str) -> str:
    """인사말 리소스."""
    return f"hello {name}"

@mcp.prompt()
def ask_sum(a: int, b: int) -> str:
    return f"{a}와 {b}를 더해줘"

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

타입 힌트(`a: int, b: int`)와 docstring만으로 JSON 스키마와 설명이 자동으로
만들어진다 — 직접 JSON Schema를 작성했던 3단계의 `TOOL_SPECS`와 비교되는
지점이다. 이 서버를 실제로 띄우고, 별도 파이썬 프로세스(클라이언트)에서
`stdio_client` + `ClientSession`으로 접속해 도구 목록·호출·리소스 읽기·프롬프트
가져오기를 전부 실제로 확인했다(장 하단 "검증 상태" 참고).

```python
# 독립 예제의 클라이언트 쪽
import asyncio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

async def main():
    params = StdioServerParameters(command="python", args=["server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([t.name for t in tools.tools])   # ['add']
            result = await session.call_tool("add", {"a": 3, "b": 4})
            print(result.content[0].text)            # '7'

asyncio.run(main())
```

`StdioServerParameters(command=..., args=...)`가 **Host가 서버 프로세스를 어떻게
띄울지**를 정하는 자리다. `stdio_client`가 그 프로세스를 실제로 실행하고
표준입출력을 파이프로 연결한다. `ClientSession`이 그 파이프 위에서 MCP
프로토콜(JSON-RPC 형태의 메시지)을 주고받는 Client다.

> SDK에는 이 두 단계(전송 열기 + 세션 초기화)를 하나로 묶은 더 간단한
> `mcp.Client`도 있다(`async with Client("http://...") as client:` 또는
> `Client(StdioServerParameters(...))` 형태로 쓴다). 이 장은 재연결·타임아웃
> 로직을 직접 제어해야 해서 `ClientSession`을 한 단계 더 낮은 수준에서 쓰지만,
> 커스텀 재연결이 필요 없다면 `mcp.Client` 쪽이 더 짧다.

### 4-2. 문서 검색 도구를 MCP 서버로 옮기기 — 그리고 하이브리드를 옮기지 않은 이유

5단계 하이브리드(Dense+Sparse) 검색을 그대로 옮기려 했다. 그런데
VERIFIED-FINDINGS.md 9절이 이미 기록한 제약이 이 장에 정확히 걸린다:

> Milvus Lite에서 sparse(BM25 Function) 필드가 있는 컬렉션은 **적재한 프로세스와
> 다른 프로세스에서 읽으면** 다음 오류로 실패하는 경우가 있다.
> `MilvusException: (code=1, message=vector column must be FixedSizeList, got binary)`

MCP 서버는 정의상 적재 스크립트(`scripts/ingest_docs.py`)와 다른 프로세스다 —
"별도 프로세스로 도구를 분리한다"는 이 장의 핵심 자체가, 하필 VERIFIED-FINDINGS가
경고한 조건("다른 프로세스에서 읽으면")을 정확히 만든다. VERIFIED-FINDINGS.md는
이 오류의 원인을 "특정하지 못했다"고 기록했으므로, 원인을 아는 것처럼 이 장에
다시 쓰지 않는다.

그래서 `mcp_server/store.py`는 **dense 필드만** 쓰는 스키마로 만든다(sparse
필드도, BM25 Function도 없다). dense 전용 컬렉션이 서로 다른 프로세스에서
안정적으로 동작하는 것은 4단계에서 이미 확인됐고, 이 장 집필 중에도 별도로
재현해 재확인했다(적재 프로세스에서 컬렉션을 만들고, 완전히 다른 파이썬
프로세스에서 `client.load_collection()` 후 검색 — 성공).

```python
# mcp_server/store.py (발췌) — sparse 필드가 없다
def _build_schema(client: MilvusClient, dense_dim: int):
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(PK_FIELD, DataType.VARCHAR, is_primary=True, max_length=_PK_MAX_LENGTH)
    schema.add_field(DENSE_FIELD, DataType.FLOAT_VECTOR, dim=dense_dim)
    schema.add_field(TEXT_FIELD, DataType.VARCHAR, max_length=_TEXT_MAX_LENGTH)
    # sparse 필드, BM25 Function 없음 — 이유는 위 본문 참고
    ...
```

하이브리드 검색을 실제로 MCP 서버 뒤에 두고 싶다면, VERIFIED-FINDINGS.md 9절의
권고대로 Milvus standalone(Docker)을 쓴다. standalone에서도 이 문제가
재현되는지는 이 튜토리얼 어디에서도 아직 확인하지 못했다 — `[확인 필요]`로
남긴다.

검색 도구 자체는 5단계 `dense_search`와 거의 동일하되, 임베딩 호출을
`docagent.llm`이 아니라 `mcp_server` 안의 최소 함수로 따로 둔다(2-2절에서 설명한
대로, 두 패키지는 서로 import하지 않으므로).

```python
# mcp_server/server.py (발췌)
@mcp.tool()
def search_docs(query: str, top_k: int = 5) -> dict:
    """사내 분기 리포트 문서에서 질의와 의미적으로 가까운 조각을 찾는다.

    매출 변화의 원인, 배경, 코멘트처럼 CSV 수치에 없는 서술형 정보를 찾을 때
    쓴다. Dense(의미) 검색만 지원한다.
    """
    if not query.strip():
        raise ToolError("query가 비어 있다. 검색어를 입력해달라.")
    top_k = max(1, min(top_k, 20))
    try:
        hits = dense_search(query, limit=top_k)
    except EmbeddingError as exc:
        raise ToolError(f"문서 검색을 위한 임베딩 호출에 실패했다: {exc}") from exc
    except Exception as exc:
        raise ToolError(f"문서 검색 중 오류가 발생했다: {exc}") from exc
    if not hits:
        return {"matched_count": 0, "hits": []}
    return {"matched_count": len(hits), "hits": hits}
```

`mcp.server.mcpserver.exceptions.ToolError`가 3단계의 `tools.ToolError`와 같은
자리에 있다 — "예상한 실패"를 표시하는 예외다. 실제로 SDK 소스에서 그 동작을
확인했다.

> `ToolError`를 던지면: 호출이 `is_error=True`로 돌아오고, `content`에 **그
> 메시지 그대로**가 담겨 모델이 읽는다. 서버는 INFO 레벨로만 기록하고 트레이스백을
> 남기지 않는다.
> `ToolError`가 아닌 다른 예외(버그로 인한 크래시)가 새면: 클라이언트는
> `"Error executing tool <name>"`이라는 **일반화된 메시지만** 받는다. 원래 예외
> 메시지는 클라이언트로 새지 않는다 — 서버는 ERROR 레벨로 전체 트레이스백을
> 기록한다.

이 구분을 직접 재현해 확인했다(4-3절 코드로, 그리고 장 하단 "검증 상태" 참고).
`sum_sales`/`lookup_sales_rows`도 같은 패턴을 쓴다 — 3단계 `tools.py`의 로직을
`mcp_server/sales_data.py`로 그대로 옮기고, `server.py`가 `SalesDataError`를
잡아 `ToolError`로 다시 던진다.

```python
# mcp_server/server.py (발췌)
@mcp.tool(name="sum_sales")
def sum_sales_tool(product: str | None = None, region: str | None = None, quarter: str | None = None) -> dict:
    """sales.csv에서 조건에 맞는 매출·판매수량 합계를 계산한다."""
    try:
        args = SumSalesArgs(product=product, region=region, quarter=quarter)
    except ValidationError as exc:
        raise ToolError(f"입력 인자가 올바르지 않다: {exc}") from exc
    try:
        return sum_sales(args)
    except SalesDataError as exc:
        raise ToolError(str(exc)) from exc
```

`@mcp.tool(name="sum_sales")`처럼 `name=`을 명시했다 — 파이썬 함수 이름
(`sum_sales_tool`)과 도구 이름(`sum_sales`)을 분리했다. 함수 이름이
`mcp_server.sales_data.sum_sales`(가져온 계산 함수)와 겹치면 안 되기 때문이다.
모델에게 보이는 도구 이름은 여전히 3단계와 같은 `sum_sales`다.

### 4-3. Host/Client 쪽: 도구 탐색과 호출

`docagent/mcp_client.py`가 이 장의 핵심 신규 코드다. `McpToolsClient` 클래스
하나가 "MCP 서버 프로세스를 언제 띄우고, 도구를 어떻게 탐색하고, 어떻게
호출하고, 연결이 끊기면 어떻게 할지"를 전부 감싼다.

**도구 탐색** — 3단계처럼 `TOOL_SPECS`를 코드에 미리 적지 않는다. 매 세션 시작
시 서버에게 물어서, `Tool.input_schema`(이미 JSON Schema 형태)를 OpenAI 호환
`tools` 배열로 그대로 옮긴다.

```python
# docagent/mcp_client.py (발췌)
async def list_tool_specs(self) -> list[dict[str, Any]]:
    session = await self._ensure_connected()
    result = await session.list_tools()
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
```

**환경 변수 전달 — 기본적으로 상속되지 않는다.** 자식 프로세스(MCP 서버)를
띄울 때, MCP 파이썬 SDK는 **부모 프로세스의 전체 환경 변수를 물려주지
않는다.** SDK 소스(`mcp/client/stdio.py`의 `get_default_environment()`)를 직접
확인한 결과, POSIX에서 기본으로 상속하는 변수는 `HOME`, `LOGNAME`, `PATH`,
`SHELL`, `TERM`, `USER`뿐이다 — 명시적으로 "안전하다고 판단한 최소 집합"만
넘긴다는 주석이 소스에 달려 있다. `OPENAI_API_KEY` 같은 값은 저절로 전달되지
**않는다.** 실제로 이걸 빠뜨렸다가 `mcp_server`가 기본값(`localhost:8000`)으로
임베딩을 호출하려다 연결 실패하는 것을 이 장을 집필하며 직접 겪었다.

```python
# docagent/mcp_client.py (발췌)
_MCP_SERVER_ENV_FORWARD_KEYS = (
    "OPENAI_BASE_URL", "OPENAI_API_KEY", "EMBEDDING_MODEL",
    "DOCAGENT_MILVUS_URI", "DOCAGENT_MILVUS_TOKEN", "DOCAGENT_MILVUS_COLLECTION",
    "DOCAGENT_MCP_SALES_CSV",
)
...
forwarded_env = {k: os.environ[k] for k in _MCP_SERVER_ENV_FORWARD_KEYS if k in os.environ}
params = StdioServerParameters(
    command=self._settings.mcp_server_command,
    args=self._settings.mcp_server_args,
    env=forwarded_env,   # 기본 안전 집합 위에 "추가"된다(대체가 아니다)
)
```

이 목록을 **필요한 이름만 골라** 넘기는 것도 의도적이다. `os.environ` 전체를
그대로 넘기지 않는다 — 이 서버는 우리가 직접 짠, 신뢰하는 서버라 필요한 값만
추리지만, 만약 서드파티가 배포한 MCP 서버를 붙이는 상황이었다면 이 목록에
API 키를 넣는 순간 그 비밀값이 그대로 그 서버로 넘어간다(6절에서 이어서 다룬다).

**도구 호출과 실패의 두 층위** — 이 장에서 가장 중요한 설계 결정이다.
`call_tool()`은 실패를 두 가지로 구분해서 돌려준다.

```python
# docagent/mcp_client.py (발췌)
async def call_tool(self, name: str, arguments: dict[str, Any]) -> McpToolCallResult:
    for attempt in range(self._max_reconnect_attempts + 1):
        session = await self._ensure_connected()
        try:
            result = await session.call_tool(name, arguments)
        except MCPError as exc:                 # 타임아웃 포함, 프로토콜 계층 오류
            await self._drop_session()
            if attempt >= self._max_reconnect_attempts:
                raise McpConnectionError(f"'{name}' 도구 호출이 실패했다(연결/타임아웃): {exc}") from exc
            continue
        except Exception as exc:                 # 파이프 끊김 등
            await self._drop_session()
            if attempt >= self._max_reconnect_attempts:
                raise McpConnectionError(f"'{name}' 도구 호출 중 연결이 끊겼다: {exc}") from exc
            continue

        text_parts = [c.text for c in result.content if getattr(c, "text", None)]
        content = "\n".join(text_parts) if text_parts else json.dumps(result.structured_content or {})
        return McpToolCallResult(ok=not result.is_error, content=content)   # <- 정상 반환
    raise McpConnectionError(f"'{name}' 도구 호출에 실패했다(재시도 소진).")
```

| 상황 | 예 | 처리 |
| --- | --- | --- |
| **도구가 실행됐지만 실패** | `sum_sales`에 없는 제품명을 줌, `search_docs`에 빈 문자열을 줌 | `McpToolCallResult(ok=False, content=...)`를 **정상 반환**한다. 모델이 이 메시지를 읽고 스스로 인자를 고치거나 사용자에게 설명한다(3단계와 동일한 설계). |
| **세션 자체가 문제** | 서버 프로세스가 죽음, 요청이 타임아웃됨, 파이프가 끊김 | `McpConnectionError`를 **예외로 던진다**. 도구 결과 메시지로 모델에게 돌려주지 않는다 — 같은 세션이 끊긴 상태라 "다시 시도해라"라고 모델에게 말해봤자 소용없다. |

`docagent/agent.py`의 루프는 이 둘을 다르게 다룬다.

```python
# docagent/agent.py (발췌)
try:
    result = await mcp_client.call_tool(name, args_dict)
except McpConnectionError as exc:
    yield AgentEvent("tool_result", {"id": call_id, "ok": False, "summary": f"MCP 연결 오류: {exc}"})
    yield AgentEvent("error", {"code": "mcp_connection_failed", "message": str(exc)})
    yield AgentEvent("done", {"finish_reason": "cancelled"})
    return   # <- 이 턴을 더 진행하지 않는다

messages.append({"role": "tool", "tool_call_id": call_id, "name": name, "content": result.content})
# result.ok가 False여도 여기까지 온다 — 루프는 계속된다
```

3단계의 `run_tool()`은 이런 구분이 필요 없었다(파이썬 함수 호출은 예외 아니면
정상 반환뿐이었다). 프로세스 경계를 넘는 순간 실패의 종류가 하나 늘어난다는
사실이, 이 두 함수를 나란히 놓고 보면 가장 분명하게 드러난다.

## 5. 실행과 결과 확인

### 5-1. 서버만 먼저 확인

```bash
python -m scripts.make_sample_data
python -m scripts.ingest_docs
python -m scripts.check_mcp_connection
```

`check_mcp_connection`은 `mcp_server/server.py`를 자식 프로세스로 직접 띄워
도구 목록을 가져온다(도구 **탐색**). `--call`로 실제 호출도 확인할 수 있다.

```bash
python -m scripts.check_mcp_connection --call sum_sales '{"product": "노트북", "quarter": "Q2"}'
```

실제로 실행한 출력(스텁 임베딩 서버 대상, 장 하단 "검증 상태" 참고):

```
MCP 서버에 연결 시도: python -m mcp_server.server
[성공] 도구 3개를 찾았다:
  - search_docs: 사내 분기 리포트 문서에서 질의와 의미적으로 가까운 조각을 찾는다.
  - sum_sales: sales.csv에서 조건에 맞는 매출·판매수량 합계를 계산한다.
  - lookup_sales_rows: sales.csv에서 조건에 맞는 판매 기록 원본 행을 최대 limit개까지 반환한다.

'sum_sales' 호출 중... 인자: {'product': '노트북', 'quarter': 'Q2'}
[성공] {
  "matched_row_count": 6,
  "total_units": 2246,
  "total_revenue": 1347600000,
  "filters": {"product": "노트북", "region": null, "quarter": "Q2"}
}
```

### 5-2. 에이전트까지 함께 실행

```bash
uvicorn docagent.app:app --reload --port 8080
```

`http://localhost:8080`에서 질문하면, `docagent.app`이 시작 시점에 `mcp_server`를
자식 프로세스로 띄우고 그 연결을 재사용한다. `/healthz/mcp`로 연결 상태만 따로
확인할 수도 있다.

```bash
curl http://localhost:8080/healthz/mcp
# {"connected": true, "tools": ["search_docs", "sum_sales", "lookup_sales_rows"]}
```

## 6. 실패 상황 실습

이 장의 완료 기준이다. 세 가지 실패를 실제로 재현하고, 각각 에이전트가 어떻게
동작해야 하는지 코드로 확인한다(`tests/test_mcp_client_failures.py`, 세 시나리오
모두 이 장을 쓰며 직접 실행해 통과를 확인했다).

### 6-1. 서버가 죽어 있을 때(연결 자체가 안 됨)

```bash
DOCAGENT_MCP_SERVER_COMMAND=/no/such/binary uvicorn docagent.app:app --port 8080
```

시작 로그에 연결 실패가 찍히지만 **애플리케이션은 죽지 않는다** — `app.py`의
`lifespan`이 시작 시점 연결 실패를 잡아서 경고만 출력하고 넘어간다(MCP 서버가
아직 안 떠 있는 상태로 호스트를 먼저 띄우는 개발 흐름을 막지 않기 위해서다).
이 상태에서 `/chat`을 보내면:

```
event: status
data: {"stage": "planning", "message": "MCP 서버의 도구 목록을 확인하는 중"}
event: error
data: {"code": "mcp_connection_failed", "message": "MCP 서버('/no/such/binary ')에 연결할 수 없다: [Errno 2] No such file or directory: '/no/such/binary'"}
event: done
data: {"finish_reason": "cancelled"}
```

HTTP 상태 코드는 200이다(SSE 스트림이 이미 시작된 뒤라 상태 코드를 바꿀 수
없다) — 그래서 `error` + `done(finish_reason="cancelled")` 이벤트 조합이 곧
"이 요청은 실패했다"는 신호다. 실제로 이 흐름을 ASGI 트랜스포트로 앱을 직접
띄워 확인했다.

이 코드 경로를 단위 테스트로도 고정해 뒀다.

```python
# tests/test_mcp_client_failures.py (발췌)
async def test_connect_to_nonexistent_command_raises_mcp_connection_error():
    settings = _settings_with(mcp_server_command="/no/such/binary-xyz", mcp_server_args=[])
    client = McpToolsClient(settings=settings, max_reconnect_attempts=0)
    with pytest.raises(McpConnectionError):
        await client.list_tool_specs()
```

### 6-2. 도중에 끊길 때(연결됐다가 서버가 죽음)

프로세스가 연결된 상태에서 죽으면 `McpToolsClient`는 **다음 호출에서 자동으로
한 번 재연결을 시도한다**(`max_reconnect_attempts` 기본값 1). 테스트에서는
두 번째 호출부터 스스로 죽는 서버를 만들어 재현했다.

```python
# tests/test_mcp_client_failures.py (발췌, 서버 스니펫 일부 생략)
async def test_reconnect_after_server_crash():
    client = McpToolsClient(settings=settings, max_reconnect_attempts=1)
    await client.connect()

    r1 = await client.call_tool("flaky", {"x": 1})
    assert r1.ok                       # 첫 호출: 정상

    r2 = await client.call_tool("flaky", {"x": 2})  # 이 호출 도중 서버가 죽는다
    assert r2.ok                       # McpToolsClient가 자동 재연결 -> 성공
```

재연결이 이렇게 "투명하게" 되는 이유는 `_ensure_connected()`가 세션이 없으면
새로 연결하고, `call_tool()`이 통신 오류를 잡으면 `_drop_session()`으로 상태를
버린 뒤 다음 시도에서 다시 `_ensure_connected()`를 부르기 때문이다.
`max_reconnect_attempts`를 넘기면 더 이상 재시도하지 않고 `McpConnectionError`를
올린다 — 무한정 재연결을 시도하며 사용자를 기다리게 하지 않기 위해서다
(PROJECT-SPEC.md 7장의 실행 한도와 같은 정신이다).

**`_drop_session()`이 실제로 연결을 닫으려 시도하는 것도 중요하다.** 처음에는
"이미 죽은 파이프에 대고 종료 절차를 밟다가 또 예외가 날 수 있다"는 이유로
참조만 버리려 했는데, 그렇게 하면 파이썬이 나중에(가비지 컬렉션 시점에)
전혀 다른 태스크에서 정리를 시도하다가 `RuntimeError: Attempted to exit
cancel scope in a different task than it was entered in`을 던지며 **동시에
진행 중이던 다른 재연결 시도까지 취소시키는 것을 직접 재현했다.** 그래서
지금 코드는 닫기를 시도하되, 그 과정에서 나는 예외는 무시한다.

```python
# docagent/mcp_client.py (발췌)
async def _drop_session(self) -> None:
    stack, self._exit_stack = self._exit_stack, None
    self._session = None
    if stack is not None:
        try:
            await stack.aclose()
        except Exception:
            pass  # 이미 죽은 연결을 정리하다 나는 오류는 무시한다
```

### 6-3. 응답이 느릴 때(타임아웃)

`ClientSession(read_timeout_seconds=...)`으로 세션 생성 시점에 타임아웃을
건다(`DOCAGENT_MCP_CALL_TIMEOUT_SECONDS`). 3초 걸리는 도구를 1초 타임아웃으로
호출하면:

```python
# tests/test_mcp_client_failures.py (발췌)
async def test_slow_tool_call_times_out():
    client = McpToolsClient(settings=settings_with_call_timeout_1s, max_reconnect_attempts=0)
    await client.connect()
    with pytest.raises(McpConnectionError):
        await client.call_tool("slow", {"x": 1})
```

실제로 이 시나리오를 실행하면 SDK가 `MCPError: Request 'tools/call' timed out`을
던지고, `McpToolsClient`가 이를 `McpConnectionError`로 감싼다. **타임아웃 뒤에도
서버 프로세스 자체는 살아 있다** — 다만 이 요청에 대한 응답을 더 기다리지
않을 뿐이다. `max_reconnect_attempts`가 남아 있으면 다음 호출에서 같은 세션을
계속 쓰거나(느린 게 일시적이었다면 성공할 수 있다), 세션이 이미 망가졌다면
자동으로 재연결한다.

### 6-4. 세 시나리오 요약

| 실패 | 감지 방법 | 에이전트의 대응 |
| --- | --- | --- |
| 서버가 죽어 있음 | 프로세스 시작 자체가 실패(`FileNotFoundError` 등) | `McpConnectionError` → 이 턴 즉시 `done(cancelled)` |
| 도중에 끊김 | 통신 중 예외(파이프 오류 등) | 세션 폐기 → 다음 호출에서 최대 N회 자동 재연결 → 그래도 실패하면 `McpConnectionError` |
| 응답이 느림 | `read_timeout_seconds` 초과 | `MCPError`(타임아웃) → `McpConnectionError`로 변환 → 재시도 또는 중단 |

## 7. 응용 과제

**과제**: 이 장의 서버에 새 도구 `top_products(quarter: str, limit: int = 3)`를
추가해라 — 지정한 분기에서 매출 상위 N개 제품을 반환한다. 힌트:
`mcp_server/sales_data.py`에 순수 파이썬 함수로 계산 로직을 먼저 만들고(3단계
스타일로 pydantic 인자 모델까지), `mcp_server/server.py`에 `@mcp.tool()`로
등록한다. 그다음 `python -m scripts.check_mcp_connection --call top_products
'{"quarter": "Q1"}'`으로 새 도구가 탐색되고 호출되는지 확인한다 — 에이전트
쪽(`docagent/`)은 **한 줄도 고치지 않아도** 새 도구를 쓸 수 있어야 한다(도구
탐색이 매번 서버에게 물어보는 방식이기 때문이다). 완료 기준: 에이전트 코드
변경 없이 새 도구가 대화에서 호출되는 것을 SSE 로그로 확인한다.

**추가 과제(선택)**: `docagent/mcp_client.py`의 `max_reconnect_attempts`를 0으로
바꾸고 6-2절 테스트를 다시 돌려봐라. 왜 실패하는지 설명할 수 있는가?

## 8. 핵심 정리와 확인 질문

- MCP는 Host(프로세스를 띄우고 조율)·Client(서버 하나와의 연결)·Server(도구를
  실제로 실행)로 역할을 나눈다. 이 장에서 Host와 Client는 `docagent`(같은
  프로세스), Server는 `mcp_server`(별도 프로세스)다.
- Tools(모델이 호출 여부를 판단)·Resources(호스트가 직접 읽는 정적/준정적
  데이터)·Prompts(클라이언트가 고르는 템플릿)는 서로 다른 원시 개념이다. 셋을
  구분하지 않고 전부 Tool로 만드는 것은 흔한 오해다.
- 로컬 함수 도구와 MCP 도구의 차이는 모델에게는 보이지 않는다 — 전부
  애플리케이션 쪽(도구 탐색 방식, 실행 주체, 실패의 두 층위)에 있다.
- MCP 서버는 실제로 함수를 실행하는 주체다. 신뢰할 수 없는 서버를 붙이는 것은
  낯선 실행 파일을 그대로 실행하는 것과 같은 무게의 결정이다. 도구
  description은 모델 프롬프트에 그대로 들어간다.
- 연결 실패는 세 가지 다른 모양으로 온다(서버 없음/도중에 끊김/응답 없음).
  파이썬 함수 호출에는 없던 실패 층위다. 도구 실행 실패(ok=False, 정상
  반환)와 연결 실패(예외로 중단)를 구분해서 다뤄야 한다.

### 확인 질문

1. `McpToolsClient.call_tool()`이 "도구 실행 실패"는 정상 반환하면서 "연결
   실패"는 예외로 던지는 이유를 설명할 수 있는가? 반대로 했다면 `agent.py`의
   루프가 어떻게 달라져야 하는가?
2. `sales_schema_resource`를 Tool 대신 Resource로 만든 이유 세 가지를 말할 수
   있는가?
3. `mcp_server/store.py`가 sparse 필드를 쓰지 않는 이유를, "왜 이 오류가
   나는지 안다"는 인상을 주지 않고 설명할 수 있는가?
4. `DOCAGENT_MCP_SERVER_ARGS`에 넘긴 환경 변수 목록에 `OPENAI_API_KEY`가 왜
   포함돼 있는지, 그리고 왜 `os.environ` 전체를 넘기지 않는지 설명할 수
   있는가?
5. 이 장의 서버를 stdio 대신 `streamable-http`로 띄운다면, `docagent` 쪽
   코드에서 정확히 어느 줄들을 바꿔야 하는가?

### 이 장의 완성 코드

`code/step10_mcp/`

### 다음 장

11단계에서는 에이전트를 하나 더 늘린다 — 조사·분석·검증 역할을 나눈
멀티에이전트 협업과, 이 장의 MCP(도구 연결)와는 다른 층위인 Agent-to-Agent(A2A,
에이전트 사이의 작업 위임)를 구분해서 다룬다. 이 장에서 만든 MCP 서버는
그대로 재사용한다 — 여러 에이전트가 같은 도구 서버에 붙을 수 있다는 점도
MCP를 프로세스로 분리해 둔 이유 중 하나다.

---

## 참고 문서

- MCP 파이썬 SDK(PyPI `mcp` 2.2.0) 배포 메타데이터(README) — "Build MCP servers
  that expose tools, resources, and prompts to any MCP host", "Build MCP
  clients that connect to any MCP server", 지원 전송(stdio, Streamable HTTP,
  SSE) 서술 확인. `pip show mcp`로 설치 후 로컬 `*.dist-info/METADATA`를 직접
  읽어 확인했다. https://pypi.org/project/mcp/
- 같은 SDK의 `mcp.server.fastmcp` 모듈이 내는 `ModuleNotFoundError` 메시지 —
  `FastMCP` → `MCPServer` 이름 변경, 마이그레이션 가이드 링크
  (`https://py.sdk.modelcontextprotocol.io/v2/migration/#fastmcp-renamed-to-mcpserver`)
  를 실제로 재현해 확인했다.
- `mcp.server.mcpserver.exceptions` 모듈 소스(설치된 패키지 파일을 직접 읽음) —
  `ToolError`/`UnexpectedToolError`/`ResourceError`의 docstring에 적힌 "모델에게
  전달되는 메시지"와 "로그에만 남는 트레이스백"의 구분을 그대로 인용했다.
- `mcp.client.stdio` 모듈 소스(`get_default_environment()`,
  `DEFAULT_INHERITED_ENV_VARS`) — stdio 자식 프로세스에 기본 상속되는 환경
  변수가 POSIX 기준 `HOME`/`LOGNAME`/`PATH`/`SHELL`/`TERM`/`USER`뿐이라는 사실을
  소스에서 직접 확인했다.
- `mcp.types.LATEST_PROTOCOL_VERSION`(설치된 패키지에서 직접 조회) — 이 SDK
  버전(2.2.0)이 구현하는 최신 MCP 프로토콜 버전이 `2026-07-28`임을 확인했다.
- VERIFIED-FINDINGS.md 6·7·9절 — Milvus Lite의 load 상태·BM25 tokenizer
  제약·sparse 컬렉션의 프로세스 간 취약성. 이 장 4-2절의 근거로 그대로
  인용했다(범위를 넘어 일반화하지 않았다).

`[확인 필요]`: 이 환경에서는 `modelcontextprotocol.io`(공식 명세 사이트)로의
네트워크 접근이 차단되어 있어, 공식 스펙 원문(Host/Client/Server의 정식 정의,
전송별 상세 요구사항)을 직접 열람하지 못했다. 대신 설치된 SDK의 소스 코드,
패키지에 포함된 README, 그리고 SDK가 실제로 내는 오류 메시지로 교차 확인할 수
있는 사실만 이 장에 넣었다. Host/Client/Server의 1:1 연결 관계, Tools/
Resources/Prompts 세 원시 개념의 존재는 SDK의 실제 타입(`ClientSession`이 한
전송에 대응, `list_tools`/`list_resources`/`list_prompts`가 별도 메서드로
분리돼 있는 것)과 동작으로도 간접 확인되지만, 명세 원문의 정확한 문구는
`[확인 필요]`로 남긴다. Milvus standalone(Docker)에서 sparse 컬렉션이 이 장과
같은 프로세스 분리 조건에서도 동작하는지도 `[확인 필요]`다(VERIFIED-FINDINGS.md
9절과 동일한 미확인 사항).

> 검증 상태: 이 장의 모든 `.py` 파일은 `python3 -m py_compile`을 통과했다.
> **MCP 서버와 클라이언트를 실제로 별도 프로세스로 띄워** 도구 탐색·호출,
> 정적 리소스·리소스 템플릿 읽기, 프롬프트 가져오기를 전부 실행해 확인했다.
> `tests/test_sales_data.py`(4개)와 `tests/test_mcp_client_failures.py`(3개,
> 연결 실패·타임아웃·크래시 후 재연결)를 포함해 오프라인 테스트 7개를 실제로
> 통과시켰다. `tests/test_mcp_server_tools.py`(3개, 실제 서버 프로세스 대상
> 통합 테스트)도 스텁 모델 서버(`code/_tools/fake_openai_server.py`)와 Milvus
> Lite를 띄운 상태로 실제로 통과시켰다(총 10개). FastAPI 앱(`docagent.app:app`)
> 을 ASGI 트랜스포트로 직접 띄워 `/chat` SSE 스트림이 실제 MCP 서버와 끝까지
> 도구 호출 루프를 도는 것, MCP 서버가 죽어 있을 때 애플리케이션이 죽지 않고
> `error`+`done(cancelled)` 이벤트로 그 요청만 실패시키는 것을 확인했다. Milvus
> Lite(dense 전용 스키마)에 문서를 적재한 프로세스와 **다른 프로세스**(별도로
> 띄운 MCP 서버)에서 검색이 성공하는 것도 확인했다.
>
> `docagent/llm.py`는 3단계 버전을 그대로 가져왔고, 3~9단계 코드베이스 전체가
> 그렇듯 PROJECT-SPEC.md 9절이 요구하는 `achat`/`achat_stream`을 아직 구현하지
> 않았다(3단계 이후 이어진 기존 차이이지 이 장에서 새로 생긴 차이가 아니다).
> 그래서 `docagent/agent.py`의 `_default_chat_fn`은 동기 `chat()`을
> `run_in_executor`로 별도 스레드에서 돌린다 — 다른 비동기 작업(동시 요청의
> MCP 호출 등)을 막지는 않지만, PROJECT-SPEC.md가 요구하는 "모델 서버 호출까지
> 실제로 취소 가능한" 수준은 아니다.
>
> streamable-http(HTTP 기반) 전송은 최소 구성(도구 하나, 인증 없음)으로 서버를
> 띄우고 클라이언트로 연결·호출까지 별도로 확인했지만, 이 장의 누적 프로젝트
> 코드(`mcp_server/server.py`, `docagent/mcp_client.py`)는 **stdio만 구현**한다.
> 스텁 서버(`fake_openai_server.py`)를 모델로 썼으므로 실제 모델의 도구 선택
> 품질은 검증하지 않았다 — 이 검증은 "MCP 배관이 올바른가"만 보장한다. 실제
> OAuth 인증이 걸린 원격 MCP 서버, Milvus standalone(Docker) 환경에서의 동작은
> 검증하지 않았다.

[^mcp-what]: Model Context Protocol을 이 장에서 "개방형 표준"으로 서술한 근거는
    설치한 파이썬 SDK(mcp 2.2.0)의 배포 메타데이터에 있는 설명(LLM 애플리케이션에
    데이터·기능을 안전하고 표준화된 방식으로 노출하는 프로토콜)이다.
[^mcp-python-sdk-readme]: MCP 파이썬 SDK(`mcp` 2.2.0) PyPI 페이지의 README —
    "Build MCP servers that expose tools, resources, and prompts to any MCP
    host / Build MCP clients that connect to any MCP server / Speak every
    standard transport: stdio, Streamable HTTP, and SSE" 문구를 근거로 Host·
    Client·Server 역할과 세 원시 개념, 지원 전송 목록을 정리했다. 로컬에 설치된
    `mcp-2.2.0.dist-info/METADATA`를 직접 읽어 확인했다.
[^mcp-v2-note]: 같은 README의 안내: "Since `pip install mcp` now installs 2.x,
    keep a `<2` upper bound on your requirement (for example `mcp>=1.28,<2`)
    until you've migrated." — 2026-09-09 시점 `pip install mcp`가 기본으로
    2.x를 설치한다는 사실과, 1.x를 유지하려면 상한을 직접 걸어야 한다는 사실을
    함께 확인했다.
