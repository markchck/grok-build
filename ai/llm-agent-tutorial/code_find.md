# 코드 위치 대조표 (code_find)

## 이 문서를 읽기 전에 — 분석 대상이 요청과 다르다

요청은 **"재무 에이전트 서버 + BFF + 챗봇 UI"** 구조를 대상으로 했다.
**이 저장소에는 그런 프로젝트가 없다.** 저장소 전체에서 `StateGraph`, `MemorySaver`,
`PostgresSaver`, `astream_events`, `interrupt(`, `httpx.stream`을 검색했을 때 걸리는 파일은
모두 이 튜토리얼 코드(`ai/llm-agent-tutorial/code/`)이며, 재무 도메인 코드도 BFF 계층도 없다.
저장소의 나머지(`crates/`, `prod/`)는 Rust로 된 별개의 코드다.

그래서 이 문서는 **이 저장소에 실제로 있는 튜토리얼 코드**를 대상으로 같은 8개 항목을 대조한다.
찾는 프로젝트가 따로 있다면 이 문서는 그 프로젝트의 답이 아니다. 다만 항목별로
"튜토리얼은 이렇게 했다"는 참고 구현으로는 쓸 수 있고, **미구현 항목(5번 BFF, 6번 인증)은
그 프로젝트에서 반드시 직접 확인해야 하는 부분**이다.

- 대조 기준 시점: 2026-09-11
- 경로는 모두 `ai/llm-agent-tutorial/` 기준 상대 경로다.

---

## 1. LangGraph 그래프 정의

**구현됨.** 그래프는 세 곳에서 각각 다른 목적으로 빌드된다.

### 1-1. 메인 RAG 그래프

- **파일**: `code/step07_langgraph/docagent/graph/build.py`
- **함수**: `build_graph(deps, checkpointer=None)`
- **노드**: `classify`, `retrieve_dense`, `retrieve_sparse`, `verify`, `expand_query`, `answer`, `chitchat_answer`

```python
graph = StateGraph(GraphState)

graph.add_node("classify", make_classify_node(deps))
graph.add_node("retrieve_dense", make_retrieve_dense_node(deps))
graph.add_node("retrieve_sparse", make_retrieve_sparse_node(deps))
graph.add_node("verify", make_verify_node(deps))
graph.add_node("expand_query", expand_query)
graph.add_node("answer", make_answer_node(deps), retry_policy=_LLM_RETRY)

graph.add_edge(START, "classify")
graph.add_conditional_edges(
    "classify", route_after_classify,
    {"chitchat_answer": "chitchat_answer",
     "retrieve_dense": "retrieve_dense", "retrieve_sparse": "retrieve_sparse"},
)
graph.add_edge("retrieve_dense", "verify")
graph.add_edge("retrieve_sparse", "verify")
graph.add_conditional_edges(
    "verify", make_route_after_verify(deps),
    {"answer": "answer", "expand_query": "expand_query"},
)
graph.add_edge("expand_query", "retrieve_dense")   # 근거 부족 시 재검색 루프
return graph.compile(checkpointer=checkpointer)
```

### 1-2. 라우팅 함수

- **파일**: `code/step07_langgraph/docagent/graph/nodes.py:146`
- **함수**: `route_after_classify(state)` — 리스트를 반환해 병렬 fan-out을 만든다

```python
def route_after_classify(state: dict) -> str | list[str]:
    """조건 분기: chitchat이면 바로 답변, factual이면 dense/sparse를 병렬로 fan-out."""
    if state["category"] == "chitchat":
        return "chitchat_answer"
    return ["retrieve_dense", "retrieve_sparse"]
```

`make_route_after_verify(deps)`는 검색 근거가 부족하면 `expand_query`로 보내 재검색시키고,
충분하면 `answer`로 보낸다. 재검색 횟수는 `max_retrieval_rounds`로 제한한다.

### 1-3. 멀티에이전트 그래프

- **파일**: `code/step11_multi_agent/docagent/multiagent/graph_supervisor.py:64` (Supervisor 위임)
- **파일**: `code/step11_multi_agent/docagent/multiagent/graph_parallel.py:83` (병렬 조사 + 리듀서)
- **파일**: `code/step11_multi_agent/docagent/multiagent/handoff.py:124` (Handoff, `Command(goto=...)` 동적 라우팅)
- 같은 파일들이 `code/step13_ui_export/docagent/multiagent/` 에 그대로 복사돼 최종 앱에 배선돼 있다.

