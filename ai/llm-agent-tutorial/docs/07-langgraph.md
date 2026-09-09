# 7단계. LangGraph와 오케스트레이션

## 1. 학습 목표와 완성 모습

6단계는 검색·모델 호출·도구를 LangChain의 컴포넌트(Runnable, Retriever,
Tool)로 감쌌다. 그 컴포넌트들을 어떤 순서로, 언제 반복하고, 언제 갈라지고,
실패하면 어떻게 되돌아갈지는 여전히 호출하는 쪽 코드가 직접 짜야 했다.
이 장은 그 "순서와 상태"를 **LangGraph**로 명시적인 그래프로 뽑아낸다.

만들 것은 5단계 RAG 파이프라인을 아래 그래프로 다시 구성한 것이다.

```
질문
 │
 ▼
[classify]  질문을 factual/chitchat으로 분류
 │
 ├─ chitchat ──────────────────────────────────────────► [chitchat_answer] ─► 끝
 │
 └─ factual
     │
     ├──────────────┬──────────────┐   (병렬)
     ▼              ▼              │
 [retrieve_dense] [retrieve_sparse]│
     │              │              │
     └──────┬───────┘              │
            ▼                      │
        [verify]  근거가 충분한가?  │
            │                      │
    ┌───────┴────────┐             │
    │충분             │부족·라운드 남음
    ▼                 ▼             │
 [answer] ─► 끝    [expand_query] ──┘  (질의를 넓혀 다시 검색)
```

완성 모습은 이렇다. 학습용 스텁 모델 서버(`code/_tools/fake_openai_server.py`)와
`docagent/rag/fake_retriever.py`(Milvus 없이 쓰는 인메모리 검색기, 4절에서
설명한다)를 붙여 실제로 실행하면:

```
$ python scripts/demo_checkpoint_run1.py
[run1] 질문: '2분기 노트북 매출이 둔화된 이유는?'
[run1] 예외로 중단됨(=모델 서버 장애로 프로세스가 죽었다고 가정): LLMError(...)
[run1] 다음에 실행할 노드: ('answer',)

$ python scripts/demo_checkpoint_run2.py   # 완전히 새로운 파이썬 프로세스
[run2] 재개 전 상태: 다음에 실행할 노드 = ('answer',)
[run2] 재개 완료. 최종 답변: ...
[run2] 재개 후 다음 노드 (완료됐으면 빈 튜플): ()
```

`retrieve_dense`/`retrieve_sparse`/`verify`는 1차 실행(run1)에서 이미 끝나
체크포인트에 저장돼 있었고, 2차 실행(run2, 별도 프로세스)은 그 세 노드를
**다시 실행하지 않고** 멈췄던 `answer`부터 이어서 끝낸다. 이 장의 완료
기준이 바로 이 장면이다 — 조건별 분기와 저장된 상태를 확인하고, 중단된
작업을 실제로 재개한다.

## 2. 핵심 개념

### 2-1. State, Node, Edge

LangGraph는 세 가지 요소로 실행을 표현한다.

| 요소 | 무엇인가 | 이 그래프에서의 예 |
| --- | --- | --- |
| **State** | 그래프가 실행되는 동안 노드들이 공유하는 데이터. 타입으로 선언한다(`TypedDict`) | `GraphState`: 질문, 분류 결과, 검색된 청크, 답변, 실행 로그 등 |
| **Node** | 상태 일부를 받아 상태 일부를 돌려주는 함수. 실제 작업(모델 호출, 검색 호출, 순수 계산)을 한다 | `classify`, `retrieve_dense`, `verify`, `answer` |
| **Edge** | 한 노드가 끝난 뒤 다음에 어느 노드로 갈지 정하는 연결. 고정 간선(`add_edge`)과 조건부 간선(`add_conditional_edges`)이 있다 | `retrieve_dense → verify`(고정), `verify → answer 또는 expand_query`(조건부) |

노드는 상태 **전체**를 새로 만들 필요가 없다 — 바뀐 부분만 담은 딕셔너리를
돌려주면 LangGraph가 나머지 필드는 그대로 둔 채 병합한다. 이 병합 규칙이
**리듀서(reducer)**다. `Annotated[list[str], operator.add]`처럼 표시하지
않은 필드는 "마지막에 쓴 값이 이전 값을 덮어쓴다"가 기본 리듀서다. 이 장의
`GraphState`(`code/step07_langgraph/docagent/graph/state.py`)는 이 기본
동작으로 충분한 필드(`category`, `answer_text` 등)와, 여러 번 누적해야 하는
필드(`trace`: `operator.add`), 그리고 병렬 노드의 결과를 안전하게 합쳐야
하는 필드(`retrieved`: 커스텀 리듀서 `merge_hits`, 2-4절)를 구분해서 쓴다.

실행 모델은 **Pregel**(그래프 전체를 "슈퍼스텝" 단위로 도는 벌크 동기
병렬 처리 모델. Google이 그래프 계산용으로 제안한 모델이고 LangGraph의
런타임이 이 이름을 그대로 쓴다)이다. 한 슈퍼스텝에서 실행 가능한 노드를
전부(병렬로 실행 가능하면 동시에) 돌리고, 그 결과로 상태를 갱신한 뒤 다음
슈퍼스텝으로 넘어간다. `retrieve_dense`와 `retrieve_sparse`가 "병렬로
실행된다"는 말은 정확히는 "같은 슈퍼스텝에 속해 동시에 실행된다"는 뜻이다
(2-4절, 5-2절에서 이 구분이 실제로 중요해진다).

### 2-2. 조건 분기와 반복

`add_conditional_edges(출발_노드, 라우팅_함수, 목적지_맵)`은 `출발_노드`가
끝난 뒤 `라우팅_함수(state)`가 돌려준 값으로 다음 노드를 정한다. 이 장은
두 곳에 조건부 간선을 쓴다.

- `classify → (chitchat_answer | retrieve_dense·retrieve_sparse)`:
  질문 유형에 따라 완전히 다른 경로로 간다.
- `verify → (answer | expand_query)`: 근거가 충분하면 답변, 부족하면 질의를
  넓혀 재검색한다.

**반복(loop)**은 특별한 문법이 아니라 그냥 간선이 그래프를 되돌아가는
것이다. `expand_query → retrieve_dense`, `expand_query → retrieve_sparse`
간선이 `retrieve_* → verify → (부족) → expand_query`로 다시 돌아오게 만들어
반복을 만든다. 반복은 반드시 멈출 조건이 있어야 한다 — 이 그래프는
`retrieval_round`가 `max_retrieval_rounds`(기본 2)에 도달하면 근거가
부족해도 `answer`로 보낸다(`docagent/graph/nodes.py`의
`make_route_after_verify`). 이건 3단계에서 배운 에이전트 반복 한도
(`MAX_AGENT_STEPS`, PROJECT-SPEC.md 7장)와 **같은 개념을 이 그래프 자신의
반복 하나에 적용한 것**이지 그 설정을 그대로 재사용한 것은 아니다 — 이
그래프의 반복은 "도구를 몇 번 부르는가"가 아니라 "재검색을 몇 라운드
하는가"라서 별도의 지역 변수(`max_retrieval_rounds`)로 관리한다. 또한
LangGraph 자체도 그래프 전체의 안전장치로 `recursion_limit`(기본값 25,
`invoke`/`stream`의 `config`로 바꿀 수 있다)을 둔다 — 이 그래프는
`max_retrieval_rounds=2`로 훨씬 먼저 멈추므로 기본값에 걸리지 않지만,
코드에 버그가 있어 반복이 멈추지 않는 최악의 경우에도 이 상한이 프로세스를
끝없이 돌지 않게 막아 준다.

