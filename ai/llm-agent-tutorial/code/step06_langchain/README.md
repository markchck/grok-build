# step06_langchain

6단계 "LangChain으로 재구성하기"의 완성 코드. 자세한 설명은
`../../docs/06-langchain.md`를 본다.

## 이 단계에서 하는 것

- 5단계 RAG(하이브리드 검색 + 인용 검증)를 `langchain_openai.ChatOpenAI`/
  `OpenAIEmbeddings` + `langchain_milvus.Milvus`(BM25 하이브리드) + LCEL
  체인으로 재구성한다 (`/chat`).
- 3단계 도구 호출 에이전트(`while` 루프)를 `langchain.agents.create_agent` +
  커스텀 미들웨어(반복 호출 차단, 도구 오류 변환, 호출 단위 로깅)로 재구성한다
  (`/agent/chat`).
- 5단계와 같은 SSE 이벤트 스키마(`token`/`status`/`tool_call`/`tool_result`/
  `sources`/`error`/`done`)를 그대로 유지한다 — 프레임워크를 바꿔도 브라우저가
  보는 프로토콜은 바뀌지 않는다.
- `scripts/compare_with_step05.py`로 5단계(직접 구현)와 같은 입력·같은
  스텁 서버로 검색 결과·에이전트 종료 사유를 실제로 비교한다.

## 1. 설치

```bash
cd code/step06_langchain
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Milvus 서버(또는 로컬 파일 경로를 준 Milvus Lite)와 OpenAI 호환 모델
서버(채팅 + 임베딩 + 도구 호출 지원)가 필요하다. 서버 없이 배관만
확인하려면 `code/_tools/fake_openai_server.py` 스텁 서버와 Milvus Lite
파일 경로를 쓴다(아래 3절).

## 2. 환경 변수

```bash
cp .env.example .env
# .env를 열어 OPENAI_BASE_URL, CHAT_MODEL, EMBEDDING_MODEL, DOCAGENT_MILVUS_URI 등을 채운다.
```

## 3. 스텁 서버 + Milvus Lite로 실행 (모델 서버 없이 배관 확인)

```bash
# 터미널 1
python ../_tools/fake_openai_server.py --port 8121

# 터미널 2
export OPENAI_BASE_URL=http://127.0.0.1:8121/v1
export OPENAI_API_KEY=sk-test
export CHAT_MODEL=stub-model
export EMBEDDING_MODEL=stub-embedding
export DOCAGENT_MILVUS_URI=/tmp/docagent_step06.db   # 로컬 파일 경로 -> Milvus Lite
uvicorn docagent.app:app --port 8080
```

**주의**: 서버가 시작할 때(`@app.on_event("startup")`) 컬렉션이 비어 있으면
`data/docs/`를 자동으로 적재한다. 문서 적재와 서비스가 반드시 같은 프로세스
안에서 이뤄져야 한다 — 별도 스크립트로 먼저 적재해 두고 나중에 서버를
새로 띄우면(Milvus Lite + langchain-milvus BM25 하이브리드 조합에서)
`vector column must be FixedSizeList, got binary` 오류가 재현된다. 원인과
재현 방법은 `docs/06-langchain.md` 6절 "실패 상황 실습"에 그대로 남겨
뒀다.

`http://localhost:8080`에서 RAG(`/chat`)를 확인하고, 도구 호출 에이전트는
아래처럼 직접 호출해서 확인한다(웹 UI는 아직 `/chat`만 쓴다).

```bash
curl -N -X POST http://localhost:8080/agent/chat \
  -H 'content-type: application/json' \
  -d '{"message": "2분기 노트북 서울 매출 합계 알려줘"}'
```

