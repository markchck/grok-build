# 11단계. 멀티에이전트와 Agent-to-Agent

## 1. 학습 목표와 완성 모습

7단계(`code/step07_langgraph/`)는 하나의 그래프 안에서 상태(State)·노드(Node)·
간선(Edge)으로 검색과 검증의 흐름을 제어했다. 그 그래프에는 노드가 여럿
있었지만 "에이전트"는 하나였다 — 노드들은 전부 같은 목적(질문에 답하기)을
공유했고, 같은 시스템 프롬프트 계열 아래 있었다. 이 장은 그 전제를 깬다:
**서로 다른 책임을 가진 여러 에이전트가 하나의 요청을 나눠 맡고, 결과를
주고받는다.**

이 장에서 다루는 것은 다음 여덟 가지다.

1. **단일 에이전트와 멀티에이전트 비교** — 언제 나누는 게 이득이고 언제
   손해인지.
2. **조사·분석·검증 역할 분리** — investigator(조사) / analyst(분석) /
   verifier(검증) 세 에이전트가 각자 다른 일을 한다.
3. **Supervisor 기반 위임과 Handoff** — 위임한 뒤 제어권이 돌아오는 구조와,
   돌아오지 않는 구조.
4. **공유 상태와 에이전트별 컨텍스트** — 무엇을 모두가 보고, 무엇을 그
   에이전트만 보는지.
5. **병렬 작업과 결과 통합** — 서로 관련 없는 조사 두 건을 동시에 실행하고
   합친다.
6. **내부 하위 에이전트 호출과 A2A 프로토콜의 차이** — 같은 파이썬
   프로세스 안의 함수 호출과, 독립적으로 운영되는 별도 에이전트에게 작업을
   맡기는 것은 근본적으로 다른 일이다.
7. **MCP의 도구 연결과 A2A의 에이전트 협업 차이** — 10단계가 다룬 "도구를
   가져다 쓰는 것"과 이 장이 다루는 "에이전트끼리 협업하는 것"의 경계.
8. **상호 위임 루프·실패·비용 제한** — 에이전트 A가 B에게, B가 다시 A에게
   넘기는 순환을 실제로 만들고, 안전장치가 이를 멈추는 것을 확인한다.

완성 모습은 이렇다. 사용자가 "노트북 매출이 왜 늘었는지 조사해줘"라고
요청하면, 슈퍼바이저가 investigator에게 원본 데이터 조회를 맡기고,
investigator가 끝내면 슈퍼바이저에게 제어권이 돌아와 analyst에게 추세
해석을, 다시 verifier에게 검증을 맡긴다. 검증은 **같은 프로세스 안의 함수
호출**로 할 수도 있고, **별도 프로세스로 떠 있는 검증 에이전트**에게
HTTP로 작업을 맡길 수도 있다 — 이 장은 이 둘을 나란히 실행해 무엇이
바뀌는지 보여준다.

```
사용자: 노트북 매출이 왜 늘었는지 조사해줘

[status:planning]   [supervisor] 다음 위임 대상: investigator
[status:retrieving] [investigator] 제품 '노트북'의 판매 행 12건을 찾았다
[status:planning]   [supervisor] 다음 위임 대상: analyst
[status:analyzing]  [analyst] 추세 판정: decrease (Q1 매출 1405200000에서 Q2 매출 1347600000로 변했다)
[status:planning]   [supervisor] 다음 위임 대상: verifier
[status:verifying]  [verifier] 검증 결과: 통과 (재계산한 분기별 합계가 analyst 보고와 일치한다)
[status:planning]   [supervisor] 다음 위임 대상: finish
[status:writing]    조사·분석·검증 결과를 하나의 답변으로 합쳤다
[done] finish_reason=stop
       partial.text="노트북의 매출은 decrease 추세다. Q1 매출 1405200000에서 Q2 매출 1347600000로 변했다"
```

(실제 데이터의 "매출이 늘었는지"는 아래에서 보듯 이 예시 데이터에서는
감소였다 — 사용자의 전제가 데이터와 다를 수 있다는 것도 조사·검증의
정상적인 결과다.)

앞 단계 연결: 이 장은 7단계의 State/Node/Edge, 조건 분기, 병렬 실행과
커스텀 리듀서를 그대로 잇는다. 8단계의 실행 한도(최대 단계·시간·토큰)와
반복 감지를 "에이전트 하나의 run"에서 "여러 에이전트가 관여하는 협업
전체"로 확장한다. 10단계(MCP, `code/step10_mcp/`)는 이 장과 같은 시기에
다른 필자가 병행 집필했다 — 이 장은 MCP 코드를 가져다 쓰지 않고, 2-7절에서
개념만 비교한다.

## 2. 핵심 개념

### 2-1. 단일 에이전트로 충분한 경우, 나누는 게 이득인 경우

**멀티에이전트가 항상 더 낫다고 전제하지 않는다.** 에이전트를 나누는
순간 새로 생기는 비용이 있다.

- **컨텍스트 전달 비용**: 한 에이전트가 다른 에이전트에게 일을 넘기려면
  "지금까지 무엇을 알아냈는지"를 어떤 형태로든 전달해야 한다. 단일
  에이전트라면 그냥 같은 대화 이력 안에 있던 정보다.
- **지연**: 에이전트가 늘어날수록 그 사이를 오가는 모델 호출(또는 프로세스
  간 통신)이 늘어난다. 이 장의 슈퍼바이저 데모(5-1절)만 해도 슈퍼바이저
  4번 + 하위 에이전트 3번, 총 7번의 판단 단계를 거친다 — 단일 에이전트라면
  도구 호출 몇 번으로 끝났을 일이다.
- **실패 지점 증가**: 에이전트가 하나 늘 때마다 그 에이전트가 실패하거나,
  그 에이전트로 가는 연결이 끊기거나, 그 에이전트가 잘못된 판단을 내리는
  경우가 하나씩 늘어난다. 2-6절의 A2A 최소 흉내 데모에서 외부 에이전트가
  응답하지 않는 경우를 실제로 만들어 본다(6-2절).
- **디버깅 난이도**: 문제가 어느 에이전트의 판단 때문인지, 아니면 에이전트
  사이의 정보 전달 때문인지 구분해야 한다.

이 비용을 감수할 만한 조건은 대략 이렇다.

| 조건 | 나누는 게 이득인 이유 |
| --- | --- |
| 역할마다 필요한 지식·프롬프트가 뚜렷이 다르다 | 검증(verifier)은 "의심하고 재계산하라"는 프롬프트가 필요하고, 분석(analyst)은 "해석하고 설명하라"는 프롬프트가 필요하다. 하나의 프롬프트에 둘 다 넣으면 모델이 두 태도를 오간다 |
| 한 에이전트의 시행착오를 다른 에이전트가 볼 필요가 없다 | investigator가 데이터를 찾느라 겪은 시행착오까지 analyst의 프롬프트에 실리면 토큰만 늘고 analyst의 판단에는 도움이 안 된다(2-3절) |
| 서로 독립적인 하위 작업을 병렬로 실행할 수 있다 | 2-5절의 병렬 조사처럼, 기다릴 이유가 없는 일을 동시에 처리해 지연을 줄인다 |
| 신뢰 경계나 운영 주체가 실제로 다르다 | 검증을 감사(audit) 목적으로 독립된 팀·시스템이 맡아야 한다면 그 자체가 "나눠야 하는" 이유다 — 이때는 2-6·2-7절의 A2A 같은 프로토콜 경계가 필요해진다 |
| 요청 하나에 서로 다른 여러 산출물이 필요하다 | "제품별로 조사해줘"처럼 병렬화할 하위 작업이 자연히 여러 개 나온다 |

반대로 **요청이 순차적인 도구 호출 몇 번으로 끝나거나, 역할 사이에 뚜렷한
경계가 없거나, 지연이 민감한 경우**에는 3단계의 단일 에이전트 루프(도구
호출 반복)가 낫다. 이 장의 슈퍼바이저 데모는 3개의 도구 호출로 충분할 일을
일부러 3개의 에이전트로 나눈 예시다 — "역할 분리를 보여주기 위해"
나눈 것이지, 이 정도 작업에 실제로 멀티에이전트가 필요하다는 뜻은 아니다.
이 점을 4절 서두에서도 다시 짚는다.

### 2-2. 조사·분석·검증 역할 분리

이 장의 협업은 세 에이전트로 구성된다.

| 에이전트 | 책임 | 무엇을 보는가 |
| --- | --- | --- |
| investigator(조사) | 요청에서 조사 대상(제품)을 정하고 원본 판매 데이터를 찾는다 | 사용자 요청만 |
| analyst(분석) | investigator가 찾은 원본 행으로 분기별 추세를 계산·해석한다 | investigator가 공유 상태에 남긴 결과만(원래 요청 문장은 보지 않는다) |
| verifier(검증) | analyst의 판정을 원본 데이터로 **독립적으로 재계산**해 맞는지 확인한다 | investigation·analysis 두 결과만(analyst가 어떻게 그 결론에 도달했는지 과정은 보지 않는다) |

verifier가 "analyst의 설명을 믿지 않고 원본 데이터로 다시 계산한다"는
점이 중요하다 — analyst가 계산을 틀렸거나 모델이 숫자를 잘못 옮겨 적었어도
verifier가 원본에서 다시 계산하므로 잡아낼 수 있다. 6절에서 이 불일치를
실제로 재현한다.