### 1-4. `tool_calls` 유무로 분기하는 라우팅 — **LangGraph 그래프 안에는 없다**

찾는 형태(그래프의 conditional edge가 `tool_calls` 유무로 `tools` 노드와 `END`를 가르는 것)는
이 코드베이스에 없다. **에이전트 루프를 LangGraph가 아니라 직접 `while` 문으로 돌리기 때문이다.**

- **파일**: `code/step13_ui_export/docagent/agent.py:630`
- **함수**: `_run_loop()` 안의 분기

```python
tool_calls = assistant_message.get("tool_calls")
if not tool_calls:
    raw_text = assistant_message.get("content") or ""
    yield AgentEvent("status", {"stage": "verifying", "message": "답변의 인용 표시를 검증하는 중"})
    retrieved = [citations.RetrievedSource(**s) for s in run.sources]
    result = citations.validate_and_link_citations(
        raw_text, retrieved, app_base_url=settings.app_base_url
    )
    run.final_text = result.text
    yield AgentEvent("token", {"text": result.text})
    yield AgentEvent("sources", {"items": result.sources})
    ...  # 루프 종료
yield AgentEvent("status", {"stage": "analyzing", "message": f"도구 {len(tool_calls)}건 실행 준비"})
for call in tool_calls:
    ...
```

`code/step06_langchain/docagent/agent.py`는 같은 루프를 `langchain.agents.create_agent`에
맡긴다(`create_agent`가 내부적으로 LangGraph 그래프를 만든다). 즉 **model↔tools 왕복 그래프는
프레임워크가 만들고 이 코드는 그 그래프를 직접 정의하지 않는다.**

---

## 2. Checkpointer (상태 영속화)

**두 가지 방식이 병존한다. 최종 앱이 쓰는 것은 LangGraph checkpointer가 아니다.**

### 2-1. LangGraph checkpointer — 7단계에만 있다

- **파일**: `code/step07_langgraph/docagent/app.py:72`
- **쓰는 것**: `AsyncSqliteSaver` (FastAPI가 비동기이므로). 데모 스크립트는 동기 `SqliteSaver`.
- **MemorySaver / PostgresSaver는 쓰지 않는다.**

```python
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

settings = get_settings()
Path(settings.checkpoint_db_path).parent.mkdir(parents=True, exist_ok=True)
async with AsyncSqliteSaver.from_conn_string(settings.checkpoint_db_path) as saver:
    app.state.graph = build_graph(_build_deps(), checkpointer=saver)
    yield
```

- **DB 연결 설정**: `checkpoint_db_path` (환경 변수 기반, `docagent/config.py`).
  Postgres 연결 설정은 어디에도 없다.
- **thread_id 생성/전달**: `code/step07_langgraph/docagent/app.py:95`

```python
thread_id = req.session_id or uuid.uuid4().hex
config = {"configurable": {"thread_id": thread_id}}
```

요청 본문의 `session_id`를 주면 그 값을 `thread_id`로 그대로 쓰고(= 이어서 재개),
없으면 매 요청 새로 발급한다(= 독립 실행).

### 2-2. 최종 앱(13단계)의 상태 영속화 — **직접 만든 SQLite 스토어**

- **파일**: `code/step13_ui_export/docagent/store.py` (원본은 `code/step08_hitl/docagent/store.py`)
- LangGraph checkpointer를 쓰지 않고 `runs` / `approvals` 테이블에 직접 저장한다.
- 식별자도 `thread_id`가 아니라 `run_id`다.

```sql
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,               -- running | paused_approval | done
    messages_json TEXT NOT NULL,
    usage_total INTEGER, usage_estimated INTEGER,
    finish_reason TEXT, final_text TEXT,
    pending_approval_id TEXT,
    ...
);
```

**주의**: 8·13단계의 `llm.py`/`agent.py`는 LangGraph를 거치지 않는 자체 루프이므로,
7단계의 checkpointer와 13단계의 `store.py`는 **서로 연결되지 않은 별개 영속화 계층**이다.

---

## 3. Interrupt / HITL 지점

