# step07_langgraph

7단계 "LangGraph와 오케스트레이션"의 완성 코드. 자세한 설명은
`../../docs/07-langgraph.md`를 본다.

## 이 단계에서 하는 것

- `docagent/graph/`에 LangGraph `StateGraph`로 "질문 분류 → 검색(dense·
  sparse 병렬) → 근거 확인 → (부족하면) 질의를 넓혀 재검색 → 답변" 그래프를
  만든다.
- 분류·근거 확인 노드는 먼저 모델의 구조화된 JSON 판단을 시도하고, 응답이
  기대한 형태가 아니면 규칙 기반 휴리스틱으로 대체한다("정해진 워크플로와
  모델 판단의 조합").
- `retrieve_dense`/`retrieve_sparse`를 같은 슈퍼스텝에서 병렬로 실행하고,
  커스텀 리듀서(`merge_hits`)로 결과를 청크 ID 기준으로 병합한다.
- `SqliteSaver`/`AsyncSqliteSaver`로 그래프 실행 상태를 체크포인트에 저장하고,
  프로세스를 껐다 새 프로세스로 다시 실행해도 중단된 지점부터 재개되는 것을
  실제로 확인한다(`scripts/demo_checkpoint_run1.py` → `run2.py`).
- 노드별로 서로 다른 오류 처리 전략을 쓴다: classify/verify는 노드 안에서
  실패를 잡아 휴리스틱으로 대체하고, retrieve_dense/retrieve_sparse는 노드
  안에서 재시도 후 실패한 쪽만 빈 결과로 남기고 계속 진행하며, answer/
  chitchat_answer는 LangGraph의 `retry_policy`로 재시도하다 안 되면 실패로
  올린다. 세 전략을 고른 이유는 `docagent/graph/build.py`와
  `docagent/graph/nodes.py`의 모듈 docstring, 그리고 이 장 6절에 있다.
- 5단계의 Milvus 하이브리드 검색·인용 검증(`docagent/rag/*.py`)을 그대로
  가져와 그래프의 실제 검색·답변 노드로 쓴다.
- Milvus 없이도 그래프 자체를 실행·테스트할 수 있게
  `docagent/rag/fake_retriever.py`(인메모리, 검색 품질을 대표하지 않는다)를
  둔다.

FastAPI·작업 큐·LangGraph의 역할 구분은 `docagent/app.py`의 모듈 docstring과
이 장 7절에 있다: FastAPI는 요청 수신과 SSE 스트리밍만, LangGraph는 상태·
분기·반복·재시도를 맡고, 이 단계에서는 별도 작업 큐를 아직 두지 않는다(이유도
같은 곳에 적었다).

## 1. 설치

```bash
cd code/step07_langgraph
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Milvus 서버와 OpenAI 호환 모델 서버(채팅 + 임베딩)가 필요하다 — 5단계와
동일하다. 이 장의 그래프 로직 자체(분기·반복·체크포인트)는 아래 3절처럼
Milvus 없이도 확인할 수 있다.

## 2. 환경 변수

```bash
cp .env.example .env
# OPENAI_BASE_URL, CHAT_MODEL, EMBEDDING_MODEL, DOCAGENT_MILVUS_URI 등을 채운다.
# DOCAGENT_CHECKPOINT_DB는 그래프 체크포인트를 저장할 SQLite 파일 경로다.
```

## 3. Milvus 없이: 그래프 단위 테스트

```bash
python -m pytest tests/ -v
```

`tests/test_graph_flow.py`가 가짜 검색·채팅 함수를 주입해(의존성 주입,
`docagent/graph/nodes.py`의 `NodeDeps`) 분류 분기, 근거 부족 시 재검색 루프와
그 상한, 검색 소스 하나가 실패해도 계속 진행하는지, 그리고 **체크포인트에서
재개했을 때 이미 끝난 노드가 다시 실행되지 않는지**를 검증한다. 전부 Milvus나
실제 모델 서버 없이 실행된다.

## 4. 학습용 스텁 모델 서버로 그래프 실제 실행 + 체크포인트 재개

```bash
python ../_tools/fake_openai_server.py --port 8131 &
export OPENAI_BASE_URL=http://127.0.0.1:8131/v1
export OPENAI_API_KEY=sk-test
export CHAT_MODEL=stub-model
export EMBEDDING_MODEL=stub-embedding

rm -f data/checkpoints.sqlite
python scripts/demo_checkpoint_run1.py   # answer 노드에서 일부러 실패한다(모델 서버 장애 흉내)
python scripts/demo_checkpoint_run2.py   # 완전히 새 프로세스. 중단된 지점(answer)부터 재개한다
```

`run2`의 출력에서 `retrieve_dense`/`retrieve_sparse`/`verify`가 다시
실행되지 않고 곧바로 `answer`부터 이어지는 것을 trace 항목 수로 확인할 수
있다.

## 5. Milvus까지 포함한 전체 실행

```bash
python -m scripts.make_sample_data
python -c "from docagent.rag.ingest import ingest_directory; ingest_directory('data/docs', recreate=True)"
uvicorn docagent.app:app --reload --port 8080
```

`http://localhost:8080`에서 질문을 입력하면 `status` 이벤트가 그래프 노드가
끝날 때마다 하나씩 도착해 실행 로그로 쌓인다(5단계는 고정된 5단계만 보여줬지만,
이 장은 실제 재검색 루프가 있으면 `retrieving`/`analyzing`이 여러 번 반복해서
찍힌다).

## 폴더 구조

```
step07_langgraph/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── config.py            # 환경 변수 (checkpoint_db_path 추가)
│   ├── events.py             # SSE 이벤트 스키마 (5단계와 동일)
│   ├── llm.py                 # chat/chat_stream/chat_json/embed (5단계와 동일)
│   ├── app.py                 # FastAPI 앱: 그래프를 SSE로 노출
│   ├── graph/
│   │   ├── state.py            # GraphState, merge_hits 리듀서
│   │   ├── nodes.py            # 노드 함수 + NodeDeps(의존성 주입)
│   │   └── build.py            # State+Node+Edge 조립, 재시도 정책
│   └── rag/
│       ├── ingest.py, store.py, search.py, citations.py   # 5단계 그대로
│       └── fake_retriever.py    # Milvus 없이 그래프를 실행하기 위한 인메모리 검색기
├── static/                    # 단순 웹 UI (실행 로그 누적 표시)
├── data/{docs/*.md, sales.csv}
├── scripts/
│   ├── make_sample_data.py
│   ├── demo_checkpoint_run1.py   # 체크포인트 실습 1/2: 도중에 실패
│   └── demo_checkpoint_run2.py   # 체크포인트 실습 2/2: 새 프로세스에서 재개
└── tests/
    ├── test_state.py           # merge_hits 리듀서
    ├── test_graph_flow.py       # 분기·반복·병렬 병합·체크포인트 재개
    ├── test_citations.py        # 5단계에서 이어받음(변경 없음)
    └── test_rrf.py               # 5단계에서 이어받음(변경 없음)
```

## 검증 상태

- `python3 -m py_compile`로 모든 `.py` 파일의 문법을 확인했다.
- `tests/`(25개, `test_graph_flow.py`의 체크포인트 재개 테스트 포함)를
  실제로 실행해 전부 통과하는 것을 확인했다. 외부 서버가 필요 없다.
- `code/_tools/fake_openai_server.py --port 8131`을 띄우고
  `docagent.rag.fake_retriever.FakeRetriever`를 주입해 그래프를 실제로
  실행했다: 분류→병렬 검색→근거 확인→답변 전체 흐름, 근거 부족 시 재검색
  루프, 검색 소스 하나가 실패했을 때의 대체 동작, `demo_checkpoint_run1.py`
  → `run2.py`의 실제 프로세스 재시작 재개, `docagent/app.py`를
  `TestClient`로 띄워 `/chat` SSE 스트림과 `/sources/{doc_id}`까지 확인했다.
- **Milvus 서버를 대상으로 한 실제 실행(컬렉션 생성·적재·검색)은 검증하지
  않았다** — 5단계와 같은 한계다. `docagent/rag/{store,search,ingest}.py`는
  5단계 코드를 그대로 가져왔고 5단계에서 Milvus Lite로 검증된 것 외에
  새로 바뀐 것이 없다.
- langgraph 1.2.11에서 재현한 문제(같은 슈퍼스텝의 형제 노드가 있을 때
  `error_handler`가 호출은 되지만 예외가 다시 던져지는 현상)는
  `docagent/graph/build.py` 모듈 docstring과 이 장 6절에 재현 스크립트
  요약과 함께 적었다.
