# step05_hybrid_rag

5단계 "하이브리드 검색과 근거 링크"의 완성 코드. 자세한 설명은
`../../docs/05-hybrid-search-citations.md`를 본다.

## 이 단계에서 하는 것

- Milvus 컬렉션에 `sparse`(SPARSE_FLOAT_VECTOR, BM25 Function) 필드를 추가한다.
- Dense(의미) 검색, Sparse(BM25 키워드) 검색, 그 둘을 RRF 또는 가중치로
  결합하는 Hybrid 검색을 구현한다.
- 모델 답변의 `[S1]`, `[S2]` 인용 표시를 "이번 검색에서 실제로 반환된 청크"와
  대조해서 검증하고, 가짜 인용은 버리고 버렸다는 사실을 답변에 남긴다.
- 검증을 통과한 인용만 `/sources/{doc_id}?page=&chunk=` 링크로 바꾼다. 그
  경로는 실제 FastAPI 라우트로 구현되어 있고, 열면 해당 청크가 강조된
  문서 원문이 보인다.

이 단계는 3단계의 도구 호출 에이전트 루프를 아직 합치지 않는다(그 작업은
6단계 LangChain 재구성에서 한다). `/chat`은 하이브리드 검색 → 인용 검증 →
답변만 하는 RAG 전용 엔드포인트다.

## 1. 설치

```bash
cd code/step05_hybrid_rag
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Milvus 서버가 필요하다. 로컬에서 가장 간단한 방법은 Milvus 공식 문서의
Docker Standalone 설치([확인 필요: 이 문서 작성 시점 기준 정확한 설치 명령은
milvus.io 공식 설치 가이드에서 최신 버전으로 확인한다]).

OpenAI 호환 모델 서버도 채팅 모델과 임베딩 모델을 모두 지원해야 한다.
`scripts/check_server_features.py`(1단계 산출물)로 도구 호출 지원 여부는
확인할 수 있지만, `/embeddings` 엔드포인트 지원 여부는 별도로 확인한다 —
모든 OpenAI 호환 서버가 임베딩 API를 지원하지는 않는다.

## 2. 환경 변수

```bash
cp .env.example .env
# .env를 열어 OPENAI_BASE_URL, CHAT_MODEL, EMBEDDING_MODEL, DOCAGENT_MILVUS_URI 등을 채운다.
```

## 3. 샘플 데이터 생성

```bash
python -m scripts.make_sample_data
```

`data/sales.csv`와 `data/docs/*.md`(제품별 분기 리포트 4개)를 만든다.

## 4. 문서 적재 (처음 시작하는 경우)

4단계 컬렉션이 아직 없다면 sparse 필드가 포함된 스키마로 바로 만든다.

```bash
python -c "from docagent.rag.ingest import ingest_directory; ingest_directory('data/docs', recreate=True)"
```

## 4-1. 4단계 컬렉션을 마이그레이션하는 경우

이미 4단계에서 dense 필드만 있는 컬렉션에 데이터를 넣어 두었다면, 지우고
새로 적재하는 대신 마이그레이션 스크립트로 sparse 필드를 추가한다.

```bash
python -m scripts.migrate_add_sparse --dense-dim 1024
```

`--dense-dim`은 기존 컬렉션의 dense 필드 차원이다(쓰는 임베딩 모델에 맞춘
값). 동작 방식은 `docagent/rag/store.py`의 `migrate_add_sparse` 함수
docstring에 설명되어 있다 — 요약하면 "내보내기 → sparse 필드가 있는 새
컬렉션 생성 → 다시 넣기 → 기존 이름으로 바꾸기" 순서다.

## 5. 실행

```bash
uvicorn docagent.app:app --reload --port 8080
```

`http://localhost:8080`을 열어 질문을 입력한다. 검색 방식(hybrid/dense/sparse)을
드롭다운에서 바꿔볼 수 있다.

## 6. Sparse/Dense/Hybrid 비교

```bash
python -m scripts.compare_search "2분기 노트북 매출이 둔화된 이유는?"
```

네 가지 결과(BM25, Dense, Hybrid-RRF, Hybrid-가중치)를 나란히 출력한다.

## 7. 단위 테스트 (Milvus/모델 서버 없이 실행 가능)

```bash
python -m pytest tests/ -v
# 또는
PYTHONPATH=. python -m unittest discover -s tests -v
```

`tests/test_citations.py`는 가짜 인용 검증 로직을, `tests/test_rrf.py`는
RRF·가중치 결합 함수를 검증한다. 두 모듈 다 Milvus·모델 서버를 호출하지
않는다.

## 폴더 구조

```
step05_hybrid_rag/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── config.py         # 환경 변수 로딩
│   ├── events.py         # SSE 이벤트 스키마 (PROJECT-SPEC.md 4장)
│   ├── llm.py             # 채팅 + 임베딩 호출
│   ├── app.py             # FastAPI 앱 (/chat, /sources/{doc_id})
│   └── rag/
│       ├── ingest.py       # 문서 읽기 → 청킹 → 임베딩 → 적재
│       ├── store.py        # Milvus 스키마·컬렉션 생성·마이그레이션
│       ├── search.py       # Dense/Sparse/Hybrid 검색, RRF/가중치 결합
│       └── citations.py    # 인용 검증 (가짜 출처 방지)
├── static/                 # 단순 웹 UI
├── data/
│   ├── docs/                # 샘플 문서 4개
│   └── sales.csv
├── scripts/
│   ├── make_sample_data.py
│   ├── migrate_add_sparse.py
│   └── compare_search.py
└── tests/
    ├── test_citations.py
    └── test_rrf.py
```

## 검증 상태

- `python3 -m py_compile`로 모든 `.py` 파일의 문법을 확인했다.
- `tests/test_citations.py`, `tests/test_rrf.py`는 실제로 실행해 17개
  테스트가 모두 통과하는 것을 확인했다(외부 서버 불필요).
- Milvus 서버·OpenAI 호환 모델 서버를 대상으로 한 실제 실행(컬렉션 생성,
  적재, 검색, `/chat` 엔드포인트 동작)은 **검증하지 않았다.** pymilvus API
  시그니처는 milvus-io/pymilvus 저장소의 소스와 예제 스크립트로 확인했다.
