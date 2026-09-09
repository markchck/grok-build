# 12단계. Deep Agents와 Skills

## 1. 학습 목표와 완성 모습

11단계까지는 "누가 무엇을 하는가"의 구도가 명확했다. 7단계는 이 저장소의
집필자가 그래프의 마디와 간선을 코드로 고정했고, 모델은 그중 일부 분기의
방향만 도왔다. 이 장은 그 구도를 뒤집는다 — **작업을 몇 단계로 나눌지,
어떤 하위 작업을 만들지, 그중 무엇을 스스로 하고 무엇을 다른 에이전트에게
맡길지를 모델이 도구 호출로 직접 정한다.** 이 저장소의 집필자는 그 결정에
쓸 수 있는 도구 집합(계획 관리, 파일 읽기/쓰기, 하위 에이전트 호출, 스킬
목록)만 준비한다.

이 구도가 성립하려면 세 가지가 더 필요하다.

1. 모델이 세운 계획이 실행 도중 바뀔 수 있어야 하고, 그 변화가 사용자에게
   보여야 한다(Todo List).
2. 검색·집계 결과 전체를 대화창에 계속 들고 있으면 컨텍스트가 금방
   찬다 — 중간 결과를 파일에 두고 필요한 부분만 다시 읽어야 한다.
3. "이런 종류의 작업은 이렇게 처리한다"는 절차·참고 자료·실행 스크립트를
   매번 시스템 프롬프트에 다 적어 넣지 않고, 필요할 때만 불러 쓸 수
   있어야 한다(Skill).

이 세 가지를 한데 묶어 실행 가능한 형태로 제공하는 것이 **Deep Agents**다.
이 장은 LangChain 진영이 만든 `deepagents` 패키지(이 문서 작성 시점
2026-09-09 기준 최신 0.7.13, 실제 설치해 확인 — 3절)를 그대로 쓴다. 별도
서버나 신규 개념을 발명하지 않는다.

완성 모습은 이렇다. 사용자가 다음처럼 요청한다.

```
사용자: 제품별 매출 변화를 분석하고 원인을 문서에서 찾아줘
```

에이전트는 먼저 계획을 세운다.

```
[todo] ☐ csv-analyst에게 매출 집계 위임
       ☐ doc-researcher에게 원인 조사 위임
       ☐ 결과 종합해 답변 작성
```

`csv-analysis` 스킬을 아는 **csv-analyst** 하위 에이전트에게 집계를
맡긴다. 그 결과("노트북 매출이 2분기에 4.1% 줄었다")를 본 메인 에이전트는
**처음 계획에 없던 항목을 추가**한다.

```
[todo] ☑ csv-analyst에게 매출 집계 위임
       ▶ doc-researcher에게 원인 조사 위임
       ☐ 노트북 2분기 감소 원인 문서에서 확인   <- 실행 중 새로 생긴 항목
       ☐ 결과 종합해 답변 작성
```

`doc-research` 스킬을 아는 **doc-researcher** 하위 에이전트가 원인 문장을
찾아 파일로 저장하면, 메인 에이전트는 그 파일과 앞서 csv-analyst가 저장한
파일을 **다시 읽어서**(재계산하지 않고) 최종 답을 만든다.

```
[todo] ☑ ☑ ☑ ☑ (전부 완료)

노트북 매출은 1분기 대비 2분기에 약 4.1% 줄었다(results/csv_summary.md).
notebook-q1-report#p1에 따르면 부산 지역 물류 센터 이전으로 배송이
지연되면서 일부 대량 구매 고객이 다음 분기 주문으로 미룬 것이 주요
원인이다.
```

이 장에서 실제로 실행해 확인한 로그 전문은 5절에 있다. 앞 단계 연결:
7단계(`code/step07_langgraph/`)의 State/Node/Edge, 8단계
(`code/step08_hitl/`)의 안전장치(반복 감지·예산), 11단계의 하위 에이전트
위임·오케스트레이션 개념을 이 장이 이어받는다. Todo 이벤트의 스키마는
PROJECT-SPEC.md 4절에서 2단계 때부터 이미 정의돼 있었지만, 실제로 값을
채워 내보내는 것은 이 장이 처음이다.

## 2. 핵심 개념

### 2-1. LangChain · LangGraph · Deep Agents — 위아래가 아니라 각자 다른 문제

세 이름을 "LangChain 위에 LangGraph, 그 위에 Deep Agents"처럼 층으로
그리면 오해가 생긴다. 셋은 **없으면 무엇이 불편한지**로 구분하는 편이
정확하다.

| | 없으면 생기는 불편 | 실제로 제공하는 것 |
| --- | --- | --- |
| **LangChain** | 모델·프롬프트·도구·Retriever마다 서로 다른 인터페이스를 익히고, 그걸 잇는 배관 코드를 매번 새로 짠다(6단계에서 직접 겪었다) | 메시지·모델·도구·미들웨어의 공통 인터페이스(`BaseChatModel`, `@tool`, `create_agent`) |
| **LangGraph** | "다음에 무슨 일이 벌어질지"가 하나의 while 루프 안에 암묵적으로만 있어서, 특정 지점에서 멈추고 나중에 정확히 그 지점부터 다시 시작하기 어렵다(8단계가 직접 SQLite로 이 문제를 풀었다) | 상태를 마디 사이에서 명시적으로 주고받는 그래프 실행기. 체크포인트, 재개, 병렬 실행, 서브그래프 |
| **Deep Agents** | 매번 "계획 관리 도구, 파일 읽기/쓰기 도구, 하위 에이전트 호출 도구, 스킬 로딩"을 그래프 마디로 손수 설계해야 한다(11단계에서 위임을 다룰 때 이미 이 수고를 일부 겪었다) | 그 네 가지를 이미 조립해 둔 **완성된 LangGraph 그래프**를 함수 호출 한 번으로 얻는다 |

즉 `deepagents.create_deep_agent()`가 돌려주는 것은 **LangGraph로 만든
`CompiledStateGraph`**다(`deepagents` 0.7.13 소스, `deepagents/graph.py` —
`langchain.agents.create_agent`를 내부에서 호출하고 그 결과는 다시
LangGraph 그래프다). 7단계에서 `StateGraph()`에 `add_node`/`add_edge`를
직접 불러 그래프를 조립했던 것과, 이 장에서 `create_deep_agent()`를 부르는
것은 **결과물의 종류가 같다.** 다른 것은 누가 마디와 간선을 정하느냐다.

- 7단계: 집필자가 `classify → retrieve → verify → answer`라는 마디를
  코드로 고정했다. 그중 "verify 다음에 answer로 갈지 expand_query로
  갈지"만 모델의 판단을 빌렸다.
- 이 장: 마디는 훨씬 단순하다(모델 노드 + 도구 실행 노드가 반복되는
  ReAct 형태에 가깝다). 대신 "이번 요청에 하위 작업이 몇 개 필요한가",
  "그 작업을 스스로 할지 위임할지", "계획을 어떻게 바꿀지"를 **모델이
  도구 호출로 직접 결정**한다. Deep Agents가 미리 준비해 붙여주는 것은 그
  결정에 쓸 수 있는 도구 집합이지, 흐름 자체가 아니다.

그래서 "Deep Agents가 LangGraph보다 상위 개념"이라는 설명은 정확하지
않다 — Deep Agents는 LangGraph *위에서* 동작하는 게 아니라 LangGraph *로*
만들어진, 미리 조립된 한 가지 그래프 구성이다. 반대로 "LangGraph가 있으니
Deep Agents는 그저 프리셋일 뿐"이라고 낮잡는 것도 정확하지 않다 — 계획
관리·파일 백엔드·스킬 로딩·하위 에이전트 위임을 서로 어긋나지 않게 맞물려
동작시키는 것(예: 하위 에이전트가 같은 파일시스템 백엔드를 공유하면서도
독립된 대화 컨텍스트를 갖는 것)은 그 자체로 적지 않은 설계 작업이고,
`deepagents`가 이미 검증해 둔 부분이다.

### 2-2. 이 장이 실제로 확인한 `deepagents`의 구성 요소

`pip install deepagents`로 실제 설치해 소스를 직접 읽어 확인했다(4-1절에
설치 로그가 있다). `deepagents.create_deep_agent()`는 아래 미들웨어를
조합해 그래프를 만든다(`deepagents/graph.py`, 0.7.13).

