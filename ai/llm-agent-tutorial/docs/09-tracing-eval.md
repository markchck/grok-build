# 9단계. 트레이싱과 품질 평가

## 1. 학습 목표와 완성 모습

2단계부터 이 프로젝트는 요청 하나마다 `request_id`를 붙이고 처리 시간을
로그로 남겨 왔다(`docagent.request` 로거, `code/step02_fastapi_chat/docagent/middleware.py`).
5단계까지 오면서 요청 하나 안에서 벌어지는 일은 점점 늘었다 — 하이브리드
검색(Dense + Sparse), 모델 호출, 인용 검증. 로그 한 줄은 "이 요청이 몇 ms
걸렸고 성공했는지"는 말해 주지만, "그 시간 안에서 검색이 몇 ms였는지, 모델
호출이 몇 ms였는지, 검색 결과가 몇 건이었는지, 그중 몇 개를 답변이
인용했는지"는 말해 주지 않는다. 답이 이상할 때 "검색이 틀렸나, 모델이
틀렸나"를 로그 한 줄로는 구분할 수 없다.

이 장은 두 가지를 추가한다.

1. **트레이싱**: 요청 하나의 실행 경로를 트레이스(trace)와 스팬(span)의
   계층 구조로 기록하고, 로컬에서 띄운 Phoenix로 들여다본다.
2. **평가**: 고정된 질문 묶음(`data/eval/questions.jsonl`)으로 검색
   품질(recall@k, MRR)과 답변 품질을 **각각** 채점하고, 검색 설정을 바꾼
   전후 결과를 비교한다.

이번 장은 `code/step05_hybrid_rag/`(하이브리드 검색 + 근거 링크)를
확장한다. 6·7·8단계(LangChain, LangGraph, HITL)는 이 장을 쓰는 시점에
아직 병행 집필 중이라 참고하지 않았다 — 이 장의 코드는 5단계 위에 직접
쌓는다. 새 코드는 `code/step09_tracing_eval/`에 있다.

완성 모습은 이렇다. `DOCAGENT_TRACING_ENABLED=true`로 두고 서버를 띄우면
시작 로그에 이렇게 남는다.

```
2026-09-09 07:49:40,913 INFO docagent.tracing tracing enabled: endpoint=http://127.0.0.1:6006/v1/traces project=docagent-app-demo
2026-09-09 07:49:40,914 INFO docagent.app OpenAIInstrumentor enabled: openai SDK 호출이 자동으로 스팬이 된다
```

"2분기 노트북 매출이 둔화된 이유는?"을 물으면, 요청 하나에 대해 로그
한 줄과 트레이스 하나가 동시에 남는다. 로그는 이렇다(실제로 실행해 얻은
줄이다).

```
2026-09-09 07:49:48,739 INFO docagent.request request_id=e6532afa-be52-4bec-947f-df66efb15b27 method=POST path=/chat status=200 elapsed_ms=1156.3
```

같은 요청의 Phoenix 트레이스는 스팬 5개로 이루어진 계층이다(실제로 이
요청을 보내고 Phoenix의 GraphQL API로 조회해 확인한 내용, 5절에서 자세히
다룬다).

```
docagent.chat (CHAIN, http.request_id=e6532afa-...)          elapsed_ms=1000.5
├─ retrieval.hybrid_search (RETRIEVER)                        3개 청크
│   └─ CreateEmbeddings (EMBEDDING, 자동 계측)
├─ ChatCompletion (LLM, 자동 계측)                             prompt=10 completion=5 total=15
└─ citations.validate (CHAIN)                                 used=[] dropped=[]
```

로그의 `request_id`와 트레이스의 `http.request_id` 속성이 같은 값이다 —
"이 요청이 얼마나 걸렸나"는 로그로, "그 시간 안에서 무슨 일이 있었나"는
트레이스로 답한다.

평가 쪽 완성 모습은 이렇다. 검색 설정을 바꿔 가며 같은 8개 질문을 채점하고
비교한다(아래 숫자는 실제로 실행해서 얻은 결과다, 5절에서 원인을 분석한다).

```
$ python -m eval.compare_runs eval/results/sparse_k1.json eval/results/sparse_k3.json
설정: sparse_k1(top_k=1) -> sparse_k3(top_k=3)
평균 recall@k: 0.750 -> 1.000 (+0.250)
평균 MRR:      0.750 -> 0.875 (+0.125)
hit rate:      0.750 -> 1.000 (+0.250)
```

## 2. 핵심 개념

### 2-1. 요청 단위 로그와 실행 경로의 트레이스 — 무엇이 다른가

`docs/02-fastapi-chat.md`가 만든 `RequestContextMiddleware`(순수 ASGI
미들웨어)는 **HTTP 요청 하나**를 단위로 개입한다. 요청이 시작할 때
`request_id`를 만들고, 끝날 때 상태 코드와 걸린 시간을 로그 한 줄로 남긴다.
이 로그는 "평평하다" — 요청 안에서 무슨 일이 몇 번, 어떤 순서로, 얼마나
걸려서 일어났는지는 담지 않는다.

**트레이스(trace)**는 요청 하나의 전체 실행 경로를 나타내는 단위이고,
**스팬(span)**은 그 경로 안의 작업 하나(검색 한 번, 모델 호출 한 번, 인용
검증 한 번)를 나타내는 단위다. 스팬은 부모-자식 관계로 계층을 이루고, 각
스팬은 시작/종료 시각과 속성(입력, 출력, 토큰 수, 검색 결과 개수 등)을
가진다. 요청 하나가 트레이스 하나이고, 그 안에서 일어난 검색·모델 호출·
인용 검증이 스팬들이다.

| | HTTP 미들웨어 로그(2단계) | 트레이싱(9단계) |
| --- | --- | --- |
| 단위 | HTTP 요청 하나 | 요청 안의 각 작업(스팬), 요청 전체(트레이스) |
| 구조 | 평평한 텍스트 한 줄 | 부모-자식 계층 |
| 담는 정보 | 상태 코드, 총 소요 시간 | 각 단계의 입출력, 소요 시간, 토큰 수, 검색 결과 |
| 저장 위치 | 표준 출력/로그 파일 | 관측 플랫폼(이 장은 Phoenix) |
| "왜 틀렸나"에 답하는가 | 아니오(요청이 실패했다는 사실만) | 예(어느 단계에서, 어떤 입력으로) |

로그를 없애고 트레이싱으로 대체하는 게 아니다 — 로그는 "지금 서버가
살아 있고 요청을 처리하고 있다"는 저비용 신호로 계속 남기고, 트레이싱은
그중 자세히 들여다봐야 할 요청의 원인 분석에 쓴다. 이 장의 `app.py`는
둘 다 남긴다(4-6절).

### 2-2. 관측 플랫폼과 계측 라이브러리의 책임 분리

이 장은 두 가지 다른 층의 도구를 함께 쓴다. 이름이 비슷해 보이는 도구가
많으므로 책임을 먼저 나눠 둔다.

| 층 | 하는 일 | 이 장에서 쓴 것 |
| --- | --- | --- |
| **계측 라이브러리**(instrumentation) | 애플리케이션 코드 안에서 스팬을 만들고 속성을 채운다 | OpenTelemetry API/SDK(`opentelemetry-api`), OpenInference(`openinference-instrumentation-openai`) |
| **관측 플랫폼**(observability platform) | 스팬을 받아 저장하고, 트레이스로 묶어 화면에 보여주고, 검색·필터링을 제공한다 | Phoenix(로컬 서버) |