**LangGraph의 `interrupt()`는 이 코드베이스에서 한 번도 호출되지 않는다. 승인 흐름은 직접 구현됐다.**

검색 결과 `langgraph.types`에서 가져다 쓰는 것은 `RetryPolicy`(7단계)와
`Command`(11단계 handoff의 `goto` 라우팅)뿐이고, `interrupt()` / `Command(resume=...)`는 없다.

### 3-1. 승인이 걸리는 지점

- **파일**: `code/step13_ui_export/docagent/agent.py` (`_run_loop` 안, 도구 실행 직전)
- **기준**: 도구별 "승인 필요" 표시. 튜토리얼의 민감 도구는 `archive_report`다
  (재무 프로젝트의 "주문 실행"에 해당하는 자리).
- 승인이 필요하면 루프를 **중단하고** run 상태를 `paused_approval`로 저장한 뒤 SSE로 승인 요청을 내보낸다.

### 3-2. payload 구조

- **파일**: `code/step13_ui_export/docagent/events.py:71`

```python
def approval_request_event(approval_id: str, action: str,
                           args: dict[str, Any], reason: str) -> str:
    return _sse("approval_request",
                {"id": approval_id, "action": action, "args": args, "reason": reason})
```

DB에는 `approvals` 테이블에 `approval_id`, `run_id`, `call_id`, `tool_name`, `args_json`,
`reason`과 최종 결정이 저장된다(`store.create_approval`).

### 3-3. 재개 코드

`Command(resume=...)`가 아니라 **HTTP 엔드포인트 + 자체 루프 재진입**이다.

- **결정 제출**: `code/step13_ui_export/docagent/app.py:194` — `POST /approvals/{approval_id}/decision`
  (`approve` / `reject` / `edit` 세 가지)
- **재개**: `code/step13_ui_export/docagent/app.py:140`

```python
@app.post("/chat/resume/{run_id}")
def resume(run_id: str, req: ResumeRequest = ResumeRequest()) -> StreamingResponse:
    conn = get_conn()
    if store.load_run(conn, run_id) is None:
        raise HTTPException(status_code=404, detail=f"run을 찾을 수 없다: {run_id}")

    def event_stream():
        for agent_event in advance_run(conn, run_id, tool_names=req.tool_names, ...):
            yield _render_event(agent_event.kind, agent_event.data)

    return StreamingResponse(event_stream(), media_type="text/event-stream", ...)
```

중복 실행 방지는 `store.try_acquire(conn, run_id)` (run 단위 잠금)와 승인 결정의 멱등 처리
(`already_decided`)로 한다.

---

## 4. SSE 스트리밍

**구현됨. 단, `astream_events`는 쓰지 않는다.**

### 4-1. 스트리밍 호출 지점

- `astream_events`는 **저장소 전체에서 사용처가 없다.**
- 대신 `graph.astream(..., stream_mode="updates")`를 쓴다.
  - `code/step07_langgraph/docagent/app.py:110`
  - `code/step06_langchain/docagent/agent.py:387` (`agent.astream`)
  - `code/step12_deep_agents/docagent/bridge.py` (`stream_mode="values"`)

```python
# code/step07_langgraph/docagent/app.py
async for update in graph.astream(initial_state, config=config, stream_mode="updates"):
    for _node_name, partial in update.items():
        for item in partial.get("trace", []):
            yield status_event(item["stage"], item["message"])
snapshot = await graph.aget_state(config)
final_state = snapshot.values
```

### 4-2. 처리하는 이벤트 타입

`on_chat_model_stream`, `on_tool_start` 같은 **LangChain 표준 이벤트 타입은 처리하지 않는다.**
`stream_mode="updates"`가 주는 `{노드 이름: 부분 상태}`를 읽어 애플리케이션이 자체 이벤트로 변환한다.
12단계 `bridge.py`만 메시지 타입(`AIMessage` / `ToolMessage`)을 직접 보고 변환한다.

### 4-3. SSE 포맷 변환 (event_generator 역할)

- **파일**: `code/step13_ui_export/docagent/events.py:38`

```python
def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

def token_event(text: str) -> str: ...
def status_event(stage: StatusStage, message: str) -> str: ...
def tool_call_event(call_id, name, args, source="local") -> str: ...
def done_event(finish_reason: FinishReason, *, partial=None) -> str: ...
```