| 미들웨어 | 무엇을 더하는가 | 이 장에서 씀? |
| --- | --- | --- |
| `FilesystemMiddleware` | `ls`/`read_file`/`write_file`/`edit_file`/`delete`/`glob`/`grep`/`execute` 도구 | 항상 포함(기본) |
| `SubAgentMiddleware` | `task` 도구 — 이름으로 지정한 하위 에이전트에게 작업을 위임 | 항상 포함(기본) |
| `SkillsMiddleware` | 스킬 목록을 시스템 프롬프트에 짧게 얹고, 본문은 `read_file`로 필요할 때만 읽게 함 | `skills=[...]`를 줄 때만 포함 |
| `TodoListMiddleware` | `write_todos` 도구, `state["todos"]` | **기본에 없다 — 명시적으로 추가해야 한다** |

마지막 줄이 이 장에서 실제로 부딪힌 점이다. `deepagents` 0.7.13의 모듈
docstring은 "planning, filesystem, subagent, summarization middleware"라고
설명하지만, `graph.py`가 실제로 import하는 목록에는 `TodoListMiddleware`가
없다(`langchain.agents.middleware.todo` 모듈 자체를 import하지 않는다 —
`grep -n "Todo" deepagents/graph.py`로 직접 확인했다, 결과 없음). `write_todos`
도구 없이 `create_deep_agent()`를 그대로 부르면, 모델이 `write_todos`를
부르려 해도 도구 목록에 없어 오류가 난다(4-3절에서 실제로 재현했다). 이
장의 `docagent/deep_agent.py`는 그래서 `middleware=[TodoListMiddleware()]`를
**명시적으로** 얹는다(`langchain.agents.middleware`에서 가져온다 — 계획
관리는 `deepagents` 고유 기능이 아니라 `langchain` 1.4.0의 일반 에이전트
미들웨어다).

### 2-3. Skill의 구성 — 지침 · 참조 자료 · 스크립트

Skill은 **디렉터리 하나**다. 그 디렉터리에는 반드시 `SKILL.md` 파일이
있고, YAML 프런트매터로 `name`과 `description`을 선언한다.

```
skills/
├── csv-analysis/
│   ├── SKILL.md          # 지침: 언제, 어떤 순서로 쓰는지
│   └── analyze_sales.py  # 스크립트: 실제 집계를 실행하는 코드
└── doc-research/
    ├── SKILL.md
    └── find_evidence.py
```

`SKILL.md`는 "지침"이고, `analyze_sales.py`/`find_evidence.py`는
"스크립트"다. 이 장의 두 스킬에는 별도 "참조 자료" 파일(예: 도메인 용어
설명 `.md`, 스키마 문서)을 따로 두지 않았지만, 자리는 같다 — `SKILL.md`
옆에 파일을 더 놓고 `SKILL.md` 본문에서 그 경로를 언급하면 된다. 세
요소의 역할이 다르다.

- **지침**(`SKILL.md`): "언제 이 스킬을 쓰는가", "어떤 순서로 도구를
  부르는가". 모델이 읽고 **따라야** 할 절차다.
- **참조 자료**: 매번 다시 설명할 필요 없는 배경 지식(내부 컬럼 이름
  규칙, 사내 약어, 예외 처리 규칙 등). 모델이 필요할 때만 읽는다.
- **스크립트**: 모델이 스스로 계산하지 않고 **실행**하는 코드. 이 장의
  `analyze_sales.py`는 숫자를 한 번도 모델에게 계산시키지 않는다 —
  모델은 스크립트를 실행하고 그 출력을 해석해서 문장으로 옮길 뿐이다.

`SkillsMiddleware`(`deepagents.middleware.skills`, 0.7.13 소스 직접 확인)는
이 세 요소를 어떻게 컨텍스트에 들여오는지가 이 장의 핵심이다 — **한 번에
다 읽지 않는다.** 시스템 프롬프트에는 각 스킬의 `name`과 `description`만
한 줄씩 얹는다(그 프롬프트 조각이 소스에 그대로 있다):

```
**Available Skills:**
{skills_list}

**How to Use Skills (Progressive Disclosure):**
1. Recognize when a skill applies: Check if the user's task matches a
   skill's description
2. Read the skill's full instructions: Use `read_file` on the path shown
   in the skill list above.
3. Follow the skill's instructions
4. Access supporting files: Skills may include helper scripts, configs, or
   reference docs - use absolute paths
```

즉 모델은 먼저 "이런 스킬들이 있다"는 목록(이름 + 한 줄 설명)만 보고,
이번 요청에 맞는 스킬이 있다고 판단하면 **일반 파일 도구인 `read_file`로
그 `SKILL.md`를 직접 읽는다.** 스킬 전용 도구가 따로 있는 게 아니다 —
"스킬을 불러온다"는 동작 자체가 이미 있는 파일 읽기 도구의 평범한 호출
하나다. 이것이 "필요할 때만 읽어 컨텍스트를 아낀다"(progressive
disclosure)의 실체다. 스킬이 10개, 20개로 늘어도 시스템 프롬프트에는
여전히 이름+설명 한 줄씩만 쌓인다 — 본문과 스크립트는 그 스킬을 실제로
쓰기로 한 순간에만 컨텍스트에 들어온다.

이 장에서 실제로 재현한 규칙 하나: **스킬 디렉터리 이름과 `SKILL.md`의
`name` 필드가 다르면 경고가 뜬다.** 처음 `csv_analysis`(밑줄)라는
디렉터리에 `name: csv-analysis`(하이픈)를 써서 만들었더니 아래 경고가
표준오류로 찍혔다(그래도 로딩은 됐다 — 경고일 뿐 오류는 아니다).

```
Skill 'csv-analysis' in /skills/csv_analysis/SKILL.md does not follow
Agent Skills specification: name 'csv-analysis' must match directory
name 'csv_analysis'. Consider renaming for spec compliance.
```

그래서 이 장의 실제 디렉터리는 `skills/csv-analysis/`,
`skills/doc-research/`로 맞췄다(4절 코드).

### 2-4. Skill과 Tool과 시스템 프롬프트의 차이

이 장에서 가장 자주 뭉개지는 구분이다. 셋 다 "모델이 무언가를 더 잘하게
만드는 방법"이지만, **무엇을 담는지**, **언제 컨텍스트에 들어오는지**,
**누가 고르는지**가 전부 다르다.

| | 무엇을 담는가 | 언제 컨텍스트에 들어오는가 | 누가 고르는가 |
| --- | --- | --- | --- |
| **시스템 프롬프트** | 이 에이전트가 항상 지켜야 할 규칙·역할·어조 | **항상** — 대화 시작부터 매 호출에 통째로 실려 간다 | 애플리케이션(집필자)이 미리 고정한다. 모델은 고르지 않는다 |
| **Tool** | 실행 가능한 코드(함수) 하나와 그 입력 스키마 | 이름 + 설명 + 스키마는 **항상**(도구 목록으로), 실제 실행 결과는 **호출했을 때만** | 모델이 "이번에 이 함수를 부를지"를 매 턴 고른다 |
| **Skill** | 절차적 지침(`SKILL.md`) + 참조 자료 + 스크립트, 여러 파일 묶음 | 이름 + 한 줄 설명만 **항상**, 본문·참조 자료·스크립트는 **`read_file`로 불러왔을 때만** | 모델이 "이번 요청에 맞는 스킬이 있는지"를 보고, 있으면 스스로 읽기로 고른다 |

세 개의 차이를 한 문장으로 정리하면 이렇다 — **시스템 프롬프트는 매번
내는 비용을 감수하고 고정한 규칙이고, Tool은 실행 코드를 가리키는 짧은
포인터이며, Skill은 "가끔만 필요한, 그러나 한 번 필요하면 꽤 긴 지침
묶음"을 필요한 순간에만 불러오는 방법이다.** Skill을 "그냥 긴 시스템
프롬프트를 여러 파일에 나눠 담은 것"이라고 뭉뚱그리면 이 세 번째 성질
(필요할 때만 읽는다는 것, 그래서 스킬이 몇 개든 평소 컨텍스트 비용은
거의 늘지 않는다는 것)을 놓친다. 반대로 Skill을 "그냥 Tool 하나"라고
보면, Skill 안에 여러 파일(지침 + 참조 자료 + 스크립트)이 함께 묶여
있다는 것과 그 지침을 모델이 **읽고 스스로 해석해서 따라야** 한다는 것
(고정된 함수 시그니처 하나를 호출하는 게 아니라)을 놓친다.