**OpenTelemetry**는 "스팬을 어떻게 만들고 어디로 보낼지"를 정한 벤더
중립 표준(사양 + API + SDK)이다. 이 표준을 따르면 애플리케이션 코드를
바꾸지 않고도 스팬을 받는 쪽(Phoenix, 다른 OpenTelemetry Collector,
LangSmith 등)을 바꿀 수 있다 — "어디로 보내는지"는 `TracerProvider` 설정
하나로 갈린다. **OpenInference**는 OpenTelemetry 위에 "LLM 애플리케이션
전용 속성 이름"을 얹은 시맨틱 컨벤션(semantic convention)이다.
`input.value`, `llm.token_count.prompt`, `retrieval.documents.0.document.id`
같은 이름을 표준화해서, 어떤 관측 플랫폼을 쓰든 "이건 모델 호출 스팬,
이건 검색 스팬"을 같은 규칙으로 알아볼 수 있게 한다. 실제 속성 이름은
`openinference-semantic-conventions` 패키지의 `SpanAttributes`를 코드로
열어 확인했다(참고 문서). 이 장의 `docagent/tracing.py`는 그 값들을
직접 복사해 상수로 둔다 — 계측 코드가 그 패키지를 반드시 설치해야만
동작하지 않게 하기 위해서다.

**계측은 자동과 수동을 섞어 쓴다.**

- `openinference-instrumentation-openai`의 `OpenAIInstrumentor`는
  `openai` 파이썬 SDK가 만드는 모든 호출(채팅, 임베딩)을 **자동으로**
  스팬으로 감싼다 — `docagent/llm.py`를 한 줄도 고치지 않았다. 이게
  가능한 이유는 `llm.py`가 실제로 `openai.OpenAI` 클라이언트를 쓰기
  때문이다(PROJECT-SPEC.md 9절의 공통 인터페이스). 만약 이 프로젝트가
  `openai` SDK 대신 원시 `httpx` 호출로 모델 서버를 불렀다면, 이
  자동 계측은 아무것도 잡지 못했을 것이다 — 자동 계측 라이브러리는
  **특정 SDK의 객체**를 몽키패치하는 방식으로 동작하지, "HTTP로 이
  URL에 보내는 모든 요청"을 잡는 범용 도구가 아니다.
- Milvus 검색(`dense_search`/`sparse_search`/`hybrid_search`)과 인용
  검증(`validate_and_link_citations`)은 `openai` SDK 호출이 아니므로
  자동 계측 대상이 아니다. 이 장은 `docagent/tracing.py`의
  `traced_span()`으로 **수동**으로 감쌌다(4-3, 4-4절).

"표준 SDK 호출은 자동 계측에 맡기고, 애플리케이션 고유 로직은 수동으로
스팬을 연다"가 이 장에서 쓰는 원칙이다.

### 2-3. Phoenix와 LangSmith — 왜 Phoenix를 기본으로 골랐는가

| | Phoenix | LangSmith |
| --- | --- | --- |
| 실행 위치 | 로컬 프로세스(`phoenix.server.main serve`), SQLite 백엔드 | SaaS(기본), 별도 셀프호스팅 제품군 있음(이 문서에서 검증 안 함) |
| 데이터가 외부로 나가는가 | 아니오 — 로컬에만 저장된다(3-2절에서 실제로 확인) | 기본 엔드포인트가 `https://api.smith.langchain.com`(langsmith 0.12.2 소스로 확인) — 기본값 그대로 쓰면 트레이스가 LangChain의 클라우드로 간다 |
| 계측 표준 | OpenTelemetry + OpenInference(벤더 중립) | 자체 SDK(`langsmith` 패키지)와 자체 트레이싱 형식. LangChain/LangGraph와의 통합이 가장 매끄럽다 |
| 이 프로젝트와의 적합성 | 이 프로젝트는 LangChain을 아직 안 썼고(6단계에서 도입), 모델 서버도 로컬/사설망을 가정한다 | LangChain 계측에 강점이 있지만, 이 장 시점에는 그 장점을 못 쓴다 |
| 이 환경에서 검증 가능한가 | 예 — 실제로 로컬에 띄우고 스팬을 주고받는 것까지 확인했다 | 아니오 — SaaS 연결은 이 작업 환경에서 검증할 수 없다(계정, 네트워크 정책 문제) |

이 튜토리얼은 **Phoenix를 기본으로 고른다.** 이유는 "로컬에서 돌아가는가"와
"데이터가 외부로 나가는가" 두 가지다 — 이 프로젝트의 샘플 데이터가 사내
매출 리포트라는 설정이므로, 학습 단계에서부터 외부 SaaS로 요청·응답 원문이
나가는 것을 기본값으로 두고 싶지 않다. Phoenix는 SQLite에 로컬로만 저장하고
(3-2절), OpenTelemetry 표준을 쓰므로 나중에 다른 플랫폼(자체 OpenTelemetry
Collector, 또는 LangSmith)으로 옮길 때도 계측 코드를 크게 바꾸지 않아도
된다 — `docagent/tracing.py`가 만드는 스팬 자체는 Phoenix 전용이 아니다.

LangSmith는 6단계에서 LangChain을 도입한 뒤 다시 비교해 볼 만한 선택지로
남겨 둔다 — LangChain 체인·에이전트를 별도 계측 코드 없이 자동으로
추적해 주는 통합이 강점이기 때문이다. 이 장은 그 통합을 검증하지
않는다.

### 2-4. 검색 품질과 답변 품질 — 왜 분리해서 평가하는가

RAG 파이프라인의 답이 틀렸을 때 원인은 최소 두 층으로 나뉜다.

```
질문 → [검색] → 청크들 → [모델이 청크를 읽고 답 작성] → 답변
         ↑ 검색 품질:                    ↑ 답변 품질:
         정답 청크를 찾았는가?            찾은 청크를 올바르게 반영했는가?
         (recall@k, MRR)                 (키워드 포함, LLM-judge)
```

두 실패는 원인도 고치는 방법도 다르다.

- **검색부터 틀린 경우**: 정답 청크가 애초에 top-k 안에 없다. 청킹
  방식, 임베딩 모델, sparse 토크나이저, 랭커(RRF/가중치), `top_k` 값을
  고쳐야 한다. 모델 프롬프트를 아무리 고쳐도 못 찾은 정보로는 답을 못
  만든다.
- **검색은 맞았는데 답이 틀린 경우**: 정답 청크가 top-k 안에 있었는데도
  모델이 잘못 요약하거나, 있지도 않은 내용을 지어내거나(hallucination),
  "모른다"고 해야 할 상황에서 답을 지어낸다. 프롬프트, 모델 선택,
  컨텍스트 구성 방식을 고쳐야 한다.

이 둘을 하나의 점수로 합쳐 버리면("답이 맞았다/틀렸다"만 본다) 어느 쪽을
고쳐야 하는지 알 수 없다. 그래서 이 장은 `eval/retrieval_metrics.py`(검색
품질, Milvus 결과의 `pk` 문자열 집합만 본다)와 `eval/answer_quality.py`(답변
품질, 모델이 만든 텍스트만 본다)를 서로 참조하지 않는 별도 모듈로 나눈다.

### 2-5. recall@k와 MRR

- **recall@k**: 정답 청크 중 top-k 검색 결과 안에 있는 비율. 이 장의
  데이터셋은 질문당 정답 청크가 보통 1개이므로 사실상 "top-k 안에 정답이
  있는가"(0 또는 1)에 가깝다.
- **MRR(Mean Reciprocal Rank)**: 정답 청크가 등장한 순위의 역수(1위=1.0,
  2위=0.5, 3위=0.33 ...)를 질문들에 대해 평균한 값. recall@k는 "찾았는가
  못 찾았는가"만 보지만 MRR은 "얼마나 상위에서 찾았는가"까지 반영한다 —
  예를 들어 정답이 항상 top-3 안에는 있지만 순위가 3위로 밀려 있다면
  recall@3는 1.0으로 만점이어도 MRR은 낮게 나온다. 5절에서 이 차이가
  실제로 관찰된다.

### 2-6. 근거 링크 정확성 — 기계적으로 판정 가능한 것과 아닌 것

5단계는 "모델이 인용한 [Sn]이 이번 검색 결과 집합에 있는가"만 검증했다
(`docagent.rag.citations.validate_and_link_citations`). 근거 링크의
정확성은 사실 두 가지 다른 질문이다.