세 에이전트 모두 7단계 `classify`/`verify` 노드와 같은 패턴을 쓴다: 모델에게
구조화된 판단(JSON)을 요청하고, 응답이 기대한 형태가 아니면 규칙 기반으로
대체하며, 판단 주체(`source`: `"model"` 또는 `"heuristic"`)를 항상 함께
기록한다(`code/step11_multi_agent/docagent/multiagent/agents.py`).

### 2-3. 공유 상태와 에이전트별 컨텍스트

멀티에이전트에서 "상태를 어떻게 나누는가"는 단일 에이전트에는 없던 새로운
설계 결정이다. 이 장은 두 층으로 나눈다(`multiagent/state.py`의
`CollabState`).

- **공유 상태(블랙보드)**: `investigation`, `analysis`, `verification`,
  `shared_notes`. 누구나 읽을 수 있고, 담당 에이전트만 쓴다. 슈퍼바이저는
  이 상태만 보고 다음 위임 대상을 정한다 — investigator가 몇 번 시행착오를
  겪었는지는 슈퍼바이저의 판단에 필요 없다.
- **에이전트별 컨텍스트**: `investigator_context`, `analyst_context`,
  `verifier_context`. 그 에이전트 자신만 쓰고 읽는 메시지 이력이다.

컨텍스트를 나누지 않으면 무슨 일이 생기는가: 세 에이전트가 하나의 대화
이력을 공유한다면, verifier의 프롬프트에는 investigator가 겪은 시행착오와
analyst의 중간 추론까지 전부 쌓여 있게 된다. 에이전트 수가 늘수록 마지막
에이전트의 프롬프트 길이(=비용)가 앞선 모든 에이전트의 대화 길이의 합으로
자란다. `tests/test_supervisor.py`의
`test_investigator_and_analyst_have_separate_private_context`가 이 분리가
실제로 지켜지는지 확인한다.

### 2-4. 슈퍼바이저 위임과 핸드오프 — 제어권이 돌아오는가

이 두 패턴의 차이는 **위임한 뒤 제어권이 다시 위임한 쪽으로 돌아오는가**
하나로 요약된다.

```
[슈퍼바이저 위임]                    [핸드오프]

supervisor                          agent_a
   │ 위임                              │ "이건 내 일이 아니다"
   ▼                                   ▼
investigator                        agent_b
   │ 끝나면 반드시 돌아온다              │ agent_a에게 돌아간다는 보장이 없다
   ▼                                   ▼
supervisor (제어권 복귀)              agent_b가 마무리(또는 또 다른 곳으로)
   │ 위임
   ▼
analyst
   │ 끝나면 반드시 돌아온다
   ▼
supervisor ...
```

이 장의 구현에서 이 차이는 그래프 구조 자체로 강제된다.

- **슈퍼바이저**(`multiagent/graph_supervisor.py`): investigator, analyst,
  verifier 세 노드 모두 `supervisor`로만 이어지는 **고정 간선**을 갖는다.
  누가 다음에 불릴지는 매번 슈퍼바이저가 다시 정한다.
- **핸드오프**(`multiagent/handoff.py`): 노드가 `dict`가 아니라
  `langgraph.types.Command(goto=다른_노드_이름)`를 직접 돌려준다. 이
  순간 실행이 그 노드로 곧장 넘어간다 — "위임한 곳으로 돌아온다"는 규칙은
  코드 어디에도 없다. LangGraph 1.2.11에서 실제로 확인했다(3절 실습 준비의
  설치 확인, 4-5절의 최소 재현 코드).

**슈퍼바이저를 고르는 이유**: 다음에 무엇을 할지 매번 중앙에서 판단하고
싶을 때, 진행 상황을 한곳에서 계속 관찰하고 싶을 때, 어떤 에이전트를
반복해서 다시 부를 수도 있을 때(예: verifier가 실패하면 investigator를
다시 부를 수도 있는 구조로 확장하기 쉽다).

**핸드오프를 고르는 이유**: 첫 번째로 요청을 받은 에이전트가 "이건 내
소관이 아니다"라고 판단하고, 그 뒤로는 자신이 관여할 필요가 없을 때(4-5절
"정상 핸드오프" 데모의 order_agent → shipping_agent가 이 예다). 중앙
조정자를 두지 않아도 되는 대신, 제어권이 어디로 갔는지 추적하는 책임이
없어진다 — 그래서 4-5·6-1절의 안전장치가 필요하다.

### 2-5. 병렬 작업과 결과 통합

"노트북이랑 모니터 매출 비교해줘"처럼 서로 관련 없는 조사 두 건은 순서대로
할 이유가 없다. 7단계 `retrieve_dense`/`retrieve_sparse`와 같은 방식으로,
조건부 간선이 두 노드 이름을 **리스트**로 돌려주면 LangGraph가 같은
슈퍼스텝에서 병렬로 실행한다(`multiagent/graph_parallel.py`의
`_route_fan_out`).

병렬로 쓰는 채널에는 리듀서가 필요하다. `investigations` 채널은
`merge_investigations`(제품명을 키로 하는 단순 dict 병합)를 쓴다 — 리듀서가
없으면(기본 리듀서는 "마지막 값이 이긴다") 두 병렬 결과 중 하나가 사라진다.
`tests/test_parallel.py`의
`test_merge_investigations_without_reducer_would_lose_data`가 이 위험을
직접 보여준다(실제로 리듀서 없이 그래프를 돌리는 대신, "마지막 값만 남았을
때 무엇이 사라지는지"를 값 비교로 보인다 — LangGraph 리듀서 자체는 채널
단위로 걸리므로 "리듀서를 뺀 그래프"를 별도로 만들어야 진짜 손실을 재현할
수 있는데, 이 장은 정상 동작 검증에 집중하고 그 별도 재현은 하지 않았다.
`[확인 필요: 리듀서를 제거한 그래프를 실제로 실행해 병렬 결과 유실을
재현하는 것]`).

### 2-6. 내부 하위 에이전트 호출과 A2A 프로토콜의 차이

**이 장에서 가장 헷갈리기 쉬운 지점**이다. "에이전트가 다른 에이전트를
부른다"는 말은 완전히 다른 두 가지를 가리킬 수 있다.

| | 내부 하위 에이전트 호출 | A2A(Agent2Agent) 프로토콜 |
| --- | --- | --- |
| 무엇이 경계를 넘는가 | 아무것도 넘지 않는다 — 파이썬 함수 호출(또는 같은 그래프의 노드 전이)이다 | 프로세스 경계, 보통 네트워크 경계(HTTP)를 넘는다 |
| 상태는 누가 들고 있는가 | 호출하는 쪽의 프로세스(이 장에서는 `CollabState`, 슈퍼바이저 그래프가 들고 있다) | 각 에이전트가 자기 상태를 스스로 들고 있다. 호출하는 쪽은 그 내부를 모른다 |
| 호출 방식 | `make_verifier_node(deps)(state)`처럼 함수를 직접 부른다 | 작업(Task)을 제출하고 결과를 폴링하거나 스트리밍으로 받는다 |
| 실패 모드 | 파이썬 예외 | 연결 실패, 타임아웃, 상대 프로세스가 응답을 안 함, 프로토콜 버전 불일치 |
| 발견(discovery) | import로 코드에 고정돼 있다 | Agent Card로 능력을 조회할 수 있다(공식 스펙 기준) |
| 배포·버전 | 한 배포 단위 안에 있다(이 코드를 배포하면 같이 배포된다) | 독립적으로 배포·재시작·버전업된다 |

이 장의 코드에서 이 차이를 정확히 대응시키면:

- **내부 호출**: `multiagent/agents.py`의 `make_verifier_node(deps)` —
  `CollabState`를 직접 읽고 쓰는 그래프 노드. `graph_supervisor.py`가
  `external_verifier=False`일 때 쓴다.
- **A2A 경계를 넘는 호출**: `multiagent/agents.py`의
  `make_verifier_node_a2a(deps)`는 계산을 하지 않는다. `a2a_min/client.py`로
  **별도 프로세스**(`a2a_min/server.py`, 독립된 `uvicorn` 프로세스)에 작업을
  제출하고 결과를 기다린다. `graph_supervisor.py`가
  `external_verifier=True`일 때 쓴다.

두 verifier 노드가 반환하는 `verification` 딕셔너리의 **모양은 거의
같다**(`ok`, `reason`, 재계산된 수치). 겉으로 보이는 결과가 비슷해 보여도
"이게 내부 호출인지 A2A 경계를 넘는 호출인지"는 코드로 명확히 구분해야
한다 — 5-2절에서 두 경로를 나란히 실행해 차이를 눈으로 확인한다.

**이 장에서 실제로 구현하는 범위**: `a2a_min/server.py`는 **A2A 프로토콜을
구현한 것이 아니다.** "별도 프로세스로 운영되는 에이전트에게 작업을 맡기고,
Agent Card로 자기소개를 하며, 상태를 공유하지 않는다"는 A2A의 핵심 아이디어
중 극히 일부만 흉내 낸 최소 예제다. 실제 A2A 프로토콜과 어디가 다른지는
2-7절에서 근거와 함께 밝힌다.

