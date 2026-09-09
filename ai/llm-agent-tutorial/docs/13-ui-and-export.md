# 13단계. 진행 화면과 CSV 다운로드 완성하기

> **개정 사실 고지(이번 개정에서 추가)**: 처음 이 장을 썼을 때는 단일
> 에이전트 루프(3·5·8·12단계 통합)만 다뤘고, 하단 완료 기준 점검표의
> 4(부분)·5·6·10번 네 항목을 "이 장의 범위 밖"이라고 명시했다. 이번
> 개정은 그 네 항목을 실제로 이 장의 코드(`code/step13_ui_export/`)에
> **배선**했다 — MCP(10단계)·Skill(12단계)을 같은 에이전트 루프에 도구로
> 추가하고, 멀티에이전트 협업·A2A 최소 흉내(11단계)를 `/collab/supervisor`
> 라우트로 실행 경로에 넣고, 트레이싱(9단계)을 앱 시작 시점에 연결했다.
> 각 장의 개념을 다시 가르치지 않는다 — "이미 만들어진 것을 최종 앱에
> 연결하는 방법"만 4-9절에서 다룬다. 8절 확인 질문 위의 표가 이번 개정
> 이후의 최종 상태다.

## 1. 학습 목표와 완성 모습

이 장은 튜토리얼의 마지막 장이자 최종 통합이다. 새 개념을 배우는 장이
아니다 — **2·3·5·8·9·10·11·12단계가 이미 정의하고 실제로 내보낸 이벤트를
화면이 어떻게 조립해서 사람이 쓸 수 있게 만드는가, 그리고 각 장이 따로
만든 조각(MCP 서버, Skill 스크립트, 멀티에이전트 그래프, 트레이싱)을 하나의
실행 가능한 앱에 실제로 연결하는가**가 이 장의 전부다.

- `token`/`status`(2단계)
- `tool_call`/`tool_result`(3단계)
- `sources`(5단계)
- `approval_request`, `done.partial`, `finish_reason`(8단계)
- `todo`(12단계에서 값이 처음 채워짐)

PROJECT-SPEC.md 4절의 이 스키마는 이 장에서 하나도 바뀌지 않는다. 딱 한
곳만 예외인데, `done.partial`에 `csv_available`이라는 필드 하나를 **추가만**
했다(4-8절에서 이유를 설명한다). 이 장이 실제로 새로 만드는 서버 기능은
CSV 다운로드 라우트 하나(`GET /export/{run_id}.csv`)뿐이다.

완성 모습:

```
사용자: 노트북 매출 원인을 문서에서 찾아 근거 링크와 함께 설명하고,
       매출 데이터는 CSV로 받을 수 있게 해줘
```

화면에는 다음이 동시에 보인다.

```
[작업 계획]                    [진행 로그]
☑ 매출 데이터 조회               [retrieving] '노트북 매출 원인' 하이브리드 검색
▶ 관련 문서 검색                 -> 도구 호출: hybrid_search_docs(...)
☐ 인용 검증                      <- 결과(ok=true): {"results":[{"label":"S1",...}]}
☐ 답변 작성

[답변]
노트북 1분기 매출은 서울·부산 모두 증가했다[S1]. ...

[근거]
[S1] 노트북 1분기 판매 리포트 (p0)   <- 클릭하면 청크가 강조된 원문이 열린다
     1분기 노트북 매출은 서울·부산 두 지역...

[종료: stop] 정상적으로 끝났다 — 단계 2회, 토큰 30 사용.
[CSV로 다운로드] 버튼
```

이 장의 코드는 `code/step13_ui_export/`에 있다. 8단계(`code/step08_hitl/`,
승인·재개·반복 감지)와 5단계(`code/step05_hybrid_rag/`, 하이브리드 검색·인용
검증)의 코드를 합치고, 계획 관리는 12단계 `bridge.py`의 id 안정화 로직을
그대로 옮겨 왔다.

## 2. 핵심 개념

### 2-1. "thinking 과정 표시"는 실행 로그이지 사고 과정이 아니다

이 튜토리얼 전체가 합의한 정의(WRITING-GUIDE.md, PROJECT-SPEC.md)를 이
장에서 다시 못 박는다. 화면에 보이는 계획·상태·도구 호출·판단 요약은
**애플리케이션이 실제로 관측한 실행 이벤트**다. 모델이 내부적으로 "이렇게
추론했다"고 문자열로 뱉는 것을 그대로 노출하는 것이 아니다. 이 장의
`static/app.js`는 이 원칙을 화면 문구로도 드러낸다 — 진행 상태 영역에
"실행 관측: [stage] message" 형태로 표시하고, 계획을 "Todo List(모델의
생각이 아니라 애플리케이션이 들고 있는 작업 목록)"라고 부른다.

이 구분이 왜 중요한지는 `todo` 이벤트의 `failed` 상태에서 가장 잘 드러난다.
12단계에서 확인했듯, LangChain의 `write_todos` 도구는 `pending`/
`in_progress`/`completed` 세 값만 안다 — "실패"라는 개념 자체가 없다. 이
장이 쓰는 `write_plan` 도구도 마찬가지다(같은 상태 값을 그대로 빌려왔다).
그래서 화면의 "✕ 실패" 표시는 **모델이 알려준 적이 없다**. 도구 호출이
실패한 것을 애플리케이션이 관측한 시점에, 그때 `running`이던 항목에
애플리케이션이 직접 붙인 것이다(`docagent/todo.py`의 `mark_running_failed`).
이것이 "실행 이벤트를 보여준다"와 "모델의 사고를 재현한다"의 차이다 — 전자는
사실이고 후자는 지어낸 것이다.

### 2-2. 계획과 실제 실행 상태의 동기화 — 이 장에서 가장 까다로운 부분

화면의 Todo 목록은 세 가지 문제를 동시에 풀어야 한다.

1. **id 안정성**: 같은 항목이 `pending -> running -> done`으로 바뀔 때
   화면에서 다른 행으로 보이면 안 된다. → 12단계 `TodoIdAssigner`를
   그대로 재사용한다(`docagent/todo.py`). 항목의 `title` 문자열이 이번
   run에서 처음 등장하는 순서대로 id를 발급하고, 같은 title이 다시
   나오면 같은 id를 돌려준다.
2. **계획 변경 반영**: 모델이 `write_plan`을 다시 불러 계획을 바꾸면(새
   항목 추가, 기존 항목 제거) 화면도 따라 바뀌어야 한다. → `todo` 이벤트의
   `items`는 **매번 그 시점의 전체 목록**이다(증분이 아니다, PROJECT-SPEC.md
   4절). 사라진 title은 다음 이벤트부터 자동으로 안 보인다.
3. **재개(resume) 이후에도 유지**: 8단계처럼 승인 대기로 스트림이 끊겼다가
   다시 이어질 때, id 매핑과 "지금 실패로 표시된 항목이 무엇인지"가
   초기화되면 안 된다. → `TodoIdAssigner.to_state()`/`from_state()`로
   할당 상태를 SQLite(`runs.todo_state_json`)에 저장하고 매 `advance_run`
   호출마다 복원한다(`docagent/store.py`).

주체를 분명히 하면: **모델**은 `write_plan` 도구 호출로 "이 제목의 항목을
이 상태로 하겠다"고만 선언한다. id를 매기고, 상태 값을 변환하고
(`in_progress -> running`), 실패를 판단하고, 화면에 보낼 스냅샷을 만드는
것은 전부 **애플리케이션**(`docagent/agent.py` + `docagent/todo.py`)이 한다.

### 2-3. 승인 UI의 멱등성은 백엔드가 보장하고 화면은 그것을 존중한다

8단계가 이미 "같은 승인 요청에 두 번 응답해도 첫 결정이 유지된다"는 것을
`status='pending'`일 때만 갱신되는 조건부 UPDATE로 구현했다
(`docagent/store.py`의 `decide_approval`). 이 장의 화면은 그 보장을
**깨뜨리지 않는** 두 가지 장치를 더한다.

- 버튼을 누르면 즉시 4개 버튼을 모두 비활성화한다(`approvalLocked` 플래그,
  `static/app.js`) — 사람의 더블 클릭이 애초에 두 번째 요청을 만들지 않게
  막는 화면 쪽 방어.
- 그래도 두 번째 요청이 서버에 도달하면(네트워크 재시도, 다른 탭에서 같은
  승인을 열어 둔 경우 등) 응답의 `already_decided` 필드를 보고 "이미
  결정된 승인이다 — 첫 결정을 유지했다"고 로그에 남긴다.

즉 **정답은 서버(SQLite의 조건부 UPDATE)가 갖고 있고, 화면은 그 정답을
사람에게 정직하게 보여줄 뿐**이다. 5절에서 이 동작을 실제로 두 번 호출해
확인한다.

### 2-4. 출처 링크와 "가짜 출처를 걸러낸다"는 규약을 화면에서도 지킨다

5단계의 규약을 다시 요약하면: 모델은 `[S1]`, `[S2]` 같은 인용 표시만 쓰고,
그 번호를 실제 URL로 바꾸는 것은 애플리케이션이다. **이번 검색에서 실제로
반환된 청크 ID 집합에 없는 인용은 버린다.** 이 장은 이 규약을 그대로
가져오되, 검색이 도구 호출(`hybrid_search_docs`) 형태로 여러 번 일어날 수
있다는 점이 5단계와 다르다. 그래서 `run.sources`는 검색 호출이 여러 번
있어도 **라벨 번호가 이어서 증가**하도록 설계했다(`S1..Sn` 다음 검색은
`S(n+1)`부터) — 5단계는 검색이 한 번뿐이라 이 문제가 없었다.

