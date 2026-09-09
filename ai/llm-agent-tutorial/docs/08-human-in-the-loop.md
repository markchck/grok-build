# 8단계. Human in the Loop와 무한루프 차단

## 1. 학습 목표와 완성 모습

3단계에서 만든 에이전트 루프는 최대 단계 수·최대 실행 시간·같은 (도구,
인자) 3회 반복이라는 세 가지 안전장치만으로 스스로 멈췄다. 그 방식은
"모델이 뭘 하든 애플리케이션이 다 실행해 버리고, 한도를 넘으면 그제서야
멈춘다"는 전제 위에 있었다. 이 장은 그 전제를 하나 깬다 — **되돌리기 어려운
작업은 실행 전에 사람이 먼저 봐야 한다.** 그리고 그 "사람이 볼 때까지
기다리는 상태"가 안전하게 다뤄지지 않으면 오히려 새로운 사고를 만든다는
것도 함께 다룬다: 같은 승인 요청이 두 번 처리되거나, 재개(resume) 과정에서
이미 실행한 도구가 다시 실행되는 사고다.

이번 장에서 다루는 것은 다음 여섯 가지다.

1. **실행 중단과 승인 후 재개** — 되돌리기 어려운 도구 호출 앞에서 에이전트
   루프를 멈추고, 사용자의 결정이 내려진 뒤에 정확히 그 지점부터 이어간다.
2. **승인·거절·수정 요청** — 사용자가 고를 수 있는 세 가지 결정. 거절은
   "실행하지 않고 끝내기"와 "실행하지 않고 대안을 찾게 하기" 두 모드로
   나눈다.
3. **승인 대기 상태의 영속화** — 메모리가 아니라 파일(SQLite)에 저장해서
   서버 프로세스가 죽어도 남아 있게 한다. 실제로 프로세스를 죽였다 다시
   띄워서 확인한다.
4. **중복 승인·중복 실행 방지** — 멱등성 키(idempotency key)로 "같은 실행
   요청은 결과가 몇 번 오든 실제 부작용은 한 번만 일어난다"를 보장한다.
5. **반복 감지 두 종류** — (a) 같은 (도구, 정규화한 인자)의 3회 반복(3단계와
   동일한 규칙), (b) 인자는 다른데 결과가 실질적으로 바뀌지 않는 반복.
6. **예산 초과 시 부분 결과 반환** — 최대 단계·시간에 이어 누적 토큰
   예산까지 관리하고, 어떤 이유로 멈췄든 `done` 이벤트에 중단 사유와 지금까지
   만든 답변을 함께 담아 돌려준다.

앞 단계 연결: 이 장은 3단계(`code/step03_tool_calling/`)의 while 루프
구조와 반복 감지 규칙을 그대로 이어받는다. 5단계(`code/step05_hybrid_rag/`)의
`events.py` SSE 스키마도 그대로 따르되, `done` 이벤트에 필드 하나를
추가한다(2절에서 이유를 설명한다). 이 문서를 쓰는 시점에 7단계
(`code/step07_langgraph/`, LangGraph)는 다른 필자가 같은 저장소에서 병행
집필 중이라 아직 코드가 없다 — 그래서 이 장은 LangGraph의 그래프를 확장하는
대신 3단계 스타일(프레임워크 없는 직접 구현)을 그대로 확장했다. 이유는
2-6절에서 자세히 설명한다.

완성 모습은 이렇다. 사용자가 분기가 끝난 오래된 리포트를 정리해 달라고
요청한다.

```
사용자: notebook-q1-report를 보관해줘

[tool_call] archive_report({"report_id": "notebook-q1-report", "reason": "..."})
[approval_request] archive_report — "archive_report'은(는) 되돌리기 어려운
                    작업이라 실행 전 승인이 필요하다."
                    (SSE 스트림이 여기서 끝난다. done 이벤트는 아직 없다.)
```

여기서 화면에 승인/거절/수정 버튼이 뜬다. 사용자가 "수정" 버튼으로 리포트
ID를 고쳐서 제출하면:

```
POST /approvals/appr_xxx/decision  {"action":"edit","args":{"report_id":"...", "reason":"..."}}
POST /chat/resume/run_xxx

[tool_call] archive_report(수정된 인자)
[tool_result] ok=true 사용자가 인자를 수정해 실행함: {"archived": true, ...}
[token] 처리를 마쳤다.
[done] finish_reason=stop, partial={"steps_used":2, "tokens_used":30, ...}
```

같은 승인 결정을 실수로 두 번 보내도(더블 클릭), 서버 프로세스가 승인
대기 중에 죽었다 다시 떠도, `archive_report`는 정확히 한 번만 실행된다 —
5절에서 이 세 가지를 전부 실제로 재현해 확인한다.

## 2. 핵심 개념

### 2-1. 실행 중단(interrupt)과 재개(resume)는 누구의 책임인가

3단계의 에이전트 루프는 하나의 파이썬 함수 호출(제너레이터) 안에서 시작부터
끝까지 돈다. 이 장의 루프는 그렇게 할 수 없다 — 승인을 "기다리는" 동안
HTTP 요청을 붙잡아 둘 수 없고(사용자가 몇 분, 며칠 뒤에 결정할 수도 있다),
그 사이 서버가 재시작될 수도 있기 때문이다. 그래서 이 장의 `docagent.agent`는
**한 번 부를 때마다 상태를 SQLite에서 읽고, 진행한 만큼 다시 SQLite에
쓰고, 멈춰야 할 지점에서 함수를 끝낸다.** "재개"는 새로운 함수 호출일 뿐,
멈췄던 그 파이썬 스택을 다시 살리는 게 아니다. 이 판단(언제 멈추고 무엇을
저장할지)은 전부 **애플리케이션**의 책임이다 — 모델은 "이 도구를 부르고
싶다"고 한 번 말할 뿐, 승인을 기다리는 것도, 기다림을 관리하는 것도 모델이
하는 일이 아니다.

이 장에서 실행 주체를 다시 정리하면 다음과 같다.

| 주체 | 하는 일 |
| --- | --- |
| 모델 | 다음에 어떤 도구를 어떤 인자로 부르고 싶은지 한 번의 응답으로 말한다. 승인을 "안다"거나 "기다린다"는 개념이 없다 |
| 애플리케이션(`docagent`) | 그 도구가 승인이 필요한지 판단하고, 필요하면 실행을 멈추고 상태를 저장한다. 사용자의 결정을 읽어 실행 여부·인자를 정한다. 반복·예산 한도를 검사한다 |
| 사용자(사람) | 승인 요청을 보고 승인/거절/수정 중 하나를 고른다 |
| 저장소(SQLite 파일) | 승인 대기 상태, 실행 진행 상태, 이미 실행한 결과를 프로세스 수명과 무관하게 보관한다 |

### 2-2. LangGraph의 `interrupt()`를 쓰지 않은 이유

PROJECT-SPEC.md는 이 장에서 LangGraph의 `interrupt` 기능을 쓸지, 직접
구현할지 선택하도록 열어 뒀다. 이 장은 **직접 구현**을 골랐다. 이유는 세
가지다.

1. 7단계(`code/step07_langgraph/`)가 이 문서를 쓰는 시점(2026-09-09)에
   아직 존재하지 않는다 — 다른 필자가 같은 저장소에서 병행 집필 중이다.
   "7단계 그래프를 확장한다"는 지시를 따르려면 확장할 그래프가 있어야
   하는데 없다. 없는 그래프를 상상해서 만들면 실제로 7단계가 완성됐을 때
   구조가 어긋날 위험이 크다.