### 2-7. MCP의 도구 연결과 A2A의 에이전트 협업 차이

10단계는 MCP(Model Context Protocol)로 **도구**를 외부 서버에서 가져다
썼다. 이 장의 A2A 최소 흉내는 **에이전트**끼리 작업을 주고받는다. 겉보기엔
둘 다 "네트워크 너머의 무언가를 부른다"는 점에서 비슷해 보이지만, 다루는
대상 자체가 다르다.

| | 내부 하위 에이전트 호출 | MCP (10단계) | A2A (이 장, 최소 흉내) |
| --- | --- | --- | --- |
| 무엇을 주고받는가 | 파이썬 값(상태 일부) | **도구 호출**: 이름, 인자, 실행 결과 | **작업(Task)**: 목표와 맥락을 담은 메시지, 결과로 다시 메시지·산출물 |
| 상대는 무엇인가 | 같은 프로세스의 함수 | 도구 목록을 노출하는 서버 — 그 자체로는 판단하지 않는다 | 스스로 판단하고 계획하는 또 다른 에이전트 |
| 상대가 "판단"하는가 | 아니오(호출한 쪽이 이미 판단을 끝냈다) | 아니오 — 서버는 요청받은 함수를 실행할 뿐이다 | 그렇다 — 넘겨받은 작업을 그 에이전트가 자기 방식대로 해석하고 처리한다 |
| 누가 상태를 들고 있는가 | 호출하는 쪽 | 서버가 도구 실행에 필요한 상태(예: 검색 인덱스)를 들고 있을 순 있지만, "대화의 맥락"은 들고 있지 않는다 | 각 에이전트가 자기 대화·작업 상태를 들고 있다 |
| 경계를 넘는 것의 정체 | 없음(경계 자체가 없다) | 함수 호출의 원격 버전 | 두 자율적인 참여자 사이의 협상·위임에 가깝다 |

이 장의 verifier를 예로 들면: `verifier`가 `sum_sales` 같은 **도구**를
불렀다면 그건 MCP(또는 3단계의 로컬 도구 호출)의 영역이었을 것이다 — "판매
데이터를 조회해 달라"는 좁고 확정된 요청이기 때문이다. 대신 이 장은
verifier **에이전트 전체**를 별도 프로세스로 옮겼다 — "이 분석이 맞는지
판단해 달라"는, 그 자체로 해석과 판단이 필요한 작업을 넘겼다. 이 구분이
"무엇을 MCP로 빼고 무엇을 A2A로 빼는가"를 결정하는 실질적인 기준이다:
**확정된 함수 호출이면 MCP(또는 로컬 도구), 그 자체로 판단이 필요한
작업이면 A2A** 방향으로 생각한다.

**A2A 프로토콜의 현재 상태(2026-09-09 기준, 웹에서 확인)**: Agent2Agent(A2A)
프로토콜은 원래 Google이 2025년 4월에 발표했고, 2025년 6월 Google이 명세와
SDK를 Linux Foundation에 이관해 이후 Linux Foundation 산하 A2A 프로젝트가
벤더 중립적으로 관리한다. 명세는 2026년 4월에 버전 1.0이 나왔고(150개
이상 조직이 참여), 이 문서를 쓰는 시점 기준 최신은 2026년 5월의 1.0.1(확장
메커니즘 추가)이다. 공식 파이썬 구현은 PyPI의 `a2a-sdk` 패키지이고, 이
장을 쓰며 PyPI 페이지를 직접 확인한 최신 버전은 1.1.2(2026-07-22 릴리스,
Python 3.10+, Apache 2.0)다. `a2a-sdk`는 "A2A Protocol Specification 1.0을
구현하고 0.3과의 호환 모드를 제공한다"고 명시한다. 프로토콜은 Agent
Card(에이전트 발견용 메타데이터), Task/Message/Artifact 구조, JSON-RPC 2.0
기반 메시지 교환, 동기 요청-응답·SSE 스트리밍·비동기 푸시 알림을 지원한다.

**이 장이 실제로 구현한 범위**: `a2a_min/server.py`·`a2a_min/client.py`는
`a2a-sdk`를 설치하지도, JSON-RPC를 쓰지도, 표준 Agent Card 경로
(`/.well-known/agent-card.json`)를 쓰지도 않는다. 평범한 FastAPI REST
엔드포인트(`GET /agent-card`, `POST /tasks`, `GET /tasks/{id}`)로 "카드
조회 → 작업 제출 → 결과 폴링"이라는 뼈대만 흉내 냈다. **"A2A 프로토콜을
구현했다"고 쓰지 않는다** — 이것은 A2A가 다루는 문제(독립 프로세스로 운영되는
에이전트에게 작업을 위임하고, 상태를 공유하지 않고, 발견 가능한 메타데이터로
자기소개한다)를 최소한으로 흉내 낸 예제다. 실제 프로덕션에서 A2A를 쓰려면
`a2a-sdk`(또는 각 언어의 공식/커뮤니티 구현)로 Agent Card·JSON-RPC 메서드·
Task 상태 머신을 스펙대로 구현해야 한다.

### 2-8. langgraph-supervisor·langgraph-swarm을 쓰지 않은 이유

LangGraph 생태계에는 이 장이 다루는 두 패턴을 미리 만들어 둔 별도 패키지가
있다. 이 장을 쓰며 실제로 설치해 API를 확인했다(3절).

- `langgraph-supervisor`(설치 확인 버전 0.0.31): `create_supervisor(agents,
  model, ...)`, `create_handoff_tool(...)`.
- `langgraph-swarm`(설치 확인 버전 0.1.0): `create_swarm(agents,
  default_active_agent=...)`, `create_handoff_tool(...)`.

이 장은 둘 다 **쓰지 않았다.** 이유는 8단계가 LangGraph의 `interrupt()`
대신 직접 구현을 고른 이유(8단계 2-2절)와 같은 종류다.

1. 두 패키지의 `agents` 인자는 `langgraph.prebuilt.create_react_agent`로
   만든, **langchain 메시지 타입과 도구 호출 루프를 갖춘 완성된 에이전트**를
   기대한다(`create_supervisor`의 시그니처를 직접 확인했다 — `model:
   Runnable[..., BaseMessage]`). 이 프로젝트는 6단계까지는 LangChain을
   선택적으로 다루고 7단계부터는 `docagent.llm`의 얇은 OpenAI SDK
   래퍼(PROJECT-SPEC.md 9절)를 계속 쓴다. 이 장에서만 갑자기
   `langchain-openai`의 `ChatOpenAI`와 도구 호출 루프를 새로 들여오면, 이
   장의 주제(역할 분리·제어권 이동·루프 차단)와 무관한 새 의존성이 생긴다.
2. 이 두 패키지는 **핵심 메커니즘을 이 장이 이미 쓰고 있는 LangGraph의
   기본 도구**(`StateGraph`, `Command(goto=...)`, 조건부 간선)로
   구현한다 — 즉 "새로운 개념"이 아니라 "이미 배운 개념의 조합에 이름을
   붙이고 표준 인터페이스를 씌운 것"이다. 그 조합을 직접 만들어 보는 것이
   "슈퍼바이저 위임과 핸드오프가 정확히 무엇을 대신 해주는지" 판단하는 데
   더 도움이 된다(8단계 2-2절과 같은 논리).
3. 이 저장소의 스텁 서버(`fake_openai_server.py`)는 "요청이 있으면 tools의
   첫 항목을 한 번만 호출한다"는 단순한 동작만 한다(8단계 5-3절에서 이미
   확인한 한계). `create_react_agent`가 기대하는 반복적인 도구 호출
   루프(도구 결과를 보고 또 도구를 부를지 말지 모델이 판단하는 과정)를 이
   스텁으로 검증하기 어렵다 — 검증하지 못하는 코드를 싣지 않는다는
   WRITING-GUIDE.md의 원칙에 따라 뺐다.

`[확인 필요: langgraph-supervisor/langgraph-swarm을 실제 도구 호출 지원
모델 서버로 끝까지 실행해 이 장의 직접 구현과 동작을 비교하는 것]` — 이
장은 두 패키지를 설치해 시그니처와 존재를 확인했을 뿐, 실제로 그래프를
만들어 실행하지는 않았다.

### 2-9. 상호 위임 루프·실패·비용 제한

8단계는 "같은 (도구, 인자) 조합 3회 반복"과 "인자는 다른데 결과가 안
바뀌는 반복"이라는 두 종류의 반복을 감지했다. 이 장은 세 번째 패턴을
추가한다: **에이전트끼리 제어권을 주고받다가 아무도 실제 작업을 진전시키지
못하는 반복**이다.

- 슈퍼바이저 패턴에서는 이 문제가 자연히 덜하다 — 슈퍼바이저가 매번
  판단을 다시 하므로, "다음엔 뭘 할지" 정하는 주체가 하나로 고정돼 있다.
  그래도 슈퍼바이저 자체가 계속 같은 선택을 반복할 수 있으므로, 협업
  전체의 단계 수(`hops_used`)와 토큰(`tokens_used`)에 여전히 상한을 둔다
  (`multiagent/budget.py`의 `check_budget`).