- **(a) 집합 소속 검증**: 인용한 청크가 이번 검색에서 실제로 반환된
  청크 집합 안에 있는가? **문자열 집합 비교라서 기계적으로 100% 판정
  가능하다.** 모호함이 없다 — 있으면 있고 없으면 없다.
- **(b) 근거 적합성(grounding) 검증**: 그 청크의 내용이 그 문장의
  주장을 실제로 뒷받침하는가? **의미를 이해해야 판정할 수 있다.**
  청크가 검색 결과 집합에는 있지만("(a)는 통과") 그 문장과 무관한
  내용일 수도 있다 — 예를 들어 모델이 "매출이 늘었다[S1]"고 썼는데
  S1 청크는 "매출이 줄었다"는 내용이면, (a)는 통과하지만 (b)는
  명백히 실패다.

이 장은 (a)를 `eval/citation_eval.py`의 `check_citation_set_membership`이
맡고, **운영 경로가 매 요청마다 하는 것과 같은 함수**
(`validate_and_link_citations`)를 그대로 재사용한다 — 평가와 운영이
서로 다른 로직으로 "인용이 유효한가"를 판단하면 평가 점수가 좋아도
운영에서는 다르게 동작하는 사고가 날 수 있기 때문이다. (b)는
`llm_judge_grounding`이 맡되, 실제 판정을 하는 함수를 인자로 주입받는
형태로 만든다 — 다음 절에서 그 이유(LLM-judge의 한계)를 다룬다.

### 2-7. LLM-judge와 그 한계