화면 쪽에서 새로 하는 일은 "미리보기"다. 근거 링크를 클릭하면 5단계가
만든 `/sources/{doc_id}` 라우트가 문서 원문 전체를 보여주면서 인용된
청크를 `<mark>`로 강조하고 그 위치로 스크롤한다. 이 라우트 자체는
5단계 코드를 그대로 옮겨 왔다 — 새로 만들지 않았다.

### 2-5. 분석 결과를 표로 구조화하기

이 프로젝트의 CSV 다운로드는 "모델이 만든 텍스트를 그대로 CSV로 밀어
넣는" 방식이 아니다. `lookup_sales_rows` 도구가 sales.csv에서 실제로
집계·필터링한 **행 자체**(날짜/제품/지역/수량/매출)를 돌려주고, 이
장은 그 결과를 `run.table`에 저장해 뒀다가 그대로 CSV로 내보낸다. 표의
숫자는 모델이 생성한 것이 아니라 파이썬 `csv.DictReader`가 원본 파일에서
읽은 값이다 — 모델은 "이 조건으로 조회해라"만 결정한다. 이 역할 분담은
3단계부터 이어온 원칙("숫자는 도구가 계산하고 모델은 해석만 한다")과
같다.

### 2-6. 부분 실패·취소·재개 상태 표시

PROJECT-SPEC.md 7절이 정의한 8개 `finish_reason` 값 각각에 대해, 이 장은
화면에 무엇을 보여줄지 표로 미리 정한다(구현은 `static/app.js`의
`FINISH_MESSAGES`, 파이썬 쪽 대응 표는 `tests/test_finish_reason_display.py`).

| finish_reason | 화면 분류 | 사용자에게 보여주는 문구 |
| --- | --- | --- |
| `stop` | 정상 | "정상적으로 끝났다." |
| `max_steps` | 경고 | "최대 실행 단계 수를 넘어 중단했다. 지금까지의 결과는 부분 결과다." |
| `timeout` | 경고 | "최대 실행 시간을 넘어 중단했다. 지금까지의 결과는 부분 결과다." |
| `repeated_tool_call` | 경고 | "같은 도구를 같은 인자로 반복 호출해 중단했다(무한루프 방지)." |
| `no_progress` | 경고 | "인자를 바꿔도 결과가 달라지지 않아 중단했다(진전 없음 감지)." |
| `rejected_by_user` | 실패 | "사용자가 작업을 거절해 실행을 중단했다." |
| `cancelled` | 실패 | "오류가 발생했거나 연결이 끊겨 실행이 취소됐다." |
| `token_budget` | 경고 | "누적 토큰 예산을 넘어 중단했다. 지금까지의 결과는 부분 결과다." |

"경고"로 분류한 다섯 값은 전부 `done.partial`이 함께 온다 — 화면은
`partial.text`(부분 답변), `partial.steps_used`/`partial.tokens_used`(어디까지
갔는지)를 이어서 보여준다. "취소·재개"는 8단계가 이미 만든 것을 그대로
쓴다: `paused_approval` 상태에서 스트림이 끊겨도 `run_id`만 있으면
`/chat/resume/{run_id}`로 정확히 그 지점부터 이어진다 — 이 장이 새로
구현한 재개 로직은 없다.

## 3. 실습 준비

```
code/step13_ui_export/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── config.py        # PROJECT-SPEC 9절 Settings (8단계 필드 + MCP·트레이싱·협업 필드 추가)
│   ├── events.py         # 5·8·12단계 이벤트 함수를 합친 것 + tool_call.source(신규, 선택 필드)
│   ├── llm.py             # 8단계 chat_completion_full + 5단계 embed() + 11단계 chat()/chat_json()(신규)
│   ├── todo.py            # 12단계 TodoIdAssigner를 write_plan 도구에 맞게 옮김
│   ├── tools.py            # sum_sales/lookup_sales_rows/archive_report(8단계) + write_plan/hybrid_search_docs
│   ├── skills_runtime.py    # 신규: 12단계 skills/ 스크립트를 도구로 노출(Skill 층)
│   ├── mcp_client.py         # 10단계 파일 그대로: MCP 세션 관리(비동기)
│   ├── mcp_runtime.py         # 신규: mcp_client.py를 동기 에이전트 루프에 잇는 asyncio.run 래퍼
│   ├── tracing.py               # 9단계 파일 그대로: traced_span, init_tracing
│   ├── salesdata.py              # 11단계 파일 그대로: 멀티에이전트 노드가 쓰는 판매 데이터 접근
│   ├── multiagent/                # 11단계 패키지 그대로: 슈퍼바이저·핸드오프·병렬 그래프
│   ├── a2a_min/                     # 11단계 패키지 그대로: A2A 최소 흉내 클라이언트·서버
│   ├── agent.py                      # 8단계 루프 + 계획·검색·표 처리 + MCP/Skill 분기·트레이싱(신규)
│   ├── store.py                       # 8단계 SQLite 저장소 + table/sources/plan 컬럼 추가
│   ├── csv_export.py                   # CSV 생성 + 헤더
│   ├── rag/                             # 5단계 store.py/search.py/citations.py/ingest.py 그대로
│   └── app.py                            # 라우팅. /export/{run_id}.csv + /collab/supervisor(신규)
├── mcp_server/            # 신규(10단계 패키지 그대로): 별도 프로세스로 뜨는 MCP 서버
├── skills/                 # 신규(12단계 그대로): csv-analysis/, doc-research/ (SKILL.md + 스크립트)
├── static/                  # index.html, app.js — 순수 HTML+JS, 빌드 도구 없음
├── data/                      # sales.csv, docs/*.md (5·12단계와 동일)
└── tests/
```

필요 패키지(범위, 각 단계 것과 동일). 이번 개정에서 MCP(10단계)·멀티에이전트
(11단계)·트레이싱(9단계) 의존성을 이 장에도 실제로 추가했다 — 전문은
`code/step13_ui_export/requirements.txt`를 본다.

```
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
pydantic>=2.7,<3
httpx>=0.28,<0.29
python-dotenv>=1.0,<2
pymilvus[milvus-lite]>=2.6,<3
openai>=3.10,<4                 # llm.py의 chat()/chat_json()(11단계 멀티에이전트가 쓴다), mcp_server의 임베딩 호출
mcp[cli]>=2.0,<3                 # 10단계 MCP 클라이언트
langgraph>=1.2,<2                 # 11단계 슈퍼바이저 그래프
arize-phoenix>=17,<19.18           # 9단계와 같은 Python 3.11 상한 이유(README 참고)
openinference-instrumentation-openai>=0.1.30,<0.2
opentelemetry-api>=1.30,<2
pytest-asyncio>=0.24,<1            # mcp_client.py가 async def이므로 필요
```

CSV 생성은 표준 라이브러리 `csv`/`io`만 쓴다.

`.env`는 8단계 값(`DOCAGENT_STATE_DB`, `MAX_AGENT_TOKENS`)과 5단계 값
(`DOCAGENT_MILVUS_*`)을 그대로 쓴다. Milvus Lite로 학습하려면
`DOCAGENT_MILVUS_URI=./data/milvus_demo.db`처럼 로컬 파일 경로를 준다.
이번 개정이 **추가만** 한 값(PROJECT-SPEC.md 2절, 각각 10·9·11단계가 이미
정의해 둔 이름을 그대로 재사용): `DOCAGENT_MCP_SERVER_COMMAND`/
`DOCAGENT_MCP_SERVER_ARGS`/`DOCAGENT_MCP_CONNECT_TIMEOUT_SECONDS`/
`DOCAGENT_MCP_CALL_TIMEOUT_SECONDS`/`DOCAGENT_MCP_SALES_CSV`,
`DOCAGENT_TRACING_ENABLED`/`DOCAGENT_PHOENIX_ENDPOINT`/
`DOCAGENT_TRACE_PROJECT_NAME`, `MAX_AGENT_HANDOFFS`/`MAX_COLLAB_TOKENS`/
`A2A_VERIFIER_URL`. `DOCAGENT_MCP_SERVER_COMMAND`는 가상환경을 활성화하지
않고 시스템 `python`을 쓰면 `mcp` 패키지를 못 찾아 자식 프로세스가 죽는다
— 실습 시 가상환경의 `python` 절대경로를 넣거나 가상환경을 활성화한
셸에서 서버를 띄운다(5-9절에서 실제로 겪은 문제다).

## 4. 단계별 구현

### 4-1. 계획 도구 `write_plan`과 id 안정화

`docagent/tools.py`에 `write_plan` 스키마를 등록하고, 실제 처리는
`docagent/agent.py`가 한다(상태를 들고 있어야 하므로 일반 도구 레지스트리를
거치지 않는다 — `STATEFUL_TOOLS`로 표시).

```python
# code/step13_ui_export/docagent/agent.py (발췌)
def _handle_write_plan(run: store.RunState, args_dict: dict[str, Any]) -> tuple[AgentEvent, AgentEvent | None]:
    items_in = args_dict.get("items", [])
    run.plan = [{"title": it["title"], "status": it["status"]} for it in items_in]
    assigner = TodoIdAssigner.from_state(run.todo_state)
    todo_evt = _emit_todo_if_changed(run, assigner)
    tool_result = AgentEvent(
        "tool_result", {"id": "", "ok": True, "summary": f"계획 {len(run.plan)}개 항목으로 갱신"}
    )
    return tool_result, todo_evt
```

`_emit_todo_if_changed`는 항목이 이전에 화면에 보낸 스냅샷과 같으면 이벤트
자체를 만들지 않는다 — 12단계 `stream_agent_events`가 `last_todo_snapshot`
메모리 변수로 하던 일을, 이 장은 재개(resume)를 넘겨야 하므로 SQLite에
저장된 `run.todo_state["last_items"]`로 한다.

