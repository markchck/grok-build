# 누적 프로젝트 고정 규약

각 장은 이 규약을 그대로 따른다. 장마다 이름을 새로 짓지 않는다.
규약을 바꿔야 하면 해당 장 본문에 "이 장에서 규약을 이렇게 바꾼다"고 명시하고 이 파일도 함께 고친다.

## 1. 프로젝트 이름과 폴더

프로젝트 이름은 `docagent`다. 각 단계 완성 코드는 아래 형태로 둔다.

```
ai/llm-agent-tutorial/
├── docs/                     # 장 본문 (00-curriculum.md, 01-*.md ... 13-*.md)
└── code/
    ├── step01_raw_api/
    ├── step02_fastapi_chat/
    ├── step03_tool_calling/
    ├── step04_basic_rag/
    ├── step05_hybrid_rag/
    ├── step06_langchain/
    ├── step07_langgraph/
    ├── step08_hitl/
    ├── step09_tracing_eval/
    ├── step10_mcp/
    ├── step11_multi_agent/
    ├── step12_deep_agents/
    └── step13_ui_export/
```

각 `stepNN_*/` 안의 공통 배치:

```
stepNN_xxx/
├── README.md            # 실행 방법 (설치 → 환경 변수 → 실행 → 확인)
├── requirements.txt     # 이 단계에서 실제로 쓰는 패키지만
├── .env.example         # 값은 전부 예시. 실제 키 금지
├── docagent/            # 파이썬 패키지
│   ├── __init__.py
│   ├── config.py        # 환경 변수 로딩 (전 단계 공통)
│   ├── llm.py           # 모델 서버 호출 (1단계에서 도입)
│   ├── app.py           # FastAPI 앱 (2단계부터)
│   ├── tools.py         # 로컬 함수 도구 (3단계부터)
│   ├── agent.py         # 에이전트 루프 (3단계부터)
│   ├── rag/             # 4단계부터
│   │   ├── ingest.py
│   │   ├── store.py
│   │   └── search.py
│   └── graph/           # 7단계부터
├── static/              # 2단계부터의 단순 웹 UI (index.html, app.js)
└── data/
    ├── docs/            # 샘플 문서
    └── sales.csv        # 샘플 CSV
```

## 2. 환경 변수 (이름 고정)

`.env.example`에는 아래 이름만 쓴다. 값은 예시다.

```
# OpenAI 호환 모델 서버
OPENAI_BASE_URL=http://localhost:8000/v1
OPENAI_API_KEY=sk-local-example-key
CHAT_MODEL=your-chat-model-name
EMBEDDING_MODEL=your-embedding-model-name

# Milvus (4단계부터)
# 주의: 변수 이름에 DOCAGENT_ 접두사를 붙인다. pymilvus가 MILVUS_URI를 자기 설정
# 환경 변수로 예약하고 있고, 그 값이 http(s):// 형식이 아니면 `import pymilvus`
# 자체가 ConnectionConfigException으로 실패한다(2.6.17, 3.0.1에서 확인).
DOCAGENT_MILVUS_URI=http://localhost:19530
DOCAGENT_MILVUS_TOKEN=
DOCAGENT_MILVUS_COLLECTION=docagent_chunks

# 애플리케이션
APP_BASE_URL=http://localhost:8080
REQUEST_TIMEOUT_SECONDS=60
MAX_AGENT_STEPS=8
MAX_AGENT_SECONDS=120

# 8단계부터 사용
MAX_AGENT_TOKENS=4000
DOCAGENT_STATE_DB=./data/hitl_state.db

# 9단계부터 사용 (트레이싱)
# 주의: 접두사를 DOCAGENT_로 붙인다. phoenix.otel.register()가
# PHOENIX_COLLECTOR_ENDPOINT, PHOENIX_PROJECT_NAME을 자기 환경 변수로 이미
# 읽는다(phoenix.otel 소스로 확인) — Milvus 때와 같은 이유로 접두사 없는
# 이름을 그대로 쓰지 않는다.
DOCAGENT_TRACING_ENABLED=false
DOCAGENT_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces
DOCAGENT_TRACE_PROJECT_NAME=docagent
```

새 환경 변수가 필요한 장은 위 목록에 **추가만** 하고 기존 이름을 바꾸지 않는다.

`OPENAI_BASE_URL`과 `OPENAI_API_KEY`는 openai SDK가 직접 읽는 이름이라 일부러 그대로 쓴다.
반대로 Milvus 쪽은 라이브러리가 읽는 이름과 겹치면 안 되므로 `DOCAGENT_` 접두사를 붙인다.
새 변수를 만들 때는 쓰려는 라이브러리가 그 이름을 이미 읽고 있는지 먼저 확인한다.

## 3. 샘플 데이터 (전 단계 공통)

- `data/sales.csv` — 컬럼: `date,product,region,units,revenue`. 2개 분기, 제품 4종.
- `data/docs/` — 제품별 분기 리포트 3~5개(Markdown 또는 PDF). 매출 변화의 원인을 서술한 문장을 포함시켜 RAG 검색 대상이 되게 한다.

