# 6단계. LangChain으로 재구성하기

## 1. 학습 목표와 완성 모습

3단계(`step03_tool_calling`)에서는 "모델 호출 → 도구 실행 → 결과 전달"을
`while` 문으로 직접 돌리는 에이전트 루프를 만들었다. 5단계(`step05_hybrid_rag`)
에서는 Milvus 스키마를 손으로 선언하고, Dense·Sparse 검색을 각각 호출하고,
모델 답변의 `[S1]` 인용을 직접 검증했다. 두 장 모두 "프레임워크 없이 원리를
이해한다"가 목표였다.

이번 장은 같은 기능을 **LangChain**으로 다시 만든다. LangChain은 모델
호출·프롬프트 조립·검색·도구 호출·에이전트 루프에 공통으로 쓰이는 패턴을
표준화한 파이썬 라이브러리다. 직접 짠 코드와 나란히 놓고 "무엇을 대신해
주는지, 무엇을 대신해 주지 않는지, 어떤 통제력을 잃는지"를 확인하는 것이
이 장의 핵심이다 — 단순히 "LangChain 버전을 만든다"가 목표가 아니다.

이번 장에서 만드는 것:

- `docagent/models.py` — `ChatOpenAI`/`OpenAIEmbeddings`로 재구성한 모델 인터페이스.
- `docagent/tools.py` — `@tool` 데코레이터로 재구성한 3단계 도구(`sum_sales`, `lookup_sales_rows`).
- `docagent/agent.py` — `langchain.agents.create_agent` + 커스텀 미들웨어로 재구성한 3단계 에이전트 루프.
- `docagent/rag/store.py`, `ingest.py`, `search.py` — `langchain_milvus.Milvus`로 재구성한 5단계 벡터 저장소·하이브리드 검색.
- `docagent/chains.py` — LCEL(`prompt | model | parser`)로 재구성한 5단계 답변 생성부.
- `docagent/app.py` — 5단계와 같은 SSE 이벤트를 내보내는 `/chat`(RAG), `/agent/chat`(도구 호출) 두 엔드포인트.
- `scripts/compare_with_step05.py` — 5단계(직접 구현)와 같은 입력으로 동작을 실제로 비교하는 스크립트.

완성 후 `/agent/chat`에 "2분기 노트북 서울 매출 합계 알려줘"를 보내면, 3단계와
같은 순서의 SSE 이벤트가 온다(값은 스텁 서버 기준 예시):

```
event: status
data: {"stage": "starting", "message": "LangChain 에이전트 실행을 시작한다"}

event: status
data: {"stage": "planning", "message": "1단계: 모델에게 다음 행동을 묻는다"}

event: status
data: {"stage": "analyzing", "message": "도구 1건 실행 준비"}

event: tool_call
data: {"id": "call_stub_1", "name": "sum_sales", "args": {}}

event: tool_result
data: {"id": "call_stub_1", "ok": true, "summary": "{\"matched_row_count\": 48, ...}"}

event: status
data: {"stage": "planning", "message": "2단계: 모델에게 다음 행동을 묻는다"}

event: token
data: {"text": "요청하신 내용을 확인했습니다."}

event: done
data: {"finish_reason": "stop"}
```

`/chat`에 "2분기 노트북 매출이 둔화된 이유는?"을 보내면 5단계와 같은 순서
(`retrieving` → `analyzing` → `verifying` → `writing`)로 이벤트가 오고,
`sources` 이벤트에는 이번 검색에서 실제로 반환된 청크만 남는다 — 이 부분은
5단계와 똑같다. **이벤트 이름과 순서가 똑같다는 것 자체가 이 장의 요점이다**:
프레임워크를 바꿔도 브라우저가 보는 프로토콜(`PROJECT-SPEC.md` 4장)은
바뀌지 않는다.

완료 기준은 "LangChain으로 다시 만들었다"가 아니라 **"5단계·3단계와 같은
입력을 줬을 때 같은 결과가 나오는지 실제로 비교했다"**다. 5절에서
`scripts/compare_with_step05.py`를 실제로 실행한 결과를 그대로 보여준다.

## 2. 핵심 개념

### 2-1. 누가 무엇을 책임지는가

이 장부터 등장인물이 하나 늘어난다. `PROJECT-SPEC.md`가 요구하는 대로,
라이브러리 이름을 나열하지 않고 각 주체의 책임을 구분해서 짚는다.

| 주체 | 책임 | 이 장에서의 예 |
| --- | --- | --- |
| 모델 서버 | 메시지를 받아 텍스트·도구 호출 요청을 만든다 | 1단계부터 그대로. OpenAI 호환 `/chat/completions`, `/embeddings` |
| 벡터 DB(Milvus) | 벡터·희소 벡터를 저장하고 유사도 검색·BM25 랭킹을 계산한다 | 4·5단계부터 그대로. 여기서도 실제 검색 연산은 Milvus 서버(또는 Milvus Lite)가 한다 |
| 애플리케이션(`docagent`) | 요청을 받고, 검색·모델 호출 순서를 정하고, 결과를 검증하고, SSE로 포장한다 | `app.py`, `chains.py`, 인용 검증(`citations.py`) |
| **프레임워크(LangChain)** | 모델 호출·프롬프트 조립·도구 스키마·에이전트 루프·벡터 저장소 연결에 **공통으로 반복되는 배관 코드**를 표준 인터페이스로 대신 짜 준다 | `ChatOpenAI`, `@tool`, `create_agent`, `langchain_milvus.Milvus` |

LangChain은 이 표 네 번째 줄, **애플리케이션 계층의 선택지 중 하나**다.
모델 서버가 실제로 텍스트를 생성하는 것도, Milvus가 실제로 벡터를 비교하는
것도 LangChain이 하는 일이 아니다. LangChain을 걷어내도 모델 서버와 Milvus는
그대로 남는다 — 반대로 LangChain만 있고 모델 서버나 Milvus가 없으면 아무것도
동작하지 않는다. "LangChain이 모든 것의 기반"이라고 보면 이 의존 관계가
거꾸로 보인다.

### 2-2. 모델 인터페이스와 메시지

1~5단계는 `openai` 파이썬 SDK의 `client.chat.completions.create(...)`를
직접 불렀다. LangChain은 그 자리에 `BaseChatModel`이라는 공통 인터페이스를
두고, OpenAI 호환 서버용 구현체로 `langchain_openai.ChatOpenAI`를 제공한다.

```python
from langchain_openai import ChatOpenAI

chat = ChatOpenAI(
    base_url="http://localhost:8000/v1",  # OPENAI_BASE_URL과 같은 값
    api_key="sk-local-example-key",         # OPENAI_API_KEY와 같은 값
    model="your-chat-model-name",            # CHAT_MODEL과 같은 값
)
```

**이 세 값의 의미가 바뀌지 않는다는 것이 중요하다.** `ChatOpenAI`라는
이름 때문에 OpenAI사의 서버에만 붙는다고 오해하기 쉽지만, `base_url`을
이 프로젝트가 쓰는 로컬/사내 모델 서버로 주면 그 서버에 붙는다 — 1단계부터
써 온 "OpenAI 호환 API"라는 개념 위에 LangChain이 얹혀 있을 뿐이다. 서버가
도구 호출이나 구조화 출력을 지원하지 않으면, `ChatOpenAI`로 불러도 여전히
지원하지 않는다. LangChain은 호출 방식을 표준화할 뿐 모델 서버의 기능을
새로 만들어 주지 않는다 — 1단계 `scripts/check_server_features.py`로 미리
확인하는 습관은 이 장에서도 그대로 필요하다.

메시지는 `{"role": "user", "content": "..."}` 딕셔너리 대신
`HumanMessage`/`SystemMessage`/`AIMessage`/`ToolMessage` 객체를 쓸 수도
있지만, `ChatOpenAI.invoke(...)`는 지금까지 써 온 딕셔너리 리스트도 그대로
받는다 — 이 장의 코드는 기존 코드와의 연속성을 위해 딕셔너리 형태를 최대한
유지한다.

### 2-3. 프롬프트 템플릿과 구조화된 출력

5단계는 컨텍스트 문자열과 질문을 f-string으로 이어 붙였다. LangChain의
`ChatPromptTemplate`은 같은 일을 "이름 있는 자리표시자가 있는 템플릿"으로
선언한다.

```python
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_messages([
    ("system", "..."),
    ("user", "[컨텍스트]\n{context}\n\n[질문]\n{question}"),
])
```