```python
# code/step13_ui_export/docagent/agent.py (발췌)
def _emit_todo_if_changed(run: store.RunState, assigner: TodoIdAssigner) -> AgentEvent | None:
    items = assigner.items_for(run.plan)
    if items == run.todo_state.get("last_items"):
        run.todo_state = assigner.to_state()
        run.todo_state["last_items"] = items
        return None
    run.todo_state = assigner.to_state()
    run.todo_state["last_items"] = items
    return AgentEvent("todo", {"items": items})
```

### 4-2. 하이브리드 검색 도구와 인용 근거 누적

```python
# code/step13_ui_export/docagent/agent.py (발췌)
def _handle_hybrid_search(run: store.RunState, args_dict: dict[str, Any], settings: Settings) -> tuple[bool, str]:
    query = args_dict.get("query", "")
    top_k = int(args_dict.get("top_k", 5))
    try:
        hits = hybrid_search(query, limit=top_k)
    except Exception as exc:
        return False, json.dumps({"error": "search_failed", "message": str(exc)}, ensure_ascii=False)

    offset = len(run.sources)
    new_sources = []
    for i, hit in enumerate(hits):
        source = citations.RetrievedSource(
            label=citations.make_source_label(offset + i),
            chunk_id=hit["pk"], doc_id=hit["doc_id"], doc_title=hit.get("doc_title", hit["doc_id"]),
            page=int(hit.get("page", 0)), chunk_index=int(hit.get("chunk_index", 0)),
            text=hit.get("text", ""), source_url=hit.get("source_url", ""), score=float(hit.get("score", 0.0)),
        )
        new_sources.append(source)
    run.sources.extend(asdict(s) for s in new_sources)
    ...
```

`offset = len(run.sources)`가 4-4절에서 말한 "라벨이 검색 호출을 넘어
이어진다"의 구현이다. 검색이 실패하면(Milvus 연결 오류 등) `ok=False`를
돌려주고, 루프는 이것을 다른 도구 실패와 똑같이 취급해 그 시점에 `running`
이던 계획 항목을 `failed`로 표시한다.

### 4-3. 최종 답변에서 인용 검증 + sources 이벤트

```python
# code/step13_ui_export/docagent/agent.py (발췌, _run_loop 안)
if not tool_calls:
    raw_text = assistant_message.get("content") or ""
    yield AgentEvent("status", {"stage": "verifying", "message": "답변의 인용 표시를 검증하는 중"})
    retrieved = [citations.RetrievedSource(**s) for s in run.sources]
    result = citations.validate_and_link_citations(raw_text, retrieved, app_base_url=settings.app_base_url)
    run.final_text = result.text
    yield AgentEvent("status", {"stage": "writing", "message": "검증된 답변을 전달하는 중"})
    yield AgentEvent("token", {"text": result.text})
    yield AgentEvent("sources", {"items": result.sources})
    yield _finish(conn, run, "stop")
    return
```

`citations.validate_and_link_citations`는 5단계 코드를 그대로 가져온
것이다 — 이 함수는 답변 문자열에서 `[Sn]`만 읽고, `run.sources`(이번 run
동안 실제로 검색된 청크)에 없는 번호는 지운 뒤 그 사실을 답변 끝에 남긴다.

### 4-4. CSV 다운로드 라우트

```python
# code/step13_ui_export/docagent/app.py (발췌)
@app.get("/export/{run_id}.csv")
def export_csv(run_id: str) -> Response:
    conn = get_conn()
    run = store.load_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run을 찾을 수 없다: {run_id}")
    if not run.table:
        raise HTTPException(status_code=404, detail="이 run에는 아직 다운로드할 분석 표가 없다.")

    body = build_analysis_csv(run.table)
    filename = f"분석결과_{run_id}.csv"
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition(filename)},
    )
```

### 4-5. CSV 생성 — 한글 인코딩

```python
# code/step13_ui_export/docagent/csv_export.py (발췌)
_COLUMN_LABELS = {"date": "날짜", "product": "제품", "region": "지역", "units": "수량", "revenue": "매출"}

def build_analysis_csv(rows: list[dict[str, Any]]) -> bytes:
    fieldnames = list(rows[0].keys()) if rows else list(_COLUMN_LABELS.keys())
    header = [_COLUMN_LABELS.get(name, name) for name in fieldnames]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([row.get(name, "") for name in fieldnames])
    return buffer.getvalue().encode("utf-8-sig")
```

**왜 `utf-8-sig`인가.** Windows의 Excel은 CSV를 열 때 파일 맨 앞에
BOM(`EF BB BF` 세 바이트)이 있으면 "UTF-8이다"라고 판단하고, 없으면
시스템 로캘(한국어 Windows에서는 CP949 계열)로 읽는다. UTF-8로 쓴 한글
CSV에 BOM이 없으면 Excel이 그 바이트를 CP949로 잘못 해석해 한글이
깨진다. `str.encode("utf-8-sig")`는 텍스트 앞에 BOM 세 바이트를 붙이고
나머지는 보통의 UTF-8로 인코딩한다. 5절에서 이 차이를 실제 바이트로
확인한다.

**BOM의 대가**(지어내지 않고 알려진 사실만 적는다): BOM은 Excel을 위한
우회책이지 만능이 아니다. 일부 CSV 파서·라인 기반 처리(`head -1 | grep`
같은 바이트 비교)는 BOM을 데이터의 일부로 읽어 첫 헤더가 깨진 것처럼
보인다. JSON에는 관례상 BOM을 붙이지 않는다. macOS/Linux 도구는 로캘이
이미 UTF-8이라 BOM이 필요 없다. 즉 BOM은 "모두에게 필요한 것"이 아니라
"Excel(특히 Windows)의 자동 인코딩 추정을 상대하기 위한 것"이다. 다른
프로그램이 다시 읽어야 하는 데이터 교환용 CSV라면 BOM 없는 UTF-8이 더
안전할 수 있다 — 이 장의 CSV는 "사람이 Excel로 열어 보는 것"이 주
용도이므로 `utf-8-sig`를 선택했다.

### 4-6. 한글 파일명 다운로드 헤더

```python
# code/step13_ui_export/docagent/csv_export.py (발췌)
def content_disposition(filename: str) -> str:
    ascii_fallback = filename.encode("ascii", errors="ignore").decode("ascii")
    stem = ascii_fallback[:-4] if ascii_fallback.lower().endswith(".csv") else ascii_fallback
    if not stem:
        ascii_fallback = "download.csv"
    elif not ascii_fallback.lower().endswith(".csv"):
        ascii_fallback = "download.csv"
    encoded = urllib.parse.quote(filename, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"
```

HTTP 헤더 값은 라틴-1 바이트만 담을 수 있다(RFC 7230). 한글 파일명을
`filename="..."`에 그대로 넣으면 브라우저마다 처리가 다르다. RFC
6266/5987의 `filename*=UTF-8''<percent-encoded>` 확장 문법을 쓰면 UTF-8로
인코딩한 파일명을 퍼센트 인코딩해 명시적으로 전달할 수 있다. 두 값을 모두
붙여서, `filename*`를 이해하는 클라이언트는 그것을 쓰고(RFC 6266 4.3절 —
같이 오면 `filename*`가 우선), 이해하지 못하는 구식 클라이언트는 ASCII로
음역한 `filename=` 폴백을 쓴다.

### 4-7. 화면 — 근거 링크와 CSV 다운로드 노출 조건

```javascript
// code/step13_ui_export/static/app.js (발췌)
if (partial && partial.csv_available) {
  exportBtn.style.display = "inline-block";
  exportBtn.onclick = () => { window.location.href = `/export/${currentRunId}.csv`; };
  exportHint.textContent = "분석에 쓰인 표를 CSV로 받는다(Excel 한글 깨짐 방지: UTF-8 BOM 포함).";
}
```

`partial.csv_available`이 이 장이 `done.partial`에 추가한 유일한 필드다.
기존 네 필드(`text`/`steps_used`/`tokens_used`/`tokens_estimated`) 중
어느 것도 "표가 있는가"를 담지 못했다. 이 필드를 모르는 이전 단계
클라이언트는 그냥 무시하고 그대로 동작한다 — 8단계가 `partial` 자체를
추가할 때 쓴 것과 같은 하위 호환 논리다.

### 4-8. 최종 통합 배선: MCP·Skill·멀티에이전트+A2A·트레이싱

여기부터는 이번 개정에서 새로 배선한 부분이다. 4-1~4-7절의 단일 에이전트
루프(`docagent/agent.py`)는 하나도 다시 쓰지 않았다 — 그 위에 도구 공급원
두 개(MCP·Skill)와 실행 관측 두 개(트레이싱·협업 라우트)를 **추가**했다.

**Tool·MCP·Skill의 차이를 코드 위치로 보면:**

| 층 | 이 장의 파일 | 실행 위치 | 인자 검증 |
| --- | --- | --- | --- |
| Tool (3단계) | `docagent/tools.py` | 같은 프로세스, 파이썬 함수 호출 | pydantic 모델 |
| MCP 도구 (10단계) | `docagent/mcp_client.py` + `docagent/mcp_runtime.py` | 별도 프로세스(`mcp_server/`, stdio) | MCP 서버가 자체 검증 |
| Skill (12단계) | `docagent/skills_runtime.py` + `skills/*/SKILL.md` | 같은 프로세스에서 subprocess로 스크립트 실행 | 스크립트 자체의 인자 파싱(argparse) |