### 4-4. StreamingResponse

10개 단계의 `docagent/app.py`가 모두 `StreamingResponse(media_type="text/event-stream")`를 쓴다
(step03~step13). 최종 앱은 `code/step13_ui_export/docagent/app.py:118` (`POST /chat`).

---

## 5. BFF의 relay 로직

**미구현.** 이 코드베이스에는 **BFF 계층 자체가 없다.** 구조가 2계층(브라우저 → FastAPI 앱)이고,
요청한 3계층(UI → BFF → 에이전트 서버)이 아니다.

유사하게 볼 수 있는 것은 두 가지지만, **relay가 아니다.**

| 찾는 것 | 이 코드베이스의 대응 | 차이 |
| --- | --- | --- |
| BFF → 에이전트 서버 스트리밍 HTTP 클라이언트 | `code/step08_hitl/docagent/llm.py`의 `httpx` 스트림 | 에이전트 서버가 아니라 **모델 서버**를 호출한다 |
| 프로세스 경계를 넘는 도구 호출 | `code/step13_ui_export/docagent/mcp_client.py` | MCP 서버(도구 공급원)이지 에이전트 서버가 아니다 |

이벤트를 가공/필터링(sanitize)해 다시 UI로 흘리는 계층도 없다. 앱이 만든 이벤트가 그대로 브라우저로 간다.
유일하게 "변환"에 해당하는 것은 `code/step12_deep_agents/docagent/bridge.py`(LangGraph 스트림 → SSE 이벤트)인데,
같은 프로세스 안의 변환이라 relay와는 성격이 다르다.

---

## 6. 인증/권한 체크

**미구현.** 어느 단계의 `app.py`에도 `Depends(...)` 기반 인증, `Authorization` 헤더 검사,
사용자 식별 코드가 없다.

- 사용자 인증 미들웨어: **없음**
- `thread_id` / `run_id` 소유권 검사: **없음.** `GET /runs/{run_id}`, `POST /chat/resume/{run_id}`,
  `GET /export/{run_id}.csv` 모두 **ID만 알면 누구나 호출할 수 있다.**
- 승인(resume) 요청에 대한 별도 권한 검사: **없음.** `POST /approvals/{approval_id}/decision`은
  "누가 승인했는가"를 확인하지도, 기록하지도 않는다.

유일하게 있는 미들웨어는 `code/step02_fastapi_chat/docagent/middleware.py`의
`RequestContextMiddleware`인데, 요청 ID와 소요 시간을 로그로 남길 뿐 인증과 무관하다.

**혼동하기 쉬운 점**: `Authorization` 문자열은 저장소에 3건 있지만
(`step01`/`step06`/`step08`의 `llm.py`), 전부 **모델 서버로 나가는 요청에 API 키를 실어 보내는
아웃바운드 헤더**다.

```python
"Authorization": f"Bearer {settings.openai_api_key}",
```

**들어오는 요청을 검사하는 코드가 아니다.** `Depends(...)`를 쓰는 app.py는 0건이다.

승인 흐름에서 실제로 하는 검사는 **중복 결정 방지**(같은 승인에 두 번 응답하면 첫 결정 유지)뿐이고,
이것은 권한이 아니라 멱등성 처리다.

---

## 7. 이벤트/메시지 영속화

**부분 구현.** 메시지와 승인 상태는 저장하지만, **이벤트 스트림 자체는 저장하지 않는다.**

### 7-1. 저장 위치

- **파일**: `code/step13_ui_export/docagent/store.py` (원본 `code/step08_hitl/docagent/store.py`)
- `runs.messages_json`에 대화 메시지 배열 전체를 JSON으로 저장한다. 메시지 단위 테이블은 없다.
- 저장 시점: `store.save_run(conn, run)` — 모델 호출 직후, 도구 실행 직후 등 루프 각 단계에서 호출.

### 7-2. "승인 대기 중" 상태

**별도 테이블로 있다.**

- `runs.status` 가 `paused_approval`
- `runs.pending_approval_id` 가 대기 중인 승인을 가리킴
- `approvals` 테이블에 `approval_id`, `run_id`, `call_id`, `tool_name`, `args_json`, `reason`, 결정 기록