- 핸드오프 패턴은 이 문제에 훨씬 취약하다 — 아무도 전체를 조망하지 않기
  때문이다. `multiagent/handoff.py`의 `detect_delegation_loop()`가 8단계
  2-4절의 두 반복 감지와 같은 발상을 핸드오프 사슬에 옮긴다.

  1. **하드 캡**(`MAX_AGENT_HANDOFFS`): 사슬 길이가 이 값을 넘으면
     무조건 멈춘다. 8단계 `MAX_AGENT_STEPS`가 반복 감지의 최종 백스톱
     역할을 한 것과 같다.
  2. **핑퐁 패턴**: 최근 6번의 핸드오프가 "A, B, A, B, A, B"처럼 정확히
     두 참가자가 번갈아 나타나면 멈춘다. 8단계의 "결과-무변화 반복
     감지"(인자는 다른데 결과 해시가 안 바뀐다)와 같은 아이디어를 "누가
     받았는지는 계속 바뀌지만 전체 참가자 집합이 둘로 고정돼 왔다갔다한다"는
     형태로 옮긴 것이다.

  두 조건 중 하나라도 걸리면 `finish_reason="delegation_loop"`로 멈춘다
  (PROJECT-SPEC.md 7절에 추가). `repeated_tool_call`을 재사용하지 않은
  이유는 8단계가 `token_budget`을 새로 추가한 이유와 같다 — 이미 있는 값을
  재사용하면 클라이언트가 "도구 호출이 반복된 것"과 "에이전트 위임이
  반복된 것"을 구분할 수 없다.

**비용(토큰) 제한은 협업 전체 단위로 건다.** 8단계의 `MAX_AGENT_TOKENS`는
"에이전트 하나의 run"을 단위로 삼았다. 이 장은 슈퍼바이저 + investigator +
analyst + verifier 넷의 모델 호출을 전부 더한 값을 `MAX_COLLAB_TOKENS` 하나로
관리한다(`CollabState.tokens_used`, `budget.add_usage()`가 매 노드 호출마다
누적한다). 이름을 `MAX_AGENT_TOKENS`로 재사용하지 않은 이유는 PROJECT-SPEC.md
9절의 "이미 있는 필드의 이름은 바꾸지 않고 필요한 필드를 추가만 한다"는
원칙과, "예산의 단위 자체가 달라졌다"는 사실을 이름에서 드러내기 위해서다.

## 3. 실습 준비

### 3-1. 패키지와 버전 (실제로 설치해 확인)

```bash
python3 -m venv /tmp/v11
python3 -m venv /tmp/v11 && /tmp/v11/bin/pip install -q langgraph langchain \
    langgraph-supervisor langgraph-swarm openai fastapi uvicorn pydantic httpx \
    python-dotenv pytest
/tmp/v11/bin/pip list | grep -iE "langgraph|langchain|openai|fastapi|pydantic|httpx"
```

이 문서를 쓰며 실제로 설치해 확인한 버전(2026-09-09):

| 패키지 | 확인한 버전 |
| --- | --- |
| `langgraph` | 1.2.11 |
| `langgraph-checkpoint` | 4.2.0 |
| `langgraph-prebuilt` | 1.1.0 |
| `langgraph-sdk` | 0.4.4 |
| `langgraph-supervisor` | 0.0.31 |
| `langgraph-swarm` | 0.1.0 |
| `langchain` | 1.4.0 |
| `langchain-core` | 1.6.2 |
| `openai` | 3.10.0 |
| `fastapi` | 0.141.1 |
| `pydantic` | 2.13.5 |
| `httpx` | 0.28.1 |

검토자가 확인한 기준선(`langgraph` 1.2.11, `langchain` 1.4.0)과 일치한다.
`langgraph-supervisor`·`langgraph-swarm`은 2-8절에서 설명한 이유로 이 장의
코드가 실제로 의존하지는 않는다 — API 확인 목적으로만 설치했다.

이 장의 코드가 실제로 쓰는 패키지는 `code/step11_multi_agent/requirements.txt`에
있다: `langgraph`(State/Node/Edge + `Command(goto=...)`), `openai`(모델 서버
호출), `fastapi`+`uvicorn`(SSE 엔드포인트와 A2A 최소 흉내 서버 둘 다),
`httpx`(A2A 클라이언트), `pydantic`, `python-dotenv`, `pytest`.

```bash
cd code/step11_multi_agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3-2. 환경 변수

PROJECT-SPEC.md 2절에 이 장에서 추가한 세 값(추가만 했다):

```
MAX_AGENT_HANDOFFS=4          # 핸드오프 사슬의 하드 캡
MAX_COLLAB_TOKENS=6000        # 협업 전체(모든 에이전트 합산) 누적 토큰 예산
A2A_VERIFIER_URL=http://127.0.0.1:8292   # A2A 최소 흉내 검증 에이전트 주소
```

### 3-3. 폴더 구조

```
code/step11_multi_agent/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── config.py            # Settings + 11단계 필드 3개
│   ├── llm.py                 # PROJECT-SPEC.md 9절 인터페이스(chat/achat/chat_json)
│   ├── events.py               # SSE 이벤트 + finish_reason=delegation_loop
│   ├── salesdata.py              # sales.csv 접근(3단계 tools.py 로직 재사용)
│   ├── app.py                     # FastAPI: POST /collab/supervisor
│   ├── multiagent/
│   │   ├── state.py                 # CollabState: 공유 상태 + 에이전트별 컨텍스트
│   │   ├── budget.py                 # 협업 전체 단위 예산 검사
│   │   ├── agents.py                  # investigator/analyst/verifier(내부·A2A)/finish
│   │   ├── supervisor.py               # 슈퍼바이저 노드
│   │   ├── graph_supervisor.py          # 슈퍼바이저 그래프 조립
│   │   ├── handoff.py                    # Command(goto=...) 핸드오프 + 루프 감지
│   │   └── graph_parallel.py              # 병렬 조사 + merge_investigations
│   └── a2a_min/
│       ├── server.py                        # A2A 최소 흉내 서버(별도 프로세스로 실행)
│       └── client.py                         # 그 서버를 부르는 클라이언트
├── data/sales.csv                            # 8단계와 동일한 데이터
├── scripts/
│   ├── demo_supervisor.py    # 내부 검증 vs A2A 검증을 나란히 실행
│   ├── demo_parallel.py       # 병렬 조사 + 통합
│   ├── demo_handoff.py         # 정상 핸드오프 vs 상호 위임 루프 차단
│   └── demo_budget.py           # 협업 단위 예산 초과
└── tests/                        # pytest 21개
```

### 3-4. 샘플 데이터

`data/sales.csv`는 8단계의 것을 그대로 가져왔다(제품 4종 × 지역 × 2개
분기, 48행). 이 장은 문서 검색(RAG)을 다루지 않으므로 `data/docs/`나
Milvus는 두지 않았다 — 4-2절의 investigator는 문서가 아니라 CSV 원본 행을
조사 대상으로 삼는다.

## 4. 단계별 구현

앞서 2-1절에서 밝혔듯, 이 장의 요청("노트북 매출이 왜 늘었는지 조사해줘")은
사실 도구 호출 두세 번으로 끝날 수 있는 일이다. 여기서는 **역할 분리·제어권
이동·경계 넘기를 눈으로 보기 위해** 일부러 세 에이전트로 나눈다 — 실제
프로젝트에서 이 정도 작업까지 나눌지는 2-1절의 판단 기준으로 따로
결정한다.

### 4-1. 협업 상태: 공유 상태와 에이전트별 컨텍스트를 같은 타입에 담는다

```python
# code/step11_multi_agent/docagent/multiagent/state.py (발췌)
class CollabState(TypedDict):
    request: str

    # 공유 상태(블랙보드): 누구나 읽고, 담당 에이전트만 쓴다
    investigation: dict[str, Any]
    analysis: dict[str, Any]
    verification: dict[str, Any]
    shared_notes: Annotated[list[str], operator.add]

    # 에이전트별 컨텍스트: 그 에이전트만 쓰고 읽는다
    investigator_context: Annotated[list[dict[str, Any]], operator.add]
    analyst_context: Annotated[list[dict[str, Any]], operator.add]
    verifier_context: Annotated[list[dict[str, Any]], operator.add]

    # 제어 흐름
    next_agent: str
    handoff_chain: Annotated[list[str], operator.add]

    # 예산(협업 전체 단위)
    hops_used: int
    tokens_used: int
    tokens_estimated: bool
```

`investigation`/`analysis`/`verification`은 그 값을 쓰는 에이전트가
한 번씩만 채우므로 기본 리듀서(마지막 값이 이긴다)로 충분하다. `shared_notes`,
`*_context`, `handoff_chain`은 여러 노드가 순서대로 追加하므로
`operator.add`(리스트 이어붙이기)를 쓴다 — 7단계 `GraphState`의 `trace`와
같은 이유다.

### 4-2. investigator: 요청에서 조사 대상을 뽑고 원본 데이터를 찾는다

```python
# code/step11_multi_agent/docagent/multiagent/agents.py (발췌)
def make_investigator_node(deps: AgentDeps) -> Callable[[CollabState], dict]:
    def investigator(state: CollabState) -> dict:
        request = state["request"]
        known = salesdata.known_products()

        parsed, tokens, estimated = _call_json(
            deps,
            [{"role": "system", "content": f"...가능한 값: {known}..."},
             {"role": "user", "content": request}],
            schema_name="pick_product", schema=_INVESTIGATOR_SCHEMA,
        )
        product = parsed.get("product")
        source = "model"
        if not isinstance(product, str) or product not in known:
            product = next((p for p in known if p in request), known[0])
            source = "heuristic"

        rows = salesdata.rows_for_product(product)
        investigation = {"product": product, "row_count": len(rows), "rows": rows, "source": source}
        ...
        return {
            "investigation": investigation,
            "shared_notes": [note],
            "investigator_context": [{"role": "assistant", "content": note}],
            "hops_used": state["hops_used"] + 1,
            "trace": [_trace("retrieving", note)],
            **add_usage(state, tokens, estimated),
        }
    return investigator