세 층 모두 `docagent/agent.py`의 `TOOL_SPECS`에 함수 호출 스펙(OpenAI
`tools` 형식)으로 노출되고 같은 `tool_choice="auto"` 루프 안에서 모델이
고른다 — 모델 입장에서는 도구 이름과 스키마만 보이고 "어디서 실행되는지"는
안 보인다. 그 차이를 사람이 화면에서 구분할 수 있어야 하므로,
`docagent/events.py`의 `tool_call` 이벤트에 `source` 필드를 추가했다
(`"local"`/`"mcp"`/`"skill"`, PROJECT-SPEC.md 4절에 근거를 남겼다):

```python
# code/step13_ui_export/docagent/agent.py (발췌)
def _tool_source(name: str) -> str:
    if name == MCP_SEARCH_TOOL_NAME:
        return "mcp"
    if name in SKILL_TOOL_NAMES:
        return "skill"
    return "local"
```

**MCP 배선.** `docagent/mcp_client.py`는 10단계 파일을 한 글자도 고치지
않고 그대로 옮겼다 — 전부 `async def`다. 이 장의 에이전트 루프
(`advance_run`)는 동기 제너레이터이므로, `docagent/mcp_runtime.py`가
`asyncio.run()`으로 새 이벤트 루프를 열어 동기 세계와 이어 붙인다. 연결은
**호출마다 새로 연다**(재사용하지 않는다) — 재사용을 시도하면
`mcp_client.py`가 이미 경고한 크로스-태스크 취소 스코프 오류가 실제로
재현된다(직접 만들어 확인했다, `mcp_runtime.py` 모듈 docstring 참고). 도구
이름은 로컬 `hybrid_search_docs`(이 프로세스가 Milvus를 직접 부른다)와
구분하려고 `mcp_search_docs`로 다르게 노출한다 — 실제 MCP 서버의 도구
이름은 `search_docs`다.

```python
# code/step13_ui_export/docagent/agent.py (발췌)
if include_mcp:
    discovered = mcp_runtime.list_mcp_tool_specs_sync(settings)  # 실제 서버에 물어본다
    ...
    extra_by_name[MCP_SEARCH_TOOL_NAME] = _MCP_SEARCH_TOOL_SPEC
```

`ChatRequest.include_mcp`(기본값 `False`)로 켠다. 기본값을 꺼 둔 이유는
MCP 서버가 실제 자식 프로세스라서 안 떠 있으면 도구 탐색 자체가 실패할 수
있고, 기존 테스트(반복 감지 등)가 도구 개수에 기댄 부분이 있어 기존 동작을
그대로 지키기 위해서다. 서버가 응답하지 않아도 도구 자체는 목록에 남겨
둔다 — 실제 호출에서 `tool_result(ok=False)`로 실패 원인이 드러나고,
트레이싱을 켜면 그 실패가 스팬으로 남는다(6-4절에서 실제로 재현한다).

**Skill 배선.** `docagent/skills_runtime.py`는 12단계
`skills/csv-analysis/analyze_sales.py`, `skills/doc-research/find_evidence.py`를
**그대로**(파일 자체를 복사) 가져와 subprocess로 돌린다. Deep Agents(12단계)는
모델이 SKILL.md 원문을 직접 읽고 `execute` 도구로 스크립트를 돌렸지만, 이
장의 루프는 Deep Agents가 아닌 3·8단계식 도구 호출 루프이므로 "이 스킬을
실행하라"는 동작 자체를 도구 하나(`csv_analysis_skill`/
`doc_research_skill`)로 감쌌다. `ChatRequest.include_skills`(기본값
`False`)로 켠다.

Skill과 시스템 프롬프트의 차이도 코드로 드러난다: `agent.py`의
`SYSTEM_PROMPT`는 매 요청 대화 이력에 들어가 모델이 직접 읽는 지시문이고,
SKILL.md는 이 장에서 **모델에게 전달되지 않는다** — 애플리케이션이 "이
스킬이 호출되면 이 스크립트를 이렇게 실행한다"는 실행 절차로만 참조한다.

**멀티에이전트 협업 + A2A 최소 흉내 배선.** `docagent/multiagent/`와
`docagent/a2a_min/`은 11단계 파일을 그대로 옮겼다(`salesdata.py`도 함께).
`docagent/app.py`가 `POST /collab/supervisor`라는 새 라우트에서 11단계의
슈퍼바이저 그래프(`build_supervisor_graph`)를 그대로 부른다 —
조사(investigator)→분석(analyst)→검증(verifier)을 슈퍼바이저가 위임하고,
셋이 끝나면 `finish` 노드가 결과를 하나의 답변으로 통합한다(최종 통합
과제 5번). `external_verifier` 플래그 하나가 검증을 내부 함수 호출로
할지, 별도 프로세스(A2A 최소 흉내 서버, `A2A_VERIFIER_URL`)에 HTTP로
위임할지를 가른다(최종 통합 과제 6번):

```python
# code/step13_ui_export/docagent/app.py (발췌)
graph = build_supervisor_graph(deps, external_verifier=req.external_verifier)
```

두 경로는 로그·화면에서 다르게 보인다 — 내부 호출은 `[verifier] ...(판단
주체: heuristic|model)`, A2A 위임은 `[verifier(A2A)] ...(판단 주체:
a2a_agent|a2a_agent_unreachable)`로 접두어와 판단 주체 값 자체가 다르다
(11단계 `docagent/multiagent/agents.py`의 `make_verifier_node`/
`make_verifier_node_a2a`를 그대로 가져왔다). **11단계가 이미 정직하게
표시했듯, `docagent/a2a_min/server.py`는 A2A 프로토콜 구현이 아니라 그
핵심 아이디어(별도 프로세스에 작업을 맡기고 카드로 자기소개하며 내부
상태를 공유하지 않는다)의 최소 흉내다** — 이 장도 그 표시를 그대로
유지했다. "A2A를 구현했다"고 쓰지 않는다.

협업 전체 단위의 실행 한도(PROJECT-SPEC.md 7절, 11단계가 확장)도 그대로
따라온다 — `MAX_AGENT_HANDOFFS`/`MAX_COLLAB_TOKENS`/`delegation_loop`는
`docagent/multiagent/budget.py`·`handoff.py`가 이미 구현한 것을 그대로
쓴다. 이 장은 이 한도 검사 로직을 새로 만들지 않았다.

**트레이싱 배선.** `docagent/tracing.py`는 9단계 파일을 그대로 옮겼다.
`docagent/app.py`의 FastAPI `lifespan`에서 `tracing.init_tracing(get_settings())`를
한 번 부른다 — `DOCAGENT_TRACING_ENABLED=false`(기본값)면 아무 일도
안 하고, `traced_span`은 OpenTelemetry API가 주는 NoOp 스팬을 돌려주므로
이 장의 나머지 코드는 트레이싱 여부를 몰라도 된다. `agent.py`의 모델
호출(`docagent.chat` 스팬)과 도구 호출(`mcp.search_docs`/
`skill.<name>` 스팬), `app.py`의 협업 라우트(`docagent.collab.supervisor`
스팬)에 수동으로 스팬을 추가했다:

```python
# code/step13_ui_export/docagent/agent.py (발췌)
with tracing.traced_span("mcp.search_docs", kind=tracing.SpanKind.RETRIEVER, ...) as span:
    try:
        result = mcp_runtime.call_mcp_tool_sync("search_docs", {...}, settings)
    except McpConnectionError as exc:
        tracing.mark_error(span, "mcp_connection_error", str(exc))
        ...
```

`tracing.mark_error`를 직접 부르는 이유는 9단계가 이미 설명한 것과
같다 — 이 장의 코드는 예외를 `error` SSE 이벤트로 바꿔 삼키므로(3단계부터의
설계), 예외가 `traced_span`의 컨텍스트 매니저 밖으로 나가지 않아
자동으로는 스팬이 오류로 표시되지 않는다.

## 5. 실행과 결과 확인

아래는 전부 실제로 실행해 나온 출력이다(스텁 서버 `code/_tools/
fake_openai_server.py --port 8213`, Milvus Lite `./data/milvus_demo.db`,
2026-09-09).

### 5-1. 문서 적재와 서버 기동

```
$ python -c "from docagent.rag.ingest import ingest_directory; \
  n=ingest_directory('data/docs', recreate=True); print('ingested', n)"
ingested 4

$ uvicorn docagent.app:app --port 8181 &
$ curl -s http://127.0.0.1:8181/healthz
{"ok":true}
```

### 5-2. 기본 흐름: 계획 → 답변 → 종료

```
$ curl -s -N -X POST http://127.0.0.1:8181/chat -H 'Content-Type: application/json' \
    -d '{"message":"안녕"}'
event: status
data: {"stage": "starting", "message": "실행을 시작한다"}

event: tool_call
data: {"id": "call_stub_1", "name": "write_plan", "args": {}}

event: tool_result
data: {"id": "call_stub_1", "ok": true, "summary": "계획 0개 항목으로 갱신"}

event: todo
data: {"items": []}
...
event: sources
data: {"items": []}

event: done
data: {"finish_reason": "stop", "partial": {"text": "요청하신 내용을 확인했습니다.",
       "steps_used": 2, "tokens_used": 30, "tokens_estimated": false, "csv_available": false}}
```

스텁 서버는 모델이 아니라서 `tools` 목록의 첫 항목(`write_plan`)을 빈
인자로 부른다 — 실제 계획 내용은 확인할 수 없지만, "이벤트가 올바른
순서·형태로 나가는가"는 이 호출로 확인된다.

### 5-3. 하이브리드 검색 도구 — 실제 Milvus Lite 검색

특정 도구만 시험하려고 `tool_names`로 강제했다(README 4절, 데모 전용
장치 — 8단계와 같은 방식).