샘플 데이터는 4단계에서 생성 스크립트(`scripts/make_sample_data.py`)로 만들고 이후 단계는 그대로 재사용한다.

## 4. SSE 이벤트 스키마 (2단계에서 도입, 13단계까지 확장)

서버는 `text/event-stream`으로 아래 이벤트를 보낸다. `event:` 이름과 `data:`의 JSON 키를 고정한다.

| event | data 필드 | 의미 |
| --- | --- | --- |
| `token` | `{"text": "..."}` | 모델 답변 조각 |
| `status` | `{"stage": "...", "message": "..."}` | 진행 표시(=요구사항의 "thinking 과정 표시"). 실제 실행 이벤트만 담는다 |
| `tool_call` | `{"id","name","args"}` | 도구 호출 시작 |
| `tool_result` | `{"id","ok","summary"}` | 도구 실행 결과 요약 |
| `todo` | `{"items":[{"id","title","state"}]}` | 작업 목록. `state`는 `pending`/`running`/`done`/`failed` |
| `sources` | `{"items":[{"id","doc","page","url","snippet"}]}` | 근거 목록 |
| `approval_request` | `{"id","action","args","reason"}` | 사용자 승인 요청 |
| `error` | `{"code","message"}` | 오류 |
| `done` | `{"finish_reason":"...", "partial"?:{...}}` | 종료. `partial`은 8단계에서 추가한 선택 필드로, 한도 초과 등으로 중간에 멈췄을 때의 부분 결과(`text`, `steps_used`, `tokens_used`, `tokens_estimated`)를 담는다. 기존 필드(`finish_reason`)는 그대로이므로 이 필드를 모르는 이전 단계 클라이언트도 그대로 동작한다 |

`status.stage` 값도 고정한다: `starting`, `planning`, `retrieving`, `analyzing`, `verifying`, `writing`.
(`starting`은 6단계에서 추가했다 — 실행을 시작했지만 아직 첫 모델 호출 전인 구간을 가리킨다.
이 값을 모르는 클라이언트는 다른 `status`와 똑같이 취급하면 되므로 하위 호환이 깨지지 않는다.)

**집계값은 이벤트 개수로 역산하지 않는다.** `steps_used`처럼 실행 중 세는 값은 그 값을 아는
쪽이 `done` 이벤트의 `partial`에 담아 보낸다. 소비자가 `status` 이벤트를 세서 되짚는 방식은
이벤트를 하나 더하거나 빼는 순간 조용히 틀린 값을 내므로 쓰지 않는다.

**진행 표시는 모델 내부 사고 원문이 아니다.** 위 이벤트는 애플리케이션이 관측한 실제 실행 사실만 담는다.

## 5. 근거 링크 규약 (5단계에서 도입)

- 청크 ID 형식: `{doc_id}#p{page}-c{chunk_index}` (예: `q2-report#p3-c2`)
- 링크 URL: `{APP_BASE_URL}/sources/{doc_id}?page={page}&chunk={chunk_index}`
- 모델 답변에는 `[S1]`, `[S2]` 형태의 인용 표시만 쓰게 하고, 애플리케이션이 `S1 → 청크 ID → URL`로 변환한다.
- **모델이 만들어낸 URL을 그대로 쓰지 않는다.** 이번 검색에서 실제로 반환된 청크 ID 집합에 없는 인용은 버리고, 버렸다는 사실을 답변에 표시한다.

## 6. Milvus 컬렉션 스키마 (4단계 도입, 5단계 확장)

필드 이름을 고정한다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `pk` | VARCHAR, primary | 청크 ID (`{doc_id}#p{page}-c{idx}`) |
| `dense` | FLOAT_VECTOR | 임베딩. 차원은 사용 모델에 맞춘다 |
| `sparse` | SPARSE_FLOAT_VECTOR | 5단계에서 추가 |
| `text` | VARCHAR | 청크 원문 |
| `doc_id` | VARCHAR | 문서 식별자 |
| `doc_title` | VARCHAR | 문서명 |
| `page` | INT64 | 페이지(없으면 0) |
| `chunk_index` | INT64 | 문서 내 청크 순번 |
| `source_url` | VARCHAR | 원본 URL 또는 로컬 경로 |

## 7. 에이전트 실행 한도 (3단계 도입, 8·11단계 확장)

- 최대 단계 수: `MAX_AGENT_STEPS`
- 최대 실행 시간: `MAX_AGENT_SECONDS`
- 같은 `(도구 이름, 정규화한 인자)` 조합이 3회 반복되면 중단한다.
- 중단 시 `error` 이벤트가 아니라 `done` 이벤트에 `finish_reason`을 넣고 부분 결과를 함께 반환한다.
  `finish_reason` 값: `stop`, `max_steps`, `timeout`, `repeated_tool_call`, `no_progress`, `rejected_by_user`, `cancelled`, `token_budget`(8단계에서 추가 — 누적 토큰 예산 초과. 기존 값 중 아무것도 이 의미를 담지 못해 새로 추가했다).

## 8. 모델 서버 기능 확인