이 장의 두 스킬로 구체적으로 보면: `analyze_sales.py`를 직접
`execute` 도구로 실행하는 것 자체는 **Tool 사용**(파일시스템
미들웨어가 제공하는 `execute`)이다. 그 스크립트를 "언제, 어떤 인자로,
결과를 어디에 저장한 다음 어떻게 인용해야 하는지"를 알려주는 것이
**Skill**(`SKILL.md`)이다. "숫자를 스스로 계산하지 말고 스크립트만
믿어라"는 규칙은 이 두 스킬 모두에 걸쳐 있어서 **시스템 프롬프트**에
한 번 적었다(`docagent/deep_agent.py`의 `ROOT_SYSTEM_PROMPT`). 하나의
요청을 처리하는 데 세 가지가 전부 함께 쓰였다.

### 2-5. 작업 계획(Todo)과 계획 변경

`write_todos` 도구(`langchain` 1.4.0, `langchain/agents/middleware/todo.py`
실측)는 `content`/`status`만 가진 `Todo` 항목의 리스트를 그래프 상태
`state["todos"]`에 통째로 갈아 끼운다. `status`는 `pending`/
`in_progress`/`completed` 세 값뿐이다.

PROJECT-SPEC.md 4절의 `todo` 이벤트 스키마는 다르다 —
`{"items":[{"id","title","state"}]}`, `state`는
`pending`/`running`/`done`/`failed`. 필드 이름도 값도 다르다. 이 장은
`langchain`의 값을 그대로 클라이언트에 내보내지 않고
`docagent/bridge.py`의 `map_todo_state()`에서 변환한다(`in_progress` ->
`running`, `completed` -> `done`). `failed`는 `write_todos` 쪽에 대응
값이 없다 — 모델이 스스로 "이 항목은 실패했다"고 표시하는 메커니즘이
`write_todos`에는 없기 때문이다. 이 장에서는 그 값을 쓰지 않았다(응용
과제 2에서 이 값을 언제 붙일지 다룬다).

**id는 어떻게 만드는가**: `write_todos`가 주는 `Todo`에는 id가 없다 —
매번 `content` 문자열 전체가 그 항목의 유일한 식별자다. 클라이언트(13단계
UI)가 "이 항목이 pending에서 running으로 바뀌었다"고 판단하려면 안정된
id가 있어야 한다. 이 장의 `TodoIdAssigner`(`docagent/bridge.py`)는
`content` 문자열을 이번 run에서 처음 보는 순서대로 `t1`, `t2`, ...로
번호를 매기고, 같은 `content`가 다시 나오면 같은 id를 돌려준다. 계획이
바뀌어 새 `content`가 나타나면 새 id를 받는다 — 이것이 "계획 변경"이
`todo` 이벤트에 드러나는 방식이다: 이전 스냅샷에 없던 id가 새로 나타난다.

**계획과 실제 실행 상태가 어긋나지 않게 하기가 왜 어려운가**: `write_todos`는
모델이 스스로 부르는 도구이지, 그래프가 강제로 갱신해 주는 값이 아니다.
모델이 하위 작업을 실제로 마쳤는데 `write_todos`를 다시 불러 `completed`로
갱신하는 걸 잊으면, 화면의 계획은 실제 실행보다 뒤처진다. 이 장의
시스템 프롬프트(`ROOT_SYSTEM_PROMPT`)는 "위임 결과를 받은 다음 계획을
갱신하라"고 명시하지만, 이것도 결국 모델의 판단에 기대는 지시일 뿐 —
8단계의 반복 감지·예산 초과처럼 애플리케이션이 강제로 검사해서 막는
안전장치가 아니다. `[확인 필요: langchain/deepagents 쪽에 "todos와 실제
tool 실행 이력이 어긋나면 경고"하는 내장 검증이 있는지는 이 장에서
찾지 못했다 — 0.7.13/1.4.0 소스에서 그런 코드를 보지 못했다는 뜻이지,
없다고 단정하는 것은 아니다]`.

### 2-6. 긴 컨텍스트 관리 — 왜 파일에 결과를 두는가

`sales.csv`는 48행이라 대화창에 통째로 올려도 크게 문제되지 않는다.
하지만 이 장의 설계는 "몇백 행, 몇천 행짜리 CSV나 수십 페이지짜리 문서를
다루게 되면 어떻게 할 것인가"를 미리 반영했다. 컨텍스트 창은 유한하고,
같은 자료를 여러 라운드에 걸쳐 다시 인용하다 보면 대화 기록 자체가
빠르게 불어난다(다음 모델 호출마다 그 기록을 통째로 다시 보낸다는 것도
비용이다).

이 장의 두 스킬은 그래서 **원본을 직접 대화에 붙여넣지 않는다.**
`analyze_sales.py --out results/csv_summary.md`는 계산 결과만 파일로
저장하고, 표준출력에는 요약과 JSON을 함께 낸다. 메인 에이전트는 이
파일을 나중에 `read_file`로 다시 읽는다 — CSV 원본(48행 전체)이 아니라
집계 결과(수치 몇 줄)만 컨텍스트에 들어온다. `find_evidence.py`도
마찬가지로 문서 전체가 아니라 검색된 문단만 저장한다.

이것이 Deep Agents가 파일시스템 백엔드를 기본으로 갖춘 이유이기도 하다
— "중간 결과를 어딘가에 남긴다"는 필요가 다단계 작업에서는 거의 항상
생기고, 그 남기는 자리가 대화 메시지 목록이 아니라 파일이면 다음 라운드
호출에 실을 토큰이 줄어든다. `write_file`/`read_file` 사이에 실제 파일이
있다는 것도 이 장이 4절에서 실제로 디스크에 파일이 생기는 것을 확인해
보여준다(스텁이 아니라 실제 로컬 파일시스템에 쓴다 —
`deepagents.backends.LocalShellBackend`).

### 2-7. 하위 에이전트 위임 — `task` 도구

`SubAgentMiddleware`(`deepagents.middleware.subagents`, 0.7.13 실측)가
`task` 도구를 만든다. 메인 에이전트가 `task(subagent_type="csv-analyst",
description="...")`를 부르면:

1. `csv-analyst`라는 이름으로 등록된 하위 에이전트가 **새로운, 격리된**
   대화(기본 `mode="isolated"`)로 시작한다 — 메인 에이전트의 지난 대화
   기록을 보지 않고, `description`만 첫 메시지로 받는다.
2. 하위 에이전트는 자신에게 준 시스템 프롬프트와 스킬 목록, 도구
   (기본은 메인 에이전트와 같은 파일시스템 백엔드를 공유)를 가지고 스스로
   여러 턴을 돈다(4절에서 실제로 `execute` -> 최종 요약, 두 턴을 도는
   것을 확인했다).
3. 하위 에이전트의 **마지막 메시지**가 하나의 `ToolMessage`로 압축돼
   메인 에이전트에게 돌아온다.

이 장의 `docagent/bridge.py`가 만드는 SSE 스트림에서 이 위임은 "task
호출" 한 건과 "task 결과" 한 건, 딱 두 이벤트로만 보인다 — 하위
에이전트가 내부에서 몇 번 `execute`를 불렀는지는 최상위 스트림에
드러나지 않는다(`agent.stream(..., stream_mode="values")`가 최상위
그래프의 상태만 보여주기 때문이다 — 이유는 `docagent/bridge.py` 상단
주석에 더 자세히 적었다). 이것은 2-6절의 "긴 컨텍스트 관리"와 같은
맥락이다 — 위임한 하위 작업의 **모든 내부 단계**가 아니라 **무엇을
시켰고 무엇을 받았는지**만 상위 대화(와 화면)에 남기는 편이 컨텍스트를
아낀다.

11단계에서 다룬 "내부 하위 에이전트 협업"과 이 `task` 도구는 같은
종류다 — 별도 프로세스·별도 프로토콜(A2A)이 아니라 **같은 프로세스,
같은 그래프 안에서** 이뤄지는 위임이다. A2A와의 구분은 11단계 본문을
그대로 따른다: `task`는 "내부 하위 에이전트 호출"이고, 이 장은 외부
에이전트와의 프로토콜 통신을 다루지 않는다.

