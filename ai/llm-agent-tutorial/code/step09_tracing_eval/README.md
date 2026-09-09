# step09_tracing_eval

9단계 "트레이싱과 품질 평가"의 완성 코드. 자세한 설명은
`../../docs/09-tracing-eval.md`를 본다.

## 이 단계에서 하는 것

- Phoenix(로컬에서 띄우는 오픈소스 관측 플랫폼)를 연결해 요청 하나의
  실행 경로를 트레이스·스팬 계층으로 기록한다.
- `openai` SDK 호출(채팅·임베딩)은 `openinference-instrumentation-openai`로
  **자동** 계측하고, Milvus 검색·인용 검증처럼 표준 SDK가 아닌 애플리케이션
  고유 로직은 `docagent/tracing.py`로 **수동** 계측한다.
- 검색 품질(recall@k, MRR)과 답변 품질(키워드 커버리지, LLM-judge)을
  서로 다른 모듈(`eval/retrieval_metrics.py` vs `eval/answer_quality.py`)로
  분리해서 평가한다.
- 근거 인용의 (a) 집합 소속 검증(기계적)과 (b) 근거 적합성 검증(LLM-judge,
  한계 있음)을 `eval/citation_eval.py`에서 구분해서 다룬다.
- 고정 질문 묶음 `data/eval/questions.jsonl`(4·5단계 샘플 문서 기반, 정답
  청크 ID 포함)로 검색 설정을 바꿔 가며 평가하고, 결과를 비교한다
  (`eval/run_eval.py`, `eval/compare_runs.py`).

## 1. 설치

```bash
cd code/step09_tracing_eval
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Python 버전 주의**: `arize-phoenix`를 Python 3.11에서 쓸 경우
`requirements.txt`의 주석에 적힌 대로 19.18.0 이상에서 import 자체가
깨지는 것을 실제로 확인했다(`ValueError: mutable default ... boolean_names`).
Python 3.11이면 그대로(`<19.18`) 설치하고, Python 3.12 이상이면 상한을
없애도 된다.

Milvus 서버와 OpenAI 호환 모델 서버(채팅 + 임베딩)가 필요하다. 4·5단계와
동일하다.

## 2. 환경 변수

```bash
cp .env.example .env
```

트레이싱을 켜려면 `DOCAGENT_TRACING_ENABLED=true`로 설정하고, 로컬
Phoenix를 먼저 띄운다(4절).

## 3. 샘플 데이터 생성 + 문서 적재

```bash
python -m scripts.make_sample_data
python -c "from docagent.rag.ingest import ingest_directory; ingest_directory('data/docs', recreate=True)"
```

## 4. Phoenix를 로컬에서 띄우기

```bash
python -m phoenix.server.main serve --port 6006
```

`http://localhost:6006`이 Phoenix UI다. 데이터는 로컬 SQLite(기본
`~/.phoenix` 또는 `PHOENIX_WORKING_DIR`)에 저장되고, 외부로 아무것도
나가지 않는다 — 09장 3절에서 실제로 확인한 내용이다.

## 5. 애플리케이션 실행

```bash
uvicorn docagent.app:app --reload --port 8080
```

`DOCAGENT_TRACING_ENABLED=true`면 시작 로그에 다음 두 줄이 남는다.

```
... INFO docagent.tracing tracing enabled: endpoint=http://localhost:6006/v1/traces project=docagent
... INFO docagent.app OpenAIInstrumentor enabled: openai SDK 호출이 자동으로 스팬이 된다
```

질문을 보내면 Phoenix UI의 해당 프로젝트에서 `docagent.chat` 루트 스팬
아래로 `retrieval.*`, `ChatCompletion`(자동 계측), `citations.validate`가
계층으로 쌓이는 것을 볼 수 있다.

## 6. 평가 실행

```bash
# 검색 설정 하나를 고정 질문 묶음으로 채점
python -m eval.run_eval --mode sparse --top-k 1 --out eval/results/sparse_k1.json
python -m eval.run_eval --mode sparse --top-k 3 --out eval/results/sparse_k3.json
python -m eval.run_eval --mode hybrid --top-k 3 --out eval/results/hybrid_k3.json

# 두 결과를 비교(개선 전후)
python -m eval.compare_runs eval/results/sparse_k1.json eval/results/sparse_k3.json
```

## 7. 단위 테스트 (Milvus/모델 서버 없이 실행 가능)

```bash
python -m pytest tests/ -v
```