`prompt.invoke({"context": ..., "question": ...})`가 f-string과 똑같은
문자열을 만든다 — 자리표시자 이름이 곧 입력 딕셔너리의 키가 된다는 점만
다르다. 구조화된 출력(1단계의 `response_format={"type": "json_schema", ...}`)은
`chat_model.with_structured_output(PydanticModel)`로 감쌀 수 있다. 이때도
**서버가 `response_format`을 지원해야 동작한다** — LangChain은 내부적으로
같은 `response_format` 파라미터를 만들어 보낼 뿐이다(직접 설치해서 스텁
서버 대상으로 확인했다. 4절 참고).

### 2-4. 문서·임베딩·벡터 저장소·Retriever

5단계는 이 네 가지를 각자 다른 이름으로 다뤘다: 청크는 딕셔너리, 임베딩은
`list[float]`, 저장은 `pymilvus.MilvusClient`, 검색 결과는 다시 딕셔너리.
LangChain은 이 네 단계에 공통 타입을 둔다.

| 개념 | LangChain 타입 | 5단계에서의 대응 |
| --- | --- | --- |
| 청크 하나 | `Document`(`page_content` + `metadata`) | 딕셔너리(`text`, `doc_id`, `page`, ...) |
| 임베딩 모델 | `Embeddings`(`embed_documents`/`embed_query`) | `embed_texts()` 함수 |
| 벡터 저장소 | `VectorStore`(`Milvus` 등 구현체) | `MilvusClient` + 직접 만든 스키마 |
| 검색기 | `Retriever`(`vector_store.as_retriever()`) | `dense_search`/`sparse_search`/`hybrid_search` 함수 |

`Retriever`는 "질문 문자열을 넣으면 `Document` 리스트가 나온다"는 계약만
지키는 인터페이스다. Milvus를 다른 벡터 DB로 바꿔도(`Chroma`, `Pinecone`
등) 이 계약은 그대로라 애플리케이션 코드(프롬프트 조립·인용 검증)를 거의
바꾸지 않아도 된다는 것이 이 추상화의 이점이다 — 대신 4-3절에서 보듯
Milvus 고유 기능(BM25 랭커 종류, 필드 이름)을 쓰려면 그 추상화 밑으로
내려가야 한다.

### 2-5. Runnable 구성과 실행

LangChain의 프롬프트·모델·출력 파서는 모두 `Runnable`이라는 공통 인터페이스를
구현한다. `Runnable`은 `invoke()`(한 번에 실행), `stream()`(조각 단위로
실행), `batch()`(여러 입력을 한 번에 실행)을 같은 방식으로 제공하고,
`|`(파이프) 연산자로 여러 `Runnable`을 이어 붙이면 새 `Runnable`이 된다 —
이 조합 방식을 LCEL(LangChain Expression Language)이라 부른다.

```python
chain = prompt | chat_model | StrOutputParser()
chain.invoke({"context": ..., "question": ...})   # 한 번에
chain.stream({"context": ..., "question": ...})    # 조각 단위로
```

5단계는 일반 호출(`chat_completion`)과 스트리밍 호출(`stream_chat_completion`)을
따로 만들어야 했다. LCEL 체인은 같은 정의로 두 실행 방식을 다 제공한다 —
"조립"과 "실행 방식"이 분리돼 있기 때문이다. 다만 이 장의 `/chat`
엔드포인트는 여전히 "답변을 다 받은 뒤 인용을 검증하고 나서 재생"하는 방식을
쓴다(4-6절) — `.stream()`을 쓴다고 해서 인용 검증처럼 전체 텍스트가 필요한
작업까지 조각 단위로 처리할 수 있는 것은 아니다.

### 2-6. Tool과 기본 에이전트

3단계는 도구를 세 조각(JSON 스키마 딕셔너리, pydantic 검증 모델, 파이썬
함수)으로 손수 맞춰야 했다. LangChain의 `@tool` 데코레이터는 파이썬 함수의
타입 힌트와 docstring만으로 이 세 조각을 자동으로 만든다.

```python
from langchain_core.tools import tool

@tool
def sum_sales(product: str | None = None, quarter: str | None = None) -> dict:
    """sales.csv에서 조건에 맞는 매출 합계를 계산한다.

    Args:
        product: 집계할 제품명. 생략하면 전체 제품.
        quarter: 집계할 분기. 생략하면 전체 기간.
    """
    ...
```

`sum_sales.args_schema`가 pydantic 모델, `sum_sales.args_schema.model_json_schema()`가
OpenAI 호환 `tools` 배열에 들어갈 JSON 스키마다. `create_agent(model=...,
tools=[sum_sales, ...])`는 3단계의 `while` 루프(모델 호출 → `tool_calls`
확인 → 도구 실행 → 결과 추가 → 다시 모델 호출)에 해당하는 그래프를 대신
만든다.

**주의**: `create_agent`는 내부적으로 **LangGraph** 위에 구현돼 있다 —
`pip install langchain`을 하면 `langgraph`가 의존성으로 함께 설치되는 것을
직접 확인했다(3절 참고). 이것은 7단계에서 배우는 "State/Node/Edge를 직접
설계하는 LangGraph"와는 다른 이야기다: `create_agent`를 쓸 때는 그 내부
그래프를 우리가 짜지 않는다. 이 장은 "미리 만들어진 에이전트 그래프를
가져다 쓰는" 단계이고, 7단계는 "그래프를 직접 설계하는" 단계다.

### 2-7. 모델·도구 호출 전후의 미들웨어

2단계에서 "미들웨어"는 HTTP 요청 하나를 감싸는 계층(요청 ID 부여, 처리
시간 측정)이었다. 여기서 말하는 미들웨어는 그것과 **다른 층**이다.
`docs/02-fastapi-chat.md` 2-6절의 비교표를 이 장의 실제 구현으로 채우면
다음과 같다.

| | HTTP 미들웨어(2단계) | 에이전트 미들웨어(이 장) |
| --- | --- | --- |
| 개입 단위 | HTTP 요청 하나 | 모델 호출 한 번, 도구 호출 한 번 |
| 실행 시점 | 요청이 들어올 때 ~ 응답이 끝날 때 | `create_agent` 그래프 안에서 모델을 부르기 직전/직후, 도구를 부르기 직전/직후 |
| 누가 제공하는가 | ASGI 서버·프레임워크(Starlette) | 에이전트 프레임워크(LangChain `AgentMiddleware`) |
| 무엇을 모르는가 | 요청 본문 안에 대화가 몇 턴 있는지, 모델이 도구를 부를지는 모른다 | 이 호출이 HTTP로 왔는지, 몇 번째 HTTP 요청인지는 모른다(`ModelRequest`/`ToolCallRequest`에 그 정보가 없다) |
| 이 장에서 실제로 쓰는 것 | (2단계 그대로, 이 장에서 다시 쓰지 않음) | `RepeatToolCallGuardMiddleware`(직접 작성), `ToolErrorMiddleware`(LangChain 내장), `CallLoggingMiddleware`(직접 작성) |
| 대표 용도 | 인증, CORS, 로깅, 레이트 리밋 | 반복 호출 차단, 도구 오류를 모델이 읽을 문자열로 변환, 프롬프트 주입 방어, 호출 단위 감사 로그 |

LangChain의 `AgentMiddleware`는 `wrap_model_call(request, handler)`,
`wrap_tool_call(request, handler)`, `before_model`, `after_model` 같은
훅을 오버라이드하는 클래스다. `handler(request)`를 부르면 실제 모델/도구
호출이 일어나고, 그 앞뒤에 원하는 코드를 끼워 넣을 수 있다 — HTTP
미들웨어의 `call_next(request)`와 같은 모양이다. `create_agent(...,
middleware=[a, b, c])`에 넘긴 순서대로 바깥에서 안쪽으로 감싼다(4-5절에서
실제 순서 문제를 코드로 보여준다).

### 2-8. 직접 구현과 LangChain 구현 비교

3·5단계에서 손으로 짠 것과 이 장에서 LangChain이 대신하는 것을 나란히
정리한다.