도구 호출·구조화 출력·스트리밍은 서버 구현마다 지원 범위가 다르다.
1단계에서 `scripts/check_server_features.py`를 만들어 각 기능을 실제로 한 번씩 호출해 지원 여부를 출력하고,
이후 장은 "이 기능이 필요하다. 미지원이면 이렇게 우회한다"를 명시한다.

## 9. `config.py`와 `llm.py`의 공통 인터페이스 (전 단계 고정)

병렬로 쓴 장들이 서로 다른 이름을 쓰면 "기능을 누적한다"는 전제가 깨진다.
아래 이름과 시그니처는 모든 단계에서 동일하다. 단계마다 새로 짓지 않는다.

```python
# docagent/config.py
@dataclass(frozen=True)
class Settings:
    openai_base_url: str
    openai_api_key: str
    chat_model: str
    embedding_model: str          # 4단계부터 사용
    milvus_uri: str               # DOCAGENT_MILVUS_URI (4단계부터)
    milvus_token: str
    milvus_collection: str
    app_base_url: str
    request_timeout_seconds: float
    max_agent_steps: int          # 3단계부터
    max_agent_seconds: float

def load_settings() -> Settings: ...   # 환경 변수를 매번 새로 읽는다. 테스트에서 쓴다
def get_settings() -> Settings: ...    # lru_cache로 한 번만 읽는다. 애플리케이션 코드는 이쪽을 쓴다
```

`Settings`는 단계별로 필요한 필드만 갖는다. **이미 있는 필드의 이름은 바꾸지 않고, 필요한 필드를 추가만 한다.**

```python
# docagent/llm.py
class LLMError(RuntimeError): ...

@dataclass(frozen=True)
class ChatResult:
    text: str
    finish_reason: str
    tool_calls: list[dict]   # 도구 호출이 없으면 빈 리스트 (3단계부터 채워진다)
    raw: dict

def chat(messages, *, settings=None, temperature=0.2, max_tokens=1024,
         tools=None, response_format=None) -> ChatResult: ...
def chat_stream(messages, *, settings=None, temperature=0.2, max_tokens=1024) -> Iterator[str]: ...
def chat_json(messages, *, schema_name, json_schema, settings=None, temperature=0.0) -> dict: ...
def embed(texts: list[str], *, settings=None) -> list[list[float]]: ...   # 4단계부터

# 비동기 변형 (2단계부터). 이름은 앞에 a를 붙인다.
async def achat(messages, *, settings=None, temperature=0.2, max_tokens=1024,
                tools=None, response_format=None) -> ChatResult: ...
def achat_stream(messages, *, settings=None, temperature=0.2,
                 max_tokens=1024) -> AsyncIterator[str]: ...
```

**동기와 비동기를 둘 다 두는 이유**: CLI와 스크립트, 단위 테스트는 동기 호출이 읽기 쉽다.
반대로 FastAPI 엔드포인트는 비동기여야 한다 — 클라이언트가 연결을 끊었을 때
**모델 서버로 가는 호출까지 실제로 취소**하려면 `AsyncOpenAI`로 열린 스트림을 이벤트 루프가
직접 닫을 수 있어야 하기 때문이다. 동기 제너레이터를 별도 스레드에서 돌리는 우회는
조각 전달은 되지만 그 스레드를 밖에서 끊을 수 없어 취소가 반쪽이 된다.
그래서 **2단계 이후 서버 코드(app.py, 그래프 노드)는 `achat`/`achat_stream`을 쓴다.**

- `settings=None`이면 `get_settings()`를 쓴다. 테스트는 `settings=`로 주입한다.
- 내부 구현은 `openai` SDK를 쓴다. **예외는 1단계뿐이다.** 1단계는 HTTP 계층을 가르치기 위해
  `raw_chat_completion`, `raw_chat_completion_stream`을 추가로 두고 SDK 구현과 나란히 비교한다.
  2단계 이후에는 SDK 경로만 쓴다.
- 모든 오류는 `LLMError`로 감싸서 올린다. 호출부가 라이브러리 예외 타입을 알 필요가 없게 한다.

## 10. 의존성 버전 정책

- 모든 단계의 `requirements.txt`는 **범위**로 쓴다(`openai>=3.10,<4`). 정확한 한 버전으로 못 박지 않는다.
- 각 줄에 해당 단계에서 그 패키지를 왜 쓰는지 한 줄 주석을 단다.
- 이전 단계에 이미 있던 패키지는 **같은 범위**를 쓴다. 단계마다 범위를 다르게 적지 않는다.
- 확인된 기준선(2026-09-09 실측): `openai>=3.10,<4`, `httpx>=0.28,<0.29`,
  `python-dotenv>=1.0,<2`, `fastapi>=0.115,<1.0`, `uvicorn[standard]>=0.30,<1.0`,
  `pydantic>=2.7,<3`, `pymilvus[milvus-lite]>=2.6,<3`.
- `openai` 3.x는 `httpx`가 아니라 `httpx2`에 의존한다. `httpx`를 직접 쓰는 단계(1단계)는
  `httpx`를 따로 명시한다. 두 패키지는 이름이 달라 함께 설치돼도 충돌하지 않는다.