```

7단계 `classify` 노드와 같은 패턴이다: 모델이 실패하거나 스키마 밖의 값을
주면(이 장의 스텁 서버는 항상 `{"answer":"42","confidence":0.9}`를 돌려주므로
실제로 항상 이 경로를 탄다 — 5절에서 확인한다) 요청 문자열에 제품명이
그대로 들어 있는지 보는 규칙으로 대체한다.

### 4-3. analyst와 verifier: 같은 데이터를 서로 다른 목적으로 두 번 계산한다

```python
# code/step11_multi_agent/docagent/multiagent/agents.py (발췌, analyst)
totals = salesdata.quarterly_totals(rows)
q1, q2 = totals.get("Q1", {...}), totals.get("Q2", {...})
heuristic_trend = "increase" if q2["revenue"] > q1["revenue"] else \
                   "decrease" if q2["revenue"] < q1["revenue"] else "flat"
# ... 모델에게 판단을 묻고, 실패하면 heuristic_trend를 쓴다
```

```python
# (발췌, verifier)
recomputed = salesdata.quarterly_totals(rows)  # investigation의 원본 행에서 "다시" 계산
numbers_match = recomputed["Q1"] == analysis.get("q1") and recomputed["Q2"] == analysis.get("q2")
# ... 모델에게 검증을 묻고, 실패하면 numbers_match를 쓴다
```

verifier가 `analysis`의 숫자를 그대로 믿지 않고 `investigation.rows`에서
**다시** `quarterly_totals()`를 부르는 것이 검증의 핵심이다 — 6-1절에서
analyst가 잘못된 숫자를 보고하는 상황을 만들어 verifier가 잡아내는 것을
확인한다.

### 4-4. 슈퍼바이저: 위임하고, 제어권이 돌아오면 다시 판단한다

```python
# code/step11_multi_agent/docagent/multiagent/supervisor.py (발췌)
def make_supervisor_node(deps: AgentDeps) -> Callable[[CollabState], dict]:
    def supervisor(state: CollabState) -> dict:
        summary = f"investigation={'있음' if state.get('investigation') else '없음'}, ..."
        parsed, tokens, estimated = _call_json(deps, [...], schema_name="route_next", schema=_ROUTE_SCHEMA)
        next_agent = parsed.get("next")
        source = "model"
        if next_agent not in ("investigator", "analyst", "verifier", "finish"):
            next_agent = _heuristic_next(state)  # 아직 안 채워진 공유 상태를 순서대로 채운다
            source = "heuristic"
        return {"next_agent": next_agent, "route_source": source, ...}
    return supervisor
```

```python
# code/step11_multi_agent/docagent/multiagent/graph_supervisor.py (발췌)
graph.add_edge(START, "supervisor")
graph.add_conditional_edges("supervisor", make_route_after_supervisor(deps), {
    "investigator": "investigator", "analyst": "analyst", "verifier": "verifier",
    "finish": "finish", "budget_exceeded": "budget_exceeded",
})
graph.add_edge("investigator", "supervisor")   # 셋 다 오직 supervisor로만 이어진다
graph.add_edge("analyst", "supervisor")
graph.add_edge("verifier", "supervisor")
```

`make_route_after_supervisor`는 다음 목적지를 정하기 전에 항상
`check_budget()`을 먼저 본다 — 예산을 넘었으면 슈퍼바이저의 판단(모델이든
휴리스틱이든)과 무관하게 `budget_exceeded`로 보낸다.

### 4-5. 핸드오프: `Command(goto=...)`로 직접 넘기고, 사슬을 스스로 추적한다

```python
# code/step11_multi_agent/docagent/multiagent/handoff.py (발췌)
def make_handoff_agent_node(name, other, decide, settings):
    def node(state: CollabState) -> Command:
        loop_reason = detect_delegation_loop(state["handoff_chain"], settings.max_agent_handoffs)
        if loop_reason is not None:
            return Command(goto="loop_stopped", update={"finish_reason": loop_reason, ...})

        action, note = decide(state)  # 이 에이전트의 판단(모델 또는 규칙)
        base_update = {"handoff_chain": [name], "hops_used": state["hops_used"] + 1, ...}
        if action == "resolve":
            return Command(goto="finish", update={**base_update, "final_text": note, "finish_reason": "stop"})
        return Command(goto=other, update=base_update)   # 곧장 상대 에이전트로 넘어간다
    return node
```

`Command(goto=...)`는 `add_edge(name, other)` 같은 고정 간선 없이도 실행
흐름을 바꾼다 — LangGraph 1.2.11에서 아래 최소 예제로 직접 확인했다(2-4절이
가리키는 근거).

```python
# 최소 재현(이 장을 쓰며 실제로 실행해 확인)
def a(state):
    return Command(goto="b", update={"hop": state["hop"] + 1})
def b(state):
    return Command(goto="a", update={"hop": state["hop"] + 1})
# add_edge(a, b)나 add_edge(b, a) 없이도 a <-> b가 서로를 오간다
```

`detect_delegation_loop`는 2-9절의 두 조건(하드 캡, 핑퐁 패턴)을 그대로
코드로 옮긴 것이다.

```python
# code/step11_multi_agent/docagent/multiagent/handoff.py (발췌)
def detect_delegation_loop(chain: list[str], max_handoffs: int) -> str | None:
    if len(chain) > max_handoffs:
        return "delegation_loop"
    if len(chain) >= 6:
        window = chain[-6:]
        a, b = window[0], window[1]
        if a != b and window == [a, b, a, b, a, b]:
            return "delegation_loop"
    return None
```

### 4-6. 병렬 조사와 결과 통합

```python
# code/step11_multi_agent/docagent/multiagent/graph_parallel.py (발췌)
def _route_fan_out(state: CollabState) -> list[str]:
    known = salesdata.known_products()
    mentioned = [p for p in known if p in state["request"]]
    targets = mentioned[:2] if len(mentioned) >= 2 else known[:2]
    return [f"investigate__{p}" for p in targets]   # 리스트를 돌려주면 병렬 실행된다

# 그래프 조립 시점에 가능한 제품을 전부 노드로 미리 등록해 둔다 —
# LangGraph는 컴파일 시점에 없는 노드로는 갈 수 없다.
for p in salesdata.known_products():
    graph.add_node(f"investigate__{p}", _make_investigate_node(p))
    graph.add_edge(f"investigate__{p}", "combine")
graph.add_conditional_edges("fan_out", _route_fan_out)
```

`investigations` 채널은 `merge_investigations`(제품명 키로 dict 병합)
리듀서를 쓴다(2-5절).

### 4-7. 같은 verifier를 내부 호출과 A2A 경계 너머로 바꿔 끼운다

```python
# code/step11_multi_agent/docagent/multiagent/agents.py (발췌)
def make_verifier_node_a2a(deps: AgentDeps) -> Callable[[CollabState], dict]:
    from docagent.a2a_min import client as a2a_client

    def verifier_a2a(state: CollabState) -> dict:
        investigation, analysis = state.get("investigation") or {}, state.get("analysis") or {}
        try:
            result = a2a_client.submit_and_wait(
                deps.settings.a2a_verifier_url,
                skill_id="verify_quarterly_analysis",
                task_input={"analysis": analysis, "rows": investigation.get("rows", [])},
            )
            source = "a2a_agent"
        except a2a_client.A2AClientError as exc:
            result = {"ok": False, "reason": f"외부 검증 에이전트 호출 실패: {exc}"}
            source = "a2a_agent_unreachable"
        return {"verification": {**result, "source": source}, ...}
    return verifier_a2a
```

```python
# code/step11_multi_agent/docagent/multiagent/graph_supervisor.py (발췌)
def build_supervisor_graph(deps: AgentDeps, *, external_verifier: bool = False) -> CompiledStateGraph:
    ...
    graph.add_node("verifier",
        make_verifier_node_a2a(deps) if external_verifier else make_verifier_node(deps))
```

그래프의 나머지 구조(슈퍼바이저가 위임하고 제어권이 돌아오는 것)는
바뀌지 않는다 — 바뀌는 것은 verifier 노드 **안에서** 계산이 일어나는
위치뿐이다. 이것이 2-6절 표를 코드로 만든 지점이다.

외부 에이전트 쪽(`a2a_min/server.py`)은 FastAPI 앱 하나다.

```python
# code/step11_multi_agent/docagent/a2a_min/server.py (발췌)
@app.get("/agent-card")
def get_agent_card() -> dict[str, Any]:
    return AGENT_CARD   # name, description, skills, 그리고 "이건 A2A 준수 카드가 아니다"라는 note