| 기능 | 직접 구현(3·5단계) | LangChain 구현(6단계) | LangChain이 대신 해주는 것 | LangChain을 써도 여전히 직접 해야 하는 것 |
| --- | --- | --- | --- | --- |
| 모델 호출 | `httpx.post`/`openai` SDK를 감싼 `chat_completion` | `ChatOpenAI.invoke()` | 재시도·타임아웃 처리, 메시지 타입 변환, 스트리밍/일반 호출 인터페이스 통일 | 서버가 실제로 그 기능(도구 호출 등)을 지원하는지 확인하는 일 |
| 도구 정의 | JSON 스키마 딕셔너리 + pydantic 모델을 손으로 맞춤 | `@tool` 데코레이터가 타입 힌트·docstring에서 자동 생성 | 스키마와 구현이 어긋날 가능성 제거 | 도구 실행 실패를 모델에게 어떻게 설명할지(의미 있는 오류 메시지)는 직접 정함 |
| 도구 실패 처리 | `ToolRunResult(ok=False, ...)`로 항상 감싸 반환 | `ToolErrorMiddleware(on_error=...)`로 예외를 선택적으로 문자열로 변환 | "예외를 ToolMessage로 바꾸는" 배관 자체 | 어떤 예외를 변환할지 판단(모르는 예외까지 삼키면 진짜 버그를 놓친다) |
| 반복 호출 차단 | `call_counts` 딕셔너리로 (도구, 정규화한 인자) 3회 감지 | 내장 `ToolCallLimitMiddleware`는 **도구 이름당** 횟수만 세고 인자는 구분하지 않는다 — PROJECT-SPEC.md 7장 규칙과 다르다 | 도구 이름 단위의 간단한 횟수 제한 | 인자까지 구분하는 반복 감지는 직접 미들웨어(`RepeatToolCallGuardMiddleware`)로 작성 |
| 에이전트 루프 | `while` 문 + 단계 수·시간 검사 | `create_agent`가 만드는 LangGraph 그래프 | 모델 호출 ↔ 도구 실행 왕복 자체 | 최대 단계 수는 `recursion_limit`으로 별도 전달, 시간 제한은 여전히 애플리케이션이 감시 |
| 벡터 저장소 스키마 | `pk`/`dense`/`sparse`/`text`/`doc_id`/... 9개 필드를 명시적으로 선언 | `langchain_milvus.Milvus`가 `primary_field`/`text_field`/`vector_field`만 받고 나머지는 동적 필드(JSON)로 묶음 | 컬렉션·인덱스 생성 코드 자체 | 5단계처럼 메타데이터 각각을 독립된 스칼라 컬럼으로 두려면 이 통합을 우회해야 한다 |
| 하이브리드 검색 결합 | `AnnSearchRequest` 두 개 + `RRFRanker`/`WeightedRanker`를 직접 조합 | `as_retriever(search_kwargs={"ranker_type": ...})` | 랭커 객체 생성·`hybrid_search` 호출 조립 | Dense 전용/Sparse 전용 검색은 LangChain 쪽에서 자연스러운 API가 없다 — 이 장은 가중치 0을 주는 우회로 흉내 낸다(4-4절) |
| 인용 검증 | 검색 결과 집합과 `[Sn]`을 대조 | (그대로 재사용) | 없음 | 이 검증 로직은 어느 구현에서든 애플리케이션이 직접 짜야 한다 — LangChain은 "모델이 만든 인용이 진짜인지"를 대신 확인해 주지 않는다 |
| SSE 이벤트 포맷 | `events.py`의 함수들 | (그대로 재사용) | 없음 | LangChain은 HTTP/SSE를 모른다 — 이 부분은 5단계와 100% 동일한 코드다 |

마지막 두 줄이 특히 중요하다. **LangChain을 도입해도 이 프로젝트 고유의
안전장치(가짜 출처 방지, 이벤트 스키마)는 하나도 저절로 생기지 않는다.**
프레임워크가 대신해 주는 것은 "여러 프로젝트에서 반복되는 배관 코드"이지,
"이 프로젝트만의 요구사항"이 아니다.

## 3. 실습 준비

### 3-1. 패키지와 버전

이 문서 작성 시점(2026-09-09)에 아래 명령으로 실제 설치해 버전을 확인했다.

```bash
python -m venv .venv && source .venv/bin/activate
pip install langchain langchain-openai langchain-milvus pymilvus
pip list | grep -iE "langchain|pymilvus|milvus|openai|langgraph"
```

```
langchain            1.4.0
langchain-core       1.6.2
langchain-milvus     0.4.0
langchain-openai     1.6.1
langchain-protocol   0.0.19
langgraph            1.2.11      # langchain의 의존성으로 자동 설치됨
langgraph-checkpoint 4.2.0
langgraph-prebuilt   1.1.0
langgraph-sdk        0.4.4
langsmith            0.12.2      # langchain의 의존성으로 자동 설치됨(9단계에서 다시 등장)
milvus-lite          3.2.1
openai               3.10.0
pymilvus             3.0.1
```

`requirements.txt`는 `PROJECT-SPEC.md` 10절대로 범위로 고정한다.

```
langchain>=1.4,<2.0
langchain-openai>=1.6,<2.0
langchain-milvus>=0.4,<0.5
openai>=3.10,<4.0
pymilvus>=2.5,<4.0
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
python-dotenv>=1.0,<2.0
pydantic>=2.7,<3.0
httpx>=0.27,<1.0
pytest>=8.0,<9.0
```

`langchain-milvus`는 아직 1.0 미만(0.4.0)이라 마이너 버전이 바뀌면 API가
바뀔 수 있다 — 그래서 다른 패키지보다 좁은 범위(`<0.5`)로 고정했다.

### 3-2. 환경 변수

`PROJECT-SPEC.md` 2절의 이름을 그대로 쓴다. 새 변수는 없다 — LangChain
쪽 설정(모델 이름, base_url, Milvus URI)도 전부 5단계까지 써 온 이름을
그대로 재사용한다는 것 자체가, "OpenAI 호환 서버·Milvus에 붙는다"는 이
프로젝트의 전제가 프레임워크를 바꿔도 변하지 않는다는 뜻이다.

```
OPENAI_BASE_URL=http://localhost:8000/v1
OPENAI_API_KEY=sk-local-example-key
CHAT_MODEL=your-chat-model-name
EMBEDDING_MODEL=your-embedding-model-name

DOCAGENT_MILVUS_URI=http://localhost:19530
DOCAGENT_MILVUS_TOKEN=
DOCAGENT_MILVUS_COLLECTION=docagent_chunks

APP_BASE_URL=http://localhost:8080
REQUEST_TIMEOUT_SECONDS=60
MAX_AGENT_STEPS=8
MAX_AGENT_SECONDS=120
```

### 3-3. 폴더 구조

```
step06_langchain/
├── docagent/
│   ├── config.py      # 환경 변수 로딩 (5단계와 필드 동일)
│   ├── llm.py           # 5단계 "직접 구현" 경로 그대로 (비교용으로 유지)
│   ├── models.py         # LangChain 모델 인터페이스
│   ├── tools.py            # @tool로 재구성한 3단계 도구
│   ├── agent.py              # create_agent + 미들웨어
│   ├── chains.py               # LCEL RAG 체인
│   ├── events.py                 # SSE 이벤트 스키마 (5단계와 동일)
│   ├── app.py                      # FastAPI 앱
│   └── rag/
│       ├── store.py                  # langchain_milvus.Milvus 구성
│       ├── ingest.py                   # 적재
│       ├── search.py                     # dense/sparse/hybrid
│       └── citations.py                    # 인용 검증 (5단계 그대로)
├── static/, data/, scripts/, tests/
```

### 3-4. 실행 조건

이 장은 서버에 **채팅 + 임베딩 + 도구 호출**(그리고 응용 과제에서
구조화 출력) 네 가지가 모두 필요하다. LangChain을 쓴다고 이 요구사항이
줄어들지 않는다 — `check_server_features.py`(1단계)로 먼저 확인한다.
서버 없이 배관만 확인하려면 `code/_tools/fake_openai_server.py` 스텁
서버와 로컬 파일 경로를 준 Milvus Lite를 쓴다(5절 참고).

## 4. 단계별 구현

### 4-1. 모델 인터페이스: `docagent/models.py`

`docagent/models.py`
```python
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from docagent.config import Settings, get_settings


def build_chat_model(settings: Settings | None = None, *, temperature: float = 0.2) -> ChatOpenAI:
    s = settings or get_settings()
    return ChatOpenAI(
        base_url=s.openai_base_url,
        api_key=s.openai_api_key,
        model=s.chat_model,
        temperature=temperature,
        timeout=s.request_timeout_seconds,
    )


def build_embeddings(settings: Settings | None = None) -> OpenAIEmbeddings:
    s = settings or get_settings()
    return OpenAIEmbeddings(
        base_url=s.openai_base_url,
        api_key=s.openai_api_key,
        model=s.embedding_model,
        check_embedding_ctx_length=False,
    )
```