### 2-3. 정해진 워크플로와 모델 판단의 조합

이 그래프의 노드와 간선(어떤 단계가 있고 무엇 다음에 무엇이 오는지)은
**애플리케이션이 코드로 고정**한다 — 모델이 "다음에 뭘 할지" 자유롭게
정하지 않는다. 그 안에서 딱 두 갈래길("이 질문은 잡담인가?", "이 근거로
충분한가?")의 방향만 모델의 판단을 빌린다.

`classify`와 `verify` 두 노드가 이 방식을 쓴다. 먼저 `docagent.llm.chat_json`
(PROJECT-SPEC.md 9절 공통 인터페이스, `response_format=json_schema`로 구조화된
출력을 요청한다)으로 모델에게 판단을 물어본다. 응답이 기대한 키
(`category`, `sufficient`)를 기대한 타입으로 담고 있으면 그 판단을 쓰고,
그렇지 않으면(호출 자체가 실패했거나, 응답이 스키마를 채우지 않았거나) 규칙
기반 휴리스틱으로 대체한다.

```python
# code/step07_langgraph/docagent/graph/nodes.py (발췌, classify)
try:
    result = deps.json_chat(..., schema_name="classify_question", json_schema=schema)
    if isinstance(result.get("category"), str) and result["category"] in ("factual", "chitchat"):
        category = result["category"]
        source = "model"
except Exception:
    category = None

if category is None:
    # 규칙 기반 휴리스틱으로 대체
    ...
    source = "heuristic"
```

**왜 항상 대체 경로를 두는가**: WRITING-GUIDE.md 정확성 규칙대로, 이 저장소는
"모든 OpenAI 호환 서버가 구조화된 출력을 동일하게 지원한다"고 가정하지
않는다. 실제로 이 저장소의 학습용 스텁 서버(`code/_tools/fake_openai_server.py`)는
`response_format`의 `type`이 `json_object`/`json_schema`이면 **요청한
스키마 내용과 무관하게** 고정된 JSON(`{"answer": "42", "confidence": 0.9}`)을
돌려준다 — 유효한 JSON인지는 확인하지만 스키마를 채웠는지는 확인하지 않는다.
그래서 이 스텁 서버로 실행하면 `classify`/`verify`는 **항상** 휴리스틱
경로로 떨어진다(5절에서 실제 실행 로그로 확인한다). 실제 모델 서버가
`json_schema`를 스키마대로 채워 준다면 `category_source`/`verify_source`
필드가 `"model"`로 바뀐다 — 이 필드를 남겨 둔 이유가 그것이다: "이번
판단이 모델에서 왔는지 규칙에서 왔는지"를 실행 후에도 구분할 수 있게
한다.

### 2-4. 병렬 실행과 결과 통합

`classify`의 조건부 간선이 `["retrieve_dense", "retrieve_sparse"]`처럼
**목적지를 리스트로** 돌려주면, LangGraph는 그 노드들을 같은 슈퍼스텝에서
나란히 실행한다(langchain-ai/langgraph 저장소
`libs/langgraph/langgraph/graph/state.py`의 `add_conditional_edges` — 참고
문서 참고). 두 노드는 서로 다른 검색(의미 검색/키워드 검색)을 실행하고, 둘
다 상태의 `retrieved` 채널에 자기 결과를 쓴다.

문제는 여기서 생긴다 — 같은 슈퍼스텝에서 같은 채널에 두 번 쓰면 기본
리듀서("마지막 값이 이긴다")는 오류를 낸다(`InvalidUpdateError: Can receive
only one value per step`, 실제로 재현했다). 그래서 `retrieved`는 커스텀
리듀서를 쓴다.

```python
# code/step07_langgraph/docagent/graph/state.py (발췌)
def merge_hits(left: list[dict], right: list[dict]) -> list[dict]:
    by_pk = {h["pk"]: h for h in left}
    for hit in right:
        prev = by_pk.get(hit["pk"])
        if prev is None or hit.get("score", 0.0) > prev.get("score", 0.0):
            by_pk[hit["pk"]] = hit
    return sorted(by_pk.values(), key=lambda h: h.get("score", 0.0), reverse=True)

class GraphState(TypedDict):
    retrieved: Annotated[list[dict], merge_hits]
    ...
```

청크 ID(`pk`)가 같은 결과(같은 청크가 dense·sparse 양쪽에서 모두 나온
경우)는 점수가 더 높은 쪽을 남기고, 나머지는 이어 붙여 점수 내림차순으로
정렬한다. 이 병합은 "같은 청크의 충돌을 어떻게 푸는가"를 보여주는 것이
목적이라 엄밀한 순위 결합(RRF, 가중치)은 아니다 — 엄밀한 결합은 이미 5단계
`hybrid_search`가 Milvus 서버 안에서 한다(`code/step05_hybrid_rag/docagent/rag/search.py`).
이 그래프도 검색을 `hybrid_search` 한 번으로 대체할 수 있지만, 그러면
"두 병렬 노드의 결과를 그래프 수준에서 병합한다"를 보여줄 자리가 없어져서
이 장은 일부러 dense/sparse를 그래프에서 직접 병렬로 돌린다.

실제로 확인한 동작(직접 재현):

```python
# 두 노드가 겹치는 pk("x")를 서로 다른 점수로 반환해도 안전하게 병합된다
{'retrieved': [{'pk': 'y', 'score': 0.9}, {'pk': 'x', 'score': 0.7}, {'pk': 'z', 'score': 0.1}]}
```

### 2-5. 체크포인트와 실행 재개

**체크포인터(checkpointer)**는 매 슈퍼스텝이 끝날 때마다(정확히는, 각
노드 태스크가 끝날 때마다) 그 시점의 상태를 저장소에 남기는 컴포넌트다.
저장 위치는 `thread_id`(하나의 대화/실행을 식별하는 키)로 구분된다. 같은
`thread_id`로 `invoke(None, config=...)`(입력을 새로 주지 않고 `None`을
준다)를 호출하면, 마지막으로 저장된 상태를 읽어 **다음에 실행해야 할
노드부터** 이어서 실행한다.

이 장은 `langgraph-checkpoint-sqlite`(SQLite 파일에 저장하는 구현)를 쓴다.
동기 코드에는 `SqliteSaver`, FastAPI 같은 비동기 코드에는 `AsyncSqliteSaver`를
쓴다(둘 다 같은 패키지, `docagent/graph/build.py`/`docagent/app.py`에서
각각 쓴다).

**실제로 확인한, 이 장에서 가장 중요한 세부 동작**: 체크포인트는
슈퍼스텝이 아니라 **태스크(노드 하나의 실행)** 단위로 저장된다. 즉
`retrieve_dense`/`retrieve_sparse`가 같은 슈퍼스텝에서 병렬로 실행되다가
한쪽만 성공하고 다른 쪽이 예외로 죽어도, 성공한 쪽의 결과는 그대로
저장되고 "다음에 실행할 노드"는 실패한 쪽 하나만 남는다. 직접 재현한 결과:

```
[run1] a 실행
[run1] b 실행 (여기서 죽는다)
[run1] 예외로 중단: <class 'SystemExit'> 프로세스 강제 종료 시뮬레이션
[run1] 저장된 state: {'log': ['a-ok']} 다음 노드: ('b',)
```

새 프로세스로 재개하면 `a`는 다시 실행되지 않고(콘솔에 `a` 실행 로그가
다시 찍히지 않는다) `b`부터, 성공하면 `join`까지 이어서 실행된다.

```
[run2] 재개 전 state: {'log': ['a-ok']} 다음 노드: ('b',)
[run2] b 실행 (수리된 버전, 이번엔 성공)
[run2] 재개 완료: {'log': ['a-ok', 'b-ok', 'join']}
```

이 장의 완성 코드에서는 `answer` 노드(모델 호출)가 실패하도록 강제해 같은
장면을 실제 그래프로 재현한다(1절, 5-2절). 체크포인트가 없다면 재개할
때마다 `classify`부터, 즉 검색까지 전부 다시 해야 한다 — 체크포인트는 "이미
끝난 비용이 드는 작업(검색, 모델 호출)을 다시 하지 않고 실패한 지점부터만
다시 시도"할 수 있게 해 준다.

### 2-6. 노드별 오류 처리와 재시도

LangGraph는 노드에 `retry_policy`(재시도 횟수·간격·대상 예외)와
`error_handler`(재시도를 다 써도 실패하면 대신 호출되는 복구 노드)를
붙일 수 있다. 이 장은 노드의 성격에 따라 **세 가지 다른 전략**을 쓴다.

| 노드 | 실패하는 이유(예) | 전략 |
| --- | --- | --- |
| `classify`, `verify` | 모델이 구조화된 출력을 못 채움, 모델 서버 오류 | 노드 함수 **안에서** `except Exception`으로 잡아 규칙 기반 휴리스틱으로 즉시 대체한다. 예외를 밖으로 보내지 않는다 |
| `retrieve_dense`, `retrieve_sparse` | 검색 서버(Milvus)나 임베딩 호출 실패 | 노드 함수 **안에서** 최대 3회 재시도 후, 그래도 실패하면 그 검색만 빈 결과로 남기고 그래프는 계속 진행한다(다른 쪽 검색 결과만으로 답을 시도) |
| `answer`, `chitchat_answer` | 모델 서버 타임아웃·5xx | LangGraph의 `retry_policy`로 최대 3회 재시도하고, 그래도 실패하면 노드 실패로 그래프 실행 자체를 실패시킨다 |

같은 "모델 호출 실패"인데 `classify`/`verify`와 `answer`가 다른 전략을
쓰는 이유는 실패의 무게가 다르기 때문이다 — 분류·근거 확인은 "틀려도 규칙
기반 대안이 있고, 최종 답변의 정확성에 결정적이지 않다." 반면 답변 생성
자체가 실패하면 사용자에게 보여줄 결과가 없으므로, 몇 번 더 시도해 볼
가치가 있고 그래도 안 되면 실패를 숨기지 않고 사용자(또는 재시도하는
운영자)에게 알린다.

`retrieve_dense`/`retrieve_sparse`가 LangGraph의 `retry_policy`/
`error_handler` 대신 노드 함수 안에서 직접 처리하는 이유는 실제로 겪은
문제 때문이다 — **langgraph 1.2.11에서 재현**: 같은 슈퍼스텝에서 병렬로
실행되는 형제 노드가 있을 때, 그중 하나가 `retry_policy`를 다 쓰고
`error_handler`로 복구되더라도(핸들러 함수는 정상 호출되고 값도 만든다)
그 슈퍼스텝을 실행한 스레드 풀 실행기가 원래 예외를 다시 던져
`graph.invoke()` 전체가 실패로 끝난다. 형제 노드가 없는 단독 노드(이 장의
`answer`, `chitchat_answer`처럼 그 슈퍼스텝에 자기 혼자만 있는 노드)에서는
`error_handler`가 정상 동작한다. 재현 스크립트와 정확한 트레이스백은 이 장
"검증 상태"와 `docagent/graph/build.py` 모듈 docstring에 있다. 이 문제
때문에 병렬로 실행되는 두 검색 노드는 프레임워크 기능 대신 파이썬 `try`/
`except`와 수동 재시도 루프(`docagent/graph/nodes.py`의
`_search_with_retry`)로 같은 효과를 낸다.

### 2-7. LangChain과 LangGraph의 역할 차이

| | LangChain (6단계) | LangGraph (7단계) |
| --- | --- | --- |
| 다루는 대상 | 모델 호출, 프롬프트, Retriever, Tool 하나하나를 감싸는 컴포넌트(Runnable) | 그 컴포넌트들을 **어떤 순서로, 언제 반복하고, 언제 갈라질지** 정하는 상태 기계 |
| 상태 | 기본적으로 없다(각 Runnable은 입력을 받아 출력만 낸다) — 대화 기록 등은 별도로 관리해야 한다 | `State`가 일급 개념이다. 모든 노드가 같은 State를 읽고 쓴다 |
| 분기·반복 | `RunnableBranch` 등으로 표현할 수 있지만 반복(사이클)은 자연스럽게 표현하기 어렵다 | `add_conditional_edges` + 되돌아가는 간선으로 분기와 반복을 그래프로 명시한다 |
| 병렬 실행 | `RunnableParallel`로 여러 Runnable을 동시에 돌릴 수 있다 | 조건부 간선이 여러 노드 이름을 반환하면 같은 슈퍼스텝에서 병렬 실행되고, 리듀서로 결과가 병합된다 |
| 중단·재개 | 기본 기능 없음(직접 구현해야 한다) | 체크포인터 + `thread_id`로 실행 중단·재개가 프레임워크 기능이다 |
| 이 프로젝트에서 쓰는 곳 | 모델 인터페이스, 프롬프트 템플릿, Retriever 래핑(6단계) | 그 위에서 도는 "질문 분류→검색→근거 확인→답변" 흐름 전체(7단계) |

정리하면 LangChain은 **부품**을, LangGraph는 그 부품들을 조립하는 **배선도와
전원 스위치**를 맡는다. 실제로 이 장의 코드는 LangChain 컴포넌트를 쓰지
않는다 — 5단계의 `docagent.llm`(PROJECT-SPEC.md 9절 공통 인터페이스)과
`docagent.rag.search`를 노드 함수 안에서 직접 호출한다. LangGraph 자체는
"무엇으로 모델을 호출하는지"에 관심이 없다 — 노드 함수 안에서 openai SDK를
직접 쓰든 LangChain의 `ChatOpenAI`를 쓰든 LangGraph 입장에서는 "상태를 받아
상태를 돌려주는 함수" 하나일 뿐이다. 이 프로젝트가 그중 직접 호출 쪽을
고른 이유는 WRITING-GUIDE.md의 "LangChain이 모든 기술의 기반이라고
설명하지 않는다" 원칙대로, 두 프레임워크가 서로 다른 축(부품 vs 배선)을
다룬다는 것을 코드로도 분리해서 보여주기 위해서다 — LangGraph를 쓰기 위해
LangChain 컴포넌트가 반드시 있어야 하는 것은 아니다.

### 2-8. FastAPI·작업 큐·LangGraph의 역할 구분

이 프로젝트에는 지금 세 후보가 있다 — 이번 장에서 실제로 쓰는 두 가지와,
아직 두지 않는 한 가지다.

| 주체 | 책임 | 이 장에서 |
| --- | --- | --- |
| **FastAPI** | HTTP 요청을 받고 SSE로 응답을 스트리밍한다. "무엇을 실행할지"는 모른다 | `docagent/app.py`의 `/chat`이 `graph.astream(...)`을 호출하고 그 결과를 `status`/`token`/`sources`/`done` 이벤트로 옮길 뿐이다 |
| **LangGraph** | 상태·분기·반복·재시도를 결정한다. HTTP나 SSE를 모른다 | `docagent/graph/`가 전부 담당한다 |
| **작업 큐**(Celery, RQ 등) | HTTP 요청-응답 범위보다 오래 걸리는 작업을 백그라운드로 넘기고, 여러 워커에 분산한다 | **아직 두지 않는다** |

**왜 아직 작업 큐를 두지 않는가** (WRITING-GUIDE.md 4.6 "과도한 운영
구성을 먼저 도입하지 않는다"): 이 그래프 한 번 실행은 `MAX_AGENT_SECONDS`
(기본 120초) 안에 끝나는, HTTP 요청 하나에 자연스럽게 대응되는 작업이다.
게다가 체크포인터가 이미 "중단된 지점부터 재개"를 제공하므로, 작업 큐가
주로 해결하는 문제 중 하나("오래 걸리는 작업이 웹 서버 프로세스가 죽어도
살아남게 한다")를 상당 부분 이미 만족한다. 작업 큐가 꼭 필요해지는 순간은
이 프로젝트에 아직 없는 종류의 작업이다 — 예를 들어 문서 수천 건을
한꺼번에 색인하는 배치 작업(HTTP 요청-응답 범위를 훨씬 넘는 시간이 걸리고,
진행 상황을 폴링으로 따로 조회해야 한다)이나, 동시에 아주 많은 사용자의
그래프 실행을 CPU/모델 서버 처리량 한도 안에서 줄 세워야 하는 상황이다.
그런 요구가 생기면 그때 큐를 들이는 것이 맞다 — 지금은 "그래프 하나 =
요청 하나"로 충분하다.

## 3. 실습 준비

### 패키지와 버전

`code/step07_langgraph/requirements.txt`를 그대로 쓴다. 이전 단계 패키지
(`openai`, `fastapi`, `pymilvus` 등)는 PROJECT-SPEC.md 10절 기준선과 같은
범위를 쓴다. 이 장에서 새로 추가하는 두 패키지는 실제로 설치해서 확인한
버전이다(이 문서 작성 시점, 2026-09-09 PyPI 실측 — "참고 문서" 절 참고).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install "langgraph>=1.2,<2" "langgraph-checkpoint-sqlite>=3.1,<4"
pip list | grep -i langgraph
```

```
langgraph                   1.2.11
langgraph-checkpoint         4.2.0   # langgraph의 의존성으로 함께 설치된다
langgraph-checkpoint-sqlite 3.1.1
```

`langgraph-checkpoint`(체크포인터의 추상 인터페이스, `BaseCheckpointSaver`
등)는 `langgraph`가 의존성으로 끌고 오므로 따로 설치할 필요는 없다 —
SQLite 구현체(`langgraph-checkpoint-sqlite`)만 추가로 설치하면 된다.

이 장의 코드는 LangChain 컴포넌트를 쓰지 않으므로(2-7절) `langchain`,
`langchain-openai`는 `requirements.txt`에 넣지 않았다. 6단계 코드와 함께
같은 가상환경을 쓴다면 이미 설치돼 있을 수 있지만 이 단계 자체에는
필요하지 않다.

### 환경 변수

`code/step07_langgraph/.env.example`을 복사해 쓴다. 새로 추가한 이름은
`DOCAGENT_CHECKPOINT_DB`(그래프 체크포인트 SQLite 파일 경로) 하나다.
`DOCAGENT_` 접두사를 붙인 이유는 PROJECT-SPEC.md 2절과 같다 — 이 변수를
다른 라이브러리가 예약하고 있지 않은지 접두사로 위험을 낮춘다.

### 폴더 구조

```
code/step07_langgraph/
├── docagent/
│   ├── config.py, events.py, llm.py, app.py   # 5단계와 같은 인터페이스
│   ├── graph/
│   │   ├── state.py     # GraphState, merge_hits 리듀서
│   │   ├── nodes.py     # 노드 함수 + NodeDeps(의존성 주입)
│   │   └── build.py     # State+Node+Edge 조립
│   └── rag/
│       ├── ingest.py, store.py, search.py, citations.py   # 5단계 그대로
│       └── fake_retriever.py   # Milvus 없이 그래프를 실행하기 위한 인메모리 검색기
├── static/, data/
├── scripts/
│   ├── make_sample_data.py
│   ├── demo_checkpoint_run1.py
│   └── demo_checkpoint_run2.py
└── tests/
```

### 학습용 모델 서버

```bash
python code/_tools/fake_openai_server.py --port 8131
```

이 스텁 서버는 모델이 아니므로 답변 품질을 확인하는 데는 쓸 수 없다(1단계
문서 참고). 이 장에서는 그래프의 상태 전이·분기·재시도·체크포인트 재개가
**실제로 동작하는지**를 확인하는 데만 쓴다.

## 4. 단계별 구현

### 4-1. 최소 예제: State/Node/Edge와 조건부 분기

Milvus나 모델 서버 없이, LangGraph의 핵심 동작 세 가지 — 조건부 분기가
여러 노드로 fan-out하는 것, 병렬 노드의 결과가 리듀서로 병합되는 것,
체크포인트가 실패한 노드만 정확히 기억하는 것 — 를 아주 작은 그래프로
먼저 확인한다(장 하단 "검증 상태"의 재현 스크립트가 이 순서 그대로다).

```python
# 최소 예제: 조건부 간선이 리스트를 반환하면 병렬 fan-out이 된다
from typing import TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, START, END

class S(TypedDict):
    log: Annotated[list, operator.add]

def classify(state): return {"log": ["classify"]}
def a(state): return {"log": ["a"]}
def b(state): return {"log": ["b"]}
def join(state): return {"log": ["join"]}

def route(state):
    return ["a", "b"]          # 리스트를 돌려주면 두 노드가 같은 슈퍼스텝에서 실행된다

g = StateGraph(S)
g.add_node("classify", classify)
g.add_node("a", a); g.add_node("b", b); g.add_node("join", join)
g.add_edge(START, "classify")
g.add_conditional_edges("classify", route, {"a": "a", "b": "b"})
g.add_edge("a", "join"); g.add_edge("b", "join")
g.add_edge("join", END)

app = g.compile()
app.invoke({"log": []})
# {'log': ['classify', 'a', 'b', 'join']}
```

### 4-2. GraphState와 리듀서

```python
# code/step07_langgraph/docagent/graph/state.py (발췌)
def merge_hits(left: list[dict], right: list[dict]) -> list[dict]:
    by_pk = {h["pk"]: h for h in left}
    for hit in right:
        prev = by_pk.get(hit["pk"])
        if prev is None or hit.get("score", 0.0) > prev.get("score", 0.0):
            by_pk[hit["pk"]] = hit
    return sorted(by_pk.values(), key=lambda h: h.get("score", 0.0), reverse=True)

class GraphState(TypedDict):
    question: str
    category: str                              # "factual" | "chitchat"
    category_source: str                        # "model" | "heuristic"
    current_query: str
    retrieved: Annotated[list[dict], merge_hits]
    queries_tried: Annotated[list[str], operator.add]
    retrieval_round: int
    sufficient: bool
    insufficiency_reason: str
    verify_source: str
    answer_text: str
    citations: list[dict]
    dropped_citations: list[str]
    trace: Annotated[list[dict], operator.add]  # {"stage", "message"} — PROJECT-SPEC.md 4장과 같은 모양
```

`trace`는 이 그래프가 SSE `status` 이벤트로 그대로 옮기는 실행 로그다(4-6절).
`stage` 값은 PROJECT-SPEC.md 4장이 고정한 다섯 값을 그대로 쓴다 —
`classify`는 `planning`, `retrieve_dense`/`retrieve_sparse`/`expand_query`는
`retrieving`, `verify`(근거가 충분한지 판단)는 `analyzing`, `answer`의
인용 검증 단계는 `verifying`, 마지막 텍스트 전달은 `writing`이다.

### 4-3. classify와 verify: 모델 판단 + 휴리스틱 대체

```python
# code/step07_langgraph/docagent/graph/nodes.py (발췌)
def make_verify_node(deps: NodeDeps) -> Callable[[dict], dict]:
    schema = {
        "type": "object",
        "properties": {"sufficient": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["sufficient", "reason"],
    }

    def verify(state: dict) -> dict:
        retrieved = state.get("retrieved", [])
        sufficient, source = None, "heuristic"
        try:
            result = deps.json_chat(
                [...], schema_name="verify_evidence", json_schema=schema,
            )
            if isinstance(result.get("sufficient"), bool):
                sufficient, source = result["sufficient"], "model"
        except Exception:
            sufficient = None

        if sufficient is None:
            distinct = {h["pk"] for h in retrieved}
            sufficient = len(distinct) >= deps.min_sufficient_hits
            # ... reason 문자열 구성 ...

        return {"sufficient": sufficient, "verify_source": source,
                "trace": [_trace("analyzing", ...)]}
    return verify
```

`NodeDeps`는 이 그래프가 쓰는 외부 호출(검색 두 개, 채팅 호출 두 개)을 한
곳에 모은 의존성 컨테이너다. 실제 서비스는 `docagent.rag.search`(Milvus)와
`docagent.llm`(모델 서버)을 주입하고, 테스트와 이 장의 데모는
`docagent.rag.fake_retriever.FakeRetriever`(4-7절)를 주입한다 — 노드
함수는 어느 쪽이 들어와도 똑같이 동작한다.

### 4-4. retrieve_dense / retrieve_sparse: 병렬 노드 + 노드 안 재시도

```python
# code/step07_langgraph/docagent/graph/nodes.py (발췌)
def _search_with_retry(search_fn, query, *, limit, max_attempts=3, initial_delay=0.2):
    delay = initial_delay
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            return search_fn(query, limit=limit), None
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                time.sleep(delay); delay *= 2
    return [], last_exc

def make_retrieve_dense_node(deps: NodeDeps):
    def retrieve_dense(state: dict) -> dict:
        query = state["current_query"]
        hits, exc = _search_with_retry(deps.dense_search, query, limit=deps.top_k)
        if exc is not None:
            return {"retrieved": [], "trace": [_trace("retrieving", f"dense 검색이 재시도 후에도 실패: {exc}")]}
        return {"retrieved": hits, "queries_tried": [f"dense:{query}"],
                "trace": [_trace("retrieving", f"dense 검색으로 {len(hits)}건을 찾았다")]}
    return retrieve_dense
```

`retrieve_sparse`는 대칭이다. 이 두 노드에 LangGraph의 `retry_policy`/
`error_handler`를 쓰지 않고 노드 안에서 직접 처리하는 이유는 2-6절에서
설명한, 병렬 형제 노드와 `error_handler`를 함께 쓸 때 실제로 재현한 문제
때문이다.

### 4-5. expand_query: 규칙 기반 질의 확장

```python
# code/step07_langgraph/docagent/graph/nodes.py (발췌)
_TOKEN_RE = re.compile(r"[\w가-힣]+")

def expand_query(state: dict) -> dict:
    tokens = _TOKEN_RE.findall(state["question"])
    core = [t for t in tokens if len(t) >= 2][:4]
    broadened = " ".join(core) if core else state["question"]
    next_round = state["retrieval_round"] + 1
    return {"current_query": broadened, "retrieval_round": next_round,
            "trace": [_trace("retrieving", f"근거가 부족해 질의를 넓힌다 ({next_round}차 재검색): '{broadened}'")]}
```

실무에서는 이 자리에 모델 호출(질의 재작성)을 넣을 수도 있다. 이 장은
그래프의 반복·분기 구조를 보이는 것이 목적이라 규칙 기반으로 남겨 둔다
(WRITING-GUIDE.md 4.6, 필요 이상으로 구성을 늘리지 않는다).

### 4-6. answer: 5단계 인용 검증 재사용 + LangGraph 재시도

```python
# code/step07_langgraph/docagent/graph/nodes.py (발췌)
def make_answer_node(deps: NodeDeps):
    def answer(state: dict) -> dict:
        retrieved = state.get("retrieved", [])[: deps.top_k]
        sources = citations.build_retrieved_sources(retrieved, app_base_url=deps.app_base_url)
        if not sources:
            return {"answer_text": "이 질문에 답할 근거를 문서에서 찾지 못했다.", ...}

        context_block = "\n\n".join(f"[{s.label}] (문서: {s.doc_title})\n{s.text}" for s in sources)
        result_chat = deps.chat([...], temperature=0.2)
        result = citations.validate_and_link_citations(result_chat.text, sources, app_base_url=deps.app_base_url)
        return {"answer_text": result.text, "citations": result.sources,
                "trace": [_trace("verifying", ...), _trace("writing", ...)]}
    return answer
```

`citations.build_retrieved_sources`/`validate_and_link_citations`은
5단계 모듈(`code/step05_hybrid_rag/docagent/rag/citations.py`)을 그대로
가져온 것이다 — "모델은 `[S1]` 번호만 쓰고, 애플리케이션이 이번 검색에서
실제로 반환된 청크와 대조해서만 URL을 만든다"는 규칙(PROJECT-SPEC.md 5장)이
그래프 노드 안에서도 그대로 적용된다.

```python
# code/step07_langgraph/docagent/graph/build.py (발췌)
_LLM_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.3, backoff_factor=2.0,
                          retry_on=lambda exc: isinstance(exc, LLMError))

graph.add_node("answer", make_answer_node(deps), retry_policy=_LLM_RETRY)
graph.add_node("chitchat_answer", make_chitchat_answer_node(deps), retry_policy=_LLM_RETRY)
```

### 4-7. Milvus 없이 그래프를 실행하기 위한 FakeRetriever

```python
# code/step07_langgraph/docagent/rag/fake_retriever.py (발췌)
class FakeRetriever:
    """dense_search/sparse_search와 같은 시그니처. 임베딩도 BM25도 계산하지
    않는다 — 단순 토큰 겹침 점수로 "그럴듯한 순위"만 만든다. 검색 품질을
    대표하지 않는다. 그래프의 상태 전이를 Milvus 없이 확인하는 용도다."""

    def dense_search(self, query, *, limit=5, filter_expr=""):
        # 질의·청크 토큰 집합의 자카드 유사도
        ...

    def sparse_search(self, query, *, limit=5, filter_expr=""):
        # 질의 토큰이 청크에 등장하는 빈도(BM25를 흉내낸 단순 카운트)
        ...
```

`NodeDeps(dense_search=retriever.dense_search, sparse_search=retriever.sparse_search, ...)`로
주입하면 `docagent.graph.build.build_graph`가 만드는 그래프는 Milvus 없이도
전부 동작한다 — 5절에서 이 조합으로 실제 실행한 로그를 본다.

### 4-8. 그래프 조립과 체크포인터 연결

```python
# code/step07_langgraph/docagent/graph/build.py (발췌)
def build_graph(deps: NodeDeps, *, checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    graph = StateGraph(GraphState)
    graph.add_node("classify", make_classify_node(deps))
    graph.add_node("retrieve_dense", make_retrieve_dense_node(deps))
    graph.add_node("retrieve_sparse", make_retrieve_sparse_node(deps))
    graph.add_node("verify", make_verify_node(deps))
    graph.add_node("expand_query", expand_query)
    graph.add_node("answer", make_answer_node(deps), retry_policy=_LLM_RETRY)
    graph.add_node("chitchat_answer", make_chitchat_answer_node(deps), retry_policy=_LLM_RETRY)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", route_after_classify, {
        "chitchat_answer": "chitchat_answer",
        "retrieve_dense": "retrieve_dense", "retrieve_sparse": "retrieve_sparse",
    })
    graph.add_edge("retrieve_dense", "verify")
    graph.add_edge("retrieve_sparse", "verify")
    graph.add_conditional_edges("verify", make_route_after_verify(deps),
                                 {"answer": "answer", "expand_query": "expand_query"})
    graph.add_edge("expand_query", "retrieve_dense")
    graph.add_edge("expand_query", "retrieve_sparse")
    graph.add_edge("answer", END)
    graph.add_edge("chitchat_answer", END)
    return graph.compile(checkpointer=checkpointer)
```

`checkpointer=None`이면 매 호출이 독립적으로 처음부터 실행된다(단순
테스트용). 실제 재개를 쓰려면 `SqliteSaver`/`AsyncSqliteSaver`를 연다.

```python
# code/step07_langgraph/scripts/demo_checkpoint_run1.py (발췌)
with SqliteSaver.from_conn_string(settings.checkpoint_db_path) as saver:
    app = build_graph(deps, checkpointer=saver)
    config = {"configurable": {"thread_id": THREAD_ID}}
    app.invoke({"question": QUESTION, "retrieved": [], "queries_tried": [], "trace": []}, config=config)
```

```python
# code/step07_langgraph/scripts/demo_checkpoint_run2.py (발췌) — 별도 프로세스
with SqliteSaver.from_conn_string(settings.checkpoint_db_path) as saver:
    app = build_graph(deps, checkpointer=saver)
    config = {"configurable": {"thread_id": THREAD_ID}}   # run1과 같은 thread_id
    result = app.invoke(None, config=config)               # None -> 재개
```

### 4-9. FastAPI: astream으로 노드별 진행을 SSE로 옮기기

```python
# code/step07_langgraph/docagent/app.py (발췌)
async def _chat_stream(req: ChatRequest, graph) -> AsyncIterator[str]:
    thread_id = req.session_id or uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {"question": req.message, "retrieved": [], "queries_tried": [], "trace": []}

    async for update in graph.astream(initial_state, config=config, stream_mode="updates"):
        for _node_name, partial in update.items():
            for item in partial.get("trace", []):
                yield status_event(item["stage"], item["message"])

    snapshot = await graph.aget_state(config)
    final_state = snapshot.values
    for piece in _chunk_for_stream(final_state.get("answer_text", "")):
        yield token_event(piece)
    yield sources_event(final_state.get("citations", []))
    yield done_event("stop")
```

`stream_mode="updates"`는 노드가 하나 끝날 때마다 `{노드 이름: 부분 상태}`를
내보낸다. 이 장은 각 노드가 채운 `trace` 항목을 그대로 `status` 이벤트로
옮긴다 — 5단계는 코드에서 손으로 다섯 단계를 순서대로 `yield`했지만, 이
장은 **그래프가 실제로 실행한 노드 순서 그대로** 이벤트가 나온다(근거가
부족해 재검색이 일어나면 `retrieving`/`analyzing`이 실제로 여러 번
반복해서 도착한다). `session_id`를 요청에 실으면 같은 `thread_id`로
이어지므로, 클라이언트가 원하면 끊긴 연결을 같은 `session_id`로 재요청해
체크포인트에서 재개할 수도 있다.

## 5. 실행과 결과 확인

### 5-1. 분류·병렬 검색·근거 확인·답변 흐름

```bash
python code/_tools/fake_openai_server.py --port 8131 &
export OPENAI_BASE_URL=http://127.0.0.1:8131/v1 OPENAI_API_KEY=sk-test \
       CHAT_MODEL=stub-model EMBEDDING_MODEL=stub-embedding
cd code/step07_langgraph
python -m pytest tests/ -v
```

`tests/test_graph_flow.py`에서 실제로 확인한 것:

- `안녕!`처럼 짧은 인사는 `category="chitchat"`으로 분류되어 검색 없이
  바로 답한다(검색 함수가 호출되면 테스트가 실패하도록 만들어 확인했다).
- 서로 다른 청크가 2건 이상 모이면(`min_sufficient_hits=2`)
  `retrieval_round=0`인 채로 바로 답한다 — 재검색이 없었다는 뜻이다.
- 검색 결과가 항상 1건만 나오는 질문은 `retrieval_round`가
  `max_retrieval_rounds`(2)까지 올라간 뒤에야 (여전히 근거가 부족한 채로)
  답변으로 넘어간다 — 무한히 반복하지 않는다.
- sparse 검색이 항상 실패해도(`LLMError`를 강제로 발생시켰다) dense 결과만
  으로 그래프가 끝까지 실행되고 `trace`에 "실패해 이번 라운드는 건너뛴다"가
  남는다.

### 5-2. 체크포인트 재개를 실제 프로세스 두 개로 확인

```bash
cd code/step07_langgraph
rm -f data/checkpoints.sqlite
python scripts/demo_checkpoint_run1.py
```

```
[run1] 질문: '2분기 노트북 매출이 둔화된 이유는?'
[run1] 예외로 중단됨(=모델 서버 장애로 프로세스가 죽었다고 가정): LLMError('모델 서버에 연결할 수 없다 (데모용 강제 실패)')
[run1] 이미 끝난 trace 항목 수: 4
    {'stage': 'planning', ...}
    {'stage': 'retrieving', ...dense...}
    {'stage': 'retrieving', ...sparse...}
    {'stage': 'analyzing', ...충분...}
[run1] 다음에 실행할 노드: ('answer',)
```

```bash
python scripts/demo_checkpoint_run2.py
```

```
[run2] 재개 전 상태: 다음에 실행할 노드 = ('answer',)
[run2] 재개 전까지 쌓인 trace 항목 수: 4
[run2] 재개 완료. 최종 답변: 요청하신 내용을 확인했습니다.
[run2] 재개 후 다음 노드 (완료됐으면 빈 튜플): ()
```

확인할 것: `run2`는 완전히 새로운 파이썬 프로세스인데도 `trace` 4개
항목(`planning`, `retrieving`×2, `analyzing`)이 **그대로** 남아 있다 —
`classify`/`retrieve_dense`/`retrieve_sparse`/`verify`가 다시 실행되지
않았다는 뜻이다. `run2`는 `answer` 노드만 실행해 최종 답변을 만든다.

### 5-3. FastAPI에서 노드별 진행 확인

```bash
uvicorn docagent.app:app --reload --port 8080
```

`http://localhost:8080`에서 질문을 입력하면 실행 로그(`#trace`)에
`status` 이벤트가 노드가 끝날 때마다 하나씩 쌓인다. 개발자 도구 네트워크
탭에서 `/chat` 응답의 `event: status` 줄이 실제로 여러 번 오는 것과, 근거가
부족한 질문(예: 코퍼스에 없는 제품)을 물으면 `retrieving`/`analyzing`이
반복해서 찍히는 것을 확인한다.

## 6. 실패 상황 실습

**모델 서버가 응답 도중 죽는 경우 (체크포인트 재개로 복구).** 5-2절
그대로다 — `DOCAGENT_FORCE_CRASH=1`로 `answer` 노드의 모델 호출이 항상
실패하게 만들면 `_LLM_RETRY`가 3번 재시도하고도 실패해 그래프 실행이
예외로 끝난다. 체크포인트에는 `answer` 이전까지의 상태가 남는다. 이
플래그를 끄고 새 프로세스로 재개하면 `answer`부터 이어서 성공한다.

**검색 소스 하나가 계속 실패하는 경우 (성능 저하만 감수하고 계속 진행).**
`retrieve_sparse`에 항상 예외를 던지는 함수를 주입해 보면
(`tests/test_graph_flow.py::RetrievalDegradationTest`), 3번 재시도 후
`retrieved`가 빈 목록으로 남고 `trace`에 "sparse 검색이 재시도 후에도
실패해 이번 라운드는 건너뛴다"가 기록된다. `retrieve_dense`가 결과를
가져왔다면 그래프는 계속 진행해 답변을 만든다 — 한쪽 검색 소스 장애가
전체 요청을 실패시키지 않는다.

**병렬 형제 노드 + `error_handler`를 함께 쓰면 실제로 깨지는 경우 (재현
기록).** 이 장을 준비하며 실제로 재현했다: 같은 슈퍼스텝에 형제 노드가
있는 상태에서 `retry_policy`+`error_handler`를 쓰면, 핸들러가 정상
호출되고 `Command`도 만드는데도 그래프 실행 자체는 실패한다.

```python
# 재현: a와 b가 같은 슈퍼스텝에서 병렬 실행되고 b에 error_handler가 있다
def handler(state, error: NodeError) -> Command:
    print("handler 호출됨:", error.node, error.error)   # <- 실제로 호출된다
    return Command(update={"log": ["b-handled"]})
...
# handler 호출됨: b 항상 실패
# RuntimeError: 항상 실패        <- 그런데도 예외가 다시 올라와 invoke()가 실패한다
```

같은 `error_handler`를 형제 노드가 없는 단독 노드에 붙이면 정상 동작한다.
그래서 이 장은 `retrieve_dense`/`retrieve_sparse`(형제가 있는 노드)에는
`error_handler`를 쓰지 않고 노드 함수 안에서 직접 처리하며(4-4절),
`answer`/`chitchat_answer`(형제가 없는 노드)에만 `retry_policy`를 쓴다.
이 문제는 langgraph 1.2.11을 대상으로 실제로 재현한 것이고, 다른 버전에서
고쳐졌는지는 `[확인 필요: langgraph 이후 릴리스의 변경 이력]`이다.

**근거가 끝내 부족한 채로 반복 상한에 도달하는 경우.** 코퍼스에 없는
내용을 물으면(`tests/test_graph_flow.py::test_insufficient_evidence_expands_query_then_stops_at_round_limit`)
`retrieval_round`가 `max_retrieval_rounds`까지 오른 뒤 `sufficient=False`인
채로 `answer`로 넘어간다. `answer` 노드는 `citations.build_retrieved_sources`가
빈 목록이면 "이 질문에 답할 근거를 문서에서 찾지 못했다"를 그대로 답하고
모델을 호출하지 않는다 — 근거가 하나도 없는데 모델에게 억지로 답을 만들게
하지 않는다(4단계부터 이어지는 원칙).

## 7. 응용 과제

1. **재검색 상한을 늘려 비교하기**: `NodeDeps(max_retrieval_rounds=...)`를
   1과 4로 바꿔 가며 5-1절의 "코퍼스에 없는 질문"을 실행하고, `trace`에
   찍히는 `retrieving`/`analyzing` 횟수가 어떻게 달라지는지 비교한다.
   완료 기준: 상한을 늘리면 재검색 횟수가 늘어나되 여전히 상한에서 멈춘다는
   것을 로그로 보인다.
2. **verify에 진짜 모델 판단을 반영하기**: `code/_tools/fake_openai_server.py`
   대신 `response_format=json_schema`를 스키마대로 채워 돌려주는 서버(또는
   그런 서버를 흉내내는 개선된 스텁)를 붙이고, `verify_source`가
   `"heuristic"`에서 `"model"`로 바뀌는 것을 확인한다. 완료 기준: 두 값이
   실제로 다른 경로(모델 응답 파싱 성공 vs 실패)에서 나온다는 것을 코드
   흐름으로 설명할 수 있다.
3. **체크포인트를 다른 지점에서 끊어 재개하기**: `demo_checkpoint_run1.py`를
   복사해 `retrieve_sparse` 안에서 강제로 예외를 던지도록 바꾸고(예:
   `NodeDeps.sparse_search`를 항상 실패하는 함수로), 재개했을 때
   `retrieve_dense`는 다시 실행되지 않고 `retrieve_sparse`부터(또는 그
   결과를 받는 `verify`부터) 이어지는 것을 trace로 확인한다. 완료 기준:
   run1과 run2의 trace를 합쳐 봤을 때 `retrieve_dense`의 실행 흔적이 한
   번만 있다.

## 8. 핵심 정리와 확인 질문

- LangGraph는 State(공유 데이터)·Node(작업 함수)·Edge(다음 노드를 정하는
  연결)로 실행 순서를 명시적인 그래프로 표현한다. 반복은 그래프를 되돌아가는
  간선일 뿐이고, 멈추는 조건은 개발자가 직접 관리해야 한다.
- 이 장의 그래프는 "무엇을 할지"는 고정하고 "어느 갈림길로 갈지"만 모델의
  구조화된 판단에 맡긴다 — 모델 응답이 기대한 형태가 아니면 규칙 기반
  휴리스틱으로 대체해, 모델 서버가 구조화된 출력을 지원하지 않아도 그래프
  전체가 멈추지 않는다.
- 조건부 간선이 여러 노드 이름을 반환하면 병렬 실행(같은 슈퍼스텝)이 되고,
  병렬 노드가 같은 상태 채널에 쓸 때는 커스텀 리듀서로 충돌을 해소해야 한다.
- 체크포인터는 노드(태스크) 단위로 상태를 저장한다 — 병렬로 실행된 노드
  중 일부만 성공해도 그 성공은 보존되고, 실패한 노드만 다시 실행하면 된다.
  이 장은 이것을 실제 프로세스 두 개로 확인했다.
- 같은 "노드 실패"에도 노드의 성격에 따라 다른 전략(내부 대체, 내부 재시도
  후 성능 저하 감수, 프레임워크 재시도 후 실패 전파)을 쓸 수 있고, 이
  전략은 프레임워크 기능의 실제 한계(2-6절, 6절의 재현 기록)에 따라 달라질
  수 있다.
- FastAPI는 요청 수신과 SSE 스트리밍, LangGraph는 상태·분기·반복·재시도를
  맡는다. 작업 큐는 지금 이 프로젝트의 실행 시간·동시성 규모에서는 아직
  필요하지 않다.

확인 질문:
1. `retrieve_dense`와 `retrieve_sparse`가 "같은 슈퍼스텝에서 실행된다"는
   말이 정확히 무엇을 뜻하는지, 그리고 왜 `retrieved` 필드에 커스텀
   리듀서가 필요한지 설명할 수 있는가?
2. 체크포인터가 슈퍼스텝이 아니라 태스크(노드) 단위로 저장된다는 것을,
   병렬 노드 중 하나만 실패하는 상황을 예로 들어 설명할 수 있는가?
3. `classify`/`verify`와 `answer`/`chitchat_answer`가 "모델 호출 실패"에
   서로 다른 전략을 쓰는 이유를 설명할 수 있는가?
4. 이 프로젝트가 아직 작업 큐를 두지 않는 이유와, 어떤 조건이 되면 큐가
   필요해질지 설명할 수 있는가?

이 장의 완성 코드는 `code/step07_langgraph/`에 있다. 다음 장(8단계)은 이
그래프에 사람의 승인을 기다리는 지점과 무한루프 차단을 더한다 — 이 장의
체크포인트·재개 메커니즘이 8단계의 "승인 대기 중 실행을 멈췄다가 승인 후
재개한다"의 기반이 된다.

## 참고 문서

- <https://pypi.org/project/langgraph/> — 이 문서 작성 시점(2026-09-09)
  최신 배포 버전 1.2.11, 2026-08-11 릴리스를 실측 확인했다.
- <https://pypi.org/project/langgraph-checkpoint-sqlite/> — 같은 날짜 기준
  최신 배포 버전 3.1.1(2026-07-30 릴리스)을 실측 확인했다.
- langchain-ai/langgraph 저장소(`main` 브랜치, 설치된 1.2.11과 소스가
  일치하는 것을 직접 대조해 확인했다):
  - <https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/langgraph/langgraph/errors.py> —
    `NodeError`(`error_handler`에 주입되는 실패 컨텍스트) 정의
  - <https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/langgraph/langgraph/types.py> —
    `RetryPolicy`, `Command`, `Send` 정의
  - <https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/langgraph/langgraph/graph/state.py> —
    `StateGraph.add_node`(`retry_policy`, `error_handler` 파라미터),
    `add_conditional_edges` 정의
  - <https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/aio.py> —
    `AsyncSqliteSaver.from_conn_string`
  - 공식 문서 사이트(`langchain-ai.github.io`)는 이 작업 환경의 네트워크
    정책으로 직접 접속이 막혀 있어(`EGRESS_BLOCKED`) 원문을 확인하지
    못했다 — 위 GitHub 소스 코드로 대신 확인했다.
- VERIFIED-FINDINGS.md, PROJECT-SPEC.md — `docagent.llm`의 `chat`/
  `chat_stream`/`chat_json`/`embed` 공통 인터페이스와 openai 3.10.0 API는
  5단계에서 이미 확인된 내용을 그대로 가져다 썼다(이 장에서 새로 바뀐
  것이 없다).

> 검증 상태: 이 장의 모든 `.py` 파일은 `python3 -m py_compile`로 문법을
> 확인했다. `code/step07_langgraph/tests/`(25개 테스트, `test_graph_flow.py`의
> 분류 분기·재검색 루프와 그 상한·검색 소스 하나의 실패·체크포인트
> 재개 4가지를 포함)를 실제로 실행해 모두 통과하는 것을 확인했다 —
> 외부 서버가 필요 없다. `code/_tools/fake_openai_server.py --port 8131`을
> 띄우고 `docagent.rag.fake_retriever.FakeRetriever`를 주입해 그래프
> 전체(분류→병렬 검색→근거 확인→답변, 근거 부족 시 재검색 루프, 검색
> 소스 하나의 실패 시 대체 동작)를 실제로 실행했고, `scripts/demo_checkpoint_run1.py`
> → `demo_checkpoint_run2.py`를 **완전히 별도의 두 파이썬 프로세스**로
> 실행해 체크포인트 재개(이미 끝난 노드가 다시 실행되지 않는 것)를
> 실제로 확인했다. `docagent/app.py`는 `fastapi.testclient.TestClient`로
> 띄워 `/chat`의 SSE 스트림(여러 `status` 이벤트 도착 순서 포함)과
> `/sources/{doc_id}`, `/`(정적 페이지) 세 라우트의 실제 응답을 확인했다.
> 2-6절과 6절에 적은 langgraph 1.2.11의 "병렬 형제 노드 + error_handler"
> 문제는 별도 최소 재현 스크립트로 직접 재현했다(위 코드 블록의 출력이 그
> 실행 결과다). **Milvus 서버를 대상으로 한 실제 실행(컬렉션 생성·적재·
> 검색)은 이 장에서도 검증하지 않았다** — 5단계와 같은 한계이고,
> `docagent/rag/{store,search,ingest,citations}.py`는 5단계에서 검증된
> 코드를 그대로 가져왔다. 실제 모델 서버의 `response_format=json_schema`가
> 스키마를 실제로 채워 돌려주는지(`classify`/`verify`가 `"model"` 경로를
> 타는지)는 학습용 스텁 서버의 특성상 이 장에서 확인할 수 없었다 — 2-3절에
> 그 이유를 적었다.