## 4. 5단계와의 비교

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8121/v1
export OPENAI_API_KEY=sk-test
export CHAT_MODEL=stub-model
export EMBEDDING_MODEL=stub-embedding
export DOCAGENT_MILVUS_URI=/tmp/docagent_compare_step06.db
python scripts/compare_with_step05.py "2분기 노트북 매출이 둔화된 이유는?" --top-k 3
```

같은 질문에 대한 6단계(LangChain)와 5단계(직접 구현)의 검색 결과 순위,
그리고 6단계와 3단계 에이전트의 `finish_reason`을 나란히 출력한다.

## 5. 단위 테스트 (모델 서버 없이 실행 가능)

```bash
python -m pytest tests/ -v
```

`tests/test_tools.py`는 `@tool`로 만든 도구가 3단계와 같은 조건을 지키는지,
`tests/test_agent.py`는 가짜 `BaseChatModel`을 주입해 도구 호출·반복 차단·
오류 변환 미들웨어가 3단계와 같은 시나리오에서 같은 결과를 내는지,
`tests/test_citations.py`는 5단계 그대로의 인용 검증 로직을 확인한다. 셋 다
Milvus·모델 서버를 호출하지 않는다.

## 폴더 구조

```
step06_langchain/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── config.py         # 환경 변수 로딩 (5단계와 필드 동일, load/get_settings 추가)
│   ├── llm.py             # 5단계 "직접 구현" 경로 그대로 (비교용으로 유지)
│   ├── models.py          # LangChain 모델 인터페이스 (ChatOpenAI/OpenAIEmbeddings)
│   ├── tools.py            # LangChain @tool로 재구성한 3단계 도구
│   ├── agent.py             # create_agent + 커스텀 미들웨어 (3단계 while 루프 대체)
│   ├── chains.py             # LCEL RAG 체인 (5단계 _chat_stream의 답변 생성부 대체)
│   ├── events.py              # SSE 이벤트 스키마 (5단계와 동일)
│   ├── app.py                  # FastAPI 앱 (/chat, /agent/chat, /sources/{doc_id})
│   └── rag/
│       ├── store.py             # langchain_milvus.Milvus 벡터 저장소 구성
│       ├── ingest.py             # 문서 읽기 → 청킹 → Milvus.add_texts
│       ├── search.py              # dense/sparse/hybrid (weighted ranker로 근사)
│       └── citations.py            # 인용 검증 (5단계 그대로, 프레임워크 무관)
├── static/                          # 5단계 웹 UI 재사용
├── data/
│   ├── docs/                         # 5단계와 동일한 샘플 문서
│   └── sales.csv
├── scripts/
│   └── compare_with_step05.py         # 5단계와의 비교 스크립트
└── tests/
    ├── test_agent.py
    ├── test_tools.py
    └── test_citations.py
```

## 검증 상태

- `python3 -m py_compile`로 모든 `.py` 파일의 문법을 확인했다.
- `tests/` 17개 테스트를 실제로 실행해 모두 통과하는 것을 확인했다(외부
  서버 불필요, 가짜 `BaseChatModel` 사용).
- `code/_tools/fake_openai_server.py` 스텁 서버 + Milvus Lite(로컬 파일)
  조합으로 `/chat`, `/agent/chat`, `/sources/{doc_id}` 세 엔드포인트를 실제
  HTTP 요청으로 실행해 SSE 이벤트가 예상한 순서로 오는 것을 확인했다.
- `scripts/compare_with_step05.py`를 실제로 실행해, 같은 질문에 대해
  6단계(LangChain)와 5단계(직접 구현)의 하이브리드 검색 결과가 청크 ID
  순서까지 완전히 일치하는 것, 3단계와 6단계 에이전트의 `finish_reason`이
  일치하는 것을 확인했다.
- 스텁 서버는 모델이 아니므로 **답변 품질과 실제 모델 서버의 도구 호출·
  구조화 출력 지원 범위는 검증하지 않았다.** 실제 Milvus 서버(Milvus Lite가
  아닌 standalone/클러스터) 대상 실행도 검증하지 않았다.