`check_embedding_ctx_length=False`가 왜 필요한지는 6절에서 실제로 재현한
오류로 설명한다 — 여기서는 "이 값을 빼면 이 프로젝트 환경에서 임베딩
자체가 안 될 수 있다"만 짚고 넘어간다.

### 4-2. Tool: `docagent/tools.py`

3단계 `sum_sales`를 `@tool`로 다시 쓰면 다음과 같다(전체 코드는
`docagent/tools.py`, 여기서는 `sum_sales`만 발췌한다).

`docagent/tools.py` (발췌)
```python
from langchain_core.tools import tool


class ToolExecutionError(Exception):
    """도구 실행 중 발생한, 모델에게 그대로 설명해도 되는 오류."""


@tool
def sum_sales(
    product: str | None = None,
    region: str | None = None,
    quarter: Literal["Q1", "Q2"] | None = None,
) -> dict[str, Any]:
    """sales.csv 판매 데이터에서 조건에 맞는 매출·판매수량 합계를 계산한다.

    제품별/지역별/분기별 매출 합계를 물을 때 사용한다.

    Args:
        product: 집계할 제품명. 예: '노트북'. 생략하면 전체 제품을 대상으로 한다.
        region: 집계할 지역명. 예: '서울'. 생략하면 전체 지역을 대상으로 한다.
        quarter: 집계할 분기. 'Q1' 또는 'Q2'. 생략하면 전체 기간을 대상으로 한다.
    """
    rows = _load_sales_rows()
    matched = _apply_filters(rows, product=product, region=region, quarter=quarter)
    if not matched:
        raise ToolExecutionError(
            f"조건에 맞는 판매 기록이 없다. (product={product!r}, region={region!r}, quarter={quarter!r})."
        )
    return {
        "matched_row_count": len(matched),
        "total_units": sum(r["units"] for r in matched),
        "total_revenue": sum(r["revenue"] for r in matched),
        "filters": {"product": product, "region": region, "quarter": quarter},
    }
```

3단계의 `SumSalesArgs`(pydantic 모델), `TOOL_SPECS`(JSON 스키마 딕셔너리),
`sum_sales(args: SumSalesArgs)`(실행 함수) 세 조각이 이 함수 하나로
줄었다. 실제로 자동 생성된 스키마를 확인해 보면 3단계가 손으로 쓴 것과
같은 모양이다.

```python
>>> sum_sales.args_schema.model_json_schema()
{'properties': {'product': {'anyOf': [{'type': 'string'}, {'type': 'null'}], 'default': None, ...},
                'region': {...}, 'quarter': {'anyOf': [{'enum': ['Q1', 'Q2'], 'type': 'string'}, {'type': 'null'}], ...}},
 'required': [], ...}
```

`required`가 빈 리스트라는 것도 3단계 `TOOL_SPECS`의 `"required": []`와
같다 — 파이썬 기본값(`= None`)이 그대로 "이 필드는 선택"이라는 의미로
번역된 것이다. `tests/test_tools.py`에서 이 사실을 코드로 확인한다.

### 4-3. Milvus 벡터 저장소: `docagent/rag/store.py`

`docagent/rag/store.py` (발췌)
```python
from langchain_milvus import BM25BuiltInFunction, Milvus

from docagent.models import build_embeddings

PK_FIELD = "pk"
DENSE_FIELD = "dense"
SPARSE_FIELD = "sparse"
TEXT_FIELD = "text"


def build_vector_store(settings=None, *, drop_old: bool = False) -> Milvus:
    s = settings or get_settings()
    embeddings = build_embeddings(s)
    return Milvus(
        embedding_function=[embeddings],
        builtin_function=[BM25BuiltInFunction(input_field_names=TEXT_FIELD, output_field_names=SPARSE_FIELD)],
        vector_field=[DENSE_FIELD, SPARSE_FIELD],
        primary_field=PK_FIELD,
        text_field=TEXT_FIELD,
        collection_name=s.milvus_collection,
        connection_args={"uri": s.milvus_uri, "token": s.milvus_token or None},
        consistency_level="Strong",
        auto_id=False,
        enable_dynamic_field=True,
        drop_old=drop_old,
    )
```

`langchain_milvus.Milvus`와 `BM25BuiltInFunction`(패키지: `langchain-milvus`
0.4.0, 직접 설치해서 `from langchain_milvus import Milvus,
BM25BuiltInFunction`이 되는 것과 생성자 시그니처를 확인했다)는 5단계
`store.py`가 `client.create_schema()` + `add_field()` + `Function(function_type=
FunctionType.BM25, ...)`로 손수 선언한 것과 같은 컬렉션을 만든다. 다른
점은 필드 9개 중 `primary_field`/`text_field`/`vector_field` 세 종류만
이름을 우리가 정할 수 있고, 나머지 메타데이터(`doc_id`, `doc_title`, `page`,
`chunk_index`, `source_url`)는 `enable_dynamic_field=True`로 켠 동적
필드(JSON 컬럼) 하나에 다 들어간다는 점이다 — 5단계처럼 각 메타데이터를
독립된 스칼라 컬럼으로 두고 그 컬럼에 별도 인덱스를 거는 것은 이 통합의
기본 경로가 아니다. 실제로 넣고 꺼내 보면 값 자체는 그대로 살아 있다(`pk`,
`doc_id`, `doc_title` 등 키로 그대로 접근된다) — 이 장 5절에서 실제로
확인한다.

### 4-4. Dense/Sparse/Hybrid 검색: `docagent/rag/search.py`

5단계는 검색 모드마다 별도 함수가 `client.search`/`client.hybrid_search`를
불렀다. 이 장은 하나의 `Milvus` 인스턴스에 대해 `similarity_search_with_score`를
부르고, 검색 모드 차이는 `ranker_type`/`ranker_params`로만 바꾼다.

`docagent/rag/search.py` (발췌)
```python
def hybrid_search(query, *, limit=5, ranker="rrf", rrf_k=60, dense_weight=0.5, sparse_weight=0.5, settings=None):
    vs = get_vector_store(settings)
    if ranker == "rrf":
        results = vs.similarity_search_with_score(query, k=limit, ranker_type="rrf", ranker_params={"k": rrf_k})
    else:
        results = vs.similarity_search_with_score(
            query, k=limit, ranker_type="weighted", ranker_params={"weights": [dense_weight, sparse_weight]}
        )
    return [_to_hit(doc, score) for doc, score in results]


def dense_search(query, *, limit=5, settings=None):
    """의미 검색만 반영하도록 sparse 가중치를 0으로 준 하이브리드 검색."""
    vs = get_vector_store(settings)
    results = vs.similarity_search_with_score(
        query, k=limit, ranker_type="weighted", ranker_params={"weights": [1.0, 0.0]}
    )
    return [_to_hit(doc, score) for doc, score in results]
```

`dense_search`/`sparse_search`가 "진짜 단일 필드 검색"이 아니라 "가중치
0을 준 하이브리드 검색"인 이유는 통제력을 잃은 지점을 정직하게 보여준다:
`langchain_milvus.Milvus`에 dense/sparse 두 필드를 함께 선언하면, 표준
`similarity_search` 경로는 항상 하이브리드 검색으로 간다 — 5단계처럼 한
필드만 검색하는 경로가 별도로 없다. 처음에는 "필드 하나만 선언한 별도
`Milvus` 인스턴스로 같은 컬렉션에 다시 연결"하는 방법을 시도했는데, 이
방법은 실제로 오류를 재현했다(6절에서 그대로 보여준다). 그래서 이 장은
같은 컬렉션을 한 번만 열고 가중치로 우회한다.

### 4-5. 에이전트: `docagent/agent.py`

미들웨어 세 개를 조립하는 부분만 발췌한다(전체 구현은 `docagent/agent.py`).