프로세스를 `kill -9`로 강제 종료한 뒤 새 프로세스에서 같은 `run_id`로 조회하면 대기 상태가
복원되는 것을 8단계에서 실제로 확인했다(`docs/08-human-in-the-loop.md` 검증 상태).

### 7-3. 히스토리 조회 엔드포인트

- **파일**: `code/step13_ui_export/docagent/app.py:163`
- `GET /runs/{run_id}` — run의 현재 상태와 메시지를 돌려준다.
- `GET /approvals/{approval_id}` — 승인 건 조회.

**한계**: 지나간 SSE 이벤트(`status`, `tool_call`, `todo` 진행 상황)는 저장하지 않으므로
새로고침 시 **화면의 진행 표시는 복원되지 않는다.** 복원되는 것은 메시지와 승인 대기 상태뿐이다.

---

## 8. 이벤트 스키마 / API 명세

**정의는 있으나, 타입 시스템으로 강제되지는 않는다.**

### 8-1. 스키마 정의 위치

- **사람이 읽는 명세(단일 출처)**: `PROJECT-SPEC.md` 4절 — 이벤트 이름과 `data` 키를 표로 고정.
- **코드상의 정의**: `code/step13_ui_export/docagent/events.py`

```python
StatusStage = Literal["starting", "planning", "retrieving", "analyzing", "verifying", "writing"]
TodoState = Literal["pending", "running", "done", "failed"]
FinishReason = Literal[
    "stop", "max_steps", "timeout", "repeated_tool_call",
    "no_progress", "rejected_by_user", "cancelled", "token_budget",
]
```

**이벤트 `data`는 Pydantic 모델이 아니라 함수 인자 + `dict`다.** `Literal` 타입 별칭이
`stage`와 `finish_reason` 값만 좁혀 주고, `tool_call`의 `args` 같은 필드는 자유 `dict`다.
Pydantic `BaseModel`은 요청 본문(`ChatRequest`, `ResumeRequest`, `DecisionRequest`)과
도구 인자(`SumSalesArgs` 등)에만 쓰인다.

### 8-2. 생산자와 소비자가 정의를 공유하는가 — **공유하지 않는다**

- 생산자: `docagent/events.py` (Python)
- 소비자: `static/app.js` — 이벤트 이름을 **문자열로 하드코딩한 `switch` 문**

```javascript
switch (eventName) {
  case "status": ...
  case "tool_call": ...
  case "tool_result": ...
  case "todo": ...
  case "approval_request": ...
  case "sources": ...
  case "token": ...
  case "error": ...
  case "done": ...
}
```

TypeScript 인터페이스는 없다(저장소에 `.ts`/`.tsx` 파일 자체가 없다).
**Python 쪽에서 이벤트 이름이나 필드를 바꿔도 JS 쪽은 조용히 그 이벤트를 무시한다.**
두 정의를 묶어 주는 것은 `PROJECT-SPEC.md`라는 문서 규약뿐이고, 기계적 검증은 없다.

---

## (a) 빠져 있거나 허술한 부분

| # | 항목 | 상태 | 내용 |
| --- | --- | --- | --- |
| 5 | BFF relay | **없음** | BFF 계층 자체가 없다. UI가 에이전트 앱을 직접 호출한다. sanitize 계층도 없다 |
| 6 | 인증/권한 | **없음** | 인증, 소유권 검사, 승인자 확인이 전부 없다 |
| 1 | tool_calls 라우팅 | 그래프 밖 | 에이전트 루프가 LangGraph가 아니라 수동 `while` 문이다 |
| 4 | 이벤트 처리 | 비표준 | `astream_events`를 쓰지 않아 `on_chat_model_stream` 같은 표준 이벤트를 못 받는다 |
| 7 | 이벤트 영속화 | 부분 | 메시지·승인은 저장하지만 진행 이벤트는 저장하지 않아 새로고침 시 진행 표시가 복원되지 않는다 |
| 8 | 스키마 공유 | 문서뿐 | 생산자(Python)와 소비자(JS)가 정의를 공유하지 않는다 |
| 2 | 영속화 이원화 | 구조 문제 | 7단계 LangGraph checkpointer와 13단계 자체 SQLite 스토어가 서로 무관하게 존재한다 |

## (b) 프로덕션 안정성 리스크