@app.post("/tasks")
def submit_task(submission: TaskSubmission) -> dict[str, Any]:
    task_id = f"task_{next(_counter)}_{uuid.uuid4().hex[:8]}"
    result = _verify(submission.input)   # 이 최소 흉내는 백그라운드 큐 없이 즉시 계산한다
    _TASKS[task_id] = {"status": "completed", "result": result}
    return {"task_id": task_id, "status": "submitted"}

@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    ...
```

## 5. 실행과 결과 확인

### 5-1. 단위 테스트 (모델 서버 없이)

```bash
cd code/step11_multi_agent
PYTHONPATH=. pytest tests -q
```

실행 결과(이 문서 작성 시 실제로 돌려 확인함):

```
.....................
21 passed, 1 warning in 54.20s
```

(경고 1개는 `starlette.testclient`의 `anyio` deprecation 경고이지 이 장의
코드 문제가 아니다.) `test_supervisor.py`가 역할 분리·제어권 복귀·예산
한도를, `test_handoff_loop.py`가 정상 핸드오프와 상호 위임 루프 차단
(하드 캡·핑퐁 패턴 둘 다)을, `test_parallel.py`가 병렬 조사와 리듀서를,
`test_a2a_min.py`가 A2A 최소 흉내 서버의 카드·작업 제출·폴링을,
`test_verifier_boundary.py`가 내부/A2A 두 verifier의 결과 모양이 같고,
외부 에이전트가 응답하지 않아도 협업 전체가 죽지 않는 것을 확인한다.
`test_token_budget_stops_collaboration`은 모델 서버가 없으면 실제 토큰
사용량이 항상 0이라는 점(모든 호출이 즉시 실패해 휴리스틱으로 넘어가고,
`_call_json`이 실패 시 토큰을 더하지 않는다) 때문에 "이미 예산을 다 쓴
상태에서 시작하는 협업"을 만들어 그래프의 조건부 간선이 실제로
`check_budget()`을 호출해 멈추는지를 검증한다 — 처음에는 `tokens_used`를
0에서 출발시켰다가 이 이유로 실패하는 것을 실제로 확인하고 고쳤다.

### 5-2. 스텁 서버 + A2A 최소 흉내 서버로 전체 흐름 실행

**터미널 1: 스텁 모델 서버**

```bash
python code/_tools/fake_openai_server.py --port 8291
```

**터미널 2: A2A 최소 흉내 검증 에이전트(별도 프로세스)**

```bash
cd code/step11_multi_agent
PYTHONPATH=. python -m docagent.a2a_min.server --port 8292
```

```bash
curl -s http://127.0.0.1:8292/agent-card
```

```json
{"name":"verifier-agent-min","description":"매출 분석 결과를 원본 데이터로 재검증하는 독립 에이전트(최소 흉내 구현).","version":"0.1.0-min-imitation","skills":[{"id":"verify_quarterly_analysis","description":"..."}],"note":"이것은 A2A 프로토콜 준수 Agent Card가 아니다. 개념을 보이기 위한 최소 흉내다."}
```

**내부 협업 vs A2A 검증을 나란히 실행**(이 문서 작성 시 실제로 실행해 받은 출력):

```bash
OPENAI_BASE_URL=http://127.0.0.1:8291/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=stub-model \
A2A_VERIFIER_URL=http://127.0.0.1:8292 \
PYTHONPATH=. python scripts/demo_supervisor.py
```

```
=== 내부 협업(같은 프로세스): '노트북 매출이 왜 늘었는지 조사해줘' ===
 - [supervisor] 다음 위임 대상: investigator (판단 주체: heuristic)
 - [investigator] 제품 '노트북'의 판매 행 12건을 찾았다 (판단 주체: heuristic)
 - [supervisor] 다음 위임 대상: analyst (판단 주체: heuristic)
 - [analyst] 추세 판정: decrease (Q1 매출 1405200000에서 Q2 매출 1347600000로 변했다, 판단 주체: heuristic)
 - [supervisor] 다음 위임 대상: verifier (판단 주체: heuristic)
 - [verifier] 검증 결과: 통과 (재계산한 분기별 합계가 analyst 보고와 일치한다, 판단 주체: heuristic)
 - [supervisor] 다음 위임 대상: finish (판단 주체: heuristic)
finish_reason: stop
hops_used: 8  tokens_used: 105  estimated: False

=== A2A(외부 프로세스) 검증: '모니터 매출 추세를 검증해줘' ===
 - [supervisor] 다음 위임 대상: investigator (판단 주체: heuristic)
 - [investigator] 제품 '모니터'의 판매 행 12건을 찾았다 (판단 주체: heuristic)
 - [supervisor] 다음 위임 대상: analyst (판단 주체: heuristic)
 - [analyst] 추세 판정: decrease (Q1 매출 346750000에서 Q2 매출 344250000로 변했다, 판단 주체: heuristic)
 - [supervisor] 다음 위임 대상: verifier (판단 주체: heuristic)
 - [verifier(A2A)] 검증 결과: 통과 (재계산한 분기별 합계가 analyst 보고와 일치한다, 판단 주체: a2a_agent)
 - [supervisor] 다음 위임 대상: finish (판단 주체: heuristic)
finish_reason: stop
hops_used: 8  tokens_used: 90  estimated: False
```

`판단 주체: heuristic`이 계속 나오는 이유는 8단계 5-3절과 같다 — 이
저장소의 스텁 서버는 `response_format`으로 요청한 스키마와 무관하게 고정된
`{"answer":"42","confidence":0.9}`만 돌려주므로, 이 장의 모든 `chat_json`
호출이 스키마 검증에 실패해 규칙 기반으로 넘어간다. `[verifier(A2A)]` 줄의
`판단 주체: a2a_agent`만 다르다 — 이건 모델 판단이 아니라 "이 결과를 만든
게 이 프로세스가 아니라 외부 에이전트"라는 뜻으로 쓴 것이다(2-6절 표의
구분과 같다).

**터미널 3: 이 장의 FastAPI 앱을 띄우고 SSE로 확인**

```bash
cd code/step11_multi_agent
OPENAI_BASE_URL=http://127.0.0.1:8291/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=stub-model \
A2A_VERIFIER_URL=http://127.0.0.1:8292 \
uvicorn docagent.app:app --port 8180 --app-dir .
```

```bash
curl -sN http://127.0.0.1:8180/collab/supervisor -H 'content-type: application/json' \
  -d '{"message":"노트북 매출이 왜 늘었는지 조사해줘"}'
```

실제로 받은 응답(발췌, 이 문서 작성 시 실행):

```
event: status
data: {"stage": "planning", "message": "[supervisor] 다음 위임 대상: investigator (판단 주체: heuristic)"}

event: status
data: {"stage": "retrieving", "message": "[investigator] 제품 '노트북'의 판매 행 12건을 찾았다 (판단 주체: heuristic)"}

... (중략, analyst/verifier도 같은 패턴) ...

event: done
data: {"finish_reason": "stop", "partial": {"text": "노트북의 매출은 decrease 추세다. Q1 매출 1405200000에서 Q2 매출 1347600000로 변했다", "steps_used": 8, "tokens_used": 105, "tokens_estimated": false}}
```

`status.stage` 값이 PROJECT-SPEC.md 4절의 고정된 값(`planning`,
`retrieving`, `analyzing`, `verifying`, `writing`)만 쓴다 — 새 stage 값이
필요 없었다(2-9절과 달리 finish_reason은 확장이 필요했지만 stage는
그대로 맞아떨어졌다).

### 5-3. 병렬 조사(모델 서버 불필요)

```bash
PYTHONPATH=. python scripts/demo_parallel.py
```

```
조사된 제품: ['노트북', '모니터']
병렬 조사 결과를 통합했다.
노트북: Q1 1405200000 -> Q2 1347600000 (감소)
모니터: Q1 346750000 -> Q2 344250000 (감소)
```

`investigations`에 두 제품이 모두 남아 있다 — 리듀서가 병렬 결과를
잃지 않고 병합했다는 뜻이다.

### 5-4. 핸드오프: 정상 흐름과 상호 위임 루프 차단(모델 서버 불필요)

```bash
PYTHONPATH=. python scripts/demo_handoff.py
```

```
=== 시나리오 1: 정상적인 핸드오프 한 번 (order_agent -> shipping_agent) ===
handoff_chain: ['order_agent', 'shipping_agent']
finish_reason: stop
확인: order_agent로 제어권이 돌아오지 않고 shipping_agent가 곧장 마무리했다.