(b)처럼 문자열 비교로 답할 수 없는 질문("이 답이 맞는가", "이 청크가
저 주장을 뒷받침하는가")은 사람이 읽고 판단하거나, LLM을 심판(judge)으로
써서 그 판단을 흉내 낼 수 있다. LLM-judge는 평가 데이터셋을 사람이 매번
읽지 않아도 되게 해 주지만, 한계를 안고 쓴다.

- **편향**: 심판 모델이 특정 스타일(길다, 인용이 많다, 자기 자신이 만든
  모델의 답변과 비슷하다)을 실제 정확성과 무관하게 후하게 평가하는
  경향이 널리 보고되어 있다. 심판의 판정이 곧 정답 기준이 아니다.
- **재현성**: temperature를 0으로 고정해도 모델 버전이 바뀌면 같은
  입력에 다른 판정을 낼 수 있다. 평가 결과에는 반드시 심판 모델의
  이름과 버전을 함께 기록해야, 시간이 지나 숫자를 비교할 때 "모델이
  좋아진 건지 심판이 바뀐 건지" 구분할 수 있다.
- **비용과 속도**: 질문마다 심판 모델을 한 번 더 호출해야 하므로,
  평가 데이터셋이 커지면 비용·시간이 선형으로 는다. 기계적으로 판정
  가능한 (a)를 먼저 걸러 범위를 줄이고, (b)는 (a)를 통과한 항목에만
  돌리는 것이 실용적이다.

이 한계 때문에 이 장의 `llm_judge_grounding`/`llm_judge_answer`는 실제
모델 호출 함수를 인자로 받는 형태(의존성 주입)로 설계했다 — 테스트는
가짜 judge를 넣어 "배관이 맞는지"(입력이 올바르게 전달되고 결과가
올바르게 반환되는지)만 확인하고, 진짜 판정 품질은 검증하지 않는다.
이 장은 진짜 LLM-judge 호출을 실행하지 않았다 — 검증 상태에서 이 점을
분명히 밝힌다.

## 3. 실습 준비

### 3-1. 패키지와 버전 (실제로 설치해 확인)

`code/step09_tracing_eval/requirements.txt`에 실제로 설치해 확인한
범위를 적어 뒀다. 요약(2026-09-09, Python 3.11.15 기준 실측):

| 패키지 | 확인한 버전 | 비고 |
| --- | --- | --- |
| `arize-phoenix` | 19.17.0 정상, 19.18.0부터 20.9.0(최신)까지 Python 3.11에서 import 실패 | 아래 "실패 상황 실습" 참고. Python 3.12.3에서는 20.9.0도 정상 |
| `openinference-instrumentation-openai` | 0.1.59 | `OpenAIInstrumentor` |
| `openinference-semantic-conventions` | 0.1.35 | `SpanAttributes`, `OpenInferenceSpanKindValues`, `DocumentAttributes` |
| `opentelemetry-api` | 1.44.0 | `docagent/tracing.py`가 직접 import |
| `opentelemetry-sdk`, `opentelemetry-exporter-otlp` | 1.44.0 | `arize-phoenix`/`openinference`의 전이 의존성 |
| `openai` | 3.10.0 | PROJECT-SPEC.md 10절 기준선과 동일 |
| `pymilvus` / `milvus-lite` | 2.6.17 / 3.2.1 | 4·5단계와 동일 |

**Python 3.11에서 `arize-phoenix` 최신 버전이 깨지는 문제**를 실제로
재현했다. `pip install arize-phoenix`(2026-09-09 시점 최신 20.9.0)로
설치한 뒤 `import phoenix`만 해도 다음 오류가 난다.

```
ValueError: mutable default <class 'mappingproxy'> for field
boolean_names is not allowed: use default_factory
```

PyPI에 공개된 버전을 하나씩 설치해 가며 이분 탐색한 결과(전부 실제
설치 확인), 19.17.0까지는 정상이고 19.18.0부터 나타난다. Python
3.12.3에서는 20.9.0도 문제없이 import된다. 원인은 phoenix 내부
`phoenix/trace/dsl/filter.py`의 `@dataclass(frozen=True)` 클래스가
Python 3.11의 dataclasses "mutable default" 검사에 걸리는 것으로
보이지만, phoenix 저장소의 이슈 트래커까지 확인하지는 못했다[확인
필요: 19.18.0에서 정확히 무엇이 바뀌었는지, 이후 버전에서 다시
고쳐지는지]. 이 튜토리얼은 Python 3.11 기준이므로
`requirements.txt`에 `arize-phoenix>=17,<19.18`로 상한을 건다.

`langsmith` 패키지도 실제로 설치해 확인했다(0.12.2). 소스
(`langsmith/utils.py`)에서 기본 엔드포인트가
`https://api.smith.langchain.com`으로 하드코딩돼 있는 것과, 엔드포인트/키를
`LANGSMITH_ENDPOINT`(또는 `LANGCHAIN_ENDPOINT`), `LANGCHAIN_API_KEY`
환경 변수로 설정한다는 것을 확인했다. 이 환경에서는 실제 계정으로
연결해 트레이스를 보내는 것까지는 검증하지 못했다 — SaaS 인증이 필요한
부분은 검증할 수 없다는 이 프로젝트의 규칙에 따라 시도하지 않았다.

### 3-2. Phoenix가 로컬에서 실제로 동작하는지 확인 (실제로 띄워 확인)

관측 플랫폼을 고르는 기준으로 "로컬에서 돌아가는가"를 들었으니, 말로만
하지 않고 실제로 띄웠다.

```bash
pip install arize-phoenix
python -m phoenix.server.main serve --port 6006
```

기동 로그에 이렇게 남는다.

```
🚀 Phoenix Server 🚀
Phoenix UI: http://localhost:6006
Authentication: False
Log traces:
  - gRPC: http://localhost:4317
  - HTTP: http://localhost:6006/v1/traces
Storage: sqlite:////.../phoenix.db
```

`Storage` 줄이 SQLite 파일 경로라는 점이 핵심이다 — 별도로 아무 것도
설정하지 않아도 로컬 파일에만 저장된다. 이 상태에서 최소 코드로 스팬을
보내고, 실제로 도착하는지 확인했다.

```python
from phoenix.otel import register
tp = register(endpoint="http://127.0.0.1:6006/v1/traces", project_name="docagent-test", batch=False)
tracer = tp.get_tracer("docagent.test")
with tracer.start_as_current_span("rag.answer") as span:
    span.set_attribute("input.value", "2분기 노트북 매출이 둔화된 이유는?")
```

`curl http://127.0.0.1:6006/v1/projects`로 `docagent-test` 프로젝트가
생긴 것을 확인했다. 이 요청 전체에서 나간 네트워크 연결은
`127.0.0.1:6006` 하나뿐이다(이 프로세스가 다른 외부 호스트로 연결을
시도하지 않는다는 것은 실행 환경의 프록시 로그로 확인했다).

### 3-3. 폴더 구조 (5단계 대비 추가분)

```
code/step09_tracing_eval/
├── docagent/
│   ├── middleware.py       # 2단계와 동일 (신규: 이 장부터 다시 포함)
│   ├── tracing.py          # 신규: 트레이싱 초기화, traced_span 헬퍼
│   ├── llm.py / app.py / rag/*.py   # 5단계 기반 + 스팬 추가
├── eval/                   # 신규: 평가 전용 패키지
│   ├── dataset.py, retrieval_metrics.py, answer_quality.py,
│   │   citation_eval.py, run_eval.py, compare_runs.py
├── data/eval/questions.jsonl   # 신규: 고정 평가 데이터셋
└── tests/  (5단계 테스트 + 평가 모듈 테스트)
```

### 3-4. 환경 변수 (추가분)

PROJECT-SPEC.md 2절에 아래 세 개를 추가했다(기존 이름은 그대로).

```
DOCAGENT_TRACING_ENABLED=false
DOCAGENT_PHOENIX_ENDPOINT=http://localhost:6006/v1/traces
DOCAGENT_TRACE_PROJECT_NAME=docagent
```

`DOCAGENT_` 접두사를 붙인 이유는 4단계의 `DOCAGENT_MILVUS_URI`와 같다 —
`phoenix.otel.register()`가 접두사 없는 `PHOENIX_COLLECTOR_ENDPOINT`,
`PHOENIX_PROJECT_NAME`을 이미 자기 환경 변수로 읽는다(phoenix.otel
소스로 확인). 이 프로젝트는 설정을 `config.py` 한 곳으로 모으는
원칙(PROJECT-SPEC.md 9절)을 지키므로, 라이브러리가 예약한 이름을 그대로
쓰지 않고 `tracing.py`에서 값을 명시적으로 전달한다.

## 4. 단계별 구현

### 4-1. `docagent/tracing.py` — 계측 라이브러리를 감싸는 얇은 층

`code/step09_tracing_eval/docagent/tracing.py`

```python
def init_tracing(settings: Settings) -> bool:
    """설정에 따라 전역 TracerProvider를 등록한다."""
    if not settings.tracing_enabled:
        logger.info("tracing disabled (DOCAGENT_TRACING_ENABLED=false)")
        return False
    try:
        from phoenix.otel import register
    except ImportError:
        logger.warning(
            "DOCAGENT_TRACING_ENABLED=true지만 'arize-phoenix'가 설치돼 있지 않다. "
            "트레이싱 없이 계속 진행한다."
        )
        return False

    register(
        endpoint=settings.phoenix_collector_endpoint,
        project_name=settings.trace_project_name,
        batch=False,   # 스팬을 만들 때마다 바로 내보낸다 (이유는 주석 참고)
        verbose=False,
    )
    return True


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("docagent")
```

핵심 설계 결정 두 가지.

1. **트레이싱을 껐거나(`DOCAGENT_TRACING_ENABLED=false`) `arize-phoenix`가
   설치돼 있지 않아도 애플리케이션이 죽지 않는다.** `get_tracer()`가
   돌려주는 트레이서는 `init_tracing()`을 부르지 않아도 동작한다 —
   OpenTelemetry API는 전역 `TracerProvider`가 설정돼 있지 않으면 아무
   일도 하지 않는 `NonRecordingSpan`을 돌려준다는 것을 실제로 확인했다
   (참고 문서). 그래서 스팬을 여는 코드를 `if tracing_enabled:`로 감쌀
   필요가 없다 — 켜져 있으면 기록되고, 꺼져 있으면 조용히 아무 일도
   하지 않는다.
2. `batch=False`로 스팬을 즉시 내보낸다. 운영에서는 배치 처리로 오버헤드를
   줄이는 게 맞지만, 튜토리얼에서는 "방금 보낸 요청의 트레이스가 화면에
   왜 안 보이지?"라는 혼란을 없애는 게 더 중요하다.

`traced_span()`은 이 프로젝트 전체에서 스팬을 여는 유일한 통로다.

```python
@contextmanager
def traced_span(name, *, kind=SpanKind.CHAIN, input_value=None, attributes=None):
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        span.set_attribute(Attr.OPENINFERENCE_SPAN_KIND, kind)
        if input_value is not None:
            span.set_attribute(Attr.INPUT_VALUE, input_value)
        ...
        start = time.perf_counter()
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise
        finally:
            span.set_attribute("elapsed_ms", (time.perf_counter() - start) * 1000)
```

**주의(6절에서 실제로 재현한다)**: 이 `except`는 예외가 `with` 블록 밖으로
**올라올 때만** 스팬을 오류로 표시한다. 이 프로젝트는 3단계부터 "예외를
그대로 올리지 않고 SSE `error` 이벤트로 바꿔서 스트림에 실어 보낸다"는
설계를 쓴다 — 그러면 예외가 스팬 밖으로 나가지 않으므로 자동으로는
오류 표시가 안 된다. 그래서 `mark_error(span, code, message)`라는 별도
헬퍼를 두어, 오류를 이벤트로 바꾸는 지점마다 명시적으로 부른다.

### 4-2. `docagent/rag/search.py` — 수동 계측: 검색 스팬

`code/step09_tracing_eval/docagent/rag/search.py`(발췌, `dense_search`)

```python
def dense_search(query: str, *, limit: int = 5, ...) -> list[dict]:
    with traced_span(
        "retrieval.dense_search",
        kind=SpanKind.RETRIEVER,
        input_value=query,
        attributes={"retrieval.mode": "dense", "retrieval.top_k": limit},
    ) as span:
        ...
        hits = [_to_hit(hit) for hit in results[0]] if results else []
        set_retrieval_documents(span, hits)   # document.id/content/score
        set_output(span, f"{len(hits)}개 청크")
        return hits
```

`sparse_search`, `hybrid_search`도 같은 형태다. `set_retrieval_documents`는
OpenInference의 `retrieval.documents.N.document.{id,content,score}` 형식으로
검색 결과를 기록한다 — 청크 ID, 본문 앞부분, 점수를 스팬에 남기면, "이
질문에 어떤 청크들이 후보로 올라왔는지"를 트레이스만 보고 알 수 있다.
`hybrid_search` 안에서 부르는 `embed()`(openai SDK)는 `OpenAIInstrumentor`가
자동으로 `CreateEmbeddings` 스팬을 만들어 주는데, 이 스팬은 `traced_span`의
`with` 블록 **안에서** 호출되므로 자동으로 `retrieval.hybrid_search`의
자식으로 붙는다 — 수동 스팬과 자동 스팬이 컨텍스트로 자연스럽게 계층을
이룬다(5절에서 실제 결과로 확인한다).

### 4-3. `docagent/rag/citations.py` — 수동 계측: 인용 검증 스팬

`code/step09_tracing_eval/docagent/rag/citations.py`(발췌)

```python
def validate_and_link_citations(answer_text, retrieved, *, app_base_url):
    with traced_span(
        "citations.validate",
        kind=SpanKind.CHAIN,
        input_value=answer_text[:500],
        attributes={"citations.candidate_count": len(retrieved)},
    ) as span:
        ...
        span.set_attribute("citations.used_count", len(used_labels))
        span.set_attribute("citations.dropped_count", len(dropped_unique))
        if dropped_unique:
            span.set_attribute("citations.dropped_labels", dropped_unique)
        set_output(span, f"used={used_labels} dropped={dropped_unique}")
        return CitationResult(...)
```

이 함수의 로직 자체는 5단계와 동일하다(가짜 인용 제거). 이 장이 더한
것은 그 판정 결과(몇 개를 썼고 몇 개를 버렸는지)를 스팬 속성으로
남기는 것뿐이다 — 이 속성이 나중에 `eval/citation_eval.py`가 재사용하는
바로 그 함수의 반환값과 같은 정보다(2-6절).

### 4-4. `docagent/app.py` — 루트 스팬, 자동 계측 등록, 로깅

`code/step09_tracing_eval/docagent/app.py`(발췌)

```python
app = FastAPI(title="docagent step09: tracing + eval")
app.add_middleware(RequestContextMiddleware)   # 2단계와 동일한 요청 ID 로그

_settings = get_settings()
if init_tracing(_settings):
    from openinference.instrumentation.openai import OpenAIInstrumentor
    OpenAIInstrumentor().instrument()   # openai SDK 호출을 전부 자동 계측
```

`/chat`의 스트리밍 제너레이터를 루트 스팬으로 감쌌다.

```python
async def _chat_stream(req: ChatRequest, request_id: str) -> AsyncIterator[str]:
    with traced_span(
        "docagent.chat",
        kind=SpanKind.CHAIN,
        input_value=req.message,
        attributes={
            "http.request_id": request_id,
            "retrieval.mode": req.search_mode,
            "retrieval.top_k": req.top_k,
        },
    ) as root_span:
        async for event in _chat_stream_body(req, root_span):
            yield event
```

`request.state.request_id`(미들웨어가 붙인 값)를 스팬 속성
`http.request_id`로도 남긴다 — 이게 2-1절에서 말한 "로그 한 줄과
트레이스를 request_id로 연결한다"는 부분의 실제 구현이다. 오류 처리
지점에는 4-1절에서 만든 `mark_error`를 명시적으로 부른다.

```python
    except Exception as exc:  # Milvus 연결 실패 등
        mark_error(root_span, "retrieval_failed", str(exc))
        yield error_event("retrieval_failed", f"검색 중 오류가 발생했다: {exc}")
        yield done_event("stop")
        return
    ...
    try:
        # achat()(동기 chat()이 아니라)을 쓴다 — PROJECT-SPEC.md 9절의 취소
        # 요구사항 때문이다(2단계와 같은 이유).
        result = await achat(messages, settings=get_settings())
    except LLMError as exc:
        mark_error(root_span, "llm_call_failed", str(exc))
        yield error_event("llm_call_failed", str(exc))
        yield done_event("stop")
        return
```

모델 응답의 `usage`(있으면)를 루트 스팬에도 남긴다 — "이 요청 전체가
총 몇 토큰을 썼는가"를 한 스팬에서 보기 위해서다. 모든 OpenAI 호환
서버가 `usage`를 채워 준다고 가정하지 않는다 — 없으면 `set_llm_usage`가
조용히 건너뛴다.

```python
    usage = (result.raw or {}).get("usage") or {}
    set_llm_usage(
        root_span, model=get_settings().chat_model,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )
```

마지막으로, `docs/02-fastapi-chat.md` 6-2절이 겪은 "INFO 로그가 조용히
사라진다" 문제를 여기서도 그대로 막았다 — `docagent` 로거에 레벨과
핸들러를 직접 준다(그렇지 않으면 `docagent.request`의 요청 ID 로그가 루트
로거의 기본 레벨(WARNING)에 걸려 안 보인다).

```python
_docagent_logger = logging.getLogger("docagent")
_docagent_logger.setLevel(logging.INFO)
if not _docagent_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _docagent_logger.addHandler(_handler)
```

### 4-5. `data/eval/questions.jsonl` — 고정 평가 데이터셋

4·5단계 샘플 문서 4개(`notebook-q1-report`, `notebook-q2-report`,
`keyboard-q2-report`, `monitor-q1-report`)는 각각 500자 미만이라 청킹 후
문서당 청크가 정확히 1개다(`chunk_text()`로 실제로 확인). 그래서 정답
청크 ID는 `{doc_id}#p0-c0` 형태 4가지뿐이다. 질문 8개를 만들었다
(`code/step09_tracing_eval/data/eval/questions.jsonl`, 한 줄 발췌).

```json
{"id": "q2", "question": "노트북 판매가 감소한 원인", "expected_chunk_ids": ["notebook-q2-report#p0-c0"], "expected_keywords": ["경쟁사", "신모델"], "note": "실패 사례. '감소한'이 문서 원문의 '둔화되었다'와 토큰이 일치하지 않아 sparse(BM25) top-1이 notebook-q1-report로 틀린다"}
{"id": "q7", "question": "이 회사의 창립일은 언제인가?", "expected_chunk_ids": [], "expected_keywords": [], "expect_no_answer": true, "note": "코퍼스에 없는 질문"}
```

`expected_chunk_ids`가 검색 품질(recall@k, MRR) 채점의 정답이고,
`expected_keywords`가 답변 품질(키워드 커버리지) 채점의 정답이다.
`expect_no_answer: true`는 "찾을 게 없는 질문"을 표시한다 — 4단계
"근거 부족 시 답변 처리"와 연결된다. q2는 **VERIFIED-FINDINGS.md 7절이
이미 지적한 한국어 BM25 tokenizer 제약**("standard는 한국어를 공백
단위로만 자르므로 '매출 감소'가 '매출이... 감소했다'와 토큰이 일치하지
않는다")을 실제 검색 결과로 재현하도록 일부러 만든 실패 사례다 — 5절과
6절에서 실제로 재현한다.

### 4-6. `eval/` 패키지 — 검색 품질과 답변 품질을 분리한 평가 도구

`code/step09_tracing_eval/eval/retrieval_metrics.py`(검색 품질, 발췌)

```python
def recall_at_k(retrieved_ids: list[str], expected_ids: list[str]) -> float:
    if not expected_ids:
        return 1.0
    expected_set, retrieved_set = set(expected_ids), set(retrieved_ids)
    return len(expected_set & retrieved_set) / len(expected_set)

def mrr(retrieved_ids: list[str], expected_ids: list[str]) -> float:
    if not expected_ids:
        return 1.0
    expected_set = set(expected_ids)
    for rank, doc_id in enumerate(retrieved_ids, start=1):
        if doc_id in expected_set:
            return 1.0 / rank
    return 0.0
```

`evaluate_retrieval(questions, retriever, top_k, config_name)`은
`dense_search`/`sparse_search`/`hybrid_search`와 시그니처가 같은 아무
호출 가능 객체나 받는다 — 그래서 진짜 검색 대신 미리 정한 결과를
돌려주는 가짜 함수를 넣으면 서버 없이도 이 함수 자체를 테스트할 수
있다(7절).

`code/step09_tracing_eval/eval/citation_eval.py`(발췌, 2-6·2-7절의 구현)

```python
def check_citation_set_membership(answer_text, retrieved, *, app_base_url="..."):
    # 운영 경로와 같은 함수를 그대로 재사용한다 (a) 검증
    result = validate_and_link_citations(answer_text, retrieved, app_base_url=app_base_url)
    return CitationSetCheck(used_labels=result.used_labels, dropped_labels=result.dropped_labels)

JudgeFn = Callable[[str, str], GroundingVerdict]  # (claim_sentence, chunk_text) -> verdict

def llm_judge_grounding(label, claim_sentence, chunk_text, *, judge: JudgeFn):
    # (b) 검증: 이 함수는 모델을 호출하지 않는다 — judge를 그대로 부를 뿐이다
    verdict = judge(claim_sentence, chunk_text)
    return GroundingCheck(label=label, claim_sentence=claim_sentence, chunk_text=chunk_text, verdict=verdict)
```

`code/step09_tracing_eval/eval/answer_quality.py`(답변 품질, 발췌)

```python
def keyword_coverage(answer_text: str, expected_keywords: list[str]) -> float:
    if not expected_keywords:
        return 1.0
    lowered = answer_text.lower()
    hit = sum(1 for kw in expected_keywords if kw.lower() in lowered)
    return hit / len(expected_keywords)

def declined_to_answer(answer_text: str) -> bool:
    lowered = answer_text.lower()
    return any(marker in lowered for marker in _DECLINE_MARKERS)  # "찾지 못했다", "모른다" 등
```

`eval/run_eval.py`는 이 모듈들을 실제 검색 함수와 연결하는 CLI다 —
평가 실행 자체도 `traced_span`으로 감싸서, "이 평가를 언제 어떤 설정으로
돌렸는지"를 Phoenix 트레이스 목록에서도 검색할 수 있게 했다.
`eval/compare_runs.py`는 저장된 결과 JSON 두 개(개선 전/후)를 질문
ID로 짝지어 recall/MRR 변화를 표로 출력한다.

## 5. 실행과 결과 확인

### 5-1. 실제 요청 하나의 트레이스 (실제로 실행해 확인)

Milvus Lite + 학습용 스텁 서버(`code/_tools/fake_openai_server.py`,
해시 기반 의사 임베딩과 고정 응답만 내는 서버)로 4개 문서를 적재하고,
`uvicorn docagent.app:app`을 띄운 뒤 `/chat`에 "2분기 노트북 매출이
둔화된 이유는?"을 실제로 보냈다. Phoenix의 GraphQL API로 저장된 스팬을
조회한 결과(부모-자식 관계, 실제 값)는 이렇다.

```
docagent.chat (CHAIN, parentId=None)
  http.request_id = e6532afa-be52-4bec-947f-df66efb15b27
  retrieval = {mode: hybrid, top_k: 3}
  llm = {model_name: stub-model, token_count: {prompt: 10, completion: 5, total: 15}}
  output.value = "요청하신 내용을 확인했습니다."
├─ retrieval.hybrid_search (RETRIEVER)
│    output.value = "3개 청크"
│    retrieval.documents.0.document.content = "# 키보드 2분기 판매 리포트..."
│    └─ CreateEmbeddings (EMBEDDING, 자동 계측)
│         llm.token_count.total = 5
├─ ChatCompletion (LLM, 자동 계측, statusCode=OK)
│    llm.input_messages[0].message.content = "당신은 사내 문서 검색 비서다..."
└─ citations.validate (CHAIN)
     citations = {candidate_count: 3, used_count: 0, dropped_count: 0}
```

같은 요청의 로그 한 줄:

```
2026-09-09 07:49:48,739 INFO docagent.request request_id=e6532afa-be52-4bec-947f-df66efb15b27 method=POST path=/chat status=200 elapsed_ms=1156.3
```

`request_id`가 정확히 같은 값이다 — 로그로 "1156.3ms 걸렸고 200으로
끝났다"를 알고, 트레이스로 "그중 얼마가 검색, 얼마가 모델 호출이었는지,
임베딩 토큰이 몇 개였는지"를 안다. `ChatCompletion`과 `CreateEmbeddings`는
`llm.py`를 한 줄도 안 고쳤는데 자동으로 생겼고, 나머지 세 스팬은
`traced_span()`으로 직접 만든 것이다.

### 5-2. 검색 품질 평가: recall@k, MRR (실제로 실행해 확인)

`eval.run_eval`을 세 가지 설정으로 실제로 돌렸다(Milvus Lite + 스텁
임베딩 서버, 8개 질문 전체).

```
$ python -m eval.run_eval --mode sparse --top-k 1
평균 recall@k: 0.750   평균 MRR: 0.750   hit rate: 0.750
  [FAIL] q2 recall=0.00 mrr=0.00  노트북 판매가 감소한 원인
        기대: ['notebook-q2-report#p0-c0']  실제: ['notebook-q1-report#p0-c0']
  [FAIL] q3 recall=0.00 mrr=0.00  1분기 노트북 매출 최고치를 기록한 날짜와 판매량은?
        기대: ['notebook-q1-report#p0-c0']  실제: ['notebook-q2-report#p0-c0']

$ python -m eval.run_eval --mode sparse --top-k 3
평균 recall@k: 1.000   평균 MRR: 0.875   hit rate: 1.000

$ python -m eval.run_eval --mode hybrid --top-k 3
평균 recall@k: 1.000   평균 MRR: 0.708   hit rate: 1.000
```

`compare_runs`로 sparse top_k=1 → top_k=3을 비교하면:

```
$ python -m eval.compare_runs eval/results/sparse_k1.json eval/results/sparse_k3.json
평균 recall@k: 0.750 -> 1.000 (+0.250)
평균 MRR:      0.750 -> 0.875 (+0.125)
hit rate:      0.750 -> 1.000 (+0.250)

질문별 변화(달라진 것만):
  [q2] 노트북 판매가 감소한 원인
    recall 0.00 -> 1.00 (+1.00)  MRR 0.00 -> 0.50 (+0.50)
  [q3] 1분기 노트북 매출 최고치를 기록한 날짜와 판매량은?
    recall 0.00 -> 1.00 (+1.00)  MRR 0.00 -> 0.50 (+0.50)
```

`top_k`를 1에서 3으로 늘리면 recall과 hit rate가 모두 1.0이 된다 — q2,
q3 모두 top-1에서는 놓쳤지만 top-3 안에는 있었다는 뜻이다. **이건
근본 원인을 고친 게 아니다** — sparse(BM25)가 "감소한"과 "둔화되었다"를
여전히 다른 토큰으로 본다는 사실은 그대로다(6절에서 원인을 트레이스로
확인한다). `top_k`를 늘리는 건 증상을 완화할 뿐이고, 그 결과 MRR은
1.0이 아니라 0.875에 머문다(정답이 1위가 아니라 2위로 밀렸기 때문) —
recall@k 하나만 봤다면 이 차이를 놓쳤을 것이다. 이게 2-5절에서 recall과
MRR을 같이 봐야 하는 이유다.

sparse top_k=3 → hybrid top_k=3 비교는 예상과 다른 결과가 나왔다.

```
$ python -m eval.compare_runs eval/results/sparse_k3.json eval/results/hybrid_k3.json
평균 recall@k: 1.000 -> 1.000 (+0.000)
평균 MRR:      0.875 -> 0.708 (-0.167)

  [q1] 2분기 노트북 매출이 둔화된 이유는?
    recall 1.00 -> 1.00 (+0.00)  MRR 1.00 -> 0.50 (-0.50)
  [q6] 노트북 1분기 부산 지역 판매량이 줄어든 이유는?
    recall 1.00 -> 1.00 (+0.00)  MRR 1.00 -> 0.33 (-0.67)
```

recall은 그대로인데 MRR이 떨어졌다 — hybrid가 정답을 top-3 밖으로
밀어내지는 않았지만, 순위를 sparse 단독보다 낮췄다. **중요한 caveat**:
이 스텁 서버의 임베딩은 문장 내용과 무관한 해시 기반 의사 벡터라서
(`code/_tools/fake_openai_server.py`), Dense 검색 점수 자체에 의미가
없다. 이 결과가 "hybrid가 항상 sparse보다 나쁘다"는 뜻은 전혀 아니다 —
실제 의미 있는 임베딩 모델을 쓰면 Dense 신호가 정답 순위를 올려 줄
것이다. 이 실험이 보여주는 진짜 교훈은 **"약한(또는 무의미한) Dense
신호도 RRF 결합에서 순위를 흔들 수 있다"**는 하이브리드 검색의 구조적
특성이다 — 결합하는 두 신호 중 하나가 신뢰할 수 없으면, 결합 결과가
단일 신호보다 나빠질 수 있다. 진짜 임베딩 모델로 이 비교를 다시 하는
것을 7절 응용 과제로 남긴다.

### 5-3. 인용 (a) 검증이 운영 경로와 같은 함수를 쓰는지 확인

`eval.citation_eval.check_citation_set_membership`이
`docagent.rag.citations.validate_and_link_citations`를 그대로 호출한다는
것은 코드를 보면 알 수 있지만(4-6절), 실제로 같은 입력에 같은 결과를
내는지 `tests/test_citation_eval.py`로 확인했다(7절).

## 6. 실패 상황 실습

### 6-1. `arize-phoenix` 최신 버전이 Python 3.11에서 import부터 깨진다

**재현**: 이 문서 작성 시점 PyPI 최신(20.9.0)을 그대로 설치하고 Python
3.11.15에서 `import phoenix`만 실행한다.

```
ValueError: mutable default <class 'mappingproxy'> for field
boolean_names is not allowed: use default_factory
```

**원인 파악**: 트레이스백을 보면 `phoenix/trace/dsl/filter.py`의
`@dataclass(frozen=True)` 클래스 정의에서 터진다 — 애플리케이션 코드가
아니라 라이브러리 import 시점이므로, 이 프로젝트의 어떤 코드를 고쳐도
해결되지 않는다.

**해결**: 버전을 낮춘다. 실제로 여러 버전을 설치해 이분 탐색한 결과
19.17.0까지는 정상이다(3-1절). `requirements.txt`에
`arize-phoenix>=17,<19.18`로 상한을 걸거나, Python 3.12 이상으로
올린다(3.12.3에서 20.9.0도 정상 import되는 것을 확인했다).

### 6-2. 오류를 이벤트로 바꾸는 코드에서는 스팬이 자동으로 오류로 표시되지 않는다

**재현**: `.env`의 `CHAT_MODEL`을 스텁 서버가 400을 돌려주는 이름
(`bad-model`)으로 바꾸고 `/chat`을 호출한다.

```
event: error
data: {"code": "llm_call_failed", "message": "모델 서버 오류 응답: 400 ..."}
```

응답은 명백히 실패다. `mark_error()`를 아직 부르지 않은 코드로 이
요청의 트레이스를 조회하면:

```
ChatCompletion (LLM, 자동 계측)         statusCode = ERROR   (자동으로 잡힘)
docagent.chat (CHAIN, 루트 스팬)         statusCode = UNSET   (!!)
```

**원인 파악**: `OpenAIInstrumentor`는 `openai` SDK 호출이 예외를 던지는
순간을 직접 감싸고 있으므로 자동으로 오류를 잡는다. 하지만 루트 스팬을
연 `traced_span()`의 `with` 블록 안에서는, `except LLMError as exc:`가
그 예외를 **잡아서 SSE 이벤트로 바꾸고 조용히 반환**한다(3단계부터의
설계) — 예외가 `with` 블록 밖으로 올라가지 않으므로 `traced_span`의
`except Exception` 절도 실행되지 않는다. 자동 계측과 수동 계측의 오류
처리 범위가 다르다는 것을 실제로 확인한 것이다.

**해결**: `app.py`의 각 `except` 블록에서 `mark_error(root_span, code,
message)`를 명시적으로 부른다(4-4절 코드). 고친 뒤 같은 요청을 다시
보내고 트레이스를 확인하면:

```
docagent.chat (CHAIN, 루트 스팬)   statusCode = ERROR
  statusMessage = "모델 서버 오류 응답: 400 Error code: 400 - ..."
```

**교훈**: "예외를 잡아서 사용자 친화적인 이벤트로 바꾸는" 설계(이
프로젝트가 3단계부터 일관되게 쓰는 방식)는 스팬 계측과 상호작용할 때
주의가 필요하다 — 자동 계측이 낮은 층(모델 호출 자체)에서 오류를
잡는다고 해서 상위 스팬(요청 전체)까지 저절로 오류로 표시되지 않는다.

### 6-3. sparse(BM25) 검색이 동의어·활용형에서 놓치는 것을 트레이스로 확인한다

**재현**: `data/eval/questions.jsonl`의 q2("노트북 판매가 감소한
원인")를 `sparse_search(q2, limit=1)`로 실제로 검색하면 정답이 아닌
`notebook-q1-report#p0-c0`가 1위로 나온다(5-2절 결과).

**원인 파악**: 이 요청의 `retrieval.sparse_search` 스팬을 열어
`retrieval.documents.0.document.content`를 보면 1위로 올라온 문서가
정답 문서(2분기 리포트, "매출은... 다소 둔화되었다")가 아니라 다른
문서라는 게 바로 보인다. VERIFIED-FINDINGS.md 7절이 이미 확인한 사실
그대로다 — Milvus Lite의 BM25 tokenizer(`standard`)는 한국어를 공백
단위로만 자르므로, 질의의 "감소한"과 원문의 "둔화되었다"는 애초에
겹치는 토큰이 하나도 없다. 순위 계산이 잘못된 게 아니라, 애초에 비교
대상이 될 토큰이 없는 것이다.

**해결(과 한계)**: `top_k`를 늘리면(5-2절) 정답이 2위 정도로는 들어오지만
근본 원인은 그대로다. 진짜 해결책(형태소 분석기 적용, 동의어 사전,
쿼리 확장, 의미 있는 임베딩 모델을 쓰는 Dense/Hybrid 검색)은 이 장의
범위를 넘는다 — 이 절의 목적은 "recall@k 숫자가 낮게 나온 이유를 트레이스
(어떤 문서가 몇 위로 왔는지)로 찾아낼 수 있다"는 것을 보여주는 것이다.

## 7. 응용 과제

1. **진짜 임베딩 모델로 하이브리드 재평가**: 5-2절의 hybrid_k3 결과는
   해시 기반 의사 임베딩 때문에 sparse보다 MRR이 낮게 나왔다. 실제
   의미 있는 임베딩 모델(예: BGE 계열)을 붙이고
   `python -m eval.run_eval --mode hybrid --top-k 3`을 다시 돌려
   `compare_runs`로 비교한다. 완료 기준: 최소 q1(경쟁사 신모델 질문처럼
   패러프레이즈가 있는 질문)에서 hybrid의 MRR이 sparse 단독보다
   같거나 나아지는지 확인하고, 그렇지 않다면 이유를 트레이스로 설명한다.
2. **답변 품질 LLM-judge를 실제로 연결**: `eval/answer_quality.py`의
   `llm_judge_answer`에 진짜 모델 호출을 하는 `judge` 함수를 만들어
   `docagent.llm.chat`으로 구현하고, 8개 질문의 실제 답변을 채점한다.
   완료 기준: 채점 결과 JSON에 심판으로 쓴 모델 이름과 temperature를
   함께 기록하고(2-7절 재현성 문제 대응), `expect_no_answer` 질문에서
   모델이 실제로 거절했는지(`declined_to_answer`)와 LLM-judge 판정이
   일치하는지 비교한다.
3. **오류 스팬을 사용자 화면에도 반영**: `mark_error`로 표시한 오류가
   있으면 SSE `status` 이벤트에 `stage: "error"`를 추가로 보내도록
   `events.py`/`app.py`를 고친다. 완료 기준: 검색 실패, 모델 호출 실패
   두 경우 모두 화면에서 구분해 보여준다.

## 8. 핵심 정리와 확인 질문

- 요청 단위 로그(2단계)는 "얼마나 걸렸나"를 평평한 한 줄로 남기고,
  트레이싱(9단계)은 그 안에서 "왜 그렇게 걸렸나/틀렸나"를 스팬의
  계층으로 남긴다. 이 장은 `http.request_id` 속성으로 둘을 연결했다.
- OpenTelemetry(계측 표준) + OpenInference(LLM 전용 속성 이름)는
  "스팬을 어떻게 만들지"를, Phoenix(관측 플랫폼)는 "그 스팬을 어디서
  보고 검색할지"를 맡는다. 표준 SDK 호출(`openai`)은
  `OpenAIInstrumentor`로 자동 계측했고, 애플리케이션 고유 로직(Milvus
  검색, 인용 검증)은 `traced_span()`으로 수동 계측했다.
- Phoenix를 기본으로 고른 이유는 로컬에서 완전히 실행되고(SQLite,
  외부 연결 없음) 실제로 검증할 수 있었기 때문이다. LangSmith는 기본
  엔드포인트가 클라우드(`api.smith.langchain.com`)이고 이 환경에서
  실제 연결을 검증할 수 없어 짧게만 비교했다.
- 검색 품질(recall@k, MRR)과 답변 품질(키워드 커버리지, LLM-judge)은
  서로 다른 모듈에서 서로 다른 데이터(청크 ID 문자열 vs 모델 텍스트)만
  본다 — "검색은 맞았는데 답이 틀렸다"와 "검색부터 틀렸다"를 구분하기
  위해서다.
- 근거 링크 정확성은 (a) 집합 소속(기계적, 100% 판정 가능)과 (b) 근거
  적합성(의미적, LLM-judge 필요)으로 나뉜다. 운영 코드와 평가 코드가
  (a)를 같은 함수로 판정하게 만들어 둘이 어긋나지 않게 했다.
- LLM-judge는 편향·재현성·비용의 한계가 있어 심판 함수를 의존성
  주입으로 분리했다 — 이 장은 배관만 테스트했고 실제 판정 품질은
  검증하지 않았다.

확인 질문:
1. 이 프로젝트의 `llm.py`가 `openai` SDK 대신 원시 `httpx` 호출만
   썼다면, `OpenAIInstrumentor`가 여전히 모델 호출을 자동으로 계측할 수
   있는가? 이유를 설명할 수 있는가?
2. `traced_span()`의 `except Exception` 절이 6-2절의 `LLMError` 처리
   경로에서는 왜 실행되지 않는지, `mark_error()`가 왜 필요한지 자신의
   말로 설명할 수 있는가?
3. recall@3이 1.0인데 MRR이 1.0보다 작다면, 무엇을 의미하는가?
   (힌트: 5-2절 sparse_k3 결과)
4. 인용 (a) 집합 소속 검증과 (b) 근거 적합성 검증 중, 어느 쪽이 사람
   개입 없이 100% 확신할 수 있는 판정인지, 그 이유를 설명할 수 있는가?
5. 관측 플랫폼을 Phoenix에서 다른 OpenTelemetry 기반 도구로 바꾼다면,
   `docagent/rag/search.py`나 `citations.py`를 고쳐야 하는가? 무엇을
   고치면 되는가?

이 장의 완성 코드는 `code/step09_tracing_eval/`에 있다. 다음 장(10단계)은
로컬 함수 도구를 별도 MCP 서버로 분리한다 — 이 장에서 다룬 "검색 결과·
토큰 사용량을 트레이스로 기록한다"는 습관은 MCP 도구 호출에도 그대로
이어진다(도구 서버 호출도 이 장의 원칙대로 애플리케이션이 수동으로
스팬을 열어야 한다 — MCP 클라이언트 SDK가 OpenInference 자동 계측
대상이 아닌 한).

## 참고 문서

- Phoenix / OpenInference — 실제 설치·실행·소스 확인(2026-09-09):
  - `pip install arize-phoenix openinference-instrumentation-openai opentelemetry-sdk`로
    실제 설치, `phoenix.otel.register`, `openinference.instrumentation.openai.OpenAIInstrumentor`,
    `openinference.semconv.trace.SpanAttributes`/`OpenInferenceSpanKindValues`/`DocumentAttributes`를
    직접 import해 시그니처와 상수 값을 확인했다(본문 2-2, 3-1절).
  - `python -m phoenix.server.main serve --port 6006`로 로컬 Phoenix를 실제로
    기동하고, `phoenix.otel.register()` + OTLP HTTP(`/v1/traces`)로 스팬을
    보내 GraphQL API(`/graphql`)로 저장된 스팬(부모-자식, 속성)을 조회해
    확인했다(3-2, 5-1절).
  - PyPI(`pip index versions arize-phoenix`, `pip install arize-phoenix==<버전>`)로
    17.x~20.9.0 사이 여러 버전을 실제로 설치해 Python 3.11.15에서의
    import 성공/실패 경계(19.17.0/19.18.0)를 확인했다(3-1, 6-1절).
  - <https://pypi.org/project/arize-phoenix/> — 패키지 메타데이터,
    `Requires-Python` 확인
- OpenTelemetry:
  - `opentelemetry-api` 1.44.0을 설치해 `trace.get_tracer(...)`가
    전역 `TracerProvider` 없이 `NonRecordingSpan`을 돌려주는 것을 실제
    코드로 확인했다(본문 2-2, 4-1절).
- LangSmith:
  - `pip install langsmith`로 실제 설치(0.12.2), `langsmith/utils.py`
    소스에서 기본 엔드포인트(`https://api.smith.langchain.com`)와
    `LANGSMITH_ENDPOINT`/`LANGCHAIN_ENDPOINT`/`LANGCHAIN_API_KEY`
    환경 변수 이름을 확인했다(본문 2-3, 3-1절). 실제 계정으로의 연결은
    검증하지 않았다.
  - <https://pypi.org/project/langsmith/>
- 4·5단계 관련(재확인):
  - `VERIFIED-FINDINGS.md` 7절 — Milvus Lite BM25 tokenizer 제약
    (`standard`/`jieba`만 지원). 이 장의 q2 실패 사례(6-3절)가 이
    사실을 실제 검색 결과로 다시 확인한 것이다.
  - `docs/02-fastapi-chat.md` 4-5, 6-2절 — `RequestContextMiddleware`,
    "INFO 로그가 조용히 사라진다" 문제. 이 장의 `middleware.py`,
    `app.py`의 로거 설정은 이 절을 그대로 재사용했다.

> 검증 상태: 이 장은 아래까지 **실제로 실행해** 확인했다.
>
> - `code/step09_tracing_eval`의 모든 `.py` 파일이
>   `python3 -m py_compile`을 통과한다.
> - `pytest tests/`로 50개 테스트가 모두 통과했다(Python 3.11.15,
>   `requirements.txt`를 그대로 설치한 새 가상환경 기준). 검색 품질 지표
>   (recall@k, MRR, `evaluate_retrieval`), 인용 (a) 집합 소속 검증,
>   `compare_runs`의 비교 로직, `tracing.py`의 NoOp 동작(전역
>   `TracerProvider`가 없어도 예외 없이 동작하고 예외를 올바르게
>   다시 던지는지)은 전부 Milvus·모델 서버 없이 검증된다.
> - Phoenix 서버를 로컬로 실제 기동해 OTLP로 스팬을 보내고 GraphQL API로
>   조회했다(3-2, 5-1, 6-1, 6-2절 전부 실제 실행 결과). Milvus Lite +
>   학습용 스텁 서버(`code/_tools/fake_openai_server.py`)로 문서 4개를
>   실제 적재하고, dense/sparse/hybrid 검색과 `/chat` 엔드포인트 전체
>   파이프라인을 uvicorn으로 실제 기동해 확인했다. `eval.run_eval`을
>   세 가지 설정(sparse k=1/3, hybrid k=3)으로 실제 실행해 5절의 모든
>   수치를 얻었다. `mark_error` 적용 전/후 오류 스팬 상태 변화(6-2절)를
>   실제로 재현했다.
> - `requirements.txt`를 완전히 새 가상환경에 설치해 정상 동작(전체
>   테스트 통과 포함)하는 것을 확인했다.
>
> **확인하지 못한 것**: 진짜 의미를 갖는 임베딩·채팅 모델 대상 실행 —
> 스텁 서버는 해시 기반 의사 임베딩과 고정 문자열 답변만 내므로, 5-2절의
> hybrid 대 sparse 비교나 답변 품질 자체는 실제 모델 품질을 반영하지
> 않는다(각 절에 caveat을 남겼다). LangSmith로의 실제 SaaS 연결. Phoenix
> UI를 실제 브라우저로 열어 시각적으로 확인하는 것(GraphQL API 조회로
> 데이터가 올바르게 저장·연결되는 것만 확인했다). `llm_judge_grounding`/
> `llm_judge_answer`를 진짜 모델로 호출했을 때의 판정 품질(가짜 judge로
> 배관만 테스트했다). Milvus Lite가 아닌 Docker standalone Milvus
> 서버 대상 실행(4·5단계와 동일하게 검증 범위 밖).