2. 커리큘럼 설계(`docs/00-curriculum.md`)상 LangGraph는 7단계에서
   처음 다루는 개념이고, 이 장의 목표는 "Human in the Loop와 무한루프
   차단의 원리"이지 "LangGraph 사용법"이 아니다. WRITING-GUIDE.md는
   "LangChain(과 그 생태계)이 모든 기술의 기반이라고 설명하지 않는다"를
   요구한다 — 프레임워크 없이도 승인·재개·멱등성·반복 감지가 무엇이고 왜
   필요한지 보여줄 수 있어야, 나중에 LangGraph의 `interrupt()`나 다른
   프레임워크의 체크포인트 기능을 봤을 때 "그게 정확히 무엇을 대신
   해주는지" 판단할 수 있다.
3. 이 장의 요구사항(멱등성 키, 결과-무변화 반복 감지, 프로세스 재시작 후
   상태 복구)은 프레임워크 지식이 아니라 "무엇을 어디에 얼마나 저장해야
   하는가"에 대한 이해가 핵심이다. 직접 SQLite로 구현하면 그 결정 하나하나가
   코드에 그대로 드러난다.

`[확인 필요: 2026-09-09 시점 LangGraph의 interrupt()/Command(resume=...)
API가 여기서 구현한 것과 정확히 어떤 지점에서 같고 다른지]` — 이 장에서는
LangGraph 패키지를 설치하거나 실행해서 비교하지 않았다. 7단계가 완성되면
그 장에서 실제로 설치해 확인한 API를 근거로 비교하는 것이 맞다고 판단했다.

### 2-3. 멱등성(idempotency)이란 무엇이고 왜 필요한가

**멱등성**은 "같은 요청을 여러 번 보내도 실제 결과(부작용)는 한 번 보낸
것과 같다"는 성질이다. 이 장에서 멱등성이 깨지기 쉬운 지점은 두 곳이다.

- **승인 결정의 중복 제출**: 사용자가 승인 버튼을 두 번 누르거나, 클라이언트가
  네트워크 오류로 같은 결정 요청을 재시도한다. 결정을 받을 때마다 도구를
  실행하면 `archive_report`가 두 번 불린다.
- **재개(resume)의 중복 처리**: 재개 요청이 두 번 들어오거나(클라이언트
  재시도), 재개 도중 서버가 죽었다 다시 뜬 뒤 같은 단계를 다시 밟는다.
  이번에는 "이미 실행됐어야 할 도구 호출"을 다시 실행하게 된다.

두 경우 모두 **"이 특정 실행 시도"를 가리키는 고유한 키**를 미리 정하고,
그 키로 "이미 처리됐는지"를 실행 직전에 확인하면 막을 수 있다. 이 키가
멱등성 키다. 이 장에서는 `(run_id, call_id, 도구 이름, 정규화한 인자)`
네 가지를 합쳐 해시 하나로 만든다(`docagent/agent.py`의
`idempotency_key()`). `call_id`를 넣는 이유가 중요하다 — 모델이 "정당하게"
같은 도구를 같은 인자로 다시 부르는 경우(예: 사용자가 같은 질문을 나중에
또 함)는 새 `tool_call`이라 새 `call_id`를 받으므로 다른 키가 된다. 반면
"이 장이 막으려는" 중복(승인 더블 클릭, 재개 재시도)은 항상 **같은**
`call_id`를 가진 같은 tool_call을 다시 처리하는 상황이라 같은 키가 된다.

멱등성 키만으로는 부족하다. "확인 후 실행"(check-then-act) 사이에 시간
간격이 있으면 두 요청이 동시에 둘 다 "아직 안 됐다"고 확인하고 둘 다
실행해 버릴 수 있다. 그래서 이 장은 두 층으로 막는다.

1. **결정 자체의 멱등성**: SQLite의 조건부 UPDATE(`UPDATE ... WHERE
   status='pending'`)로 승인 상태를 원자적으로 한 번만 바꾼다(`store.py`의
   `decide_approval()`). 두 번째 결정 요청은 행이 이미 `pending`이 아니므로
   아무것도 바꾸지 못하고 저장된 첫 결정을 그대로 돌려받는다.
2. **실행 자체의 멱등성**: 실제 도구를 실행하기 직전에 멱등성 키로 결과
   캐시(`tool_cache` 테이블)를 먼저 찾는다. 있으면 그 결과를 재사용하고
   실제 함수를 다시 부르지 않는다(`store.py`의 `cache_get`/`cache_set`,
   `agent.py`의 `_execute_with_cache()`).
3. **동시 진행 자체의 방지**: 같은 run을 두 요청이 동시에 진행시키는 것
   자체를 막기 위해 `runs.processing` 플래그를 조건부 UPDATE로 잠근다
   (`store.try_acquire()`/`store.release()`). 두 번째 진행 요청은 즉시
   `run_busy` 오류를 받는다.

세 층 중 하나만으로도 이 장의 데모는 통과하지만, 실무에서는 "락은 걸었는데
캐시가 없다"거나 그 반대인 경우 둘 다 사고로 이어질 수 있어 함께 쓴다.

### 2-4. 반복 감지 두 종류

3단계에서 다룬 반복 감지는 한 가지뿐이었다: **같은 (도구, 정규화한 인자)
조합이 3회 반복되면 멈춘다.** 이 장은 이를 "반복 (a)"라 부르고, 그것으로
잡히지 않는 또 다른 패턴을 "반복 (b)"로 추가한다.

- **반복 (a) — 같은 시도의 반복**: 모델이 정확히 같은 도구를 정확히 같은
  인자로 계속 부른다. 도구 이름 + `json.dumps(args, sort_keys=True)`로
  정규화한 문자열을 키로 써서 횟수를 센다(`_normalize_args`). 3단계와 같은
  규칙이다.
- **반복 (b) — 결과가 바뀌지 않는 반복**: 모델이 인자는 계속 바꿔가며
  시도하지만(그래서 (a)에는 걸리지 않는다), 도구가 되돌려주는 결과가
  실질적으로 똑같다. 검색 인덱스가 깨졌거나, 캐시가 오염됐거나, 도구
  자체에 버그가 있어서 어떤 입력을 줘도 같은 답만 돌아오는 상황을 흉내
  낸다. 모델은 "다르게 시도하고 있으니 곧 될 것"이라고 판단해 계속
  시도할 수 있지만, 애플리케이션 입장에서는 "단계만 소모되고 있다"는 것을
  알 수 있다.

**반복 (b)의 판정 방법(구체적 구현)**: 이 도구 호출의 (도구, 정규화한
인자) 조합이 이번 run에서 **처음** 등장했을 때만(즉 반복 (a) 카운터가 1일
때만) 그 결과 문자열의 SHA-256 해시를 "진행 기록"(`progress_hashes`)에
추가한다. 최근 `NO_PROGRESS_WINDOW`(=3)개의 해시가 전부 같으면 — 서로 다른
인자로 3번 시도했는데 결과 해시가 세 번 다 똑같으면 — `no_progress`로
멈춘다(`agent.py`의 `_register_progress()`, `_no_progress_triggered()`).

반복 (a)에 걸리는 호출(완전히 같은 인자의 재시도)은 진행 기록에 넣지 않는다
— 그러면 두 감지 로직이 같은 상황을 서로 다른 사유로 보고하며 경쟁하게
된다. "인자가 완전히 같다"는 사실 자체가 이미 반복 (a)로 설명되므로,
반복 (b)는 "인자가 새로운데도" 진전이 없는 경우만 판정하게 나눴다.