## 3. 실습 준비

### 3-1. 패키지와 버전 (2026-09-09 실제 설치로 확인)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```
deepagents>=0.7,<0.8        # 계획/파일시스템/스킬/위임 미들웨어를 조립한 create_deep_agent()
langchain>=1.4,<2.0          # TodoListMiddleware(계획), deepagents가 내부적으로도 의존
langchain-openai>=1.6,<2.0   # ChatOpenAI — Deep Agents가 요구하는 BaseChatModel 구현체
openai>=3.10,<4.0            # docagent.llm(PROJECT-SPEC.md 9절 인터페이스)이 쓰는 SDK
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
python-dotenv>=1.0,<2.0
pydantic>=2.7,<3.0
httpx>=0.28,<0.29
pytest>=8.0,<9.0
```

실제로 설치해 `pip list`로 확인한 버전(이 문서 작성 시점):
`deepagents` 0.7.13, `langchain` 1.4.0, `langchain-core` 1.6.2,
`langgraph` 1.2.11(7단계와 같은 버전대 — `langgraph>=1.2,<2`),
`langchain-openai` 1.6.1, `openai` 3.10.0, `fastapi` 0.141.1,
`pydantic` 2.13.5, `python-dotenv` 1.2.3.

`deepagents`는 `langchain-anthropic`, `langchain-google-genai`도 함께
설치한다(멀티 프로바이더 프로필을 지원하기 때문 —
`deepagents/profiles/provider/` 소스에서 확인). 이 장은 그중 아무것도
쓰지 않고 `langchain-openai`의 `ChatOpenAI`만 쓴다(OpenAI 호환 서버라는
이 저장소의 기본 방향과 맞춘다).

### 3-2. 환경 변수

PROJECT-SPEC.md 2절에 이미 있는 이름을 그대로 쓰고, 하나만 추가한다.

```
DOCAGENT_AGENT_WORKDIR=.   # Deep Agents 파일시스템 백엔드의 루트(skills/, data/, results/가 이 아래)
```

### 3-3. 폴더 구조

```
code/step12_deep_agents/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── config.py       # Settings + agent_workdir 필드 추가
│   ├── llm.py            # PROJECT-SPEC.md 9절 인터페이스(이 장의 실행 경로는 직접 쓰지 않는다 — 이유는 파일 상단)
│   ├── events.py          # SSE 이벤트. todo_event를 이 장에서 처음 실제로 채워 쓴다
│   ├── fake_model.py       # 결정적 가짜 LangChain 모델(스텁 서버의 한계를 메운다)
│   ├── deep_agent.py        # create_deep_agent() 조립: 스킬 + 하위 에이전트 + 파일시스템 백엔드
│   ├── bridge.py              # LangGraph 스트림 -> PROJECT-SPEC.md SSE 이벤트, todo id/state 매핑
│   └── app.py                  # FastAPI /chat (astream 사용)
├── skills/
│   ├── csv-analysis/
│   │   ├── SKILL.md
│   │   └── analyze_sales.py
│   └── doc-research/
│       ├── SKILL.md
│       └── find_evidence.py
├── static/               # todo 패널이 있는 간단한 웹 UI
├── data/
│   ├── docs/              # 4단계에서 만든 분기 리포트 재사용
│   └── sales.csv
├── results/               # 에이전트가 중간 결과를 저장하는 곳(빈 채로 시작)
├── scripts/
│   └── demo_deep_agent_run.py   # 결정적 시나리오로 전체 흐름을 재현
└── tests/                 # pytest 11개
```

### 3-4. 샘플 데이터

`data/sales.csv`, `data/docs/`는 4·7단계에서 만든 것을 그대로 가져왔다
(PROJECT-SPEC.md 3절 규약). 새로 만들지 않는다.

## 4. 단계별 구현

### 4-1. 실제로 설치해 API를 확인한다

문서를 쓰기 전에 먼저 설치하고 소스를 읽었다(기억으로 API를 쓰지
않는다는 WRITING-GUIDE.md 규칙에 따른다).

```bash
python3 -m venv /tmp/v12 && /tmp/v12/bin/pip install deepagents
/tmp/v12/bin/pip show deepagents
```

```
Name: deepagents
Version: 0.7.13
Summary: Production-ready, extensible agent harness with a built-in
  filesystem and context management, sub-agent delegation, skills, and
  long-term memory.
Requires: langchain, langchain-anthropic, langchain-core,
  langchain-google-genai, langsmith, packaging, wcmatch
```

`deepagents.create_deep_agent`의 실제 시그니처(`inspect.signature`로
확인, 발췌):

```python
def create_deep_agent(
    model=None, tools=None, *, system_prompt=None, middleware=(),
    subagents=None, skills=None, memory=None, permissions=None,
    backend=None, interrupt_on=None, response_format=None,
    state_schema=None, context_schema=None, checkpointer=None,
    store=None, debug=False, name=None, cache=None,
) -> CompiledStateGraph: ...
```

### 4-2. 두 Skill을 만든다

```markdown
<!-- code/step12_deep_agents/skills/csv-analysis/SKILL.md -->
---
name: csv-analysis
description: sales.csv에서 제품별·지역별·분기별 매출 합계와 증감률을
  계산한다. "매출을 분석해줘"처럼 CSV 수치 집계가 필요한 요청에 쓴다.
---

# CSV 분석 스킬

sales.csv에서 매출 수치를 집계할 때 이 스킬을 따른다. 숫자를 눈대중으로
계산하지 않는다 — 반드시 `analyze_sales.py` 스크립트를 실행해서 얻은
값만 근거로 쓴다.

## 절차
1. `execute` 도구로 스크립트를 돌린다.
   `python3 skills/csv-analysis/analyze_sales.py <csv경로> --out <결과파일경로>`
2. `--out`으로 저장한 결과 파일을 `read_file`로 다시 읽어 답변에
   인용한다. 전체 CSV를 대화에 올리지 않는다.
3. 원인 설명이 필요하면 doc-research 스킬로 넘긴다.
```

스크립트는 모델을 전혀 호출하지 않는 순수 집계다(전체 코드는
`code/step12_deep_agents/skills/csv-analysis/analyze_sales.py`).

```python
# code/step12_deep_agents/skills/csv-analysis/analyze_sales.py (발췌)
def analyze(csv_path: Path) -> dict:
    by_product: dict[str, float] = defaultdict(float)
    by_quarter_product: dict[tuple[str, str], float] = defaultdict(float)
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            revenue = float(row["revenue"])
            by_product[row["product"]] += revenue
            by_quarter_product[(_quarter(row["date"]), row["product"])] += revenue
    # ... 분기 대비 증감률(quarter_over_quarter_pct) 계산은 본문 참고
    return {"by_product": ..., "quarter_over_quarter_pct": ...}
```

단독 실행 결과(이 문서 작성 시 실제로 돌려 확인함):

```bash
python3 skills/csv-analysis/analyze_sales.py data/sales.csv --out /tmp/csv_summary.md
```

```
결과를 저장했다: /tmp/csv_summary.md
{"row_count": 48, "quarters": ["2026-Q1", "2026-Q2"],
 "by_product": {"노트북": 2752800000, "마우스": 13470000, ...},
 "quarter_over_quarter_pct": {"노트북": {"2026-Q1": null, "2026-Q2": -4.1}, ...}}
```

`doc-research` 스킬도 같은 구조다(`find_evidence.py`는 `data/docs/`
아래 Markdown 문서에서 주어진 키워드가 **모두** 포함된 문단을 찾아
`{doc_id}#p{page}` 꼬리표와 함께 저장한다). 전체 코드는
`code/step12_deep_agents/skills/doc-research/find_evidence.py`. 이 장은
Skill 구성 자체가 학습 목표이므로 검색은 단순 키워드 포함 검색으로
남겨 뒀다 — 5단계의 Dense/Sparse 벡터 검색으로 바꾸고 싶다면 그 스크립트
안에서 5단계 검색 모듈을 재사용하면 된다(`SKILL.md`에 이 판단을
그대로 적어 뒀다).

### 4-3. `write_todos`가 기본에 없다는 것을 실제로 재현한다