=== 시나리오 2: 상호 위임 루프 (agent_a <-> agent_b, 둘 다 서로에게 떠넘긴다) ===
handoff_chain: ['agent_a', 'agent_b', 'agent_a', 'agent_b', 'agent_a']
finish_reason: delegation_loop
확인: 두 에이전트가 서로 5번 떠넘긴 뒤 delegation_loop로 멈췄다.
```

시나리오 2는 `MAX_AGENT_HANDOFFS`(기본 4)의 하드 캡이 먼저 걸렸다(사슬
길이 5 > 4). `MAX_AGENT_HANDOFFS=20`으로 올려서 다시 실행하면 하드 캡보다
핑퐁 패턴 감지가 먼저 걸리는 것도 확인했다(`chain` 길이 6에서 멈춘다) —
두 감지 로직이 서로 독립적으로 동작한다는 뜻이다.

### 5-5. 협업 단위 예산 한도(모델 서버 불필요)

```bash
PYTHONPATH=. python scripts/demo_budget.py
```

```
=== 협업 단계 한도(MAX_AGENT_STEPS) 초과 ===
MAX_AGENT_STEPS=8, hops_used=9 -> max_steps
=== 협업 토큰 예산(MAX_COLLAB_TOKENS) 초과 ===
MAX_COLLAB_TOKENS=6000, tokens_used=6001 -> token_budget
```

## 6. 실패 상황 실습

### 6-1. analyst가 틀린 숫자를 보고했을 때 verifier가 잡아내는지

`analysis.q1`을 일부러 실제 값과 다르게 조작한 상태에서 verifier 노드만
직접 불러 확인한다.

```python
from docagent.multiagent.agents import AgentDeps, make_verifier_node
from docagent.config import load_settings