이 판정은 휴리스틱이다. 진행 기록을 도구 전체에 걸쳐 하나로 관리하므로,
서로 다른 도구를 번갈아 호출하다가 우연히 해시가 겹치면 오탐할 여지가
있다. 6절에서 실제로 재현하는 데모는 같은 도구(`flaky_lookup`)를 계속
부르는 상황이라 이 한계가 드러나지 않지만, 도구별로 진행 기록을 나누는
편이 더 정확하다 — 이 장에서는 구현을 단순하게 유지하기 위해 하지 않았다.

### 2-5. 토큰 예산과 "usage를 안 주는 서버"

PROJECT-SPEC.md 7절은 최대 단계 수와 최대 실행 시간만 이름을 고정했다.
이 장은 여기에 **누적 토큰 예산**(`MAX_AGENT_TOKENS`)을 더한다. 매
`chat.completions` 호출의 응답에 있는 `usage.total_tokens`를 run 전체에
걸쳐 누적하고, 설정값을 넘으면 멈춘다.

문제는 **모든 OpenAI 호환 서버가 `usage`를 돌려주지 않는다**는 것이다.
필드 자체가 없거나, 스트리밍에서는 마지막 청크에만 실려 오는 구현이
흔하다. 이 장의 `llm.py`는 `usage`가 없으면 예외를 던지지 않고 빈
딕셔너리(`{}`)를 돌려준다. `agent.py`의 `_accumulate_usage()`는 이 경우
**글자 수를 4로 나눈 값**을 프롬프트·완성 각각의 토큰 수 추정치로 쓰고,
그 run의 `usage_estimated` 플래그를 `True`로 세운다. "글자 수 / 4"는 영어
기준의 매우 거친 경험칙이고 한국어 텍스트에는 더 안 맞는다 — 이 장은 이
값을 정확한 토큰 수라고 주장하지 않는다. 목적은 "예산을 완전히 무시하는
것보다는, 부정확하더라도 계속 커지는 하한선을 갖고 있는 편이 낫다"는
것뿐이다. 화면(그리고 `done` 이벤트의 `partial.tokens_estimated`)에는 이
값이 추정치임을 항상 표시한다. `[확인 필요: 2026-09-09 시점 실제 상용
모델 서버들의 usage 필드 지원 비율]` — 이 장은 스텁 서버(`usage`를 항상
주는 구현)로만 실행 검증했고, 여러 실제 서버를 대상으로 지원 여부를
비교 조사하지는 않았다.

토큰 예산 초과를 알릴 `finish_reason` 값이 PROJECT-SPEC.md 7절의 기존
목록(`stop`, `max_steps`, `timeout`, `repeated_tool_call`, `no_progress`,
`rejected_by_user`, `cancelled`)에는 없었다. 기존 값 중 하나(예:
`max_steps`)를 재사용하면 클라이언트가 "왜 멈췄는지"를 구분할 수 없게
되므로, 이 장은 `token_budget` 값 하나를 새로 추가했다 — PROJECT-SPEC.md의
"규약을 바꿔야 하면 해당 장 본문에 명시하고 이 파일도 함께 고친다"는
규칙에 따라 PROJECT-SPEC.md 7절도 함께 갱신했다.

### 2-6. `done` 이벤트와 부분 결과 — `error`가 아니다

PROJECT-SPEC.md 4절은 `done` 이벤트의 `data`를
`{"finish_reason": "..."}`로 고정했다. 이 장은 필드 하나를 **추가로**
얹었다: `partial`(지금까지 만든 답변 텍스트, 사용한 단계 수, 누적 토큰
사용량, 그 값이 추정치인지 여부). 기존 필드는 그대로이므로 `partial`을
모르는 클라이언트는 `finish_reason`만 읽으면 이전과 똑같이 동작한다 — 이
장에서 이 확장이 안전한 이유다. PROJECT-SPEC.md 4절 표에도 이 확장을
반영했다.

**왜 `error`가 아니라 `done`인가**: `error`는 "요청을 처리하는 데
실패했다"는 뜻이다. 최대 단계 수를 넘기거나 반복이 감지된 것은 애플리케이션
버그도, 사용자 입력 오류도 아니다 — 안전장치가 **의도한 대로** 작동한
것이다. 이걸 `error`로 보내면 클라이언트가 "무언가 고장 났다"고 오해하고
부분 결과를 버릴 수 있다. `done` 이벤트에 사유와 부분 결과를 함께 담아
보내면, 클라이언트는 "끝까지 못 갔지만 여기까지는 확인했다"를 사용자에게
보여줄 수 있다.

### 2-7. 세 가지 흐름도

```
[정상 승인 흐름]
POST /chat
  -> tool_call(archive_report) 발생
  -> approval_request 이벤트, SQLite에 상태 저장(paused_approval)
  -> SSE 스트림 종료(done 없음)

사용자가 결정
POST /approvals/{id}/decision {action: approve|reject|edit}
  -> SQLite: status='pending'일 때만 원자적으로 갱신

POST /chat/resume/{run_id}
  -> SQLite에서 상태 로드
  -> 결정에 따라 실행/건너뜀/대안 처리
  -> 루프 계속 -> 최종 답변 -> done 이벤트
```

```
[반복 감지 (a): 같은 인자 반복]
tool_call(sum_sales, {"product":"노트북"})  1회째 실행
tool_call(sum_sales, {"product":"노트북"})  2회째 실행
tool_call(sum_sales, {"product":"노트북"})  3회째 실행
tool_call(sum_sales, {"product":"노트북"})  4회째 -> 실행하지 않고 차단
  -> done(finish_reason=repeated_tool_call, partial=...)
```

```
[반복 감지 (b): 결과가 안 바뀌는 반복]
tool_call(flaky_lookup, {"query":"질의-1"})  결과 해시 H
tool_call(flaky_lookup, {"query":"질의-2"})  결과 해시 H (같음)
tool_call(flaky_lookup, {"query":"질의-3"})  결과 해시 H (같음, 창 3개 모두 동일)
  -> done(finish_reason=no_progress, partial=...)
```

## 3. 실습 준비

### 3-1. 패키지와 버전

이 장은 새 외부 의존성을 추가하지 않는다. 상태 저장은 표준 라이브러리
`sqlite3`만 쓴다(`import sqlite3`가 되는지는 파이썬 배포판에 내장 여부만
확인하면 된다 — 이 문서 작성 환경의 Python 3.11.15에는 기본 포함돼 있다).

```
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
pydantic>=2.7,<3
httpx>=0.28,<0.29
python-dotenv>=1.0,<2
pytest>=8,<9
```