```
$ curl -s -N -X POST http://127.0.0.1:8181/chat -H 'Content-Type: application/json' \
    -d '{"message":"노트북 매출 원인","tool_names":["hybrid_search_docs"]}'
event: tool_call
data: {"id": "call_stub_1", "name": "hybrid_search_docs", "args": {}}

event: status
data: {"stage": "retrieving", "message": "'' 하이브리드 검색"}

event: tool_result
data: {"id": "call_stub_1", "ok": true, "summary": "{\"results\": [{\"label\": \"S1\",
       \"doc\": \"노트북 1분기 판매 리포트\", \"snippet\": \"# 노트북 1분기 판매 리포트\\n\\n
       1분기 노트북 매출은 서울·부산 두 지역 모두에서 꾸준히 증가했다...\"}]}"}
```

Dense+Sparse 하이브리드 검색이 실제로 Milvus Lite에 붙어 청크를
반환했다(5단계 코드 그대로).

### 5-4. lookup_sales_rows → CSV 다운로드

```
$ curl -s -D - -o /dev/null -X POST http://127.0.0.1:8181/chat -H 'Content-Type: application/json' \
    -d '{"message":"매출 행 보여줘","tool_names":["lookup_sales_rows"]}' | grep -i x-run-id
x-run-id: run_ced8f6a4d324

(done 이벤트의 partial.csv_available은 true였다)

$ curl -sD - "http://127.0.0.1:8181/export/run_ced8f6a4d324.csv" -o /tmp/out.csv
HTTP/1.1 200 OK
content-disposition: attachment; filename="_run_ced8f6a4d324.csv"; filename*=UTF-8''%EB%B6%84%EC%84%9D%EA%B2%B0%EA%B3%BC_run_ced8f6a4d324.csv
content-length: 865
content-type: text/csv; charset=utf-8

$ python3 -c "
data = open('/tmp/out.csv','rb').read()
print('BOM 있음:', data[:3] == b'\xef\xbb\xbf')
text = data.decode('utf-8-sig')
print(text.splitlines()[0]); print(text.splitlines()[1])
"
BOM 있음: True
날짜,제품,지역,수량,매출
2026-01-05,노트북,서울,373,223800000
```

`filename*=UTF-8''...`로 한글 파일명이 퍼센트 인코딩되어 나갔고, 실제로
받은 파일의 처음 3바이트가 BOM(`EF BB BF`)이며 `utf-8-sig`로 정상
디코딩된다는 것을 직접 확인했다.

### 5-5. BOM 유무에 따른 바이트 차이를 직접 만들어 확인

```
$ od -An -tx1 -N20 /tmp/csv_demo/with_bom.csv
 ef bb bf eb 82 a0 ec a7 9c 2c ec a0 9c ed 92 88 2c ec a0 9c 
$ od -An -tx1 -N20 /tmp/csv_demo/without_bom.csv
 eb 82 a0 ec a7 9c 2c ec a0 9c ed 92 88 2c ec a7 80 ec 97 ad

$ python3 -c "
data = open('/tmp/csv_demo/without_bom.csv','rb').read()
print(data.decode('cp949', errors='replace').splitlines()[0])
"
�궇吏�,�젣�뭹,吏��뿭,�닔�웾,留ㅼ텧
```

BOM 없는 UTF-8 파일의 첫 줄(`날짜,제품,...`)을 CP949로 강제 디코딩하면
저렇게 깨진다. 이것이 정확히 Excel 내부 감지 알고리즘의 재구현은
아니지만(그 알고리즘 자체는 마이크로소프트가 공개하지 않는다), "BOM이
없으면 UTF-8 바이트열이 CP949 로캘로 잘못 해석될 수 있고, 그 결과가
읽을 수 없는 글자가 된다"는 근본 원인은 이 재현으로 실제 바이트를 통해
확인했다.

### 5-6. 근거 링크가 실제로 열리는지 확인

```
$ curl -s -o /dev/null -w "%{http_code}\n" \
    "http://127.0.0.1:8181/sources/notebook-q1-report?page=0&chunk=0"
200
$ curl -s "http://127.0.0.1:8181/sources/notebook-q1-report?page=0&chunk=0" | grep -o '<mark id="highlight">'
<mark id="highlight">
```

라우트가 200을 반환하고, 청크 원문이 실제로 `<mark>`로 강조되어 있다.

### 5-7. 승인 UI — 승인·거절·수정과 더블 클릭 멱등성

```
$ curl -s -X POST http://127.0.0.1:8181/chat ... -d '{"tool_names":["archive_report"]}'
event: approval_request
data: {"id": "appr_83da92e50778", "action": "archive_report", "args": {}, ...}

$ curl -s -X POST http://127.0.0.1:8181/approvals/appr_83da92e50778/decision \
    -d '{"action":"approve"}'
{"approval":{...,"status":"approved",...},"already_decided":false}

$ curl -s -X POST http://127.0.0.1:8181/approvals/appr_83da92e50778/decision \
    -d '{"action":"reject","mode":"abort","note":"too late"}'
{"approval":{...,"status":"approved",...},"already_decided":true}
```

두 번째 요청(다른 결정을 제출)은 `already_decided: true`를 받고, 실제
`status`는 여전히 첫 결정인 `"approved"`다 — 화면 버튼을 두 번 눌러도
서버 상태는 첫 결정을 유지한다는 것을 실제 HTTP 응답으로 확인했다.

수정(edit) 흐름도 확인했다: 잘못된(빈) 인자로 승인 요청이 왔을 때
`{"action":"edit","args":{"report_id":"notebook-q1-report","reason":"분기 종료로
보관"}}`로 결정하고 재개하면, `tool_call` 이벤트의 `args`가 수정한
값으로 나가고 `tool_result`가 `"archived": true`로 성공한다. 거절(중단)
흐름도 확인했다: `finish_reason: "rejected_by_user"`로 끝난다.

### 5-8. 인용 검증 배선 — 가짜 검색 결과로 [Sn] 필터링 확인

실제 모델이 없어 "모델이 지어낸 인용 번호"를 스텁 서버로는 재현할 수
없으므로, `tests/test_citation_wiring.py`에서 `chat_fn`을 직접 주입해
`advance_run` 전체를 실행했다(모델 서버는 가짜지만 `agent.py`의 루프,
`citations.py`의 검증 로직, SQLite 저장은 전부 실제로 돈다).

```
$ pytest tests/test_citation_wiring.py -q
1 passed
```

검증한 것: 검색 결과에 있던 `[S1]`은 답변에 남고 링크(`/sources/
notebook-q1-report...`)가 붙는다. 검색 결과에 없는 `[S9]`는 지워지고
"제거했다"는 알림이 답변 끝에 남는다.

### 5-9. 최종 통합 실행 — 스텁 서버 + Milvus Lite + 실제 MCP 서버 프로세스 + A2A 최소 서버 + Phoenix

4-8절에서 배선한 네 가지를 전부 동시에 띄워 실제로 확인했다(2026-09-09).
프로세스 5개가 동시에 뜬다: 스텁 OpenAI 호환 서버(8241) · Phoenix(6046) ·
A2A 최소 흉내 서버(8293) · `docagent.app`(8280, 이 안에서 `mcp_server`를
필요할 때마다 자식 프로세스로 새로 띄운다) · (문서 적재 스크립트는 먼저
한 번 실행하고 끝난다).

```
$ python -c "from docagent.rag.ingest import ingest_directory; \
  print(ingest_directory('data/docs', recreate=True))"
4

$ python code/_tools/fake_openai_server.py --port 8241 &
$ python -m phoenix.server.main serve --port 6046 &
$ python -m docagent.a2a_min.server --port 8293 &
$ DOCAGENT_TRACING_ENABLED=true DOCAGENT_PHOENIX_ENDPOINT=http://127.0.0.1:6046/v1/traces \
  DOCAGENT_MCP_SERVER_COMMAND=$(which python) A2A_VERIFIER_URL=http://127.0.0.1:8293 \
  uvicorn docagent.app:app --port 8280 &
$ curl -s http://127.0.0.1:8280/healthz
{"ok":true}
```

**MCP 도구가 실제 별도 프로세스로 동작하는 것을 직접 확인**(Milvus의
Dense 검색 + 실제 임베딩 호출까지 전부 실제로 실행됨):

```
$ python -c "
from docagent.config import load_settings
from docagent import mcp_runtime
r = mcp_runtime.call_mcp_tool_sync('search_docs', {'query': '노트북 매출 증가', 'top_k': 3}, load_settings())
print(r.ok); print(r.content[:200])
"
True
{"matched_count": 3, "hits": [{"pk": "notebook-q1-report#p0-c0", "text": "# 노트북 1분기 판매
리포트\n\n1분기 노트북 매출은 서울·부산 두 지역 모두에서 꾸준히 증가했다. ...
```

**앱을 통해 `mcp_search_docs` 도구가 로컬 도구와 함께 호출되는 것**(스텁
서버가 `tools[0]`을 무조건 호출하는 성질을 이용해 첫 도구로 지정했다.
`args`가 빈 값인 것은 스텁 서버의 한계이지 MCP 배선의 문제가 아니다 —
6-2절과 같은 이유다):

```
$ curl -sN -X POST http://127.0.0.1:8280/chat -H 'content-type: application/json' \
  -d '{"message":"노트북 매출 원인을 MCP로 찾아줘","tool_names":["mcp_search_docs"],"include_mcp":true}'
event: tool_call
data: {"id": "call_stub_1", "name": "mcp_search_docs", "args": {}, "source": "mcp"}

event: status
data: {"stage": "retrieving", "message": "'' MCP 문서 검색"}

event: tool_result
data: {"id": "call_stub_1", "ok": false, "summary": "Error executing tool search_docs: query가 비어
있다. 검색어를 입력해달라."}
...
event: done
data: {"finish_reason": "stop", ...}
```