먼저 `middleware=[TodoListMiddleware()]` 없이 `create_deep_agent()`를
불러 봤다.

```python
agent = create_deep_agent(model=model, skills=["skills/"], backend=backend)
# middleware= 인자를 주지 않음
```

모델이 `write_todos`를 부르게 했더니 이런 도구 오류가 돌아왔다(실제
실행 로그):

```
ToolMessage: "Error: write_todos is not a valid tool, try one of
[ls, read_file, write_file, edit_file, delete, glob, grep, execute, task]."
```

`langchain.agents.middleware.TodoListMiddleware`를 얹은 뒤에는
`write_todos`가 도구 목록에 나타났다(2-2절에서 이유를 설명했다). 이
장의 `docagent/deep_agent.py`는 그래서 처음부터 이 미들웨어를 포함한다.

### 4-4. Deep Agent를 조립한다

```python
# code/step12_deep_agents/docagent/deep_agent.py (발췌)
def build_agent(*, model=None, settings=None, workdir=None):
    settings = settings or get_settings()
    resolved_model = model if model is not None else build_chat_model(settings)
    backend = LocalShellBackend(root_dir=Path(workdir or settings.agent_workdir))

    return create_deep_agent(
        model=resolved_model,
        system_prompt=ROOT_SYSTEM_PROMPT,
        backend=backend,
        skills=["skills/"],
        subagents=[
            {"name": "csv-analyst", "description": "...",
             "system_prompt": CSV_SUBAGENT_PROMPT, "skills": ["skills/"]},
            {"name": "doc-researcher", "description": "...",
             "system_prompt": DOC_SUBAGENT_PROMPT, "skills": ["skills/"]},
        ],
        middleware=[TodoListMiddleware()],
    )
```

`backend=LocalShellBackend(root_dir=...)`가 이 장의 파일시스템·스크립트
실행을 실제 로컬 디스크에 연결하는 지점이다(`deepagents.backends`,
0.7.13). `LocalShellBackend`는 `FilesystemBackend`를 상속하고 `execute`
(진짜 셸 명령 실행)를 더한 것이라는 것도 소스로 확인했다
(`deepagents/backends/local_shell.py`: `class LocalShellBackend(
FilesystemBackend, SandboxBackendProtocol)`). 기본 백엔드(인자를 생략하면
쓰이는 `StateBackend`)는 그래프 상태 안에서만 파일을 흉내 내는
휘발성 백엔드라 실제 디스크에 아무것도 남기지 않는다 — 이 장은 스크립트를
**실제로 실행**해야 하므로 처음부터 `LocalShellBackend`를 명시했다.