평가 지표(recall@k, MRR), 인용 (a)집합 검증, compare_runs 비교 로직,
tracing.py의 NoOp 안전성은 모두 외부 서버 없이 검증된다. LLM-judge 관련
테스트는 가짜 judge 함수를 주입한 배관 테스트이며, 실제 판정 품질은
검증하지 않는다.

## 폴더 구조

```
step09_tracing_eval/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── config.py         # 환경 변수 로딩 (+ 트레이싱 3개 필드)
│   ├── middleware.py      # 2단계와 동일: 요청 ID·처리 시간 로그
│   ├── tracing.py         # 트레이싱 초기화, traced_span 헬퍼 (신규)
│   ├── llm.py              # 채팅 + 임베딩 (openai SDK, 자동 계측 대상)
│   ├── app.py              # FastAPI 앱: /chat, /sources, 루트 스팬
│   └── rag/
│       ├── ingest.py
│       ├── store.py
│       ├── search.py       # dense/sparse/hybrid_search에 수동 스팬 추가
│       └── citations.py    # 인용 검증에 수동 스팬 추가
├── eval/                   # 신규: 평가 전용 패키지
│   ├── dataset.py           # questions.jsonl 로딩
│   ├── retrieval_metrics.py # recall@k, MRR (검색 품질)
│   ├── answer_quality.py    # 키워드 커버리지, LLM-judge (답변 품질)
│   ├── citation_eval.py     # (a) 집합 소속 검증 + (b) LLM-judge 근거 적합성
│   ├── run_eval.py          # CLI: 실제 검색으로 평가 실행
│   └── compare_runs.py      # 두 평가 결과 비교(개선 전후)
├── static/
├── data/
│   ├── docs/                 # 샘플 문서 4개
│   ├── sales.csv
│   └── eval/
│       └── questions.jsonl   # 고정 평가 데이터셋 (8문항)
├── scripts/
│   ├── make_sample_data.py
│   └── compare_search.py
└── tests/
    ├── test_citations.py       (5단계에서 그대로)
    ├── test_rrf.py              (5단계에서 그대로)
    ├── test_retrieval_metrics.py
    ├── test_citation_eval.py
    ├── test_answer_quality.py
    ├── test_compare_runs.py
    ├── test_dataset.py
    └── test_tracing.py
```

## 검증 상태

- `python3 -m py_compile`로 모든 `.py` 파일의 문법을 확인했다.
- `pytest tests/`로 50개 테스트가 모두 통과하는 것을 실제로 확인했다
  (Python 3.11.15, 이 디렉터리의 `requirements.txt`를 그대로 설치한
  가상환경 기준). 외부 서버가 필요 없는 부분(평가 지표 계산, 인용 (a)
  검증, compare_runs, tracing.py의 NoOp 동작)만 이 테스트로 검증된다.
- **로컬에서 실제로 띄워 확인한 것**: Phoenix 서버(`phoenix.server.main
  serve`, SQLite 백엔드)를 실제로 기동해 `/v1/traces`로 스팬을 보내고
  GraphQL API로 저장된 스팬(부모-자식 관계, 속성값)을 조회해 확인했다.
  Milvus Lite + 스텁 OpenAI 호환 서버(`code/_tools/fake_openai_server.py`)를
  띄워 문서 적재 → dense/sparse/hybrid 검색 → `eval.run_eval` 실행까지
  전체 파이프라인을 실행하고, `docagent.app`을 uvicorn으로 띄워 실제
  `/chat` 요청 하나가 요청 ID 로그 한 줄과 Phoenix의 트레이스(루트 스팬+
  자식 스팬 4개, `http.request_id`로 상호 연결됨)로 동시에 남는 것을
  실제로 확인했다. 자세한 수치는 09장 5절을 본다.
- **검증하지 못한 것**: 진짜 의미를 갖는 임베딩 모델·채팅 모델을 대상으로
  한 실행(스텁은 해시 기반 의사 임베딩과 고정 답변 문자열만 낸다 —
  검색 순위와 답변 내용의 실제 품질은 검증할 수 없다). LangSmith(SaaS)로의
  실제 연결은 이 환경에서 검증할 수 없다 — 이 문서는 LangSmith를 "로컬에서
  실행할 수 없다"는 이유로 대안으로만 짧게 비교하고 실제 연결 검증은
  하지 않는다. LLM-judge 함수(`llm_judge_grounding`, `llm_judge_answer`)의
  실제 판정 품질도 검증하지 않았다 — 테스트는 가짜 judge로 배관만 확인한다.