`source: "mcp"`가 실제로 찍혔다. 도구 자체는 실제 MCP 서버 프로세스가
받아 처리했고(`query`가 비어서 그 서버가 스스로 검증 오류를 냈다), 그
오류가 `tool_result(ok=false)`로 정상적으로 SSE에 실렸다.

**Skill 도구도 같은 방식으로 확인**:

```
$ curl -sN -X POST http://127.0.0.1:8280/chat -H 'content-type: application/json' \
  -d '{"message":"매출을 스킬로 분석해줘","tool_names":["csv_analysis_skill"],"include_skills":true}'
event: tool_call
data: {"id": "call_stub_1", "name": "csv_analysis_skill", "args": {}, "source": "skill"}
...
event: tool_result
data: {"id": "call_stub_1", "ok": true, "summary": "{\"result_file\": \"results/csv_analysis.md\",
\"row_count\": 48, ..., \"by_product\": {\"노트북\": 2752800000, ...}"}
```

**멀티에이전트 협업 — 내부 검증과 A2A 검증을 같은 요청으로 둘 다 실행**:

```
$ curl -sN -X POST http://127.0.0.1:8280/collab/supervisor -H 'content-type: application/json' \
  -d '{"message":"노트북 매출이 왜 늘었는지 조사해줘"}'
event: status
data: {"stage": "verifying", "message": "[verifier] 검증 결과: 통과 (재계산한 분기별 합계가 analyst
보고와 일치한다, 판단 주체: heuristic)"}
...
event: done
data: {"finish_reason": "stop", "partial": {"text": "노트북의 매출은 decrease 추세다. ...", "steps_used": 8, ...}}

$ curl -sN -X POST http://127.0.0.1:8280/collab/supervisor -H 'content-type: application/json' \
  -d '{"message":"노트북 매출이 왜 늘었는지 조사해줘","external_verifier":true}'
event: status
data: {"stage": "verifying", "message": "[verifier(A2A)] 검증 결과: 통과 (재계산한 분기별 합계가
analyst 보고와 일치한다, 판단 주체: a2a_agent)"}
...
event: done
data: {"finish_reason": "stop", "partial": {"text": "노트북의 매출은 decrease 추세다. ...", ...}}
```

`[verifier]`와 `[verifier(A2A)]`, `판단 주체: heuristic`과
`판단 주체: a2a_agent` — 로그만 보고도 검증이 어느 경로로 이뤄졌는지
구분된다. A2A 경로는 실제로 8293 포트의 별도 프로세스가 응답한 결과다
(그 프로세스를 끄고 같은 요청을 보내면 `a2a_agent_unreachable`로
바뀐다 — `tests/test_collab_wiring.py::test_external_a2a_verifier_unreachable_degrades_gracefully`
로 이 경로를 자동화된 테스트로도 고정했다).

**트레이싱 — Phoenix에 실제로 스팬이 쌓이는 것을 GraphQL API로 확인**
(9단계가 쓴 것과 같은 방법):

```
$ python -c "
import httpx
q = 'query { projects(first: 5) { edges { node { name spans(first: 30) { edges { node {
  name spanKind statusCode } } } } } } }'
d = httpx.post('http://127.0.0.1:6046/graphql', json={'query': q}, timeout=10).json()
for e in d['data']['projects']['edges']:
    n = e['node']
    if n['name'] == 'docagent-step13-integration':
        for s in n['spans']['edges']:
            print(s['node'])
"
{'name': 'MCP send initialize', 'spanKind': 'unknown', 'statusCode': 'UNSET'}
{'name': 'MCP send tools/list', 'spanKind': 'unknown', 'statusCode': 'UNSET'}
{'name': 'docagent.chat', 'spanKind': 'llm', 'statusCode': 'UNSET'}
{'name': 'MCP send tools/call search_docs', 'spanKind': 'unknown', 'statusCode': 'UNSET'}
{'name': 'mcp.search_docs', 'spanKind': 'retriever', 'statusCode': 'ERROR'}
{'name': 'skill.csv_analysis_skill', 'spanKind': 'chain', 'statusCode': 'UNSET'}
{'name': 'docagent.collab.supervisor', 'spanKind': 'chain', 'statusCode': 'UNSET'}
...
```

`mcp.search_docs` 스팬의 상태가 실제로 `ERROR`로 찍혔다(위 "MCP 도구가
로컬 도구와 함께 호출되는 것" 실행에서 낸 바로 그 실패다). 그 스팬의
`statusMessage`와 속성을 그대로 조회하면:

```
statusMessage: "Error executing tool search_docs: query가 비어 있다. 검색어를 입력해달라."
attributes: {"mcp": {"top_k": 5, "tool_name": "search_docs"}, "error": {"code": "mcp_tool_error"},
             "output": {"value": "[mcp_tool_error] Error executing tool search_docs: query가 ..."},
             "elapsed_ms": 2232.34}
```

이것이 최종 통합 과제 10번 "실패 원인을 트레이스로 확인한다"의 실제
시연이다 — 화면의 `tool_result(ok=false)`만 봐도 실패 사실은 알 수
있지만, **왜** 실패했는지(어떤 스팬이, 어떤 속성값으로, 몇 ms 걸려서
실패했는지)를 트레이스에서 추적해 확인했다. `MCP send *` 스팬들은
`openinference-instrumentation-openai`가 아니라 MCP 파이썬 SDK 자체가
내는 스팬이다(SDK가 opentelemetry-api를 이미 물고 있어, 이 장이 따로
계측하지 않아도 자동으로 잡혔다 — `mcp_client.py`를 고치지 않았는데도
나타난 스팬이라 이 장 집필 중 실제로 확인하고서야 알았다).

이 절의 모든 명령은 위 순서 그대로 한 세션에서 실행해 나온 실제 출력이다.
프로세스는 확인 후 전부 종료했다.

## 6. 실패 상황 실습

### 6-1. 존재하지 않는 run으로 CSV를 요청하면

```
$ curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8181/export/run_없음.csv
404
```

`run_id`가 없으면 404, 있어도 `run.table`이 비어 있으면(표를 만드는
도구를 아직 안 불렀으면) 마찬가지로 404를 돌려준다("이 run에는 아직
다운로드할 분석 표가 없다") — 빈 파일을 내려주지 않는다.

### 6-2. 반복 호출·진전 없음·한도 초과는 실제 서버로 재현하지 못했다

이 장이 쓰는 스텁 서버(`fake_openai_server.py`)는 "도구 결과가 하나라도
생기면 다음 호출에서 바로 최종 답변으로 끝내는" 단순한 구현이다(3단계·
5단계·8단계와 같은 스텁). 그래서 `repeated_tool_call`/`no_progress`/
`max_steps`/`timeout`/`token_budget`은 실제 HTTP 서버로는 재현할 수
없다 — 8단계가 같은 문제를 겪었을 때와 같은 이유로, 이 장도
`tests/test_agent_limits.py`에서 가짜 `chat_fn`을 `advance_run`에 직접
주입해 **에이전트 루프 자체는 실제로 돌려서** 확인한다(모델만 가짜다).

```
$ pytest tests/test_agent_limits.py -q
8 passed
```

`no_progress`는 8단계의 `flaky_lookup`(일부러 결함을 넣은 도구)을 이
장에 옮기지 않았으므로, `run_tool`을 몽키패치해 "인자가 달라도 결과가
항상 같다"를 흉내 내는 방식으로 확인했다(테스트 파일 docstring에 이유를
적어 뒀다).

### 6-3. 승인 인자가 비어 있을 때(스텁 서버의 한계)

5-7절에서 승인을 그대로(수정 없이) 실행하면 스텁 서버가 `archive_report`를
빈 인자(`{}`)로 호출하기 때문에 `validate_args`가 "report_id, reason
필드가 필요하다"는 오류를 낸다. 이것은 이 장의 승인 메커니즘 결함이
아니라 스텁 서버(진짜 모델이 아니므로 필수 인자를 채우지 못한다)의
한계다 — 실제 결과:

```
event: tool_result
data: {"id": "call_stub_1", "ok": false, "summary": "승인됨: {\"error\": \"invalid_args\",
       \"message\": \"입력 인자가 스키마와 맞지 않는다.\", ...}"}
```

승인은 정상적으로 이뤄졌고("승인됨"), 그 다음 인자 검증이 별도로
실패했다는 것을 화면(그리고 로그)이 구분해서 보여준다. 실제 모델은
`report_id`/`reason`을 채워서 호출하므로 이 문제가 생기지 않는다 — 5-7절
뒷부분의 edit 시나리오가 그것을 보여준다.

## 7. 응용 과제

1. **표 미리보기 확장**: 지금 화면은 `run.table` 행 개수만 보여주고
   실제 표는 CSV 다운로드로만 확인할 수 있다(`GET /runs/{run_id}`가 표
   자체를 돌려주지 않기 때문). `/runs/{run_id}`에 `table` 필드를 추가
   노출하고, `static/app.js`의 `renderTablePreview`가 이미 갖고 있는
   렌더링 함수를 이어 붙여 처음 10행을 화면에서 바로 보여줘라. 완료
   기준: CSV를 내려받지 않고도 표 내용이 화면에 보인다.
2. **다른 집계 단위로 CSV 만들기**: `lookup_sales_rows`는 원본 행을 그대로
   내보낸다. `sum_sales`처럼 집계된 결과(제품별 합계)를 CSV로 받고 싶다면
   `run.table`을 채우는 지점(`docagent/agent.py`의 `lookup_sales_rows`
   분기)을 `sum_sales` 결과도 표 형태(1행짜리 딕셔너리 리스트)로 바꿔
   저장하도록 고쳐라. 완료 기준: `sum_sales`만 호출한 run에서도
   `csv_available`이 `true`가 되고 CSV가 한 줄(헤더+집계행)로 나온다.