`docagent/agent.py` (발췌)
```python
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ToolCallRequest, ToolErrorMiddleware


class RepeatToolCallGuardMiddleware(AgentMiddleware):
    """같은 (도구 이름, 정규화한 인자) 조합이 REPEAT_LIMIT회 넘게 반복되면 중단한다."""

    def __init__(self, *, limit: int = REPEAT_LIMIT) -> None:
        super().__init__()
        self.limit = limit
        self.call_counts: dict[tuple[str, str], int] = {}

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        key = (request.tool_call["name"], _normalize_args(request.tool_call.get("args", {})))
        self.call_counts[key] = self.call_counts.get(key, 0) + 1
        if self.call_counts[key] > self.limit:
            raise RepeatedToolCallError(f"도구 '{key[0]}'을(를) 같은 인자로 {self.limit}회 넘게 호출했다.")
        return handler(request)


def _handle_tool_execution_error(exc: Exception, request: ToolCallRequest) -> str | None:
    if isinstance(exc, ToolExecutionError):
        return json.dumps({"error": "tool_error", "message": str(exc)}, ensure_ascii=False)
    return None  # 모르는 예외는 그대로 전파한다 — 버그를 삼키지 않는다


def build_agent(*, model=None):
    call_log_mw = CallLoggingMiddleware()
    repeat_guard_mw = RepeatToolCallGuardMiddleware(limit=REPEAT_LIMIT)
    tool_error_mw = ToolErrorMiddleware(on_error=_handle_tool_execution_error)

    agent = create_agent(
        model=model or build_chat_model(),
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        # 목록 순서대로 바깥에서 안쪽으로 감싼다: 로깅 -> 반복 감지 -> 오류 변환.
        middleware=[call_log_mw, repeat_guard_mw, tool_error_mw],
    )
    return agent, call_log_mw, repeat_guard_mw
```

두 가지를 짚는다.

- `ToolErrorMiddleware`는 **직접 만든 것이 아니라 `langchain.agents.middleware`가
  이미 제공하는 것**이다(실제로 `dir(langchain.agents.middleware)`로
  확인했다). 처음에는 `wrap_tool_call`을 직접 오버라이드해서 같은 일을
  하는 클래스를 만들었는데, 정확히 같은 역할의 내장 미들웨어가 있다는 것을
  나중에 확인하고 그쪽으로 바꿨다 — "필요한 걸 항상 직접 만들어야 하는
  것은 아니다"라는 것도 이 비교의 일부다. 반면 `RepeatToolCallGuardMiddleware`는
  내장 `ToolCallLimitMiddleware`가 인자를 구분하지 않기 때문에 직접
  만들어야 했다(2-8절 표 참고).
- 미들웨어를 감싸는 **순서가 관찰되는 것을 바꾼다.** `tool_error_mw`가
  예외를 `ToolMessage`로 바꾸고 나면, 그 바깥의 `repeat_guard_mw`와
  `call_log_mw`는 "성공한 호출"만 보게 된다(변환된 결과가 정상적인
  `ToolMessage`로 돌아오기 때문이다). 만약 반복 호출 감지가 도구 오류
  변환보다 안쪽에 있었다면 "실패한 호출"까지 반복 횟수에 셀지 말지가
  또 다른 설계 결정이 된다 — 이 장은 "성공한 호출만 반복으로 센다"를
  택했다.

`create_agent`가 만든 그래프는 `agent.stream(..., stream_mode="updates")`로
실행한다. 이 스트림은 그래프의 각 노드(`model`, `tools`)가 끝날 때마다 그
노드가 만든 변경분만 주는데, 이 지점들이 3단계 `while` 루프가 매 반복마다
`yield`하던 지점과 정확히 대응한다(`docagent/agent.py`의 `run_agent()` 참고).

### 4-6. LCEL RAG 체인: `docagent/chains.py`

`docagent/chains.py` (발췌)
```python
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM_PROMPT),
    ("user", "[컨텍스트]\n{context}\n\n[질문]\n{question}"),
])


def build_answer_chain(*, model=None, temperature: float = 0.2):
    return _PROMPT | (model or build_chat_model(temperature=temperature)) | StrOutputParser()


def answer_with_citations(question, *, settings=None, model=None, top_k=5, ranker="rrf"):
    s = settings or get_settings()
    hits = hybrid_search(question, limit=top_k, ranker=ranker, settings=s)
    if not hits:
        return citations.CitationResult(text="등록된 문서에서 관련 내용을 찾지 못했다...", sources=[], used_labels=[], dropped_labels=[]), []

    retrieved = citations.build_retrieved_sources(hits, app_base_url=s.app_base_url)
    chain = build_answer_chain(model=model)
    raw_answer = chain.invoke({"context": _build_context_block(retrieved), "question": question})

    result = citations.validate_and_link_citations(raw_answer, retrieved, app_base_url=s.app_base_url)
    return result, retrieved
```

`검색 -> 프롬프트 조립 -> 모델 호출 -> 검증` 순서는 5단계 `_chat_stream`과
완전히 같다. 바뀐 것은 "프롬프트 조립 -> 모델 호출" 두 단계를 `_PROMPT |
model | StrOutputParser()`라는 하나의 `Runnable`로 표현했다는 것뿐이다.
검색과 인용 검증을 이 체인 안에 억지로 우겨 넣지 않은 이유는 2-8절에서
설명한 대로다 — "이번 검색에서 실제로 반환된 청크가 몇 개인지"를 알아야
검증할 수 있는 이 프로젝트 고유의 규칙은 범용 `Runnable` 합성으로 표현할
이유가 없다.

### 4-7. FastAPI 앱: `docagent/app.py`

```python
@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(_rag_chat_stream(req), media_type="text/event-stream")


@app.post("/agent/chat")
async def agent_chat(req: AgentChatRequest) -> StreamingResponse:
    return StreamingResponse(_agent_chat_stream(req), media_type="text/event-stream")
```

`docagent/events.py`는 5단계 파일을 한 글자도 바꾸지 않고 옮겨왔다. SSE
프레임을 만드는 `token_event`/`status_event`/`tool_call_event`/... 함수들은
LangChain을 전혀 모른다 — `agent.py`와 `chains.py`가 만든 결과를 이 함수에
넘기기만 하면 된다. 이것이 "이벤트 스키마는 프레임워크와 무관하다"는
2-1절 주장의 증거다.