`read_file`/`write_file`의 `file_path`는 **절대 경로**를 요구한다
(`WriteFileSchema`/`ReadFileSchema` 스키마 설명: "Must be absolute, not
relative"). 이 절대 경로는 진짜 파일시스템의 절대 경로가 아니라
**백엔드 루트 기준의 가상 절대 경로**다 — 실제로 `file_path="/results/x.md"`로
써 봤더니 디스크에는 `<root_dir>/results/x.md`로 저장됐다(4-6절에서
확인).

### 4-5. LangGraph 스트림을 PROJECT-SPEC.md SSE로 바꾼다

```python
# code/step12_deep_agents/docagent/bridge.py (발췌)
def stream_agent_events(agent, user_message, *, config=None):
    seen = 0
    assigner = TodoIdAssigner()
    last_todo_snapshot = None

    for state in agent.stream({"messages": [{"role": "user", "content": user_message}]},
                               config=config, stream_mode="values"):
        new_messages = state.get("messages", [])[seen:]
        seen = len(state.get("messages", []))

        for message in new_messages:
            if type(message).__name__ == "AIMessage":
                if message.tool_calls:
                    for call in message.tool_calls:
                        yield ("status", {"stage": stage_for_tool(call["name"]), ...})
                        yield ("tool_call", {"id": call["id"], "name": call["name"], "args": call["args"]})
                elif message.content:
                    yield ("token", {"text": message.content})
            elif type(message).__name__ == "ToolMessage":
                ok = not is_error_result(message.content)
                yield ("tool_result", {"id": message.tool_call_id, "ok": ok, "summary": truncate(message.content)})

        todos = state.get("todos")
        if todos is not None:
            items = assigner.items_for(todos)
            if items != last_todo_snapshot:
                last_todo_snapshot = items
                yield ("todo", {"items": items})

    yield ("done", {"finish_reason": "stop", "partial": {"text": final_text}})
```

`stream_mode="values"`는 "이번 슈퍼스텝이 끝난 뒤의 전체 상태"를 순서대로
내보낸다(LangGraph, 7단계에서 이미 다룬 개념). 이 함수는 그중 **새로
추가된 메시지**만 골라내고(`seen` 커서), `todos` 필드가 실제로 바뀐
경우만 `todo` 이벤트를 낸다(같은 상태를 반복해서 내보내는 슈퍼스텝이
많기 때문에 스냅샷 비교로 중복을 걸렀다).

`TodoIdAssigner`(2-5절에서 설명한 id 발급기)와 상태 매핑은 이렇다.

```python
# code/step12_deep_agents/docagent/bridge.py (발췌)
_STATE_MAP = {"pending": "pending", "in_progress": "running", "completed": "done"}

class TodoIdAssigner:
    def items_for(self, todos):
        items = []
        for todo in todos:
            content = todo["content"]
            if content not in self._ids:
                self._ids[content] = f"t{self._next}"
                self._next += 1
            items.append({"id": self._ids[content], "title": content,
                          "state": _STATE_MAP.get(todo["status"], "pending")})
        return items
```

### 4-6. FastAPI 엔드포인트

```python
# code/step12_deep_agents/docagent/app.py (발췌)
@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    agent = build_agent()

    async def event_stream():
        try:
            async for name, data in _astream_agent_events(agent, req.message):
                if name == "done":
                    yield events.done_event(data["finish_reason"], partial=data.get("partial"))
                    return
                renderer = _RENDER.get(name)
                if renderer is not None:
                    yield renderer(data)
        except Exception as exc:
            yield events.error_event("agent_error", str(exc))
            yield events.done_event("cancelled")

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

PROJECT-SPEC.md 9절은 "2단계 이후 서버 코드는 `achat`/`achat_stream`을
쓴다"고 정했다 — 클라이언트가 연결을 끊었을 때 모델 서버로 가는 호출까지
실제로 취소하려면 이벤트 루프가 스트림을 직접 닫을 수 있어야 하기
때문이다. 이 장은 `docagent.llm.achat`을 쓰지 않지만(Deep Agents는
LangGraph 그래프이지 이 저장소의 `llm.py` 함수를 호출하는 구조가
아니다), 같은 이유로 `agent.stream()`이 아니라 **`agent.astream()`**을
쓴다 — LangGraph의 비동기 스트림도 같은 취소 보장을 준다.

`write_file`/`read_file`이 요구하는 절대 경로 확인(이 문서 작성 시 실제로
실행):

```python
responses = [
    AIMessage(content="", tool_calls=[{"name": "write_file",
        "args": {"file_path": "/results/probe.md", "content": "hello"}, "id": "c1"}]),
    AIMessage(content="", tool_calls=[{"name": "read_file",
        "args": {"file_path": "/results/probe.md"}, "id": "c2"}]),
    AIMessage(content="done", tool_calls=[]),
]
```

실행 결과:

```
ToolMessage 'Updated file /results/probe.md'
ToolMessage '1  hello'
```

그리고 실제 디스크의 `<workdir>/results/probe.md`에 `hello`가 그대로
저장돼 있는 것을 `cat`으로 확인했다.

## 5. 실행과 결과 확인

### 5-1. 단위 테스트 (모델 서버 없이)

```bash
cd code/step12_deep_agents
PYTHONPATH=. pytest tests -q
```

실행 결과(이 문서 작성 시 실제로 돌려 확인함):

```
...........                                                              [100%]
11 passed in 2.74s
```

`tests/test_bridge_todo_diff.py`는 상태 매핑과 id 안정성을,
`tests/test_skill_scripts.py`는 두 Skill 스크립트를 서브프로세스로 직접
실행해 확인하고, `tests/test_deep_agent_scenario.py`는 아래 5-2절의
시나리오 전체를 pytest로도 검증한다(계획 변경 지점, 위임 2건, 파일 저장,
최종 답변에 핵심 수치·근거가 포함되는지).

### 5-2. 결정적 시나리오로 전체 흐름 보기 (모델 서버 없이, 실제 Deep Agents 그래프로)

스텁 서버(`code/_tools/fake_openai_server.py`)는 사용자 메시지를 읽지
않고 도구 목록의 첫 항목을 한 번만 부른다(8단계 5-3절에서 이미 확인한
사실). 이 장이 보여줘야 하는 여러 라운드짜리 흐름(계획 → 위임 → 계획
수정 → 위임 → 파일 재읽기 → 답변)은 그 스텁으로 재현할 수 없다. 그래서
8단계 `demo_infinite_loop.py`와 같은 방법을 쓴다 — LangChain의
`BaseChatModel`을 상속한 **결정적** 가짜 모델(`docagent/fake_model.py`의
`ScriptedChatModel`)에 미리 정해둔 12개의 응답을 순서대로 물려서 **실제
`deepagents` 그래프**에 흘려보낸다. 모델의 판단은 시뮬레이션이지만,
그래프 실행·파일 I/O·스크립트 실행은 전부 진짜다.

```bash
PYTHONPATH=. python scripts/demo_deep_agent_run.py
```

실제 실행 결과(발췌, 전문은 스크립트를 직접 돌려 확인할 수 있다):

```
event: tool_call
data: {"id": "call_1", "name": "write_todos", "args": {"todos": [
  {"content": "csv-analyst에게 매출 집계 위임", "status": "in_progress"},
  {"content": "doc-researcher에게 원인 조사 위임", "status": "pending"},
  {"content": "결과 종합해 답변 작성", "status": "pending"}]}}

event: todo
data: {"items": [
  {"id": "t1", "title": "csv-analyst에게 매출 집계 위임", "state": "running"},
  {"id": "t2", "title": "doc-researcher에게 원인 조사 위임", "state": "pending"},
  {"id": "t3", "title": "결과 종합해 답변 작성", "state": "pending"}]}

event: tool_call
data: {"id": "call_2", "name": "task", "args": {
  "description": "sales.csv에서 제품별·분기별 매출을 집계하고 results/csv_summary.md에 저장하라.",
  "subagent_type": "csv-analyst"}}

event: tool_result
data: {"id": "call_2", "ok": true, "summary":
  "results/csv_summary.md에 저장했다. 노트북 매출이 1분기 대비 2분기에 4.1% 줄었다. ..."}
```

여기서 **계획이 실제로 바뀐다** — csv 집계 결과를 본 메인 에이전트가
`write_todos`를 다시 불러 새 항목을 추가했다.

```
event: tool_call
data: {"id": "call_5", "name": "write_todos", "args": {"todos": [
  {"content": "csv-analyst에게 매출 집계 위임", "status": "completed"},
  {"content": "doc-researcher에게 원인 조사 위임", "status": "in_progress"},
  {"content": "노트북 2분기 감소 원인 문서에서 확인", "status": "pending"},
  {"content": "결과 종합해 답변 작성", "status": "pending"}]}}

event: todo
data: {"items": [
  {"id": "t1", "title": "csv-analyst에게 매출 집계 위임", "state": "done"},
  {"id": "t2", "title": "doc-researcher에게 원인 조사 위임", "state": "running"},
  {"id": "t4", "title": "노트북 2분기 감소 원인 문서에서 확인", "state": "pending"},
  {"id": "t3", "title": "결과 종합해 답변 작성", "state": "pending"}]}
```

`t1`/`t2`/`t3`는 첫 계획 때 발급된 id 그대로다(항목 내용이 같으므로
유지됐다). `t4`는 이번에 처음 나타난 항목이라 새 id를 받았다 — 이것이
"계획이 바뀌었다"는 사실이 클라이언트에 드러나는 방식이다. doc-researcher
위임과 최종 정리까지 마친 뒤 마지막 `todo` 이벤트는 전부 `done`이다.
전체 실행에서 `todo` 이벤트는 정확히 3번 나갔다(초기 계획, 계획 수정,
최종 완료) — 상태가 실제로 바뀌지 않은 중간 슈퍼스텝은 4-5절에서 설명한
스냅샷 비교로 걸러졌다.

마지막으로 메인 에이전트는 저장된 두 파일을 **다시 읽어서** 답을
만든다(재계산하지 않는다).

```
event: tool_call
data: {"id": "call_10", "name": "read_file", "args": {"file_path": "/results/csv_summary.md"}}
event: tool_result
data: {"id": "call_10", "ok": true, "summary": " 1  # CSV 분석 결과 (sales.csv, 48행)\n ..."}

event: done
data: {"finish_reason": "stop", "partial": {"text":
  "노트북 매출은 1분기 대비 2분기에 약 4.1% 줄었다(results/csv_summary.md). notebook-q1-report#p1에 따르면 부산 지역 물류 센터 이전으로 배송이 지연되면서 일부 대량 구매 고객이 다음 분기 주문으로 미룬 것이 주요 원인이다."}}
```

그리고 실제로 디스크에 두 결과 파일이 남아 있는 것을 확인했다.

```bash
$ cat results/csv_summary.md
# CSV 분석 결과 (sales.csv, 48행)
## 제품별 매출 합계
- 노트북: 2,752,800,000원
...

$ cat results/doc_findings.md
# 문서 조사 결과 (키워드: 노트북, 부산)
## notebook-q1-report#p1
1분기 노트북 매출은 서울·부산 두 지역 모두에서 꾸준히 증가했다.
...
```

### 5-3. 스텁 서버로 기본 연결 확인 (실제 모델 판단은 검증하지 않는다)

```bash
# 터미널 1
python code/_tools/fake_openai_server.py --port 8192

# 터미널 2
cd code/step12_deep_agents
OPENAI_BASE_URL=http://127.0.0.1:8192/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=stub-model \
DOCAGENT_AGENT_WORKDIR=$(pwd) uvicorn docagent.app:app --port 8193 --app-dir .
```

```bash
curl -sN http://127.0.0.1:8193/chat -H 'content-type: application/json' \
  -d '{"message":"csv 분석해줘"}'
```

실제 응답(이 문서 작성 시 실행):

```
event: status
data: {"stage": "retrieving", "message": "ls 호출 준비"}

event: tool_call
data: {"id": "call_stub_1", "name": "ls", "args": {}}

event: tool_result
data: {"id": "call_stub_1", "ok": false, "summary": "Error invoking tool 'ls' with kwargs {} with error:\n path: Field required\n Please fix the error and try again."}

event: token
data: {"text": "요청하신 내용을 확인했습니다."}

event: done
data: {"finish_reason": "stop", "partial": {"text": "요청하신 내용을 확인했습니다."}}
```

스텁은 사용자 메시지("csv 분석해줘")를 전혀 보지 않고, Deep Agents가
등록한 도구 목록의 첫 항목(`ls`)을 인자 없이 부른다. `path`가 필수라
도구 오류가 나고, 스텁은 도구를 한 번만 부르는 성질(8단계에서 확인한
동작) 때문에 그대로 고정 문구로 답을 마친다. 이 실행이 확인해 주는
것은 **요청/응답 배선이 맞다는 것**(FastAPI -> Deep Agents ->
ChatOpenAI -> 스텁 -> SSE)뿐이다. 계획 수립·스킬 선택·위임 판단은 이
서버로 검증할 수 없다 — 5-2절의 결정적 시나리오가 그 부분을 메운다.

## 6. 실패 상황 실습

### 6-1. `write_todos`를 빼먹으면 어떤 오류가 나는가

4-3절에서 재현한 것을 다시 정리한다. `TodoListMiddleware()`를 빼고
`create_deep_agent()`를 만들면, 모델이 `write_todos`를 시도할 때(스크립트가
그렇게 지시하든, 실제 모델이 스스로 판단하든) 아래 오류가 도구 결과로
돌아온다.

```
Error: write_todos is not a valid tool, try one of
[ls, read_file, write_file, edit_file, delete, glob, grep, execute, task].
```

이 오류는 애플리케이션 버그가 아니라 "이 그래프에 애초에 그 도구가
없다"는 사실을 그대로 알려주는 것이다 — 8단계의 도구 오류 처리 원칙과
같다: 오류를 숨기지 않고 도구 결과로 모델에게 돌려주면, 모델이 다른
방법을 찾을 기회라도 준다(이 경우엔 모델이 스스로 고칠 방법이 없다 —
미들웨어 자체를 애플리케이션이 등록해야 한다).

### 6-2. 스킬 디렉터리 이름과 `name` 필드가 다르면

2-3절에서 실제로 재현한 경고를 실패 상황으로 다시 짚는다.
`skills/csv_analysis/`(밑줄) 디렉터리 안에 `name: csv-analysis`(하이픈)를
쓰면:

```
Skill 'csv-analysis' in /skills/csv_analysis/SKILL.md does not follow
Agent Skills specification: name 'csv-analysis' must match directory
name 'csv_analysis'. Consider renaming for spec compliance.
```

로딩 자체는 실패하지 않는다(경고만 찍히고 스킬은 목록에 나타난다) —
하지만 스킬이 여러 개로 늘고 이 경고가 표준오류에 쌓이기 시작하면
운영 로그에서 알아채기 어렵다. 이 장은 처음부터 디렉터리 이름과
`name` 필드를 맞춰서(`skills/csv-analysis/`) 이 경고를 만들지 않는다.

### 6-3. `execute`가 지원되지 않는 백엔드를 쓰면

기본 백엔드(`backend=` 인자를 생략했을 때 쓰이는 `StateBackend`)나
`execute`를 지원하지 않는 백엔드로 이 장의 스킬을 그대로 쓰면 어떻게
될까. `deepagents`의 `execute` 도구 설명 자체가 이렇게 밝힌다(0.7.13
소스, `create_deep_agent` 문서 문자열 발췌): "For non-sandbox backends,
the `execute` tool will return an error message." 즉 스크립트를 실행하지
못하는 백엔드에서는 `execute` 호출이 예외 없이 **도구 오류 메시지**로
돌아온다 — 이 장은 이 경로를 실제로 재현하지는 않았다(처음부터
`LocalShellBackend`를 썼기 때문이다). `[확인 필요: StateBackend에서
execute를 호출했을 때 정확한 오류 문구]` — 소스 문서 문자열의 설명을
근거로 적었을 뿐, 이 장에서 직접 실행해 그 문구를 확인하지는 않았다.

## 7. 응용 과제

1. **`failed` 상태 연결하기**: `task` 도구 호출의 결과가 오류였을 때
   (`docagent.bridge.is_error_result()`가 `True`를 돌려주는 경우),
   해당 위임과 연결된 todo 항목의 상태를 `failed`로 강제 표시해 보라.
   힌트: `stream_agent_events()`에서 `task` 도구의 `tool_result`가
   오류일 때, 다음 `todo` 이벤트를 만들기 전에 `TodoIdAssigner`가 아는
   항목 중 어느 것을 `failed`로 덮어씌울지 정해야 한다(어떤 항목이 그
   위임과 연결된 것인지는 애플리케이션이 판단해야 한다 — `write_todos`
   자체는 그 연결 정보를 모른다). 완료 기준: 일부러 실패하는 스크립트로
   시나리오를 하나 더 만들고, 그 항목의 `todo` 이벤트가 `failed`로
   나오는 것을 테스트로 확인한다.
2. **세 번째 Skill 추가하기**: 지역별 매출 비교나 이상치 탐지 같은
   새 스킬을 `skills/` 아래에 만들어 보라. 완료 기준: `SKILL.md`의
   `name`이 디렉터리 이름과 일치하고(6-2절의 경고가 뜨지 않고),
   `analyze_sales.py`처럼 실제로 실행 가능한 스크립트를 포함하며,
   `demo_deep_agent_run.py`와 비슷한 시나리오로 이 스킬이 선택돼
   실행되는 것을 확인한다.
3. **MAX_AGENT_STEPS를 실제로 넘겨 보기**: `run_config`의
   `recursion_limit`(4-6절 주석 참고)을 아주 작은 값(예: 2)으로 낮추고
   5-2절의 시나리오를 다시 돌려 보라. `GraphRecursionError`가 어디서
   나는지, 8단계처럼 부분 결과를 담은 `done`으로 곱게 마무리하려면
   `docagent/app.py`의 `except Exception`을 어떻게 바꿔야 할지 직접
   고쳐 보라. 완료 기준: 한도를 넘겨도 서버가 500으로 죽지 않고
   `error`+`done` 이벤트로 스트림이 정상 종료된다.

## 8. 핵심 정리와 확인 질문

### 핵심 정리

- LangChain·LangGraph·Deep Agents는 층이 아니라 각자 다른 불편을 없앤다
  — 공통 인터페이스(LangChain), 명시적 상태·재개(LangGraph), 계획·파일·
  위임·스킬을 미리 조립한 그래프(Deep Agents).
- Deep Agents가 만드는 것도 결국 LangGraph `CompiledStateGraph`다. 다른
  것은 마디와 간선을 누가 정하느냐다 — 이 장은 그 결정의 상당 부분을
  모델에게 넘긴다.
- Skill은 지침(`SKILL.md`) + 참조 자료 + 스크립트를 묶은 디렉터리다.
  이름과 한 줄 설명만 항상 시스템 프롬프트에 실리고, 본문은 모델이
  `read_file`로 필요할 때만 읽는다(progressive disclosure) — 이 점이
  Skill을 "그냥 긴 시스템 프롬프트"나 "그냥 Tool 하나"와 구분 짓는다.
- `write_todos`는 `deepagents`의 기본 미들웨어 스택에 없다 — `langchain`의
  `TodoListMiddleware`를 명시적으로 얹어야 한다(0.7.13/1.4.0 실측).
- `write_todos`의 상태값(`pending`/`in_progress`/`completed`)과
  PROJECT-SPEC.md의 `todo` 이벤트 상태값(`pending`/`running`/`done`/
  `failed`)은 다르다 — 애플리케이션이 매핑하고, id도 애플리케이션이
  `content` 문자열로 안정적으로 발급해야 한다.
- 중간 결과를 파일로 저장하고 다시 읽는 것은 컨텍스트 관리다 — 원본
  전체가 아니라 계산된 결과만 다음 라운드의 컨텍스트에 들어온다.
- `task` 도구로의 위임은 하위 에이전트 내부의 낱개 단계를 상위 대화에
  노출하지 않는다 — "무엇을 시켰고 무엇을 받았는지"만 남는다.

### 확인 질문

1. "Deep Agents는 LangGraph보다 상위 계층이다"라는 문장이 왜 정확하지
   않은지, `create_deep_agent()`가 실제로 돌려주는 타입을 근거로
   설명해 보라.
2. Skill과 Tool과 시스템 프롬프트 중 "컨텍스트에 항상 실리는 것"과
   "필요할 때만 실리는 것"을 구분해 보라. Skill의 어느 부분이 어느
   쪽에 속하는가?
3. `write_todos`의 `status` 값과 PROJECT-SPEC.md `todo` 이벤트의
   `state` 값이 왜 1:1로 같지 않은지, 이 장이 그 차이를 어디서 어떻게
   메우는지 설명해 보라.
4. 이 장의 CSV 분석을 7단계 스타일(고정된 노드·간선의 LangGraph
   워크플로)로 다시 만든다면 무엇이 더 쉬워지고 무엇이 더 번거로워질지
   말해 보라(힌트: "요청마다 필요한 하위 작업의 개수와 종류가 미리
   정해져 있는가"를 기준으로 생각해 본다).

**이 장의 완성 코드**: `code/step12_deep_agents/`

**이 장 대 7단계(고정 워크플로) — 언제 어느 쪽을 쓰는가**: 7단계는
"이번 요청에서 밟을 단계의 종류와 순서가 미리 알려져 있고, 그 순서를
지키는 것 자체가 품질에 중요한" 경우에 강하다(예: 근거를 확인하기 전에는
절대 답하지 않는다, 라는 규칙을 그래프 구조 자체로 강제할 수 있다).
이 장(Deep Agents)은 "요청마다 필요한 하위 작업의 개수·종류·순서가
그때그때 달라서 미리 그래프로 고정하기 어려운" 경우에 강하다 — 이번
장의 예시처럼 "csv 집계 결과를 보고 나서야 어떤 문서를 조사해야 할지
알 수 있는" 상황이 전형적이다. 반대로 이 장의 방식은 "모델이 계획을
얼마나 잘 세우고 얼마나 성실히 갱신하는가"에 결과 품질이 크게
좌우된다(2-5절) — 7단계는 이 부분을 아예 애플리케이션이 강제해 모델의
판단에 맡기지 않는다. 두 방식은 한 프로젝트 안에서도 섞어 쓸 수 있다 —
7단계의 `answer` 노드 하나를, 필요하면 이 장의 `task` 도구로 하위
에이전트에게 위임하도록 바꾸는 것도 가능하다(`CompiledSubAgent`가
컴파일된 LangGraph 그래프를 그대로 하위 에이전트로 등록할 수 있게
해 준다 — `deepagents/middleware/subagents.py`의 `CompiledSubAgent`
TypedDict, 이 장에서는 실제로 시도하지 않았다).

**다음 장 연결점**: 13단계는 이 장의 `todo` 이벤트를 실제 화면의 작업
목록 UI로 그린다(대기·진행·완료·실패 네 상태를 색이나 아이콘으로
구분). 이 장의 `results/`에 저장된 CSV 분석 결과는 13단계의 CSV
다운로드 기능이 그대로 활용할 수 있는 형태다.

---

## 검증 상태

**실제로 실행해 확인한 것**

- `pip install deepagents`로 실제 설치하고(2026-09-09), `pip show`·
  `inspect.signature`·소스 코드 직접 읽기로 `create_deep_agent()`의
  시그니처, 기본 미들웨어 스택, `SkillsMiddleware`의 progressive
  disclosure 프롬프트, `SubAgent`/`CompiledSubAgent`의 필드,
  `LocalShellBackend`가 `FilesystemBackend`를 상속한다는 것을 확인했다
  (3-1절, 4절, 2절 각주).
- `write_todos`가 `deepagents`의 기본 미들웨어 스택에 없다는 것을
  `create_deep_agent()`를 `middleware=` 없이 불러 실제 도구 오류
  메시지로 재현했고, `langchain.agents.middleware.TodoListMiddleware`를
  얹으면 해결되는 것도 실제로 확인했다(4-3절, 6-1절).
- 스킬 디렉터리 이름과 `SKILL.md`의 `name`이 다르면 나는 실제 경고
  메시지를 재현했다(2-3절, 6-2절).
- `write_file`/`read_file`이 요구하는 절대 경로가 `LocalShellBackend`의
  `root_dir` 기준 가상 경로로 실제 디스크 파일에 매핑되는 것을
  `write_file` -> `cat`으로 실제 확인했다(4-6절).
- `execute` 도구로 `analyze_sales.py`/`find_evidence.py`를 실제로
  실행시켜 `results/`에 실제 파일이 생기는 것을 확인했다(4절, 5-2절).
- `task` 도구로 위임하면 **같은 모델 인스턴스**가 하위 에이전트 내부
  호출에도 재사용된다는 것을(`ScriptedChatModel`의 공유 순번 소비로)
  실제로 확인했다(2-7절, `docagent/fake_model.py` 상단 설명).
- `docagent/fake_model.py`의 `ScriptedChatModel`로 12단계짜리 결정적
  시나리오(계획 → 위임 → 계획 수정 → 위임 → 파일 재읽기 → 답변)를
  **실제 `deepagents` 그래프**에 흘려 `scripts/demo_deep_agent_run.py`로
  실행했고, `todo` 이벤트가 정확히 3번(초기 계획·계획 수정·완료) 나가는
  것, 두 번째 스냅샷에 새 항목이 추가되는 것, id가 계획 수정 전후로
  유지/신규 발급되는 것, 위임 2건(`csv-analyst`, `doc-researcher`)이
  `tool_call`로 남는 것, `results/csv_summary.md`·`results/doc_findings.md`가
  실제로 디스크에 저장되는 것을 콘솔 출력과 `cat`으로 확인했다(5-2절).
- `code/_tools/fake_openai_server.py --port 8192`를 실제로 띄우고, 이
  장의 FastAPI 앱(`uvicorn docagent.app:app --port 8193`)을 그 스텁을
  바라보게 실행해 `/healthz`와 `/chat`을 curl로 실제 호출했다(5-3절).
- `PYTHONPATH=. pytest tests -q`로 단위 테스트 11개(상태 매핑·id
  안정성 5개, 스킬 스크립트 3개, 결정적 시나리오 통합 1개— 나머지는
  세부 단언)를 실행해 전부 통과를 확인했다(5-1절).
- 이 장의 모든 `.py` 파일에 `python3 -m py_compile`을 통과시켰다.

**확인하지 못한 것 / 검증이 제한된 것**

- 스텁 서버는 모델이 아니므로, 이 장이 정말 보여주고 싶은 것 —
  **모델이 스스로 계획을 세우고, 스킬을 고르고, 계획을 언제 어떻게
  수정할지 판단하는 것** — 은 스텁으로 검증할 수 없다. 5-2절의 결정적
  시나리오는 "이런 순서의 도구 호출이 오면 Deep Agents 메커니즘이
  실제로 동작하는가"만 확인한다 — "실제 모델이 이 순서를 스스로
  골랐는가"는 검증하지 않았다. 실제 상용 모델을 붙였을 때 계획이
  이 장의 시나리오와 다르게 나올 수 있고, 그 자체는 실패가 아니다.
- `StateBackend`(기본 백엔드)에서 `execute`를 호출했을 때의 정확한
  오류 문구는 소스 문서 문자열을 근거로만 적었고, 직접 실행해 확인하지
  않았다(6-3절 `[확인 필요]`).
- `agent.stream(..., subgraphs=True)`로 하위 에이전트 내부 이벤트까지
  받았을 때 이 장의 브리지 가정이 어떻게 달라지는지는 실험하지
  않았다(2-7절 `[확인 필요]`).
- `langchain`/`deepagents`에 "계획과 실제 실행 이력이 어긋나면 경고"하는
  내장 검증이 있는지는 소스에서 찾지 못했다는 것을 확인했을 뿐, 없다고
  단정하지는 않았다(2-5절 `[확인 필요]`).
- `CompiledSubAgent`로 7단계의 컴파일된 그래프를 이 장의 하위 에이전트로
  등록하는 것은 필드 존재만 소스로 확인했고, 실제로 시도하지 않았다
  (8절 "이 장 대 7단계").
- 실제 상용 OpenAI 호환 모델 서버를 대상으로 한 실행은 검증하지
  않았다 — 5-3절의 스텁 서버와 5-2절의 결정적 가짜 모델로만 검증했다.

## 참고 문서

- PROJECT-SPEC.md(이 저장소 루트) — SSE 이벤트 스키마(4절), 공통
  인터페이스(9절), 의존성 버전 정책(10절).
- VERIFIED-FINDINGS.md(이 저장소 루트) — 이전 단계까지 확인된 패키지
  버전·pymilvus 이슈 기록. 이 장은 Milvus를 쓰지 않는다.
- `deepagents` 0.7.13 소스 코드(PyPI에서 실제 설치해 직접 읽음,
  2026-09-09): `deepagents/graph.py`(`create_deep_agent` 조립 로직,
  기본 미들웨어 목록), `deepagents/middleware/skills.py`
  (`SkillsMiddleware`, progressive disclosure 프롬프트, 스킬 이름/
  디렉터리 검증), `deepagents/middleware/subagents.py`(`SubAgent`,
  `CompiledSubAgent`, `task` 도구), `deepagents/middleware/filesystem.py`
  (`WriteFileSchema`/`ReadFileSchema`/`ExecuteSchema`),
  `deepagents/backends/local_shell.py`(`LocalShellBackend`).
- `langchain` 1.4.0 소스 코드: `langchain/agents/middleware/todo.py`
  (`TodoListMiddleware`, `Todo` TypedDict, `write_todos` 도구 — 상태값
  `pending`/`in_progress`/`completed` 확인).
- `code/_tools/fake_openai_server.py` 소스 코드 — 도구 호출을 "한 번만,
  tools의 첫 항목만" 만든다는 사실(8단계에서 이미 확인, 이 장 5-3절에서
  재확인).
- 이 문서의 모든 버전 정보와 실행 결과는 2026-09-09 기준이다(작성 시점
  질문에 명시된 오늘 날짜).