3. **검색 실패를 사용자에게 더 분명히 보여주기**: 지금은 `hybrid_search_docs`
   실패 시 계획 항목이 `failed`로 바뀌는 것 외에 화면에 별다른 안내가
   없다. `error` 이벤트를 추가로 보내 "문서 검색을 사용할 수 없다.
   Milvus 서버 상태를 확인해라" 같은 안내를 붙여라. 힌트: `_handle_hybrid_search`
   가 `ok=False`를 돌려주는 지점에서 `AgentEvent("error", ...)`를 하나 더
   `yield`하면 된다. 완료 기준: Milvus를 일부러 내려서(`DOCAGENT_MILVUS_URI`를
   존재하지 않는 포트로 바꿔) 검색을 시도하면 화면에 명확한 오류 안내가
   보인다.

## 8. 핵심 정리와 확인 질문

### 핵심 정리

- 이 장은 새 SSE 이벤트를 만들지 않는다. `todo`/`sources`/
  `approval_request`/`done.partial` 등 앞 장들이 이미 정의한 이벤트를
  화면이 어떻게 조립하는지가 이 장의 내용이다.
- "thinking 과정 표시"는 실행 이벤트다. 계획의 `failed` 상태처럼, 모델이
  주지 않는 정보(도구 실패)를 애플리케이션이 직접 관측해서 채우는
  경우가 있다 — 이것도 지어낸 것이 아니라 실행 사실이다.
- 계획 id 안정화(12단계 로직 재사용) + SQLite 영속화로, 계획이 바뀌거나
  실행이 승인 대기로 끊겼다 재개돼도 화면의 계획 목록이 일관되게 보인다.
- 승인의 멱등성은 SQLite의 조건부 UPDATE(8단계)가 보장하고, 화면은
  더블 클릭을 막는 것과 `already_decided`를 정직하게 보여주는 것만
  한다 — 화면이 정답을 만들지 않는다.
- 근거 링크는 5단계의 인용 검증(모델이 지어낸 출처 필터링)을 그대로
  쓰고, 이 장은 검색이 여러 번 있을 때도 라벨이 이어지도록만 확장했다.
- CSV 다운로드의 한글 인코딩은 `utf-8-sig`(BOM)로 Excel 호환성을
  얻는 대신, 다른 도구에서의 대가가 있다는 것을 실제 바이트로 확인하고
  문서에 남겼다.
- `finish_reason` 8개 값 각각에 대해 화면에 무엇을 보여줄지 미리
  정의했고, 그중 다섯은 `partial`로 부분 결과를 함께 보여준다.

### 확인 질문

1. 화면의 "✕ 실패" Todo 상태는 어디서 오는가? 모델이 그렇게 말했는가?
2. 계획이 실행 중 바뀌었을 때 이전 항목의 id가 재사용되지 않으면(매번
   새 id를 발급하면) 화면에 어떤 문제가 생기는가?
3. 승인 버튼을 두 번 눌렀을 때 실제로 실행을 막는 것은 화면 코드인가,
   서버 코드인가? 화면 쪽 방어를 지워도 여전히 안전한 이유는?
4. `[S9]`처럼 검색 결과에 없는 인용 번호를 모델이 답변에 썼을 때, 그
   URL은 왜 화면에 나타나지 않는가?
5. CSV에 BOM을 붙이지 않으면 어떤 상황에서 문제가 생기는가? 반대로
   BOM을 붙였을 때 대가는 무엇인가?
6. `done.partial`에 `csv_available`을 추가한 것이 왜 하위 호환을
   깨지 않는가?

### 이 장의 완성 코드

`code/step13_ui_export/`. 실행: `README.md`의 1~4절(단일 에이전트 루프),
5절(최종 통합: MCP·Skill·협업·트레이싱을 함께 띄우는 절차).

### 완료 기준 점검 (docs/00-curriculum.md 5절, 11개 항목)

이 표는 **이번 개정 시점**(2026-09-09, MCP·Skill·멀티에이전트+A2A·트레이싱을
이 장에 배선한 이후)의 최종 상태다. 이전 개정에서 "확인하지 못함"이었던
4(부분)·5·6·10번 네 항목의 상태가 이번에 바뀌었다. 바뀌지 않은 항목은
이전과 같은 근거를 그대로 남겼다.

| # | 완료 기준 | 확인 방법과 결과 |
| --- | --- | --- |
| 1 | API 응답과 실행 상태가 스트리밍된다 | 실제로 확인. 5-2절 `curl -N`으로 `status`/`tool_call`/`tool_result`/`todo`/`token`/`sources`/`done`이 SSE로 순서대로 도착하는 것을 직접 봤다. |
| 2 | 하이브리드 검색으로 근거를 찾는다 | 실제로 확인. 5-3절에서 `hybrid_search_docs` 도구가 Milvus Lite의 Dense+Sparse 하이브리드 검색(5단계 코드 그대로)을 호출해 실제 청크를 반환했다. |
| 3 | 답변의 출처 링크를 실제로 열 수 있다 | 실제로 확인. 5-6절에서 `/sources/{doc_id}` 라우트가 200을 반환하고 청크가 `<mark>`로 강조되는 것을 확인했다. 인용 검증 배선(모델 답변의 `[Sn]` → 실제 URL)은 5-8절에서 가짜 `chat_fn`으로 루프 전체를 돌려 확인했다. |
| 4 | Tool·MCP·Skill의 역할을 구분해 구현한다 | **실제로 확인**(이번 개정). Tool(`sum_sales`/`lookup_sales_rows`/`archive_report`/`write_plan`/`hybrid_search_docs`), MCP(`mcp_search_docs` — 별도 프로세스 `mcp_server/`, 5-9절에서 실제 자식 프로세스로 띄워 Dense 검색까지 성공 확인), Skill(`csv_analysis_skill`/`doc_research_skill` — 12단계 스크립트를 subprocess로 그대로 실행)이 **같은 에이전트 루프 안에서 함께 실행되고**, `tool_call` 이벤트의 `source` 필드(`"local"`/`"mcp"`/`"skill"`)로 화면·로그에서 구분된다. `tests/test_mcp_wiring.py`(8개, 실제 MCP 서버 프로세스 포함)·`tests/test_skill_wiring.py`(7개, 실제 스크립트 실행)로 자동화했고, 5-9절에서 세 층을 한 세션에서 함께 실행해 확인했다. |
| 5 | 여러 에이전트의 위임과 결과 통합이 동작한다 | **실제로 확인**(이번 개정). 11단계 슈퍼바이저 그래프(`docagent/multiagent/graph_supervisor.py`, 코드 변경 없이 그대로 옮김)를 `POST /collab/supervisor`가 실행한다. investigator→analyst→verifier 위임과 `finish` 노드의 결과 통합을 5-9절에서 실제 HTTP 요청으로 확인했고, `tests/test_collab_wiring.py`로 회귀 테스트를 고정했다. |
| 6 | 내부 멀티에이전트 협업과 A2A 연결을 구분한다 | **실제로 확인**(이번 개정). `external_verifier` 플래그로 같은 그래프 위에서 내부 함수 호출(`[verifier] ...판단 주체: heuristic`)과 별도 프로세스 A2A 최소 흉내 서버 위임(`[verifier(A2A)] ...판단 주체: a2a_agent`)을 가른다 — 5-9절에서 실제로 A2A 서버 프로세스를 띄워 둘 다 실행해 로그 차이를 확인했다. A2A 서버가 응답하지 않을 때도 협업이 죽지 않고 `a2a_agent_unreachable`로 정상 마무리되는 것도 확인했다(`tests/test_collab_wiring.py::test_external_a2a_verifier_unreachable_degrades_gracefully`). **`docagent/a2a_min/server.py`는 A2A 프로토콜 구현이 아니라 그 개념의 최소 흉내라는 11단계의 표시를 그대로 유지했다** — 이 장도 "A2A를 구현했다"고 쓰지 않는다. |
| 7 | Todo List가 실제 작업 상태를 반영한다 | 실제로 확인. 5-3절 이하에서 `write_plan` 호출과 도구 실패에 따라 `todo` 이벤트가 실제로 나가는 것을 확인했고, id 안정화·상태 동기화·재개 후 유지·실패 관측은 `tests/test_todo_sync.py`(7개 테스트, 모두 통과)로 검증했다. |
| 8 | 승인·거절·수정 후 재개가 동작한다 | 실제로 확인. 5-7절에서 승인, 거절(중단), 수정(edit) 세 경로 모두 `curl`로 직접 실행해 `finish_reason`과 `tool_result`가 기대대로 나오는 것을 봤다. 더블 클릭 멱등성도 실제 HTTP 응답(`already_decided`)으로 확인했고, `tests/test_agent_limits.py`의 두 테스트로도 별도 확인했다. |
| 9 | 무한루프와 실행 예산 초과를 차단한다 | **실제 서버로는 확인하지 못함, 루프 자체는 확인함**(이전과 동일). 6-2절 설명대로 이 장의 스텁 서버로는 반복 호출 시나리오를 만들 수 없어, `tests/test_agent_limits.py`에서 가짜 `chat_fn`을 `advance_run`(실제 함수)에 주입해 `repeated_tool_call`/`no_progress`/`max_steps`/`timeout`/`token_budget` 5가지 모두를 확인했다(8개 테스트 통과). 협업 전체 단위 한도(`MAX_AGENT_HANDOFFS`/`MAX_COLLAB_TOKENS`/`delegation_loop`, 11단계 확장분)는 이 장이 새로 검증하지 않았다 — `docagent/multiagent/budget.py`·`handoff.py`를 코드 변경 없이 그대로 가져왔을 뿐이고, 그 로직 자체의 검증은 11단계 `tests/`(`test_handoff_loop.py` 등)가 이미 했다. |
| 10 | 실패 원인을 트레이스로 확인한다 | **실제로 확인**(이번 개정). Phoenix를 로컬로 띄우고(`python -m phoenix.server.main serve`) `DOCAGENT_TRACING_ENABLED=true`로 이 장의 앱을 실행한 뒤, 일부러 빈 `query`로 `mcp_search_docs`를 호출해 실패를 냈다. GraphQL API로 Phoenix에서 `mcp.search_docs` 스팬의 `statusCode: ERROR`와 `statusMessage`("query가 비어 있다..."), `error.code` 속성을 실제로 조회해 **실패 원인을 트레이스에서 추적**했다 — 5-9절에 명령과 실제 출력을 그대로 남겼다. `tests/test_tracing_wiring.py`는 결정론적인 부분(NoOp 기본 동작, `init_tracing`의 활성화 여부)만 자동화했고, 실제 Phoenix로의 전달 확인은 pytest가 아니라 이 수동 통합 실행에서 했다(이유: 외부 서버 기동이 필요해 결정론적 자동 테스트에 넣지 않았다 — 9단계와 같은 판단). |
| 11 | 분석 결과를 CSV로 다운로드한다 | 실제로 확인. 5-4절·5-5절에서 실제로 CSV 파일을 내려받아 `Content-Disposition`의 `filename*=UTF-8''...` 헤더, BOM 3바이트, `utf-8-sig` 디코딩 결과, BOM 없는 파일과의 바이트 차이까지 전부 직접 확인했다. `tests/test_csv_export.py`(8개 테스트)로 인코딩·헤더·빈 표 처리를 회귀 테스트로 고정했다. |