두 기능(RAG, 도구 호출)을 하나의 에이전트로 완전히 합치지 않고 엔드포인트
둘로 나눈 이유는 `docagent/app.py`의 모듈 docstring에 그대로 남겨 뒀다 —
요약하면, `create_retriever_tool`로 검색을 도구화해 한 에이전트에 얹는
것 자체는 쉽지만, PROJECT-SPEC.md 5장의 인용 검증 규칙("이번 검색에서
실제로 반환된 청크 집합"이 하나로 고정된다는 전제)이 "검색 도구를 여러
번, 다른 시점에 부를 수 있는" 에이전트와 잘 맞지 않기 때문이다. 이 문제를
제대로 풀려면 상태 추적이 필요하고, 이는 7단계(LangGraph로 상태를 명시적
으로 설계)의 주제에 더 가깝다.

## 5. 실행과 결과 확인

### 5-1. 스텁 서버 + Milvus Lite로 실행

```bash
cd code/step06_langchain
pip install -r requirements.txt

# 터미널 1
python ../_tools/fake_openai_server.py --port 8121

# 터미널 2
export OPENAI_BASE_URL=http://127.0.0.1:8121/v1
export OPENAI_API_KEY=sk-test
export CHAT_MODEL=stub-model
export EMBEDDING_MODEL=stub-embedding
export DOCAGENT_MILVUS_URI=/tmp/docagent_step06.db
uvicorn docagent.app:app --port 8080
```

서버 로그에 다음이 뜨면 시작할 때 문서 4개가 자동으로 적재된 것이다.

```
[startup] 4개 청크를 적재했다.
```

```bash
curl -N -X POST http://localhost:8080/agent/chat \
  -H 'content-type: application/json' \
  -d '{"message": "2분기 노트북 서울 매출 합계 알려줘"}'
```

1절의 예시와 같은 순서(`status starting` → `status planning` →
`status analyzing` → `tool_call` → `tool_result` → `status planning` →
`token` → `done`)로 이벤트가 오면 정상이다. `/chat`도 같은 방식으로
확인한다(1절 참고). 실제로 실행해 확인했다.

### 5-2. 5단계·3단계와의 비교

```bash
python scripts/compare_with_step05.py "2분기 노트북 매출이 둔화된 이유는?" --top-k 3
```

실제로 실행한 결과는 다음과 같다(스텁 서버·Milvus Lite 기준).

```
=== RAG 검색 비교: '2분기 노트북 매출이 둔화된 이유는?' (top_k=3) ===
6단계 컬렉션(langchain-milvus 스키마)에 4개 청크를 적재했다.
5단계 컬렉션(pymilvus 직접 스키마, uri=/tmp/docagent_compare_step05.db)에도 문서를 적재했다.
6단계(LangChain) 결과 순서: ['keyboard-q2-report#p0-c0', 'notebook-q2-report#p0-c0', 'notebook-q1-report#p0-c0']
5단계(직접 구현) 결과 순서: ['keyboard-q2-report#p0-c0', 'notebook-q2-report#p0-c0', 'notebook-q1-report#p0-c0']
겹치는 청크 수: 3 / 3
-> 순위까지 완전히 같다.

=== 도구 호출 에이전트 비교: '2분기 노트북 서울 매출 합계 알려줘' ===
6단계(LangChain create_agent): finish_reason=stop, steps_used=2
3단계(직접 구현 while 루프): finish_reason=stop, steps_used=2
-> finish_reason이 같다.
```

검색 결과는 청크 ID 순서까지 완전히 일치했다 — 같은 청킹 규칙, 같은
임베딩(스텁), 같은 RRF 결합(`k=60`)을 쓰므로 당연한 결과지만, "프레임워크를
바꿔도 검색 알고리즘 자체는 바뀌지 않는다"는 것을 코드로 확인한 것이다.
`finish_reason`과 `steps_used`(모델을 부른 횟수 집계) 모두 일치했다.

이 비교로 처음 실행했을 때는 `steps_used`가 3 대 2로 어긋났다. 원인은
알고리즘 차이가 아니라 **버그**였다 — `docagent/agent.py`의 `run_agent`가
루프를 돌기 전에 `stage: "planning"`인 시작 안내 이벤트
(`"LangChain 에이전트 실행을 시작한다"`)를 한 번 더 내보내는데,
`run_agent_collect`는 `stage == "planning"`인 `status` 이벤트 수를 그대로
세서 `steps_used`로 쓰고 있었다 — 실제 모델 호출(`model` 노드) 횟수가
아니라 이 시작 안내 이벤트까지 함께 세어 1이 더 많이 잡혔다. 3단계의
`run_agent`는 이런 시작 전용 이벤트를 내보내지 않으므로 이 문제가 없다.

**고친 내용은 두 가지다.**

첫째, 시작 안내 이벤트의 `stage`를 `"planning"`에서 `"starting"`으로 바꿨다.
`stage == "planning"`인 이벤트가 실제 모델 호출 시점에만 나오게 하기 위해서다.
`starting`은 PROJECT-SPEC.md 4절의 `status.stage` 목록에 추가했다 — 실행을
시작했지만 아직 첫 모델 호출 전인 구간을 가리킨다.

둘째, 이것이 더 중요한 수정이다. **집계값을 이벤트 개수로 역산하는 방식 자체를
버렸다.** `stage` 이름을 바꾸는 것만으로는 같은 종류의 버그가 다시 생긴다 —
누군가 나중에 `planning` 이벤트를 하나 더 내보내는 순간 `steps_used`는 다시
조용히 틀린 값이 된다. 소비자가 이벤트를 세는 방식은 생산자가 이벤트를
하나 더하거나 빼면 깨지는데, 그 깨짐이 예외가 아니라 **틀린 숫자**로 나타나기
때문에 알아차리기 어렵다.

그래서 `run_agent`가 실행 중 이미 세고 있는 `steps_used`를 `done` 이벤트의
`partial`에 실어 보내고, `run_agent_collect`는 그 값을 그대로 읽는다.
값을 아는 쪽이 값을 전달하고, 받는 쪽은 되짚지 않는다.

```python
# docagent/agent.py — 값을 아는 쪽이 실어 보낸다
yield AgentEvent(
    "done",
    {"finish_reason": finish_reason, "partial": {"steps_used": steps_used, "text": final_text}},
)

# 받는 쪽은 세지 않고 읽기만 한다
if event.kind == "done":
    finish_reason = event.data["finish_reason"]
    steps_used = event.data.get("partial", {}).get("steps_used", 0)
```

`done` 이벤트의 `partial` 필드는 8단계에서 부분 결과를 담으려고 추가한 선택
필드다(PROJECT-SPEC.md 4절). 이 값을 모르는 클라이언트는 그냥 무시하면 되므로
앞 단계와의 호환이 깨지지 않는다. 이 규칙도 PROJECT-SPEC.md 4절에 적어
이후 단계가 같은 실수를 반복하지 않게 했다.

**검증**: `pytest` 17개 통과, 스텁 서버에 붙여 `run_agent_collect`가
`finish_reason=stop, steps_used=2`를 반환하는 것과 이벤트 순서가
`status starting → status planning → status analyzing → tool_call →
tool_result → status planning → token → done`인 것을 실제로 확인했다.
`steps_used`는 3단계와 같은 2다.

### 5-3. 단위 테스트

```bash
python -m pytest tests/ -v
```

17개 테스트가 모두 통과하는 것을 실제로 확인했다(`test_agent.py` 4개,
`test_tools.py` 4개, `test_citations.py` 9개). `test_agent.py`는
`BaseChatModel`을 상속한 가짜 모델(`ScriptedChatModel`)을 `run_agent(...,
model=...)`에 주입해서, 모델 서버 없이도 도구 호출·반복 차단·오류 변환이
3단계와 같은 시나리오에서 같은 결과를 내는지 확인한다.

## 6. 실패 상황 실습

### 6-1. `OpenAIEmbeddings`가 조용히 네트워크 요청을 보내려다 막힌다

기본 설정(`check_embedding_ctx_length` 인자를 주지 않음)으로 임베딩을
불러본다.

```python
from langchain_openai import OpenAIEmbeddings

emb = OpenAIEmbeddings(base_url="http://127.0.0.1:8121/v1", api_key="sk-test", model="stub-embedding")
emb.embed_documents(["hello world"])
```

이 환경(사내망/오프라인/egress가 제한된 샌드박스)에서는 다음 오류로
실패하는 것을 실제로 재현했다.

```
requests.exceptions.ProxyError: HTTPSConnectionPool(host='openaipublic.blob.core.windows.net', ...):
  Max retries exceeded with url: /encodings/cl100k_base.tiktoken
  (Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden')))
```

**원인**: `OpenAIEmbeddings`는 기본적으로 `tiktoken.get_encoding("cl100k_base")`로
입력 텍스트의 토큰 수를 미리 세서, 모델의 컨텍스트 길이를 넘지 않게
나눠 보낸다(`check_embedding_ctx_length=True`가 기본값). 이 인코딩 파일이
로컬 캐시에 없으면 OpenAI가 호스팅하는 blob 저장소로 내려받으려
시도한다 — 이 프로젝트가 붙이는 서버가 OpenAI 정품 서버가 아닌데도,
**클라이언트 라이브러리가 토큰 계산을 위해 OpenAI 인프라에 접속하려 한다.**
`tiktoken`이 쓰는 `cl100k_base` 인코딩이 이 프로젝트가 실제로 쓰는
임베딩 모델의 토크나이저와 같다는 보장도 없다 — 어차피 근사값일 뿐이다.

**해결**: `check_embedding_ctx_length=False`를 준다(`docagent/models.py`에
반영돼 있다). 토큰 수를 세지 않고 원문 문자열을 그대로 `/embeddings`에
보낸다 — 4·5단계의 `embed_texts()`가 애초에 하던 방식과 같아진다. 이
문제는 네트워크가 열려 있는 환경에서는 재현되지 않을 수 있다 — 재현
여부는 인코딩 파일이 로컬에 이미 캐시돼 있는지에 달려 있으므로, "항상
실패한다"가 아니라 "이 환경에서 실제로 재현했다"는 사실만 기록해 둔다.

### 6-2. Milvus Lite + langchain-milvus 하이브리드 컬렉션이 재연결에서 깨진다

**재현 1 (단일 필드로 재연결)**: 처음에는 "진짜 Dense 전용 검색"을
만들려고, `vector_field="dense"`만 선언한 별도 `Milvus` 인스턴스로 이미
있는 하이브리드 컬렉션에 다시 연결해 봤다. Dense 단독 연결은 됐지만,
`vector_field="sparse"`만 선언한 별도 인스턴스로 같은 컬렉션에 연결하면
다음 오류가 재현됐다.

```
pymilvus.exceptions.MilvusException: (code=1, message=vector column must be FixedSizeList, got binary)
```

**재현 2 (그냥 새 프로세스에서 재연결)**: 원인을 좁히려고 더 단순한
경우를 시도했다 — 문서를 적재한 프로세스를 끝내고, **같은 dense+sparse
스키마 그대로** 새 파이썬 프로세스에서 다시 연결만 했다. 결과는 같았다.

```python
# 프로세스 A: 적재
vs = build_vector_store(drop_old=True)
vs.add_texts([...], metadatas=[...], ids=[...])
# 프로세스 A 종료

# 프로세스 B (새 프로세스): 재연결만
vs = build_vector_store()   # <- 여기서 바로 위와 같은 MilvusException 재현
```

`vs.client.flush(vs.collection_name)`를 **같은 프로세스 안에서** 명시적으로
불러도 flush 자체가 다음 오류를 내며 실패했다 — 즉 "재연결"이 아니라
"flush(디스크에 확정 반영)"가 실패 지점이라는 것을 확인했다.

```
background compaction/index build failed; will retry on next flush
ValueError: vector column must be FixedSizeList, got binary
```

**대조 실험(초기 결론, 틀렸음)**: 처음에는 여기서 "5단계가 `pymilvus`로
직접 선언한 스키마는 같은 절차로 문제없이 동작하니 이 오류는
`langchain_milvus.Milvus`가 하이브리드 컬렉션을 선언하는 방식에 특정된
문제"라고 결론 내렸다. 이 결론은 **틀렸다** — 이후 별도로 pymilvus만으로
최소 재현 스키마(pk/dense/text/sparse 네 필드만)를 만들어 같은 절차를
반복했더니, langchain-milvus를 전혀 쓰지 않았는데도 같은 오류가
재현됐다. 직접 실험해 확인한 결과는 다음과 같다.

| 구성 | 새 프로세스에서 sparse/hybrid 검색 |
| --- | --- |
| 5단계 코드의 실제 스키마(pymilvus 2.6.17로 직접 선언) | **동작한다**(여러 번 재확인) |
| 최소 재현 스키마(pk/dense/text/sparse만, pymilvus 2.6.17) | 실패 |
| 같은 최소 재현 스키마, pymilvus 3.0.1 | 실패 |
| `flush()` 호출을 뺀 경우 | 여전히 실패 |
| 필드 선언 순서를 5단계와 같게 바꾼 경우 | 여전히 실패 |
| langchain-milvus 0.4.0이 만든 컬렉션(이 장) | 실패 |

**즉 이 오류는 langchain-milvus 고유의 문제가 아니다.** 순수 pymilvus로
만든 컬렉션에서도 재현된다. pymilvus 버전, `flush()` 호출 여부, 필드
선언 순서는 모두 원인이 아님을 실험으로 배제했다. 반대로 5단계가 실제로
쓰는 스키마는 같은 조건에서 안정적으로 동작한다. **두 구성의 어떤
차이가 이 경계를 가르는지는 특정하지 못했다** — 원인을 아는 것처럼
쓰지 않는다.

[확인 필요: 이 오류를 유발하는 정확한 조건. 그리고 Milvus
standalone(Docker로 띄운 진짜 서버)에서도 같은 문제가 있는지 — 이
장은 Milvus Lite로만 확인했고 standalone 대상 실행은 검증하지 않았다]

**대응(우회책)**: `docagent/app.py`는 FastAPI `startup` 이벤트에서
"컬렉션이 없으면 적재한다"를 수행하고, 그 결과로 만들어진 `Milvus`
인스턴스를 `docagent.rag.search` 모듈 전역에 그대로 유지한다 —
**적재와 서비스를 같은 프로세스 안에 두어 재연결이나 flush를 피하는**
방식이다. 이것은 원인을 고친 것이 아니라 **원인을 모른 채 증상을
피한 우회책**이다 — 재연결이나 flush가 왜 실패하는지 모르는 채로,
그 상황 자체가 일어나지 않게만 만들었다. 하이브리드 검색을 실제로
쓰려면 이 우회책에 기대지 말고 Milvus standalone(Docker)을 권한다.
Milvus Lite는 이 우회책이 통하는 범위 안에서만, 즉 5단계처럼 적재와
검색을 같은 프로세스에서 끝내는 학습용 구성에 한해 쓸 수 있다.

### 6-3. `@tool` 함수가 던진 예외는 기본적으로 그대로 전파된다

`ToolErrorMiddleware` 없이 도구 함수가 일반 예외(`ValueError` 등)를
던지면 어떻게 되는지 직접 확인했다.

```python
@tool
def bad_tool(x: int = 1) -> dict:
    raise ValueError("실제 판매 데이터가 없다")

agent = create_agent(model=chat, tools=[bad_tool])
agent.invoke({"messages": [{"role": "user", "content": "call it"}]})
```

`ValueError: 실제 판매 데이터가 없다`가 그대로 위로 올라와 `agent.invoke(...)`
호출 자체가 예외로 끝났다(`langgraph/prebuilt/tool_node.py`의
`_default_handle_tool_errors`가 "도구 실행 자체의 예외는 다시 던진다"로
구현돼 있는 것을 소스에서 확인했다) — 반면 인자가 스키마와 맞지 않는
**검증 실패**(예: 필수 인자를 빠뜨림)는 자동으로 `ToolMessage("... Field
required ...")`로 바뀌어 모델에게 전달됐다. 즉 LangChain은 "입력이
스키마와 맞는가"까지는 자동으로 봐주지만, "도구가 실행되다가 비즈니스
로직상 실패했는가"는 기본적으로 봐주지 않는다 — 3단계가 `ToolRunResult`로
모든 실패를 감싸던 것과 다른 기본값이다. `docagent/agent.py`가
`ToolErrorMiddleware`를 반드시 추가하는 이유가 이것이다.

## 7. 응용 과제

**과제 1**: `search_docs`라는 이름으로 `hybrid_search`를 감싼 검색 도구를
`langchain_core.tools.create_retriever_tool`(retriever를 받아 `Tool`로
바꿔주는 함수, 실제로 설치해 시그니처를 확인했다)로 만들어 `/agent/chat`의
`create_agent(tools=[...])`에 `sum_sales`, `lookup_sales_rows`와 함께
추가해 본다. 모델이 "2분기 노트북 매출 둔화 원인을 문서에서 찾아 CSV
수치와 함께 설명해줘" 같은 질문에 검색 도구와 CSV 도구를 모두 쓰는지
확인한다. **인용 검증(`[Sn]` -> 근거 링크)은 이 도구 경로에 아직 연결돼
있지 않다** — 검색 도구가 여러 번 호출될 수 있어 "이번 답변의 근거
집합"이 하나로 고정되지 않기 때문이다(4-7절). 이 문제를 어떻게 풀지
설계만 해 보고, 실제 구현은 7단계 이후로 남겨도 된다.

**과제 2**: `docagent/rag/ingest.py`의 청킹 함수를 `langchain_text_splitters.
RecursiveCharacterTextSplitter`로 바꿔 보고, `scripts/compare_with_step05.py`로
검색 결과가 얼마나 달라지는지 비교한다. 청크 경계가 달라지면 청크 ID
(`{doc_id}#p{page}-c{chunk_index}`)도 달라진다는 점에 주의한다 — 근거
링크(`/sources/{doc_id}?...`)가 여전히 올바른 청크를 가리키는지 함께
확인한다.

**과제 3**: `ToolCallLimitMiddleware(run_limit=3, exit_behavior="end")`를
`RepeatToolCallGuardMiddleware`와 함께(또는 대신) 추가해서, "도구 이름
당" 제한과 "도구+인자 조합당" 제한이 실제로 어떻게 다르게 동작하는지
`tests/test_agent.py`에 테스트를 추가해 확인한다.

완료 기준: 세 과제 모두 `python -m pytest tests/ -v`로 회귀가 없는지
확인하고, 과제 1은 최소한 `tool_call` 이벤트에 `search_docs`가 등장하는
로그를 하나 남긴다.

## 8. 핵심 정리와 확인 질문

**핵심 정리**

- LangChain은 모델 서버·벡터 DB·애플리케이션과 별개의 계층이다 — 애플리케이션
  코드에서 반복되는 배관(모델 호출, 프롬프트 조립, 도구 스키마, 에이전트
  루프, 벡터 저장소 연결)을 표준 인터페이스로 대신 짜 줄 뿐, 모델이나
  Milvus가 실제로 하는 일을 대신하지 않는다.
- `ChatOpenAI`는 이름과 달리 OpenAI 정품 서버 전용이 아니다 — `base_url`로
  이 프로젝트의 OpenAI 호환 서버를 그대로 가리킨다. 서버가 지원하지 않는
  기능(도구 호출, 구조화 출력)은 LangChain을 써도 여전히 안 된다.
- `create_agent`는 3단계의 `while` 루프에 해당하는 그래프를 대신 만들어
  주지만, 내부적으로 LangGraph 위에 구현돼 있다 — 7단계에서 배우는
  "그래프를 직접 설계하는" LangGraph와는 다른 사용 방식이다.
- 에이전트 미들웨어(`wrap_model_call`/`wrap_tool_call`)는 모델·도구 호출
  한 번 단위로 개입한다 — HTTP 요청 하나 단위로 개입하는 2단계 미들웨어와
  다른 층이다. 반복 호출 차단(인자까지 구분)처럼 이 프로젝트 고유의
  규칙은 내장 미들웨어만으로 충분하지 않을 수 있다.
- 인용 검증, SSE 이벤트 스키마처럼 "이 프로젝트만의 요구사항"은 프레임워크를
  바꿔도 저절로 생기지 않는다 — 5단계 코드를 그대로 재사용해야 했다.
- 임베딩 토크나이저의 숨은 네트워크 요청은 실제로 재현했고 원인도
  특정했다(6-1절) — "프레임워크가 배관을 대신해 준다"는 편리함이 "그
  배관 안에서 무슨 일이 일어나는지 더 알기 어려워진다"는 대가와 함께
  온다는 것을 보여준다. Milvus Lite 하이브리드 컬렉션의 flush/재연결
  실패(6-2절)도 실제로 재현했지만, 정확한 원인은 이 문서 작성 시점까지
  특정하지 못했다 — pymilvus 최소 재현 스키마에서도 같은 오류가
  나는 것까지만 확인했고, langchain-milvus 탓이라고 단정할 근거는
  없다.

**확인 질문**

1. `ChatOpenAI(base_url=..., model=...)`를 쓸 때, 도구 호출이 지원되는지
   확인하는 책임은 LangChain에 있는가 애플리케이션에 있는가? 왜 그런가?
2. `create_agent`가 대신 만들어 주는 것과, `docagent/agent.py`가 미들웨어로
   직접 채워 넣어야 했던 것을 각각 하나씩 예로 들어 설명할 수 있는가?
3. `docagent/rag/search.py`의 `dense_search`가 "진짜 dense 전용 검색"이
   아니라 "가중치 0을 준 하이브리드 검색"인 이유는 무엇인가?
4. HTTP 미들웨어(2단계)와 에이전트 미들웨어(이 장)가 각각 "무엇을 모르는지"
   한 가지씩 설명할 수 있는가?
5. `scripts/compare_with_step05.py`가 두 구현을 같은 프로세스가 아니라
   서브프로세스로 각각 실행하는 이유는 무엇인가?

**이 장의 완성 코드**: `code/step06_langchain/`

**다음 장 연결**: 7단계(`step07_langgraph`)는 이 장이 `create_agent`에
맡겨 뒀던 "모델 호출 ↔ 도구 실행을 언제까지 반복할지"를 State·Node·Edge로
직접 설계한다. 이 장의 응용 과제 1에서 미뤄 둔 "검색 도구를 여러 번 부를
수 있을 때 인용 근거를 어떻게 추적할지" 문제도 7단계의 명시적 상태 설계로
다시 다룬다.

## 참고 문서

- 패키지 버전 확인: `pip install langchain langchain-openai langchain-milvus pymilvus` 후 `pip list`(2026-09-09 실측, 3절 표 참고).
- `create_agent` 시그니처, `langchain.agents.middleware`의 `AgentMiddleware`/`wrap_model_call`/`wrap_tool_call`/`ToolErrorMiddleware`/`ToolCallLimitMiddleware`: `pip install`한 `langchain` 1.4.0 패키지를 직접 `import`하고 `inspect.signature`/`inspect.getsource`/`inspect.getdoc`로 확인했다.
- `langchain_openai.ChatOpenAI`/`OpenAIEmbeddings`(1.6.1), `langchain_core.tools.tool`/`create_retriever_tool`, `langchain_core.prompts.ChatPromptTemplate`: 같은 방식으로 설치 후 직접 확인했다.
- `langchain_milvus.Milvus`/`BM25BuiltInFunction`(langchain-milvus 0.4.0) 생성자 시그니처: 설치한 패키지 소스(`vectorstores/milvus.py`, `function.py`)를 직접 읽어 확인했다.
- `langgraph`가 `langchain` 1.4.0의 의존성으로 함께 설치되는 것: `pip list` 결과로 직접 확인했다(`langgraph==1.2.11` 등).
- LangChain 에이전트/미들웨어 공식 참조 페이지(WebSearch 결과 요약으로 URL만 확인, 본문은 이 문서 작성 환경에서 `docs.langchain.com`/`reference.langchain.com` 접속이 막혀 있어 fetch하지 못했다):
  - https://reference.langchain.com/python/langchain/agents
  - https://docs.langchain.com/oss/python/langchain/middleware/custom
  - https://reference.langchain.com/python/langchain/agents/middleware/types/wrap_model_call
  - https://reference.langchain.com/python/langchain/agents/middleware/types/AgentMiddleware/wrap_tool_call
  - https://reference.langchain.com/python/langchain-milvus/function/BM25BuiltInFunction
- Milvus Lite 하이브리드(dense+sparse) 컬렉션의 flush/재연결 실패(`vector column must be FixedSizeList, got binary`): milvus-lite 3.2.1 + pymilvus 2.6.17/3.0.1 조합으로 직접 재현했다(6-2절) — langchain-milvus 0.4.0이 만든 컬렉션과, langchain-milvus를 전혀 쓰지 않은 순수 pymilvus 최소 재현 스키마 양쪽 모두에서. 정확한 원인은 특정하지 못했다(6-2절, `[확인 필요]` 참고).
- `OpenAIEmbeddings`의 `tiktoken` 원격 다운로드 시도(`openaipublic.blob.core.windows.net`): 이 문서 작성 환경(egress 제한)에서 직접 재현했다(6-1절). `check_embedding_ctx_length` 옵션은 `langchain_openai/embeddings/base.py` 소스에서 확인했다.
- `docs/02-fastapi-chat.md` 2-6절(HTTP 미들웨어와 에이전트 미들웨어 비교표) — 이 장 2-7절은 그 표를 그대로 이어받아 채웠다.

> 검증 상태: `docagent/`의 모든 `.py` 파일은 `python3 -m py_compile`을
> 통과했다. `tests/`의 17개 단위 테스트(모델 서버 불필요, 가짜
> `BaseChatModel` 사용)를 실제로 실행해 모두 통과하는 것을 확인했다.
> `code/_tools/fake_openai_server.py` 스텁 서버와 로컬 파일 경로의
> Milvus Lite를 조합해 `/chat`, `/agent/chat`, `/sources/{doc_id}`
> 세 엔드포인트를 실제 HTTP 요청으로 실행하고 SSE 이벤트 순서를
> 확인했다. `scripts/compare_with_step05.py`를 실제로 실행해 5단계·
> 3단계와의 비교 결과(5-2절)를 얻었다 — 이 비교로 `run_agent`의
> `steps_used` 집계 버그(시작 안내 이벤트를 모델 호출로 잘못 세던 것)를
> 실제로 찾아 `docagent/agent.py`를 고쳤고, 고친 뒤 다시 실행해
> `steps_used`가 3단계와 같은 값이 되는 것을 확인했다. 6절의 두 실패
> 상황은 모두 이 환경에서 실제로 재현한 것이며, 재현되지 않는 환경이
> 있을 수 있다는 점을 본문에 명시했다. **실제 LLM 모델 서버, 실제
> Milvus 서버(Standalone/클러스터, Milvus Lite가 아닌)를 대상으로 한
> 실행은 검증하지 않았다** —
> 스텁 서버는 모델이 아니므로 답변 품질과 실제 서버별 도구 호출·구조화
> 출력 지원 범위는 확인되지 않는다. 웹 UI(`static/`)는 5단계 파일을
> 그대로 재사용했고 `/chat` 경로 호환은 확인했지만 브라우저에서 직접
> 열어 클릭까지 확인하지는 않았다.