deps = AgentDeps(settings=load_settings())
state = {  # investigation은 실제 원본, analysis만 조작
    "investigation": {"rows": [...실제 12행...]},
    "analysis": {"q1": {"units": 0, "revenue": 0}, "q2": {"units": 0, "revenue": 0}},  # 틀린 값
    "hops_used": 0,
    "tokens_used": 0,
    "tokens_estimated": False,
}
result = make_verifier_node(deps)(state)
print(result["verification"])
# {'ok': False, 'reason': '재계산한 합계가 analyst 보고와 다르다', 'recomputed_q1': {...실제 값...}, ...}
```

이 경로는 `tests/test_supervisor.py`에는 없다(정상 analyst 판정만
테스트했다) — 이 절의 재현은 이 문서를 위해 대화형으로 직접 실행해
확인했을 뿐 별도 pytest 케이스로 저장하지는 않았다. `[확인 필요: 이
불일치 경로를 정식 pytest 테스트로 추가하는 것]`.

### 6-2. A2A 최소 흉내 서버가 응답하지 않을 때

`A2A_VERIFIER_URL`을 아무도 듣지 않는 주소로 두고 실행한다.

```bash
OPENAI_BASE_URL=http://127.0.0.1:8291/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=stub-model \
A2A_VERIFIER_URL=http://127.0.0.1:1 \
PYTHONPATH=. python -c "
from docagent.config import load_settings
from docagent.multiagent.agents import AgentDeps
from docagent.multiagent.graph_supervisor import build_supervisor_graph, initial_state
deps = AgentDeps(settings=load_settings())
g = build_supervisor_graph(deps, external_verifier=True)
result = g.invoke(initial_state('노트북 매출이 왜 늘었는지 조사해줘'), config={'recursion_limit': 50})
print(result['finish_reason'], result['verification'])
"
```

실제 실행 결과:

```
stop {'ok': False, 'reason': '외부 검증 에이전트 호출 실패: 작업 제출 실패(http://127.0.0.1:1): [Errno 111] Connection refused', 'source': 'a2a_agent_unreachable'}
```

협업 전체는 `stop`으로 끝난다(연결 실패가 곧 전체 실패는 아니다) — 다만
`verification.ok=False`이므로 `finish` 노드가 만드는 최종 답변에는 "검증
단계에서 불일치가 발견됐다"는 문구가 섞여 나간다(`agents.py`의
`make_finish_node`). 이것이 3·7·8단계와 같은 원칙("외부 호출 실패가 곧
애플리케이션 실패는 아니다")이 A2A 경계에도 그대로 적용된 것이다. 실제
서비스라면 "검증이 통과해서 불충분이 아니라, 검증 자체를 못 했다"는 사실을
`verification.source == "a2a_agent_unreachable"`로 구분해 사용자에게 다르게
보여줘야 한다 — 이 장의 `finish` 노드는 그 구분까지는 하지 않는다(단순화).

### 6-3. 상호 위임 루프 (완료 기준의 핵심)

5-4절이 이미 이 실습이다. 안전장치 없이 실행하면 어떻게 될지도 짚어본다:
`detect_delegation_loop`를 부르지 않게 주석 처리하고 같은 시나리오 2를
`recursion_limit=1000`으로 돌리면, LangGraph 자체의 재귀 한도
(`GraphRecursionError`)에 부딪힐 때까지 두 에이전트가 계속 서로에게
떠넘긴다 — 즉 이 장의 안전장치가 없으면 "그래프 실행기가 강제로 막을
때까지" 멈추지 않는다. `[확인 필요: 이 "안전장치 제거" 버전을 실제로
실행해 GraphRecursionError가 발생하는 것까지 재현하는 것]` — 이 문서는
안전장치가 있을 때의 정상 동작만 재현했고, 제거했을 때의 실패까지는
별도로 실행하지 않았다.

## 7. 응용 과제

1. **네 번째 에이전트 추가**: "요약(summarizer)" 에이전트를 만들어
   슈퍼바이저 그래프에 끼워 넣어 보라(`verifier` 다음, `finish` 이전).
   완료 기준: `shared_notes`에 `[summarizer]`로 시작하는 노트가 남고,
   기존 investigator/analyst/verifier의 컨텍스트 분리가 깨지지 않는다.
2. **핑퐁 패턴 창 크기 바꾸기**: `detect_delegation_loop`의 판정 창(현재
   최근 6개, 즉 패턴 3회 반복)을 4(패턴 2회 반복)로 줄이면 몇 번의 핸드오프
   만에 멈추는지 `demo_handoff.py`로 확인해 보라. 완료 기준: 창 크기와
   멈추기까지의 핸드오프 횟수 사이의 관계를 자기 말로 설명할 수 있다.
3. **3인 이상 핑퐁 감지**: 2-9절 끝에서 언급했듯 현재 `detect_delegation_loop`는
   참가자가 정확히 둘일 때만 핑퐁을 잡는다. 세 에이전트(A→B→C→A→B→C→...)가
   도는 경우도 잡도록 고쳐 보라. 힌트: 윈도우 크기를 참가자 수의 배수로
   일반화하고, 그 윈도우 안에 서로 다른 이름이 정확히 그 개수만큼만
   나타나는지 확인한다. 완료 기준: 3인 순환을 만드는 테스트를 새로 추가해
   통과시킨다.
4. **A2A 최소 흉내에 재시도 추가**: `a2a_min/client.py`의
   `submit_and_wait`에 지수 백오프 재시도를 추가해 보라(7단계
   `_search_with_retry`를 참고). 완료 기준: 서버가 일시적으로 느릴 때
   (`time.sleep`으로 흉내) 첫 시도가 타임아웃 나도 재시도로 성공하는 것을
   테스트로 확인한다.

## 8. 핵심 정리와 확인 질문

### 핵심 정리

- 멀티에이전트는 항상 이득이 아니다. 컨텍스트 전달 비용·지연·실패 지점이
  에이전트 수만큼 늘어난다. 역할별 프롬프트가 뚜렷이 다르거나, 병렬화할
  독립 작업이 있거나, 신뢰 경계가 실제로 다를 때 나누는 이득이 비용을
  넘는다.
- 슈퍼바이저 위임은 위임한 에이전트가 끝나면 반드시 슈퍼바이저에게
  제어권이 돌아온다. 핸드오프는 `Command(goto=...)`로 곧장 다른 에이전트로
  넘어가고, 돌아온다는 보장이 없다. 이 차이가 그래프 구조(고정 간선 vs
  동적 라우팅) 자체로 나타난다.
- 공유 상태(블랙보드)는 모두가 읽고 담당 에이전트만 쓴다. 에이전트별
  컨텍스트는 그 에이전트만 쓰고 읽는다 — 나누지 않으면 프롬프트 길이가
  에이전트 수만큼 누적된다.
- 병렬 작업은 조건부 간선이 노드 이름의 **리스트**를 돌려주면 같은
  슈퍼스텝에서 동시에 실행된다. 병렬로 쓰는 채널에는 반드시 병합 리듀서가
  있어야 결과가 사라지지 않는다.
- 내부 하위 에이전트 호출은 프로세스 경계를 넘지 않는 함수 호출이다. A2A는
  독립 프로세스로 운영되는 자율적인 에이전트에게 작업을 위임하는 별도
  프로토콜이다(Linux Foundation 관리, 2026-09-09 기준 명세 1.0.1, 공식
  파이썬 구현 `a2a-sdk` 1.1.2). 이 장의 `a2a_min/`은 그 문제를 최소한으로
  흉내 낸 예제이지 프로토콜 구현이 아니다.
- MCP는 "도구"를 원격에서 가져다 쓴다(요청받은 함수를 실행할 뿐 스스로
  판단하지 않는다). A2A는 "에이전트"에게 판단이 필요한 작업 자체를
  맡긴다.
- 상호 위임 루프는 하드 캡(사슬 길이 상한)과 핑퐁 패턴 감지 두 층으로
  막는다. 비용(토큰) 제한은 협업에 관여한 모든 에이전트의 호출을 합산한
  값에 건다.

### 확인 질문

1. 슈퍼바이저 패턴과 핸드오프 패턴 중 어느 쪽이 "누가 지금 이 요청을
   처리하고 있는지"를 추적하기 쉬운가? 그 이유를 그래프 구조(고정 간선 vs
   `Command(goto=...)`)로 설명해 보라.
2. `investigator_context`와 `shared_notes`를 하나로 합쳤다면 analyst의
   프롬프트에 무엇이 더 실리게 됐을지 설명해 보라.
3. `verify_quarterly_analysis`라는 이 장의 verifier 작업을 MCP 도구로
   구현하지 않고 A2A(최소 흉내)로 구현한 이유를 2-7절의 판단 기준("확정된
   함수 호출인가, 판단이 필요한 작업인가")으로 설명해 보라.
4. `detect_delegation_loop`가 하드 캡과 핑퐁 패턴 두 조건을 모두 두는
   이유는 무엇인가? 하드 캡만 있었다면 5-4절의 시나리오 2에서 무엇이
   달라졌을지 말해 보라.

**이 장의 완성 코드**: `code/step11_multi_agent/`

**다음 장 연결점**: 12단계(Deep Agents와 Skills)는 이 장의 "여러 역할로
나누어 위임한다"는 개념을 계획 수립·파일 기반 중간 결과 저장·Skill
적용까지 확장한다. 이 장의 `CollabState`가 다루는 공유 상태 개념은
Deep Agents의 파일 시스템 기반 상태 공유와 대조된다.

---

## 검증 상태

**실제로 실행해 확인한 것**

- `python3 -m venv`로 별도 가상환경을 만들고 `langgraph`, `langchain`,
  `langgraph-supervisor`, `langgraph-swarm`, `openai`, `fastapi`, `uvicorn`,
  `pydantic`, `httpx`, `python-dotenv`, `pytest`를 실제로 설치해
  `pip list`로 버전을 확인했다(3-1절 표). `langgraph` 1.2.11, `langchain`
  1.4.0으로 검토자 확인 기준선과 일치했다.
- `langgraph_supervisor.create_supervisor`, `create_handoff_tool`과
  `langgraph_swarm.create_swarm`, `create_handoff_tool`의 실제 시그니처를
  `inspect.signature()`로 확인했다(2-8절 근거).
- `langgraph.types.Command(goto=...)`가 고정 간선 없이 노드 사이를 오갈 수
  있다는 것을 최소 2노드 그래프로 실제로 실행해 확인했다(4-5절에 재현
  코드 포함).
- `python3 -m py_compile`로 `code/step11_multi_agent/` 아래 모든 `.py`
  파일의 문법을 확인했다(`docagent/**/*.py`, `tests/*.py`, `scripts/*.py`).
- `PYTHONPATH=. pytest tests -q`로 단위 테스트 21개를 실행해 전부 통과를
  확인했다(모델 서버 없이 — `conftest.py`가 `OPENAI_BASE_URL`을 존재하지
  않는 주소로 고정한다). 슈퍼바이저 위임과 제어권 복귀, 협업 단위 단계·
  토큰 예산 초과, 에이전트별 컨텍스트 분리, 핸드오프의 정상 흐름과 상호
  위임 루프 차단(하드 캡·핑퐁 패턴 둘 다 개별적으로), 병렬 조사와
  `merge_investigations` 리듀서, A2A 최소 흉내 서버(카드·작업 제출·폴링·
  알 수 없는 skill/task_id 처리), 내부 verifier와 A2A verifier의 결과
  모양 일치, 외부 에이전트 연결 실패 시에도 협업이 죽지 않는 것을 모두
  포함한다.
- `code/_tools/fake_openai_server.py --port 8291`(스텁 모델 서버)과
  `docagent.a2a_min.server --port 8292`(A2A 최소 흉내 검증 에이전트)를
  각각 별도 프로세스로 실제로 띄우고, `scripts/demo_supervisor.py`로 내부
  협업과 A2A 검증 두 경로를 나란히 실행해 실제 출력을 확인했다(5-2절).
- 이 장의 FastAPI 앱(`uvicorn docagent.app:app --port 8180`)을 실제로
  띄우고 `curl -sN /collab/supervisor`로 SSE 스트림이 PROJECT-SPEC.md
  4절 스키마(고정된 `status.stage` 값, `done.finish_reason`,
  `done.partial`)를 그대로 따르는 것을 확인했다.
- `scripts/demo_parallel.py`, `scripts/demo_handoff.py`,
  `scripts/demo_budget.py`를 모두 실제로 실행해 병렬 결과 병합, 정상
  핸드오프에서 제어권이 돌아오지 않는 것, 상호 위임 루프가 하드 캡과
  핑퐁 패턴 각각으로 차단되는 것(`MAX_AGENT_HANDOFFS`를 4와 20 두 값으로
  바꿔 각각 확인), 협업 단위 단계·토큰 예산 초과를 콘솔 출력으로
  확인했다.
- A2A 최소 흉내 서버가 응답하지 않는 상황(존재하지 않는 포트로
  `A2A_VERIFIER_URL` 설정)을 실제로 만들어 협업 전체가 `stop`으로
  끝나되 `verification.source == "a2a_agent_unreachable"`로 구분되는
  것을 확인했다(6-2절).
- A2A(Agent2Agent) 프로토콜의 현재 관리 주체(Linux Foundation), 명세
  버전(1.0, 이후 1.0.1), 공식 파이썬 구현(`a2a-sdk`, PyPI 확인 버전
  1.1.2, 2026-07-22 릴리스)을 웹 검색과 PyPI·GitHub 페이지 조회로
  확인했다(2-7절, 근거는 아래 "참고 문서").

**확인하지 못한 것**

- `langgraph-supervisor`/`langgraph-swarm`을 실제 도구 호출 지원 모델
  서버로 끝까지 실행해 이 장의 직접 구현과 동작을 비교하지 않았다(2-8절
  `[확인 필요]`). 설치와 시그니처 확인까지만 했다.
- 6-1절(analyst의 잘못된 숫자를 verifier가 잡아내는 경로)은 이 문서를
  쓰며 대화형으로 직접 실행해 확인했을 뿐 정식 pytest 테스트로 추가하지
  않았다.
- 6-3절의 "안전장치를 제거했을 때 `GraphRecursionError`까지 실제로
  발생하는지"는 재현하지 않았다 — 안전장치가 있을 때의 정상 동작만
  확인했다.
- `merge_investigations` 리듀서를 실제로 제거한 그래프를 만들어 병렬
  결과 유실을 직접 재현하지는 않았다(2-5절 `[확인 필요]`) — 리듀서가
  있을 때 두 결과가 모두 남는다는 사실과, 리듀서 로직 자체가 값을 잃지
  않는다는 것은 단위 테스트로 확인했다.
- 실제 상용 A2A 서버(예: `a2a-sdk`로 만든 서버)와 이 장의 최소 흉내
  서버를 나란히 실행해 프로토콜 수준의 차이(JSON-RPC vs REST, 표준 Agent
  Card 경로 등)를 실물로 비교하지는 않았다 — 문서와 PyPI 페이지로만
  확인했다.
- 답변 품질이나 실제 모델의 위임 판단력은 검증하지 않았다 — 이 장의
  스텁 서버는 모델이 아니며 `response_format`으로 요청한 스키마를
  반영하지 않는다는 한계를 5-2절에 그대로 밝혔다.

## 참고 문서

- PROJECT-SPEC.md (이 저장소 루트) — SSE 이벤트 스키마(4절), 에이전트
  실행 한도(7절), 공통 인터페이스(9절). 이 장에서 2절(환경 변수)과
  7절(finish_reason=delegation_loop, 협업 단위 예산 확장)을 함께
  갱신했다.
- VERIFIED-FINDINGS.md (이 저장소 루트) — 이전 단계의 패키지 버전 실측
  기록과 스텁 서버의 한계(8절)를 그대로 물려받았다.
- `code/step07_langgraph/`, `code/step08_hitl/` 소스 코드와 본문 —
  State/Node/Edge, 병렬 실행과 커스텀 리듀서, 실행 한도와 반복 감지의
  원형을 그대로 확장했다는 근거.
- PyPI `a2a-sdk` 페이지(https://pypi.org/project/a2a-sdk/) — 2026-09-09
  기준 실제로 조회해 버전 1.1.2(2026-07-22 릴리스), Python 3.10+,
  Apache 2.0, "A2A Protocol Specification 1.0을 구현하고 0.3과의 호환
  모드를 제공한다"는 설명을 확인했다.
- GitHub `a2aproject/A2A` 저장소 — Agent Card·Task/Message/Artifact
  구조, JSON-RPC 2.0 기반 메시지 교환, Linux Foundation 관리, Apache
  2.0 라이선스를 확인했다.
- 웹 검색(2026-09-09 수행) — Linux Foundation의 A2A 프로젝트 발표
  (2025년 6월 Google이 명세·SDK 이관), 명세 버전 1.0(2026년 4월)과
  1.0.1(2026년 5월, 확장 메커니즘 추가), "A2A Protocol Surpasses 150
  Organizations..." 보도자료(Linux Foundation)를 근거로 2-7절의 연혁을
  작성했다.
- `langgraph`, `langgraph-supervisor`, `langgraph-swarm` 실제 설치 후
  `inspect.signature()` 출력 — 2-8·3-1절의 API 확인 근거.
- 이 문서의 모든 버전 정보와 실행 결과는 2026-09-09 기준이다(작성 시점
  질문에 명시된 오늘 날짜).