**요약**: 11개 중 실제로 실행해 확인한 것은 1·2·3·4·5·6·7·8·10·11 10개다.
9번만 "루프 자체(단일 run 단위 한도)는 실제 스텁 서버로 재현할 수 없어
가짜 `chat_fn` 주입으로 확인했고, 협업 전체 단위 한도(11단계 확장분)는
코드를 그대로 옮겼을 뿐 이 장이 다시 검증하지 않았다"는 절반의 확인으로
남는다. **아직 확인하지 못한 것**: (a) 협업 전체 단위 실행 한도
(`MAX_AGENT_HANDOFFS`/`MAX_COLLAB_TOKENS`/`delegation_loop`)를 이 장의
`/collab/supervisor` 경로로 직접 넘겨 재현하는 것 — 11단계 테스트가 이미
검증한 로직을 가져왔을 뿐 13단계 관점에서 재실행하지 않았다. (b) 실제
의미를 갖는 모델(스텁이 아닌 실 모델 서버)을 대상으로 한 전 구간 실행 —
스텁 서버는 고정 응답만 내므로 "모델이 MCP·Skill·협업 중 무엇을 언제 쓸지
스스로 판단하는 것"은 검증 범위 밖이다. (c) 브라우저 화면에서 `source`
필드가 실제로 구분되어 보이는지의 시각적 확인 — `static/app.js`는
`tool_call.source`를 로그 줄에 표시하도록 고쳤지만(4-8절 코드 참고),
실제 브라우저 렌더링은 이전 개정과 마찬가지로 시각적으로 확인하지
못했다.

## 참고 문서

- RFC 6266, *Use of the Content-Disposition Header Field in the Hypertext
  Transfer Protocol (HTTP)* — `filename*` 확장 문법과 우선순위(4.3절).
  https://www.rfc-editor.org/rfc/rfc6266
- RFC 5987, *Character Set and Language Encoding for Hypertext Transfer
  Protocol (HTTP) Header Field Parameters* — `ext-value`(`charset'lang'value`)
  문법. https://www.rfc-editor.org/rfc/rfc5987
- RFC 7230 3.2.4절, HTTP 헤더 필드 값의 문자 집합(라틴-1 바이트) 제약.
  https://www.rfc-editor.org/rfc/rfc7230
- Python 표준 라이브러리 `codecs` 문서, `utf-8-sig` 코덱 설명(BOM 추가/
  제거). https://docs.python.org/3/library/codecs.html#encodings-and-unicode
- 이 튜토리얼 `code/step05_hybrid_rag/`, `code/step08_hitl/`,
  `code/step09_tracing_eval/`, `code/step10_mcp/`, `code/step11_multi_agent/`,
  `code/step12_deep_agents/docagent/bridge.py` — 이 장이 그대로 재사용한
  코드의 원본. 각 장 하단 "참고 문서"에 개별 API 근거가 있다.
- 실제 실행 확인은 이 문서 5절에 기록한 명령과 출력(2026-09-09,
  `code/step13_ui_export/`)을 그대로 옮긴 것이다.

> 검증 상태: 이 장의 모든 `.py` 파일은 `python3 -m py_compile`을
> 통과했다(`docagent/`, `mcp_server/`, `skills/`, `tests/` 전부 포함).
> `pytest tests/`(50개 테스트 — 기존 26개 `test_csv_export.py`·
> `test_todo_sync.py`·`test_finish_reason_display.py`·
> `test_agent_limits.py`·`test_citation_wiring.py`에 이번 개정에서 추가한
> 24개 `test_mcp_wiring.py`(8개)·`test_skill_wiring.py`(7개)·
> `test_collab_wiring.py`(5개)·`test_tracing_wiring.py`(4개))를 실제로
> 실행해 전부 통과를 확인했다(가상환경 하나에 이 장의 `requirements.txt`를
> 그대로 설치, Python 3.11.15). 새로 추가한 테스트들은 목이 아니라 실제
> 자식 프로세스를 띄운다 — `test_mcp_wiring.py`는 `python -m
> mcp_server.server`를 실제로 자식 프로세스로 실행하고(`DOCAGENT_MCP_SERVER_COMMAND`가
> 그 테스트를 돌리는 가상환경의 `python`을 가리켜야 한다 — 시스템
> `python`에는 `mcp` 패키지가 없어 죽는다, 3절 참고), `test_collab_wiring.py`는
> `python -m docagent.a2a_min.server`를 별도 포트로 실제로 띄운다.
>
> 백엔드는 실제 FastAPI 서버(`uvicorn`)와 스텁 OpenAI 호환 서버
> (`code/_tools/fake_openai_server.py`), Milvus Lite를 띄워 `/chat`(기본
> 흐름, 하이브리드 검색, 판매 행 조회), 승인 3경로(승인/거절/수정)와
> 더블 클릭 멱등성, `/export/{run_id}.csv`(CSV 바이트·BOM·헤더),
> `/sources/{doc_id}`를 실제 HTTP 요청으로 확인했다.
>
> **이번 개정에서 추가로 실제 실행해 확인한 것**(5-9절에 명령과 출력
> 전문): 실제 MCP 서버 자식 프로세스를 통한 Dense 검색(임베딩 호출 포함)과
> 도구 탐색, `/chat`에 `include_mcp=true`를 줬을 때 `mcp_search_docs`
> 도구 호출과 `tool_call.source="mcp"`, `include_skills=true`를 줬을 때
> `csv_analysis_skill`이 실제 스크립트를 돌려 낸 집계, `/collab/supervisor`가
> 내부 검증과 실제로 띄운 A2A 최소 흉내 서버(별도 포트) 검증 둘 다에서
> `stop`으로 끝나는 것, 로컬에서 띄운 Phoenix(`python -m
> phoenix.server.main serve`)에 스팬이 실제로 도착하고 GraphQL API로
> `mcp.search_docs` 스팬의 `ERROR` 상태와 실패 메시지를 조회한 것.
>
> 반복 호출·진전 없음·시간·토큰 한도 초과(`repeated_tool_call`/
> `no_progress`/`timeout`/`token_budget`/`max_steps`, 단일 run 단위)는
> 실제 스텁 서버로는 재현할 수 없어(6-2절 이유) 가짜 `chat_fn`을 에이전트
> 루프에 직접 주입하는 방식으로 확인했다 — 모델 서버 호출만 가짜이고
> 나머지(`docagent.agent.advance_run`, SQLite 저장)는 실제 코드가
> 실행됐다. **협업 전체 단위 한도**(`MAX_AGENT_HANDOFFS`/
> `MAX_COLLAB_TOKENS`/`delegation_loop`, 11단계가 추가한 것)는 이 장이
> 코드를 옮기기만 했고 13단계 관점에서 다시 실행해 확인하지 않았다 — 그
> 로직 자체의 검증은 11단계 `tests/test_handoff_loop.py` 등이 이미 했다.
>
> **여전히 확인하지 못한 것**: 실제 의미를 갖는 모델(스텁이 아닌 실
> 모델 서버)을 대상으로 MCP·Skill·협업을 모델 스스로 판단해 골라 쓰는
> 전 구간 실행 — 스텁 서버는 고정 응답만 내므로 "언제 MCP를, 언제
> Skill을, 언제 협업 라우트를 쓸지" 자체는 모델의 판단이 필요한 부분이라
> 검증이 제한된다(스텁 서버가 항상 `tools[0]`을 부르는 성질을 이용해
> 특정 도구를 강제로 호출시키는 방식으로 배선 자체만 확인했다). 협업
> 전체 단위 실행 한도를 이 장의 `/collab/supervisor` 경로로 직접 넘겨
> 재현하는 것. 브라우저에서 화면을 눈으로 확인하는 것(`static/app.js`가
> `tool_call.source`를 로그 줄에 표시하도록 코드는 고쳤지만 — 4-8절 —
> 실제 브라우저 렌더링·클릭 동작은 이 세션에서 시각적으로 확인하지
> 못했다). LangSmith 등 다른 관측 플랫폼으로의 연결(9단계와 같은 이유로
> 이 환경에서 검증할 수 없다).
