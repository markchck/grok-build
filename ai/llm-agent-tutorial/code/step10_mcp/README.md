# step10_mcp

10단계 "MCP로 외부 도구 연결하기"의 완성 코드. 자세한 설명은
`../../docs/10-mcp.md`를 본다.

이 단계는 **두 개의 독립적인 파이썬 패키지**로 이루어진다 — 그 자체가 이
장의 핵심이다.

- `mcp_server/` — 문서 검색·CSV 조회 도구를 제공하는 **MCP 서버**. 별도
  프로세스로 띄운다.
- `docagent/` — 그 서버에 연결해 도구를 쓰는 **에이전트(Host + Client)**.
  FastAPI로 서비스한다.

두 패키지는 서로 import하지 않는다. 통신은 오직 MCP 프로토콜(이 장은
stdio 전송)을 통해서만 이뤄진다.

## 1. 설치

```bash
cd code/step10_mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Milvus 서버가 필요하다(문서 검색 도구용). 로컬에서 가장 간단한 방법은
`DOCAGENT_MILVUS_URI`를 로컬 파일 경로로 두어 Milvus Lite(내장 모드)를
쓰는 것이다 — 아래 3번 단계 참고. 실제 운영에서는 Milvus standalone(Docker)을
권한다(이유는 장 본문 4-2절과 `mcp_server/store.py` 상단 주석 참고).

OpenAI 호환 모델 서버는 채팅과 임베딩을 모두 지원해야 한다(4·5단계와 동일).

## 2. 환경 변수

```bash
cp .env.example .env
# .env를 열어 OPENAI_BASE_URL, CHAT_MODEL, EMBEDDING_MODEL, DOCAGENT_MILVUS_URI 등을 채운다.
```

## 3. 문서 적재 (MCP 서버가 검색할 데이터 준비)

```bash
python -m scripts.make_sample_data   # data/sales.csv, data/docs/*.md
python -m scripts.ingest_docs         # data/docs/*.md -> Milvus dense 컬렉션
```

`ingest_docs`는 sparse(BM25) 필드를 만들지 않는다 — 이유는
`mcp_server/store.py` 상단 주석과 장 본문 4-2절에 자세히 적었다
(VERIFIED-FINDINGS.md 9절의 Milvus Lite sparse 취약성이 MCP 서버 프로세스
분리 구조에 그대로 걸린다).

## 4. MCP 서버만 따로 확인하기

에이전트를 띄우기 전에, 서버 혼자 잘 동작하는지 먼저 확인할 수 있다.

```bash
python -m scripts.check_mcp_connection
python -m scripts.check_mcp_connection --call sum_sales '{"product": "노트북"}'
python -m scripts.check_mcp_connection --call search_docs '{"query": "노트북 매출 감소 원인"}'
```

이 스크립트는 `mcp_server/server.py`를 자식 프로세스로 직접 띄워서 도구
목록을 가져오고(도구 탐색), 지정한 도구를 호출한다(실제 호출) — 이 장의
완료 기준 앞부분을 손으로 확인하는 것이다.

## 5. 에이전트 실행

```bash
uvicorn docagent.app:app --reload --port 8080
```

`http://localhost:8080`을 열어 질문을 입력한다. 이 서버는 시작할 때
`mcp_server`를 자식 프로세스로 띄운다(`DOCAGENT_MCP_SERVER_COMMAND`/
`DOCAGENT_MCP_SERVER_ARGS`로 어떤 명령인지 지정한다).

## 6. 실패 상황 실습

장 본문 6절에서 자세히 다룬다. 빠르게 재현해 보려면:

```bash
# 서버가 죽어 있을 때: 일부러 잘못된 명령으로 설정하고 실행
DOCAGENT_MCP_SERVER_COMMAND=/no/such/binary uvicorn docagent.app:app --port 8080
# -> 시작 로그에 연결 실패가 찍히지만 앱은 뜬다. /chat을 보내면 error+done(cancelled) 이벤트로 끝난다.
# curl http://localhost:8080/healthz/mcp 로도 확인할 수 있다.
```

```bash
python -m pytest tests/test_mcp_client_failures.py -v
```

이 테스트 파일이 "서버가 죽어 있을 때", "호출이 타임아웃될 때", "연결
도중 서버가 죽었다가 자동 재연결로 복구될 때" 세 시나리오를 코드로
재현한다(외부 서버 불필요 — 테스트 안에서 즉석 스텁 서버를 띄운다).

## 7. 테스트