우선순위 순이다.

### 1. 인증 부재 — 가장 큰 리스크

`run_id`만 알면 누구나 남의 대화를 읽고(`GET /runs/{run_id}`), 남의 작업을 재개하고
(`POST /chat/resume/{run_id}`), 남의 데이터를 CSV로 내려받을 수 있다(`GET /export/{run_id}.csv`).
`run_id`가 UUID라 추측은 어렵지만, **추측 난이도는 접근 제어가 아니다.**

특히 **승인(HITL)에 권한 검사가 없는 것이 위험하다.** 승인은 민감한 동작(재무 프로젝트라면
주문 실행)을 통과시키는 관문인데, 누가 승인했는지 확인하지도 기록하지도 않는다.
감사 추적이 불가능하고, 승인 권한이 없는 사람의 승인을 막을 수단이 없다.

→ 최소한 요청 주체 식별, `run.owner_id` 대조, `approvals`에 승인자 기록이 필요하다.

### 2. SQLite 단일 파일 — 수평 확장 불가

checkpointer(7단계 `AsyncSqliteSaver`)와 자체 스토어(13단계) 모두 로컬 SQLite 파일이다.

- 앱 인스턴스를 2개 이상 띄우면 **각자 다른 파일을 보게 되어 상태가 갈라진다.**
- 컨테이너가 재시작되면 볼륨을 붙이지 않는 한 상태가 사라진다.
- `store.try_acquire`의 run 단위 잠금도 **한 프로세스 안에서만 유효**하다. 여러 인스턴스가
  같은 파일을 공유해도 SQLite 잠금 경합으로 동작이 불안정해진다.

→ 프로덕션에서는 Postgres(LangGraph는 `PostgresSaver`)로 옮기고, 잠금도 DB 수준으로 올려야 한다.

### 3. 영속화 계층이 둘로 갈라져 있음

LangGraph checkpointer와 자체 `store.py`가 **같은 실행에 대한 상태를 서로 모른 채** 존재한다.
지금은 서로 다른 단계에서만 쓰여 충돌하지 않지만, 둘을 한 앱에서 같이 쓰는 순간
"어느 쪽이 진실인가"가 불명확해진다. 최종 앱(13단계)은 자체 스토어만 쓰므로
**LangGraph의 재개·타임트래블 기능을 전혀 활용하지 못한다.**

→ 하나를 정본으로 정해야 한다. HITL을 LangGraph `interrupt()`로 옮기면 checkpointer 하나로
합칠 수 있지만, 그러면 지금의 승인 저장 로직을 버려야 한다.

### 4. 이벤트 스키마 불일치가 조용히 발생

Python이 내보내는 이벤트 이름과 JS의 `switch` 문이 문서 규약으로만 묶여 있다.
이름을 바꾸면 **오류 없이 그 이벤트만 화면에서 사라진다.** 테스트도 이 경계를 검사하지 않는다.

→ 스키마를 한 곳에서 생성(JSON Schema → TS 타입)하거나, 최소한 알 수 없는 이벤트 이름이
오면 콘솔에 경고를 남기는 `default` 분기가 필요하다.

### 5. 토큰 사용량 추정치의 신뢰도

`usage`를 돌려주지 않는 모델 서버에서는 토큰 예산을 추정치로 계산한다
(`runs.usage_estimated` 플래그). 예산 초과 차단이 추정치에 기반하면
**실제 비용과 어긋날 수 있다.** 플래그는 남기고 있으므로 운영 시 이 값을 모니터링해야 한다.

---

## 검증 상태

> 이 문서의 모든 경로·함수명·코드 스니펫은 2026-09-11 기준 저장소의 실제 파일에서
> `grep`과 직접 열람으로 확인한 것이다. 스니펫은 실제 파일 내용을 그대로 옮겼다(생략 부분은 `...`로 표시).
> "미구현"으로 표시한 항목(5번 BFF relay, 6번 인증/권한, `interrupt()`, `astream_events`,
> `MemorySaver`/`PostgresSaver`)은 저장소 전체 검색에서 사용처가 **0건**임을 확인한 결과다.
> (b)의 리스크 분석은 코드를 읽고 내린 판단이며, 부하 시험이나 실제 운영 환경에서
> 재현해 본 것은 아니다.