확인된 기준선(2026-09-09, 이 문서 작성 시 실제 설치): `fastapi` 0.141.1,
`pydantic` 2.13.5. 나머지는 VERIFIED-FINDINGS.md 1절의 값(`httpx` 0.28.1,
`python-dotenv` 1.2.3)과 같은 범위다. 설치:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r code/step08_hitl/requirements.txt
```

### 3-2. 환경 변수

이 장에서 두 개를 새로 쓴다(PROJECT-SPEC.md 2절에 추가만 했다).

```
MAX_AGENT_TOKENS=4000       # run 하나가 쓸 수 있는 누적 토큰 예산
DOCAGENT_STATE_DB=./data/hitl_state.db   # 승인·실행 상태를 저장할 SQLite 파일
```

### 3-3. 폴더 구조

```
code/step08_hitl/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── config.py     # Settings에 max_agent_tokens, state_db_path 추가
│   ├── llm.py         # chat()/achat()이 ChatResult.raw로 usage까지 돌려준다
│   ├── events.py      # done 이벤트에 partial 필드, finish_reason에 token_budget 추가
│   ├── tools.py        # 3단계 도구 + archive_report(승인 필요) + flaky_lookup(결함 데모)
│   ├── store.py        # SQLite: runs / approvals / tool_cache 세 테이블
│   ├── agent.py         # 재개 가능한 에이전트 루프. 이 장의 핵심 파일
│   └── app.py            # /chat, /chat/resume, /approvals/* 엔드포인트
├── static/               # 승인 버튼이 있는 간단한 웹 UI
├── data/sales.csv
├── scripts/demo_infinite_loop.py   # 모델 서버 없이 무한 루프 차단을 보여주는 스크립트
└── tests/                # pytest 15개
```

### 3-4. 샘플 데이터

`data/sales.csv`는 4단계에서 만든 것을 그대로 가져왔다(제품·지역·분기별
판매 기록). 이 장은 문서 검색(RAG)을 다루지 않으므로 `data/docs/`나
Milvus는 두지 않았다 — HITL과 반복 차단은 검색 방식과 무관한 주제이기
때문이다.

## 4. 단계별 구현

### 4-1. 상태를 어디에 얼마나 저장할지 먼저 정한다

이 장에서 가장 먼저 결정해야 하는 것은 코드가 아니라 **무엇을 영속화할
것인가**다. 세 가지로 나눴다.

- `runs` — run 하나의 전체 상태(메시지 목록, 단계 수, 반복 카운터, 진행
  해시, 누적 토큰 사용량, 승인 대기 여부와 그 승인 ID, 처리 중 잠금 플래그).
- `approvals` — 승인 요청 하나하나의 기록(도구·인자·사유·멱등성 키)과
  최종 결정.
- `tool_cache` — 멱등성 키 -> 실행 결과. "이 실행은 이미 했다"를 판단하는
  근거.

```python
# code/step08_hitl/docagent/store.py (발췌)
def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,               -- running | paused_approval | done
            processing INTEGER NOT NULL DEFAULT 0,
            messages_json TEXT NOT NULL,
            step INTEGER NOT NULL DEFAULT 0,
            start_time REAL NOT NULL,
            call_counts_json TEXT NOT NULL DEFAULT '{}',
            progress_hashes_json TEXT NOT NULL DEFAULT '[]',
            usage_prompt INTEGER NOT NULL DEFAULT 0,
            usage_completion INTEGER NOT NULL DEFAULT 0,
            usage_total INTEGER NOT NULL DEFAULT 0,
            usage_estimated INTEGER NOT NULL DEFAULT 0,
            finish_reason TEXT,
            final_text TEXT,
            pending_approval_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        -- approvals, tool_cache 테이블은 본문 하단 저장소 코드 참고
        """
    )
```

`sqlite3.connect(..., check_same_thread=False)`와 `PRAGMA journal_mode=WAL`을
쓴다. WAL(Write-Ahead Logging)은 읽기와 쓰기가 서로를 덜 막게 해서, 이
장처럼 요청마다 파일을 열고 닫는 방식에서 잠금 경합을 줄인다. 새 프로세스가
같은 파일을 열어도 커밋된 내용은 그대로 보인다 — 이것이 "프로세스가
죽어도 상태가 살아남는다"의 실제 메커니즘이다.

### 4-2. 멱등성 키와 실행 캐시

```python
# code/step08_hitl/docagent/agent.py (발췌)
def idempotency_key(run_id: str, call_id: str, tool_name: str, args: dict[str, Any]) -> str:
    raw = f"{run_id}|{call_id}|{tool_name}|{_normalize_args(args)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _execute_with_cache(conn, run, key, tool_name, args):
    cached = store.cache_get(conn, key)
    if cached is not None:
        return ToolRunResult(ok=bool(cached["result_ok"]), content=cached["result_content"]), True
    result = run_tool(tool_name, args)
    store.cache_set(conn, idempotency_key=key, run_id=run.run_id, tool_name=tool_name,
                     args=args, ok=result.ok, content=result.content)
    return result, False
```

캐시 저장은 `INSERT OR IGNORE`를 쓴다(`store.cache_set`). 이미 그 키로
저장된 행이 있으면 조용히 무시한다 — "이 키의 결과는 하나여야 한다"는
불변식을 지킨다. 승인이 필요 없는 일반 도구(`sum_sales`,
`lookup_sales_rows`, `flaky_lookup`)에도 똑같이 이 함수를 거치게 했다.
승인 여부와 무관하게 "재개 중 같은 단계를 다시 밟을 수 있다"는 사실
자체는 똑같기 때문이다.

### 4-3. 승인이 필요한 도구를 표시한다

```python
# code/step08_hitl/docagent/tools.py (발췌)
APPROVAL_REQUIRED_TOOLS: frozenset[str] = frozenset({"archive_report"})


class ArchiveReportArgs(BaseModel):
    report_id: str = Field(description="보관할 리포트 문서 ID.")
    reason: str = Field(description="왜 이 리포트를 보관하는지에 대한 짧은 설명.")


ARCHIVED_REPORTS: list[str] = []  # 실제로 실행됐는지 테스트·데모에서 확인하기 위한 기록


def archive_report(args: ArchiveReportArgs) -> dict[str, Any]:
    """리포트를 보관 처리한다(시뮬레이션). 되돌리기 어려운 작업의 예시다."""
    ARCHIVED_REPORTS.append(args.report_id)
    return {"archived": True, "report_id": args.report_id, "reason": args.reason}
```

실제로 파일을 지우지 않는다 — 이 장의 목적은 "승인이 실행을 실제로
막는지"를 보여주는 것이지 삭제 기능 자체가 아니다. `ARCHIVED_REPORTS`
리스트에 실행된 것만 쌓이므로, 테스트와 데모에서 "승인 전에는 비어 있고,
승인 후에는 정확히 한 번 채워진다"를 그대로 확인할 수 있다.

### 4-4. 에이전트 루프: 승인 지점에서 멈추기

`_run_loop()`는 3단계의 while 루프와 거의 같은 모양이지만, 도구를 실제로
실행하기 전에 승인이 필요한지 검사하는 분기가 하나 늘었다.

```python
# code/step08_hitl/docagent/agent.py (발췌, _run_loop 안)
if name in APPROVAL_REQUIRED_TOOLS:
    idem_key = idempotency_key(run.run_id, call_id, name, args_dict)
    cached = store.cache_get(conn, idem_key)
    if cached is not None:
        # 재개 과정에서 이미 승인·실행된 호출이 다시 나타난 경우
        result = ToolRunResult(ok=bool(cached["result_ok"]), content=cached["result_content"])
        run.messages.append({"role": "tool", "tool_call_id": call_id, "name": name,
                              "content": result.content})
        yield AgentEvent("tool_result", {"id": call_id, "ok": result.ok,
                                          "summary": "(캐시됨) 이미 승인·실행된 호출"})
        continue

    approval_id = store.create_approval(
        conn, run_id=run.run_id, call_id=call_id, tool_name=name, args=args_dict,
        reason=f"'{name}'은(는) 되돌리기 어려운 작업이라 실행 전 승인이 필요하다.",
        idempotency_key=idem_key,
    )
    run.status = "paused_approval"
    run.pending_approval_id = approval_id
    store.save_run(conn, run)
    yield AgentEvent("approval_request", {"id": approval_id, "action": name,
                                           "args": args_dict, "reason": ...})
    return  # 여기서 제너레이터가 끝난다 — done 이벤트는 아직 내보내지 않는다
```

핵심은 마지막 `return`이다. 승인이 필요하면 함수를 그냥 끝낸다. 호출한 쪽
(FastAPI의 `StreamingResponse`)은 스트림이 끝났다고 보고 HTTP 응답을
닫는다. `done` 이벤트를 보내지 않았으므로 클라이언트는 "이 실행이 끝났다"고
오해하지 않고, "승인 대기 중"이라는 걸 `approval_request` 이벤트로
안다.

### 4-5. 승인 결정 반영: 재개 시작 지점

재개(`advance_run`)는 상태가 `paused_approval`이면 먼저 결정을 확인한다.

```python
# code/step08_hitl/docagent/agent.py (발췌)
def _resolve_pending_approval(conn, run) -> Iterator[AgentEvent]:
    approval = store.get_approval(conn, run.pending_approval_id)
    if approval is None or approval["status"] == "pending":
        return "still_pending"

    if approval["status"] == "rejected":
        mode = approval["decision_mode"] or "alternative"
        if mode == "abort":
            run.status = "done"
            run.finish_reason = "rejected_by_user"
            ...
            return "aborted"
        # mode == "alternative": 실행하지 않은 채 거절 사실만 도구 결과로 돌려주고 계속 진행
        ...
        return "continue"

    if approval["status"] == "edited":
        edited_args = json.loads(approval["decision_args_json"])
        parsed, error_result = validate_args(tool_name, edited_args)
        ...  # 검증 실패 시에도 죽지 않고 도구 결과로 되돌린다
        # 검증 성공 -> _execute_with_cache로 "수정된 인자"를 실행
        return "continue"

    # approved: 원래 인자 그대로 _execute_with_cache로 실행
    return "continue"
```

`거절(mode=abort)`은 `run.finish_reason = "rejected_by_user"`로 즉시
끝낸다. `거절(mode=alternative)`은 도구 결과 메시지에 "사용자가 이 작업을
거절했다"는 오류를 담아 모델에게 돌려주고 루프를 계속한다 — 모델이 이
정보를 보고 다른 방법을 제안하거나, 그 작업 없이 답할 기회를 준다. `수정`은
사용자가 보낸 새 인자를 **같은 pydantic 스키마로 다시 검증**한 뒤(도구가
직접 실행되는 게 아니라 `validate_args()`를 먼저 거친다), 통과하면 그
인자로 실행한다. 새 인자는 원래 `call_id`와 도구 이름은 같지만 인자가
다르므로 멱등성 키도 새로 계산된다 — "원래 시도"와 "수정된 시도"가 서로
다른 캐시 항목으로 남는다.

### 4-6. 중복 결정 방지: 조건부 UPDATE

```python
# code/step08_hitl/docagent/store.py (발췌)
def decide_approval(conn, approval_id, *, status, decision_args, mode):
    cur = conn.execute(
        """UPDATE approvals SET status=?, decision_args_json=?, decision_mode=?, decided_at=?
           WHERE approval_id=? AND status='pending'""",
        (status, json.dumps(decision_args, ensure_ascii=False) if decision_args else None,
         mode, time.time(), approval_id),
    )
    conn.commit()
    already_decided = cur.rowcount == 0
    approval = get_approval(conn, approval_id)
    return approval, already_decided
```

`WHERE status='pending'`이 이 함수의 전부다. 두 번째 호출이 들어오면
`rowcount == 0`이라 `already_decided=True`를 돌려주고, **저장된 값은 첫
번째 결정 그대로**다. 5절에서 이걸 curl로 두 번 호출해서 실제로 확인한다.

### 4-7. FastAPI 엔드포인트

```python
# code/step08_hitl/docagent/app.py (발췌)
@app.post("/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    conn = get_conn()
    run_id = start_run(conn, req.message)

    async def event_stream():
        try:
            async for agent_event in aadvance_run(conn, run_id, tool_names=req.tool_names):
                if await request.is_disconnected():
                    return
                yield _render_event(agent_event.kind, agent_event.data)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                              headers={"X-Run-Id": run_id})
```

`advance_run`(동기, `llm.chat`을 쓴다)이 아니라 `aadvance_run`
(비동기, `llm.achat`을 쓴다)을 쓰는 이유는 PROJECT-SPEC.md 9절의
취소 요구사항이다 — 2단계부터 이어진 것과 같은 이유로, 클라이언트가 연결을
끊었을 때 모델 서버로 나가는 HTTP 호출까지 실제로 취소하려면 이벤트 루프가 그
호출을 직접 취소할 수 있어야 한다. 로직은 두 함수가 완전히 같고 모델 호출
지점만 다르다 — 동기 `advance_run`은 테스트가 계속 쓴다.


@app.post("/approvals/{approval_id}/decision")
def decide(approval_id: str, req: DecisionRequest) -> dict:
    conn = get_conn()
    ...
    approval, already_decided = store.decide_approval(
        conn, approval_id, status=status_value, decision_args=decision_args, mode=mode
    )
    return {"approval": dict(approval), "already_decided": already_decided}


@app.post("/chat/resume/{run_id}")
def resume(run_id: str, req: ResumeRequest = ResumeRequest()) -> StreamingResponse:
    ...
```

`run_id`는 응답 헤더 `X-Run-Id`로 돌려준다 — SSE의 `data:` 스키마는
PROJECT-SPEC.md 4절에 고정돼 있어 이벤트 안에 새 필드를 끼워 넣는 대신,
스키마 밖의 HTTP 헤더를 썼다. `ChatRequest.tool_names`는 운영 코드가 아니라
**이 장의 스텁 서버로 승인 흐름을 시연하기 위한 디버그용 필드**다 — 이유는
5절에서 설명한다.

## 5. 실행과 결과 확인

### 5-1. 단위 테스트 (모델 서버 없이)

```bash
cd code/step08_hitl
PYTHONPATH=. pytest tests -q
```

실행 결과(이 문서 작성 시 실제로 돌려 확인함):

```
...............                                                          [100%]
15 passed in 0.22s
```

`tests/test_limits.py`가 최대 단계·시간·토큰 예산(실측/추정 각각)과 반복
감지 두 종류를, `tests/test_approval_flow.py`가 승인/거절(두 모드)/수정,
중복 결정, "재개해도 재실행 안 함", "DB 파일을 새로 열어도(=프로세스 재시작
흉내) 상태가 이어짐"을 확인한다.

### 5-2. 무한 루프 차단을 눈으로 보기 (모델 서버 없이)

```bash
PYTHONPATH=. python scripts/demo_infinite_loop.py
```

실제 실행 결과(발췌):

```
=== 시나리오 1: 같은 (도구, 인자) 무한 반복 -> repeated_tool_call ===
[tool_call] sum_sales({'product': '노트북'})
[tool_result] ok=True {"matched_row_count": 12, ...}
[tool_call] sum_sales({'product': '노트북'})
[tool_result] ok=True {"matched_row_count": 12, ...}
[tool_call] sum_sales({'product': '노트북'})
[tool_result] ok=True {"matched_row_count": 12, ...}
[tool_call] sum_sales({'product': '노트북'})
[tool_result] ok=False 같은 도구·인자 반복 호출 감지
[done] finish_reason=repeated_tool_call
       partial={'text': '', 'steps_used': 4, 'tokens_used': 987, 'tokens_estimated': True}

=== 시나리오 2: 인자는 다른데 결과가 안 바뀌는 반복 -> no_progress ===
[tool_call] flaky_lookup({'query': '질의-1'})
[tool_result] ok=True {"matches": ["결과를 찾을 수 없음(색인 오류)"], "match_count": 0}
[tool_call] flaky_lookup({'query': '질의-2'})
[tool_result] ok=True {"matches": ["결과를 찾을 수 없음(색인 오류)"], "match_count": 0}
[tool_call] flaky_lookup({'query': '질의-3'})
[tool_result] ok=True {"matches": ["결과를 찾을 수 없음(색인 오류)"], "match_count": 0}
[done] finish_reason=no_progress
       partial={'text': '', 'steps_used': 3, 'tokens_used': 522, 'tokens_estimated': True}
```

시나리오 1은 완전히 같은 인자로 계속 부르는 "고장 난 모델"을 흉내 낸
가짜 `chat_fn`을 썼고, 3번 실행된 뒤 4번째 시도에서 차단됐다. 시나리오
2는 질의는 매번 다르지만 결함 있는 도구(`flaky_lookup`)가 항상 같은
고정 결과만 돌려주는 상황이라, 서로 다른 인자 3번 만에 "진전이 없다"고
판단해 멈췄다. 두 경우 모두 이 데모의 가짜 `chat_fn`은 스스로 멈추지
않는다 — 멈춘 것은 전적으로 `docagent.agent`의 안전장치다.

### 5-3. 스텁 서버로 전체 흐름 실행 — 왜 커스텀 `chat_fn`과 스텁 서버를 함께 쓰는가

`code/_tools/fake_openai_server.py`는 모델이 아니라 **요청 형식과 응답
해석이 맞는지**만 확인하는 스텁이다. 이 스텁의 도구 호출 동작은 코드에
이렇게 적혀 있다: "tools가 있고 아직 도구 결과가 없으면 tool_call을
**한 번** 만들고, `role="tool"` 메시지가 들어오면 최종 답을 낸다." 즉 이
스텁은 **한 번의 대화에서 도구를 딱 한 번만** 부른다 — 그래서 5-2절 같은
"여러 라운드에 걸친 반복"은 이 스텁으로 재현할 수 없다(5-2절은 그래서
스크립트로 만든 가짜 `chat_fn`을 썼다). 또한 이 스텁은 사용자 질문
내용을 보지 않고 **`tools` 목록의 첫 번째 항목**을 항상 호출한다.

이 장의 `TOOL_SPECS`는 `[sum_sales, lookup_sales_rows, archive_report,
flaky_lookup]` 순서로 등록돼 있다. 그래서 그냥 `/chat`을 부르면 스텁은
항상 `sum_sales`를 호출한다(승인 없는 기본 흐름을 보여주기에는 적당하다).
승인 흐름을 이 스텁으로 시연하려면 스텁이 `archive_report`를 부르게
해야 하는데, 실제 모델이라면 사용자 메시지로 그렇게 유도하겠지만 이 스텁은
메시지를 보지 않는다. 그래서 `/chat`과 `/chat/resume`에 **데모 전용**
`tool_names` 필드를 뒀다 — 이 요청에 한해 모델에게 보여줄 도구 목록의
순서를 지정한다(운영 코드에는 영향이 없다: 생략하면 항상 원래 순서를
쓴다). 이 필드가 없었다면 이 스텁으로는 `archive_report` 승인 흐름 자체를
켤 방법이 없었다 — 실제 모델을 쓸 때는 이 필드 없이 사용자 메시지만으로
충분하다.

**터미널 1: 스텁 모델 서버**

```bash
python code/_tools/fake_openai_server.py --port 8141
```

**터미널 2: 이 장의 앱**

```bash
cd code/step08_hitl
OPENAI_BASE_URL=http://127.0.0.1:8141/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=stub-model \
DOCAGENT_STATE_DB=/tmp/hitl_demo.db MAX_AGENT_STEPS=8 \
uvicorn docagent.app:app --port 8180 --app-dir .
```

**기본 흐름(승인 없음)** — 이 문서 작성 시 실제로 실행해 받은 응답:

```bash
curl -sN http://127.0.0.1:8180/chat -H 'content-type: application/json' \
  -d '{"message":"1분기 노트북 매출 합계 알려줘"}'
```

```
event: status
data: {"stage": "planning", "message": "1단계: 모델에게 다음 행동을 묻는다"}

event: tool_call
data: {"id": "call_stub_1", "name": "sum_sales", "args": {}}

event: tool_result
data: {"id": "call_stub_1", "ok": true, "summary": "{\"matched_row_count\": 48, ...}"}

event: token
data: {"text": "요청하신 내용을 확인했습니다."}

event: done
data: {"finish_reason": "stop", "partial": {"text": "요청하신 내용을 확인했습니다.", "steps_used": 2, "tokens_used": 30, "tokens_estimated": false}}
```

응답 헤더에 `x-run-id: run_201d566e013c`가 실려 온다. 스텁 서버는 usage를
항상 주므로(`fake_openai_server.py`의 `_completion()`) `tokens_estimated`가
`false`다.

**승인 요청** — `tool_names`로 `archive_report`를 먼저 부르게 한다.

```bash
curl -sN -D headers.txt http://127.0.0.1:8180/chat -H 'content-type: application/json' \
  -d '{"message":"notebook-q1-report를 보관해줘", "tool_names": ["archive_report"]}'
```

```
event: tool_call
data: {"id": "call_stub_1", "name": "archive_report", "args": {}}

event: approval_request
data: {"id": "appr_788a20df625c", "action": "archive_report", "args": {}, "reason": "'archive_report'은(는) 되돌리기 어려운 작업이라 실행 전 승인이 필요하다."}
```

여기서 스트림이 끝난다(`done` 이벤트 없음). 스텁이 인자를 항상 `{}`로
비워서 보내기 때문에 `args`가 비어 있다 — `ArchiveReportArgs`는
`report_id`, `reason`이 필수라서 이대로 승인해도 실행 시 검증에 실패한다.
그래서 이 데모는 자연스럽게 "수정 요청"으로 이어진다(아래).

`GET /runs/{run_id}`로 상태를 확인한다.

```bash
curl -s http://127.0.0.1:8180/runs/run_6f0b1ad9c9d0
```

```json
{"run_id":"run_6f0b1ad9c9d0","status":"paused_approval","step":1,
 "finish_reason":null,"final_text":null,
 "pending_approval_id":"appr_788a20df625c","usage_total":15,
 "usage_estimated":false,"message_count":3}
```

**여기서 서버 프로세스를 실제로 죽인다.**

```bash
pgrep -f "uvicorn docagent.app:app --port 8180"    # PID 확인, 예: 7335
kill -9 7335
pgrep -f "uvicorn docagent.app:app --port 8180" || echo "확실히 죽었다"
```

실행 결과: `확실히 죽었다`. 그리고 같은 `DOCAGENT_STATE_DB` 파일로 새
프로세스를 띄운다(터미널 2 명령을 그대로 다시 실행 — 이번엔 새 PID로
뜬다. 이 문서 작성 시 실측: 죽인 PID는 7335, 새로 뜬 PID는 7424).

```bash
curl -s http://127.0.0.1:8180/healthz
curl -s http://127.0.0.1:8180/runs/run_6f0b1ad9c9d0
```

```json
{"ok":true}
{"run_id":"run_6f0b1ad9c9d0","status":"paused_approval","step":1,
 "finish_reason":null,"final_text":null,
 "pending_approval_id":"appr_788a20df625c","usage_total":15,
 "usage_estimated":false,"message_count":3}
```

**완전히 새 프로세스**(다른 PID)가 킬 전과 정확히 같은 상태를 돌려줬다.
메모리에는 아무것도 남아 있지 않았다 — 전부 `/tmp/hitl_demo.db` 파일에서
읽은 값이다.

**수정 요청으로 결정하고, 같은 결정을 한 번 더 보내 중복을 확인한다.**

```bash
APPROVAL_ID=appr_788a20df625c
curl -s -X POST http://127.0.0.1:8180/approvals/$APPROVAL_ID/decision \
  -H 'content-type: application/json' \
  -d '{"action":"edit","args":{"report_id":"notebook-q1-report","reason":"분기 종료로 보관"}}'
```

```json
{"approval":{"...":"...", "status":"edited",
  "decision_args_json":"{\"report_id\": \"notebook-q1-report\", \"reason\": \"분기 종료로 보관\"}"},
 "already_decided":false}
```

같은 승인에 실수로 "승인" 결정을 한 번 더 보낸다(더블 클릭을 흉내):

```bash
curl -s -X POST http://127.0.0.1:8180/approvals/$APPROVAL_ID/decision \
  -H 'content-type: application/json' -d '{"action":"approve"}'
```

```json
{"approval":{"...":"...", "status":"edited", "..."}, "already_decided":true}
```

`already_decided: true`이고 `status`는 여전히 `"edited"`다 — 두 번째
요청은 아무것도 바꾸지 못했다.

**재개해서 마무리한다.**

```bash
curl -sN http://127.0.0.1:8180/chat/resume/run_6f0b1ad9c9d0 \
  -H 'content-type: application/json' -d '{"tool_names":["archive_report"]}'
```

```
event: tool_call
data: {"id": "call_stub_1", "name": "archive_report", "args": {"report_id": "notebook-q1-report", "reason": "분기 종료로 보관"}}

event: tool_result
data: {"id": "call_stub_1", "ok": true, "summary": "사용자가 인자를 수정해 실행함: {\"archived\": true, \"report_id\": \"notebook-q1-report\", \"reason\": \"분기 종료로 보관\"}"}

event: token
data: {"text": "요청하신 내용을 확인했습니다."}

event: done
data: {"finish_reason": "stop", "partial": {"text": "요청하신 내용을 확인했습니다.", "steps_used": 2, "tokens_used": 30, "tokens_estimated": false}}
```

원래 스텁이 보낸 빈 인자(`{}`)가 아니라 **수정된 인자**로 실행됐다. 그리고
이 모든 과정(승인 대기 -> 프로세스 강제 종료 -> 재시작 -> 상태 복구 ->
수정 결정 -> 중복 결정 무시 -> 재개 -> 정확히 한 번 실행)이 실제 curl
호출과 실제 `kill -9`로 확인됐다.

## 6. 실패 상황 실습

### 6-1. 일부러 무한 루프를 만드는 도구

5-2절의 `scripts/demo_infinite_loop.py`가 이 절의 실습이다. 두 개의 가짜
모델(`chat_fn`)을 만들었다.

- **결코 멈추지 않는 모델 1**: `sum_sales`를 항상 완전히 같은 인자로
  부른다. 안전장치가 없다면 `MAX_AGENT_STEPS`까지 계속 실행됐을 것이다.
  실제로는 3번 실행된 뒤 4번째 시도에서 `repeated_tool_call`로 멈췄다.
- **결코 멈추지 않는 모델 2**: `flaky_lookup`을 매번 다른 질의로 부른다
  (반복 감지 (a)를 피해가도록 일부러 설계했다). 도구 자체가 결함이
  있어(`flaky_lookup`은 `query`를 결과에 반영하지 않는다) 어떤 질의를
  넣어도 항상 같은 고정 결과만 돌려준다. `no_progress`로 3번 만에 멈췄다.

두 모델 모두 스스로 멈추는 로직이 전혀 없다 — 5절의 실행 로그가 보여주듯
멈춘 것은 전부 `docagent.agent`의 판단이다. 이 스크립트를
`max_agent_steps=50`으로 실행해도(코드에서 실제로 그렇게 설정했다) 50단계까지
가지 않고 훨씬 먼저 멈췄다는 것이 "반복 감지가 최대 단계 수보다 먼저
작동한다"는 것을 보여준다.

### 6-2. `archive_report`에 잘못된 인자로 승인하면

5-3절 데모에서 스텁이 보낸 `args: {}`를 그대로 "승인"했다면 어떻게
될까. `ArchiveReportArgs`는 `report_id`, `reason`을 필수로 요구하므로
`_execute_with_cache` -> `run_tool`이 `ToolRunResult(ok=False,
error_kind="invalid_args", ...)`를 돌려준다. 이 결과는 예외로 죽지
않고 `role="tool"` 메시지로 모델에게 그대로 전달된다 — 3단계에서 배운
"도구 오류는 애플리케이션이 처리하되, 모델이 스스로 고칠 기회를 주기 위해
결과로 돌려준다"는 원칙이 승인된 도구에도 그대로 적용된다. 이 경로는
`tests/test_approval_flow.py`에는 없지만
`_resolve_pending_approval`의 "edited인데 검증 실패" 분기와 같은 코드
경로를 탄다(그 분기는 테스트에 없으므로 "검증 상태" 절에 명시한다).

### 6-3. 두 요청이 동시에 같은 run을 재개하면

`store.try_acquire()`가 없다면 어떤 일이 생기는지 코드로 짚어본다. 두
요청이 거의 동시에 `advance_run(conn, run_id)`를 부르면, 둘 다 SQLite에서
같은 `paused_approval` 상태를 읽고, 둘 다 승인을 처리하고, 둘 다 다음
단계로 진행하려 한다 — 메시지 목록에 중복된 도구 결과가 쌓이거나 모델
호출이 두 번 나갈 수 있다. `try_acquire()`의 `UPDATE runs SET
processing=1 WHERE run_id=? AND processing=0`은 오직 하나의 요청만
`rowcount == 1`을 받게 해서, 나머지는 즉시 `run_busy` 오류를 받고
물러난다. `[확인 필요: 실제 동시 요청 두 개를 만들어 레이스를 재현하는
테스트는 이 장에서 작성하지 않았다]` — SQLite의 조건부 UPDATE가 원자적
이라는 것은 SQLite 문서상의 보장에 의존했고, 이 장에서 스레드 두 개로
실제 경쟁 상황을 만들어 검증하지는 않았다.

## 7. 응용 과제

1. **승인 대상 도구 추가**: `tools.py`에 새 도구를 만들고
   `APPROVAL_REQUIRED_TOOLS`에 이름을 추가해 보라. 완료 기준: 그 도구를
   호출하면 `approval_request`가 뜨고, 거절(abort)하면 `ARCHIVED_REPORTS`
   같은 부작용이 전혀 남지 않는 것을 확인한다.
2. **반복 (b)의 창 크기 바꾸기**: `NO_PROGRESS_WINDOW`를 2로 바꾸면 몇 번
   시도 만에 멈추는지, 5로 바꾸면 어떻게 달라지는지 `demo_infinite_loop.py`
   로 확인해 보라. 완료 기준: 창 크기와 멈추기까지의 시도 횟수 사이의
   관계를 자기 말로 설명할 수 있다.
3. **도구별로 진행 기록 나누기**: 2-4절 끝에서 언급한 한계(진행 기록이
   도구 전체에 걸쳐 하나로 관리된다)를 고쳐 보라. 힌트:
   `run.progress_hashes`를 리스트 대신 `dict[str, list[str]]`(도구 이름
   -> 최근 해시들)로 바꾸고 `_register_progress`/`_no_progress_triggered`를
   그에 맞게 고친다. 완료 기준: 서로 다른 두 도구를 번갈아 호출해도
   `no_progress`가 잘못 발동하지 않는 테스트를 새로 추가해 통과시킨다.

## 8. 핵심 정리와 확인 질문

### 핵심 정리

- 승인이 필요한 실행은 애플리케이션이 멈추고 상태를 저장한 뒤 함수를
  끝낸다. "재개"는 저장된 상태에서 다시 시작하는 새 호출이지, 멈췄던
  스택을 살리는 게 아니다.
- 승인 대기 상태·실행 진행 상태·실행 결과 캐시는 전부 SQLite 파일에
  있다. 프로세스가 죽어도 파일이 남아 있으면 다음 프로세스가 그대로
  이어받는다.
- 멱등성 키(`run_id + call_id + 도구 이름 + 정규화한 인자`)로 "이미 실행한
  것"을 실행 직전에 확인한다. 결정 자체의 중복도 조건부 UPDATE(`WHERE
  status='pending'`)로 원자적으로 막는다.
- 반복 감지는 두 가지다: 같은 인자의 반복(3회)과, 인자는 다른데 결과
  해시가 바뀌지 않는 반복(창 3개 연속 동일). 후자는 전자에 걸리지 않는
  호출만 대상으로 삼아 두 판정이 서로 겹치지 않게 했다.
- 토큰 예산은 `usage`가 있으면 그대로, 없으면 글자 수 기반 추정치로
  누적한다. 추정인지 여부를 항상 함께 알린다.
- 중단은 `error`가 아니라 `done` 이벤트에 `finish_reason`과 `partial`
  (부분 결과)을 함께 담아 보낸다. `finish_reason`에는 이 장에서 추가한
  `token_budget`이 있다.

### 확인 질문

1. 승인 대기 중인 run을 재개했더니 이미 실행됐던 도구 호출이 `tool_call`
   이벤트로 다시 나타났다. 이게 왜 문제가 아닌지(혹은 문제라면 무엇을
   봐야 하는지) 멱등성 키의 구성 요소로 설명해 보라.
2. `repeated_tool_call`과 `no_progress`가 같은 호출에서 동시에 트리거될
   수 없는 이유를 `_register_progress()`의 조건(`call_counts.get(key_str,
   0) != 1`)으로 설명해 보라.
3. `done` 이벤트에 `error`가 아니라 `finish_reason`을 쓰는 이유를, 이
   장에서 새로 추가한 `token_budget` 값을 예로 들어 설명해 보라.
4. 스텁 서버(`fake_openai_server.py`)로는 왜 "여러 라운드에 걸친 반복
   호출"을 재현할 수 없었는지, 그 스텁의 어떤 동작 때문인지 짚어보라.

**이 장의 완성 코드**: `code/step08_hitl/`

**다음 장 연결점**: 9단계는 이 장에서 다룬 실행 흐름(단계·도구 호출·승인·
중단 사유)을 Trace/Span으로 기록해 실패 원인을 사후에 추적하는 방법을
다룬다. 이 장의 `run_id`, `step`, `finish_reason`, `partial`은 그대로
트레이스의 기본 축이 된다.

---

## 검증 상태

**실제로 실행해 확인한 것**

- `python3 -m py_compile`로 `code/step08_hitl/` 아래 모든 `.py` 파일의
  문법을 확인했다(`docagent/*.py`, `tests/*.py`, `scripts/*.py`).
- `pytest tests -q`로 단위 테스트 15개를 실행해 전부 통과를 확인했다(모델
  서버 없이 가짜 `chat_fn`만 사용) — 승인/거절(중단·대안 두 모드)/수정,
  중복 결정 무시, "재개해도 재실행하지 않음", "DB 파일을 새로 열어도
  상태가 이어짐"(프로세스 재시작을 새 `sqlite3` 커넥션으로 흉내), 최대
  단계·시간, 반복 (a)·(b), 토큰 예산(실측 usage·추정치 두 경우)을 모두
  포함한다.
- `scripts/demo_infinite_loop.py`를 실제로 실행해 두 무한 루프 시나리오가
  각각 `repeated_tool_call`, `no_progress`로 멈추는 것을 콘솔 출력으로
  확인했다(5-2절에 실제 출력 전문).
- `code/_tools/fake_openai_server.py --port 8141`을 실제로 띄우고, 이 장의
  FastAPI 앱(`uvicorn docagent.app:app --port 8180`)을 그 스텁을 바라보게
  실행해 다음을 curl로 실제로 확인했다: 기본 도구 호출 흐름(`sum_sales`,
  `finish_reason=stop`), 승인 요청 발생과 `done` 이벤트 없이 스트림이
  끝나는 것, `GET /runs/{id}`로 저장된 상태 조회.
- **서버 프로세스를 실제로 `kill -9`로 강제 종료한 뒤, 같은
  `DOCAGENT_STATE_DB` 파일로 새 프로세스(다른 PID로 확인)를 띄우고
  `GET /runs/{id}`가 종료 전과 동일한 상태(`paused_approval`, 같은
  `pending_approval_id`)를 돌려주는 것을 확인했다.**
- 그 새 프로세스에 대해 승인을 "수정"으로 결정하고, 같은 승인에 "승인"
  결정을 한 번 더 보내 `already_decided: true`와 함께 원래("edited")
  상태가 그대로 유지되는 것을 확인했다(중복 결정 방지).
- `/chat/resume`으로 재개해 `archive_report`가 **수정된 인자**로 정확히
  한 번 실행되고 `finish_reason=stop`으로 끝나는 것을 확인했다.
- `pip install`로 이 장이 실제로 쓰는 패키지(fastapi, pydantic, httpx,
  python-dotenv, pytest)를 설치해 버전을 확인했다(3-1절에 기록).

**확인하지 못한 것**

- LangGraph의 `interrupt()`/`Command(resume=...)` API를 실제로 설치해
  이 장의 구현과 비교하지 않았다(2-2절에 `[확인 필요]`로 표시). 이유는
  이 장에서 그 프레임워크를 쓰지 않기로 한 설계 결정 자체와 같다.
- 실제 여러 상용 OpenAI 호환 모델 서버를 대상으로 `usage` 필드 지원
  여부를 비교 조사하지 않았다(2-5절). 스텁 서버는 항상 `usage`를 주므로,
  "usage 없음" 경로는 가짜 `chat_fn`으로 단위 테스트에서만 확인했다(5-1절).
- 두 개의 재개 요청이 실제로 동시에 들어오는 레이스 컨디션을 스레드나
  별도 프로세스로 재현하는 테스트는 작성하지 않았다(6-3절). `try_acquire()`
  의 원자성은 SQLite의 조건부 UPDATE가 원자적이라는 문서상의 보장에
  의존했다.
- 6-2절(잘못된 인자를 그대로 "승인"하는 경로)은 코드 경로만 짚었을 뿐
  이 문서를 위해 별도로 재현·기록하지 않았다.
- 답변 품질이나 실제 모델의 도구 선택 판단력은 검증하지 않았다 — 5-3절의
  스텁은 모델이 아니며, 사용자 메시지 내용을 반영하지 않는다는 한계를
  본문에 그대로 밝혔다.

## 참고 문서

- PROJECT-SPEC.md (이 저장소 루트) — SSE 이벤트 스키마(4절), 에이전트
  실행 한도(7절), 공통 인터페이스(9절). 이 장에서 4절·7절·2절을 확장하며
  함께 갱신했다.
- VERIFIED-FINDINGS.md (이 저장소 루트) — 패키지 버전 실측 기록(1절).
- Python 표준 라이브러리 `sqlite3` 문서 — 이 문서 작성 환경(Python
  3.11.15)에 기본 포함되어 있음을 `import sqlite3`로 확인.
- `code/_tools/fake_openai_server.py` 소스 코드 — 도구 호출을 "한 번만,
  tools의 첫 항목만" 만든다는 사실은 이 파일의 주석과 `_chat()` 구현을
  직접 읽어 확인했다(5-3절 근거).
- 이 문서의 모든 버전 정보와 실행 결과는 2026-09-09 기준이다(작성 시점
  질문에 명시된 오늘 날짜).