```bash
# 외부 서버 없이 실행 가능
python -m pytest tests/test_sales_data.py tests/test_mcp_client_failures.py -v

# OpenAI 호환 서버 + Milvus(4번 단계까지 마친 상태)가 필요. 없으면 자동으로 skip된다.
python -m pytest tests/test_mcp_server_tools.py -v
```

## 폴더 구조

```
step10_mcp/
├── README.md
├── requirements.txt
├── .env.example
├── pytest.ini
├── mcp_server/              # MCP 서버 (별도 프로세스)
│   ├── config.py
│   ├── store.py              # Milvus dense 전용 스키마
│   ├── search.py              # dense_search + 임베딩 호출
│   ├── sales_data.py           # sum_sales/lookup_sales_rows 로직 (3단계와 동일한 계산)
│   └── server.py                # MCPServer 정의: tools/resources/prompts
├── docagent/                # 에이전트 (Host + Client)
│   ├── config.py
│   ├── llm.py                 # 3단계와 동일 (tools 지원 chat)
│   ├── events.py
│   ├── mcp_client.py           # MCP 연결 관리 (재연결·타임아웃·오류 처리)
│   ├── agent.py                 # 에이전트 루프 (MCP 도구 호출)
│   └── app.py                    # FastAPI 앱
├── static/
├── data/
│   ├── docs/
│   └── sales.csv
├── scripts/
│   ├── make_sample_data.py
│   ├── ingest_docs.py
│   └── check_mcp_connection.py
└── tests/
    ├── test_sales_data.py
    ├── test_mcp_client_failures.py
    └── test_mcp_server_tools.py
```

## 검증 상태

- `python3 -m py_compile`로 모든 `.py` 파일의 문법을 확인했다.
- **MCP 서버·클라이언트를 실제로 별도 프로세스로 띄워** 도구 탐색(`list_tools`),
  세 도구(`search_docs`/`sum_sales`/`lookup_sales_rows`) 호출, 정적 리소스
  (`data://sales-schema`)와 리소스 템플릿(`docs://{doc_id}`) 읽기, 프롬프트
  (`analyze_sales`) 가져오기를 모두 실제로 실행해 확인했다.
- 연결 실패(잘못된 명령), 호출 타임아웃, 연결 도중 서버 프로세스 크래시 후
  자동 재연결 세 시나리오를 실제로 재현해 `McpConnectionError`가 의도한
  대로 나는지/재연결이 되는지 확인했다(`tests/test_mcp_client_failures.py`,
  10개 테스트 모두 통과).
- FastAPI 앱(`docagent/app.py`)을 ASGI 트랜스포트로 직접 띄워 `/chat`
  SSE 스트림이 실제 MCP 서버·스텁 모델 서버(`code/_tools/fake_openai_server.py`)
  와 끝까지 도구 호출 루프를 도는 것과, MCP 서버가 죽어 있을 때 앱이
  죽지 않고 `error`+`done(cancelled)` 이벤트로 그 요청만 실패시키는 것을
  확인했다.
- Milvus Lite(dense 전용 스키마)에 문서를 적재하고, **적재 프로세스와
  다른 프로세스**(별도로 띄운 MCP 서버)에서 검색이 성공하는 것을 확인했다
  — VERIFIED-FINDINGS.md 9절의 sparse 취약성이 dense 전용 구성에는
  적용되지 않는다는 것을 이 장 집필 중 별도로 재확인했다(장 본문 "검증
  상태" 참고).
- 스텁 서버(`fake_openai_server.py`)를 모델로 썼으므로 **실제 모델의 답변
  품질은 검증하지 않았다.** 스텁은 항상 고정 문자열을 반환하고, tools가
  있으면 첫 번째 도구를 인자 없이 한 번 호출하는 규칙만 있다 — 그래서 이
  검증은 "MCP 배관이 올바른가"만 보장하고 "모델이 도구를 올바르게
  선택하는가"는 보장하지 않는다.
- streamable-http 전송(HTTP 기반)은 최소 구성(도구 하나, 인증 없음)으로
  서버를 띄우고 클라이언트로 연결·호출까지 확인했지만, 이 장의 기본
  실습 코드(`mcp_server/server.py`, `docagent/mcp_client.py`)는 stdio만
  구현한다 — HTTP 기반은 장 본문 3절에서 개념과 차이만 설명하고 코드로
  포함하지 않았다.
- Milvus standalone(Docker)에서의 동작, 실제 OAuth 인증이 걸린 원격 MCP
  서버 연결은 **검증하지 않았다.**
