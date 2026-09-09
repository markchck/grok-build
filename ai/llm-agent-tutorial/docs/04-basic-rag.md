# 4단계. 기본 RAG 직접 구현하기

## 1. 학습 목표와 완성 모습

3단계까지는 모델이 계산 도구와 CSV 조회 도구를 직접 호출해 답을 만들었다. 이번
단계에서는 도구 대신 **문서 검색**을 근거로 답하는 흐름을 처음부터 직접 구현한다.
이 방식을 RAG(Retrieval-Augmented Generation, 검색으로 찾은 자료를 근거로 생성하는
방식)라고 부른다.

이번 장에서 만드는 것은 다음과 같다.

- 문서(Markdown)를 읽어 검색 단위(청크)로 자르는 파이프라인
- 청크를 임베딩(embedding, 텍스트를 의미가 가까울수록 가까운 좌표로 바꾼 벡터)으로
  바꿔 Milvus에 저장하는 코드
- 질문이 오면 같은 방식으로 임베딩해 Milvus에서 가장 가까운 청크를 찾는 코드
- 찾은 청크를 프롬프트에 넣어 모델이 그 내용만 근거로 답하게 만드는 코드
- 근거가 부족하면(검색 결과가 비었거나 유사도가 낮으면) 모델을 호출하지 않고
  "자료에서 확인되지 않는다"고 답하는 애플리케이션 쪽 차단 로직

완성 모습은 이렇다. 서버를 띄우고 `POST /api/ingest`로 `data/docs`의 리포트 5개를
적재한 뒤, "노트북 프로 15의 2분기 매출이 줄어든 이유는?"이라고 물으면 검색된
청크(`q2-2026-laptop-pro#p0-c0` 등)를 근거로 "디스플레이 패널 공급 차질로 5월
출하량이 줄었다"는 취지의 답을 받는다. 반대로 문서에 없는 내용, 예를 들어
"3분기 전망은?"이라고 물으면 모델이 지어내지 않고 근거가 없다는 답을 받는다.

이 장의 흐름표는 아래와 같다.

```
[질문]
   │
   ▼
질문 임베딩 생성 (모델 서버 - 임베딩 모델)
   │
   ▼
Milvus 벡터 검색 (애플리케이션 - top_k개 청크 + 유사도 점수)
   │
   ├─ 점수가 임계값 미만 또는 결과 없음 ──▶ 고정 응답 반환, 모델 미호출
   │
   ▼ (임계값 통과)
[자료] 블록 구성 (애플리케이션 - 청크 텍스트 + 문서명)
   │
   ▼
모델 호출 (모델 서버 - [자료] 안에서만 답하도록 지시된 프롬프트)
   │
   ▼
[답변 스트리밍]
```

## 2. 핵심 개념

### RAG가 왜 필요한가

모델은 학습 시점까지의 지식만 갖고 있고, 우리가 등록한 사내 문서나 최신 리포트는
모른다. 매번 문서 전체를 프롬프트에 넣으면 컨텍스트 한계(모델이 한 번에 읽을 수
있는 토큰 수 상한)에 걸리거나 비용이 커진다. RAG는 질문과 관련된 부분만 검색해서
그 부분만 프롬프트에 넣는 방식으로 이 문제를 푼다.

### 이 흐름에서 누가 무엇을 하는가

| 주체 | 역할 |
| --- | --- |
| 애플리케이션(`docagent`) | 문서를 읽고 자르고(청킹), 임베딩 요청을 보내고, Milvus에 저장·검색하고, 검색 결과로 프롬프트를 구성하고, 근거 부족을 판단해 모델 호출 여부를 결정한다 |
| 모델 서버(임베딩 모델) | 텍스트를 벡터로 바꾼다. 벡터의 의미(무엇이 "가깝다"인지)를 결정하는 것은 이 모델이지 애플리케이션이 아니다 |
| 모델 서버(채팅 모델) | 프롬프트에 담긴 [자료]를 읽고 질문에 답한다. 자료에 없는 내용을 채워 넣을지 말지는 지시를 따르지만, 강제하지는 못한다(그래서 애플리케이션 쪽 차단이 따로 필요하다) |
| Milvus(벡터 데이터베이스) | 임베딩 벡터를 저장하고, 질의 벡터와 가장 가까운 벡터를 빠르게 찾아준다. 검색 알고리즘 자체(근사 최근접 이웃 탐색)는 Milvus가 담당하고, 애플리케이션은 결과를 받아쓸 뿐이다 |

이 장에는 프레임워크(LangChain 등)를 쓰지 않는다. 문서 읽기, 청킹, 임베딩 호출,
Milvus 클라이언트 호출을 모두 `docagent/rag/` 아래에 직접 구현해서 각 단계에서
실제로 무슨 일이 일어나는지 보이게 한다. 6단계에서 같은 기능을 LangChain으로
다시 만들어 무엇이 줄어드는지 비교한다.

### 문서 읽기와 청킹

문서 형식마다 텍스트를 뽑는 방법이 다르다. 이 장에서는 페이지 개념이 없는
Markdown 문서를 쓰고 `page=0`으로 고정한다. PDF처럼 실제 페이지가 있는 형식은
페이지별로 텍스트를 뽑아 그 페이지 번호를 그대로 `page`에 넣으면 된다(이 장에서는
다루지 않는다).

청킹(chunking, 긴 문서를 검색 단위로 자르는 작업)이 필요한 이유는 두 가지다.

1. **컨텍스트 한계** — 문서 전체를 프롬프트에 넣을 수 없다.
2. **검색 정밀도** — 문서 하나를 통째로 임베딩하면 "이 문서에 관련 내용이 있다"까지만
   알 수 있고 "어느 부분"인지는 알 수 없다. 잘게 자를수록 질문과 실제로 관련된
   부분만 골라낼 수 있다.

청크 크기와 중첩(overlap)은 서로 다른 문제를 다룬다.

- **청크 크기**가 너무 작으면 문장이 중간에 잘려 "매출이 늘었다"와 "왜 늘었는지"가
  서로 다른 청크로 흩어진다. 검색이 그중 하나만 찾으면 원인 없이 결과만 남는다.
- **청크 크기**가 너무 크면 청크 하나에 여러 문단·주제가 섞여 임베딩이 뭉뚱그려지고,
  질문과 무관한 문장까지 모델에 함께 전달돼 정밀도가 떨어진다.
- **중첩**은 청크 경계에서 문맥이 끊기는 문제를 줄인다. 앞 청크의 끝부분을 다음
  청크 시작에 다시 포함시켜, 경계에 걸친 문장이 최소 한 청크에는 온전히 담기게
  한다. 중첩이 0이면 경계에 걸린 문맥을 잃을 수 있고, 중첩이 너무 크면(청크
  크기의 절반 이상) 같은 내용이 여러 청크에 중복돼 검색 결과가 비슷한 청크로
  도배된다.

구체적인 비교는 5절에서 실제로 크기를 바꿔 확인한다.

### 임베딩 차원을 하드코딩하지 않는 이유

임베딩 모델마다(같은 모델이라도 설정에 따라) 벡터 길이(차원)가 다르다. 예를 들어
`text-embedding-3-small`은 1536차원이지만 로컬에 띄운 다른 임베딩 모델은 384나
1024차원일 수 있다. Milvus 컬렉션은 벡터 필드의 차원을 스키마에 고정해두고,
그와 다른 길이의 벡터를 넣으면 오류를 낸다. 그래서 컬렉션을 만들기 **전에** 실제로
임베딩 API를 한 번 호출해 반환된 벡터의 길이(`len(vector)`)를 읽고, 그 값을
`dim`으로 써서 컬렉션을 만든다. 이렇게 하면 `EMBEDDING_MODEL` 값을 바꿔도 스키마
코드를 손댈 필요가 없다.

### 검색 결과를 프롬프트에 전달하기

검색으로 찾은 청크를 모델에 그냥 붙여넣지 않고, 문서명과 청크 ID를 함께 표시한
블록으로 만들어 "이 자료 안에서만 답하라"는 지시와 함께 보낸다(`docagent/rag/search.py`의
`build_messages`). 문서명을 함께 주는 이유는 모델이 답변 안에서 어떤 자료를
근거로 했는지 사람이 확인할 수 있게 하기 위해서다. 청크 ID까지 인용 표시로
연결해 클릭 가능한 링크로 바꾸는 작업은 5단계에서 한다.

### 근거 부족을 어떻게 막는가

검색 결과가 비어 있거나 점수가 낮은데도 모델에게 "모른다고 답하라"는 지시만
믿고 그대로 호출하면, 모델이 지시를 무시하고 그럴듯한 답을 지어낼 수 있다.
그래서 이 장은 두 겹으로 막는다.

1. **애플리케이션 쪽 차단(강제)**: 검색 결과가 없거나 모든 결과의 유사도 점수가
   임계값(`RETRIEVAL_SCORE_THRESHOLD`) 미만이면 **모델을 호출하지 않고** 고정된
   "근거를 찾지 못했다" 메시지를 돌려준다.
2. **모델 쪽 지시(보조)**: 임계값을 통과해 컨텍스트가 채워진 경우에도, 시스템
   프롬프트에 "[자료]에 없는 내용은 일반 지식으로 채우지 말라"고 명시한다.

1번이 실제로 지어낸 답을 막는 장치이고, 2번은 자료가 있을 때도 모델이 자료를
벗어나 답하지 않게 돕는 역할이다. 2번만 두면 모델이 지시를 지키지 않을 가능성이
남는다.

### 비슷한 개념과의 차이

| 개념 | 무엇을 하는가 | 이 장에서 하는가 |
| --- | --- | --- |
| Full-text 검색(키워드 매칭, BM25 등) | 질문의 단어가 문서에 그대로 등장하는지로 순위를 매긴다 | 아니다. 5단계에서 sparse 검색으로 추가한다 |
| Dense 벡터 검색(이 장) | 질문과 문서를 같은 임베딩 공간의 좌표로 바꿔 "의미가 가까운" 문서를 찾는다 | 한다 |
| 하이브리드 검색 | 위 두 방식의 결과를 점수로 결합한다 | 아니다. 5단계 |
| 리랭킹(reranking) | 1차 검색 결과를 더 정밀한 모델로 다시 채점해 순위를 조정한다 | 아니다. 5단계 이후 응용 대상 |

## 3. 실습 준비

### 패키지와 버전

`code/step04_basic_rag/requirements.txt`에 고정했다. 버전은 범위로 쓴다
(`PROJECT-SPEC.md` 10절). `VERIFIED-FINDINGS.md` 3절: pymilvus의 `MilvusClient.create_schema`
/ `add_field` / `prepare_index_params` 같은 이 장이 쓰는 API는 2.6.17과 3.0.1(2026-09-09
PyPI 기준 각각 2.x 최신·전체 최신) 두 휠을 직접 풀어 비교해 **동일한 형태로 존재하는 것을
확인했다.** `pymilvus[milvus-lite]` extra로 Milvus Lite(내장 모드)도 함께 설치된다
(Windows는 미지원).

```
pymilvus[milvus-lite]>=2.6,<3
openai>=3.10,<4
fastapi>=0.115,<1.0
uvicorn[standard]>=0.30,<1.0
python-dotenv>=1.0,<2
pydantic>=2.7,<3
```

### 환경 변수

`code/step04_basic_rag/.env.example`. 기존 이름(PROJECT-SPEC.md 2절)은 그대로
두고, 이 장에서 쓰는 청킹·검색 파라미터를 추가했다.

```
OPENAI_BASE_URL=http://localhost:8000/v1
OPENAI_API_KEY=sk-local-example-key
CHAT_MODEL=your-chat-model-name
EMBEDDING_MODEL=your-embedding-model-name

DOCAGENT_MILVUS_URI=http://localhost:19530
DOCAGENT_MILVUS_TOKEN=
DOCAGENT_MILVUS_COLLECTION=docagent_chunks

APP_BASE_URL=http://localhost:8080
REQUEST_TIMEOUT_SECONDS=60

# 이 장에서 추가
CHUNK_SIZE_CHARS=800
CHUNK_OVERLAP_CHARS=150
RETRIEVAL_TOP_K=4
RETRIEVAL_SCORE_THRESHOLD=0.35
```

`RETRIEVAL_SCORE_THRESHOLD`는 COSINE 유사도 기준값이다. 임베딩 모델과 문서
성격에 따라 적절한 값이 달라지므로, 실제 서버로 검색해보며 조정해야 하는 값이다.
0.35는 이 장의 샘플 문서 기준 시작값일 뿐 보편적인 값이 아니다(`[확인 필요: 실제
사용할 임베딩 모델에서의 적정 임계값]`).

### 폴더 구조

```
code/step04_basic_rag/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── __init__.py
│   ├── config.py
│   ├── llm.py
│   ├── app.py
│   ├── events.py
│   └── rag/
│       ├── __init__.py
│       ├── ingest.py
│       ├── store.py
│       └── search.py
├── scripts/
│   └── make_sample_data.py
├── static/
│   ├── index.html
│   └── app.js
└── data/
    ├── sales.csv
    └── docs/
        ├── q1-2026-laptop-pro.md
        ├── q2-2026-laptop-pro.md
        ├── q1-2026-earbuds-x.md
        ├── q2-2026-earbuds-x.md
        └── q2-2026-watch-mouse.md
```

### 샘플 데이터

`code/step04_basic_rag/scripts/make_sample_data.py`를 이 장에서 새로 만들었다.
이후 5~13단계는 이 스크립트가 만든 `data/sales.csv`와 `data/docs/`를 그대로
재사용한다. 실행하면 다음을 만든다.

- `data/sales.csv` — 컬럼 `date,product,region,units,revenue`. 제품 4종(노트북
  프로 15, 무선 이어버드 X, 스마트워치 라이트, 게이밍 마우스 G3) x 2개 분기
  (2026년 1~2분기, 월별 집계) x 지역 3곳.
- `data/docs/*.md` — 분기 리포트 5개. 각 리포트에는 매출이 늘거나 준 **원인**을
  서술한 문장이 들어 있다(예: "2월 대학가 개강 할인 프로모션", "디스플레이 패널
  공급사 생산 차질"). 이 원인 문장이 이 장의 RAG가 실제로 찾아야 하는 검색
  대상이다.

문서 하나는 `ingest.py`가 파싱할 수 있도록 아래 형식의 머리말을 갖는다.

```markdown
---
doc_id: q2-2026-laptop-pro
title: 노트북 프로 15 2026년 2분기 리포트
source_url: data/docs/q2-2026-laptop-pro.md
---
(본문)
```

실행:

```bash
cd code/step04_basic_rag
python scripts/make_sample_data.py
```

### 실행 조건

- 임베딩과 채팅 모두 지원하는 OpenAI 호환 모델 서버(1단계 참고)
- Milvus(Docker standalone 또는 Milvus Lite, 3단계 아래 "실행과 결과 확인" 앞에서 안내)

## 4. 단계별 구현

### 4.1 최소 예제 — 임베딩 한 번 호출해 차원 확인하기

Milvus 스키마를 만들기 전에, 실제로 임베딩 API를 한 번 호출해 벡터 길이를
확인하는 최소 예제부터 본다.

`docagent/llm.py`는 `PROJECT-SPEC.md` 9절이 고정한 공통 인터페이스를 쓴다. 이 장에서
처음 실제로 쓰는 함수가 `embed()`다(`EMBEDDING_MODEL`이 `Settings`에 추가되는 장이다).

`code/step04_basic_rag/docagent/llm.py` (관련 부분)
```python
def embed(texts: list[str], *, settings: Settings | None = None) -> list[list[float]]:
    if not texts:
        return []
    settings = settings or get_settings()
    client = _client(settings)
    resp = client.embeddings.create(model=settings.embedding_model, input=texts)
    items = sorted(resp.data, key=lambda item: item.index)
    return [item.embedding for item in items]
```

이 함수로 텍스트 하나를 임베딩해보면 다음처럼 차원을 알아낼 수 있다.

```python
vectors = embed(["샘플 문장"])
dense_dim = len(vectors[0])  # 예: 1536, 768, 384 ... 모델에 따라 다르다
```

이 `dense_dim` 값을 컬렉션 생성에 그대로 쓴다. 4.3절에서 이 값을 실제로 어디에
쓰는지 이어서 본다.

### 4.2 문서 읽기와 청킹

`code/step04_basic_rag/docagent/rag/ingest.py`의 핵심 부분이다. 전체 코드는
파일을 참고하고, 여기서는 역할이 나뉜 세 함수만 본다.

```python
def read_document(path: Path) -> RawDocument:
    """머리말(doc_id/title/source_url)을 파싱하고 본문 텍스트를 뽑는다."""
    ...


def chunk_text(text: str, chunk_size_chars: int, chunk_overlap_chars: int) -> list[str]:
    """문단 단위로 묶어가다가 chunk_size_chars를 넘으면 새 청크를 시작하고,
    새 청크 앞에 이전 청크의 끝 overlap_chars만큼을 이어붙인다."""
    ...


def build_chunks(
    documents: list[RawDocument], chunk_size_chars: int, chunk_overlap_chars: int
) -> list[Chunk]:
    """문서마다 chunk_text를 적용하고, PROJECT-SPEC.md 5절 형식의 청크 ID를 만든다."""
    for doc in documents:
        pieces = chunk_text(doc.text, chunk_size_chars, chunk_overlap_chars)
        for idx, piece in enumerate(pieces):
            pk = f"{doc.meta.doc_id}#p0-c{idx}"   # {doc_id}#p{page}-c{chunk_index}
            ...
```

청크 ID는 `{doc_id}#p{page}-c{chunk_index}` 형식을 이 장부터 쓴다. 페이지가 없는
Markdown 문서라 `page`는 항상 0이다. 이 ID는 Milvus 컬렉션의 기본 키(`pk`)로도
쓰인다 — 같은 문서를 같은 설정으로 다시 적재하면 같은 ID가 나오므로, 나중에
"이 답변이 어느 청크에서 왔는지"를 그대로 역추적할 수 있다.

### 4.3 컬렉션 만들기 — 실제 임베딩 차원으로

`code/step04_basic_rag/docagent/rag/store.py`

```python
def create_chunks_collection(
    client: MilvusClient, collection_name: str, dense_dim: int
) -> None:
    if client.has_collection(collection_name):
        return

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("pk", DataType.VARCHAR, is_primary=True, max_length=512)
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=dense_dim)
    schema.add_field("text", DataType.VARCHAR, max_length=8192)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=256)
    schema.add_field("doc_title", DataType.VARCHAR, max_length=512)
    schema.add_field("page", DataType.INT64)
    schema.add_field("chunk_index", DataType.INT64)
    schema.add_field("source_url", DataType.VARCHAR, max_length=1024)

    index_params = client.prepare_index_params()
    index_params.add_index(field_name="dense", index_type="AUTOINDEX", metric_type="COSINE")

    client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)
```

필드 이름과 타입은 PROJECT-SPEC.md 6절 표를 그대로 따른다. `sparse`
(SPARSE_FLOAT_VECTOR) 필드는 이 장의 스키마에 없다 — 5단계(하이브리드 검색)에서
같은 컬렉션에 추가한다. `dense`의 `dim`은 하드코딩하지 않고 4.1절에서 실제로
호출해 얻은 `dense_dim`을 그대로 받는다. 인덱스 타입은 `AUTOINDEX`(Milvus가
데이터 규모에 맞춰 적절한 인덱스를 자동으로 고르는 옵션)를 썼고, 메트릭은
`COSINE`(코사인 유사도, 1에 가까울수록 유사)으로 고정했다 — 근거 부족 판단에
쓰는 점수 임계값도 이 메트릭 기준이다.

### 4.4 저장하기

`code/step04_basic_rag/docagent/rag/store.py`

```python
def insert_chunks(
    client: MilvusClient,
    collection_name: str,
    chunks: list[Chunk],
    dense_vectors: list[list[float]],
) -> int:
    rows = [
        {
            "pk": chunk.pk,
            "dense": vector,
            "text": chunk.text,
            "doc_id": chunk.doc_id,
            "doc_title": chunk.doc_title,
            "page": chunk.page,
            "chunk_index": chunk.chunk_index,
            "source_url": chunk.source_url,
        }
        for chunk, vector in zip(chunks, dense_vectors)
    ]
    result = client.insert(collection_name=collection_name, data=rows)
    return int(result.get("insert_count", len(rows)))
```

청크와 벡터를 딕셔너리 목록으로 만들어 `client.insert()`에 한 번에 넘긴다. 필드
키 이름이 스키마의 필드 이름과 정확히 일치해야 한다.

### 4.5 검색하기

`code/step04_basic_rag/docagent/rag/store.py`

```python
def search_dense(
    client: MilvusClient,
    collection_name: str,
    query_vector: list[float],
    top_k: int,
) -> list[SearchHit]:
    results = client.search(
        collection_name=collection_name,
        data=[query_vector],
        anns_field="dense",
        limit=top_k,
        output_fields=["text", "doc_id", "doc_title", "page", "chunk_index", "source_url"],
    )
    ...
```

`data`는 질의 벡터의 리스트다(여러 질의를 한 번에 검색할 수 있어서 리스트로
받는다). 이 장은 질의 하나만 다루므로 `results[0]`이 그 질의의 검색 결과다.
`output_fields`에 적은 필드만 검색 결과에 함께 돌아온다 — 적지 않으면 `pk`와
점수만 오고 본문 텍스트나 메타데이터는 따로 조회해야 한다.

### 4.6 근거 부족 판단과 프롬프트 구성

`code/step04_basic_rag/docagent/rag/search.py`

```python
NO_EVIDENCE_MESSAGE = (
    "등록된 자료에서 이 질문에 대한 근거를 찾지 못했다. "
    "다른 표현으로 다시 질문하거나, 관련 문서를 먼저 등록해달라."
)

SYSTEM_PROMPT = (
    "당신은 사내 문서 검색 결과를 근거로만 답하는 어시스턴트다. "
    "아래 [자료] 안의 내용만 근거로 답하고, [자료]에 없는 내용은 "
    "일반 지식으로 채우지 말고 '제공된 자료에서 확인되지 않는다'고 답하라. "
    "[자료]의 문서명을 답변 안에서 밝혀 어떤 자료를 근거로 했는지 알 수 있게 하라."
)


def retrieve(..., score_threshold: float) -> RetrievalResult:
    query_vector = embed([question])[0]
    hits = search_dense(milvus_client, collection_name, query_vector, top_k)
    accepted = [h for h in hits if h.score >= score_threshold]
    return RetrievalResult(hits=hits, accepted=accepted, has_evidence=bool(accepted))
```

`accepted`가 비어 있으면(`has_evidence=False`) 호출 쪽(`app.py`)이 모델을 아예
부르지 않는다. 이게 3절에서 말한 "애플리케이션 쪽 차단"이다. `accepted`가 있을
때만 `build_messages`가 `SYSTEM_PROMPT`와 `[자료]` 블록을 채운 메시지를 만든다.

### 4.7 프로젝트에 적용 — FastAPI 엔드포인트

`code/step04_basic_rag/docagent/app.py` (발췌)

```python
@app.post("/api/ingest")
def ingest():
    documents = load_documents(DOCS_DIR)
    chunks = build_chunks(documents, chunk_size_chars=..., chunk_overlap_chars=...)
    texts = [c.text for c in chunks]
    vectors = embed(texts, settings=_settings)
    dense_dim = len(vectors[0])                      # 4.1절 — 하드코딩하지 않는다
    create_chunks_collection(_milvus_client, _settings.milvus_collection, dense_dim)
    inserted = insert_chunks(_milvus_client, _settings.milvus_collection, chunks, vectors)
    return {"ok": True, "chunks": len(chunks), "inserted": inserted, "dense_dim": dense_dim}
```

```python
async def _chat_event_stream(question: str, request: Request):
    yield events.status(events.STAGE_RETRIEVING, "관련 자료를 검색하는 중이다.")
    result = retrieve(..., question=question, top_k=..., score_threshold=...)
    yield events.sources([...])                       # PROJECT-SPEC.md 4절 sources 이벤트

    if not result.has_evidence:
        yield events.status(events.STAGE_VERIFYING, "임계값을 넘는 근거를 찾지 못했다.")
        yield events.token(NO_EVIDENCE_MESSAGE)
        yield events.done(events.FINISH_STOP)
        return

    yield events.status(events.STAGE_WRITING, "검색된 자료를 근거로 답변을 작성하는 중이다.")
    messages = build_messages(question, result.accepted)
    async for piece in achat_stream(messages, settings=_settings):
        if await request.is_disconnected():
            return
        yield events.token(piece)
    yield events.done(events.FINISH_STOP)
```

`sources` 이벤트는 2단계에서 정한 SSE 스키마를 그대로 쓰되, `accepted`(임계값을
통과한 청크)만 담는다 — 근거로 쓰지 않은 검색 결과까지 "출처"라고 보여주지 않기
위해서다. `status` 이벤트의 `stage` 값은 PROJECT-SPEC.md에 고정된
`retrieving`/`verifying`/`writing`을 그대로 쓴다.

**`chat_stream()`이 아니라 `achat_stream()`을 쓰는 이유**: PROJECT-SPEC.md 9절 —
클라이언트가 연결을 끊었을 때 모델 서버로 나가는 HTTP 스트림까지 실제로 취소하려면
이벤트 루프가 그 스트림을 직접 닫을 수 있어야 한다(2단계와 같은 이유). 동기
`chat_stream()`은 `docagent.llm`에 그대로 남아 있고, 스크립트나 서버 없이 확인하는
용도로 계속 쓸 수 있다.

## 5. 실행과 결과 확인

### 서버 없이 확인할 수 있는 부분

청킹과 청크 ID 생성은 외부 서버 없이 그 자리에서 확인할 수 있다. 이 장의
샘플 문서로 청크 크기를 바꿔 실제로 실행한 결과다.

```bash
cd code/step04_basic_rag
python scripts/make_sample_data.py
python3 - <<'PY'
from pathlib import Path
from docagent.rag.ingest import load_documents, build_chunks

docs = load_documents(Path("data/docs"))
for size, overlap in [(300, 60), (800, 150)]:
    chunks = build_chunks(docs, chunk_size_chars=size, chunk_overlap_chars=overlap)
    print(f"chunk_size={size}, overlap={overlap} -> 청크 {len(chunks)}개")
PY
```

실제로 실행한 결과:

```
chunk_size=300, overlap=60 -> 청크 6개
chunk_size=800, overlap=150 -> 청크 5개
```

문서는 5개인데 `chunk_size=300`에서는 6개가 나온다 — 리포트 하나(스마트워치
라이트·게이밍 마우스 G3 통합 리포트, 343자)가 300자를 넘어 두 청크로 나뉘었기
때문이다. 두 청크의 실제 내용을 보면 중첩이 어떻게 동작하는지 확인할 수 있다.

```
q2-2026-watch-mouse#p0-c0 (246자)
...그 결과\n6월 판매량이 특히 크게 늘었다.\n\n# 게이밍 마우스 G3 2026년 2분기 리포트\n\n게이밍 마우스 G3는 1분기 물류 지연으로 판매가 부진했으나 2분기 들어 회복했다.

q2-2026-watch-mouse#p0-c1 (157자)
026년 2분기 리포트\n\n게이밍 마우스 G3는 1분기 물류 지연으로 판매가 부진했으나 2분기 들어 회복했다.\n\n회복의 원인은...
```

"게이밍 마우스 G3는 1분기 물류 지연으로 판매가 부진했으나 2분기 들어 회복했다."라는
문장이 두 청크 모두에 걸쳐 있다 — c0 끝에도, c1 시작(중첩분)에도 들어 있어서,
검색이 c0와 c1 중 어느 쪽을 찾아도 이 문장이 잘리지 않은 채로 딸려온다. 이것이
overlap의 실제 효과다. (`chunk_size=800`에서는 모든 문서가 800자 미만이라 문서당
청크 1개, 총 5개로 나온다.)

이 확인은 이 저장소에서 실제로 실행해 통과했다(모델 서버·Milvus는 쓰지 않았다).

### 모델 서버·Milvus까지 포함한 확인

아래는 실제 서버를 띄운 상태에서 확인하는 절차다. `VERIFIED-FINDINGS.md` 8절: 이
장은 스텁 서버(`code/_tools/fake_openai_server.py`)와 Milvus Lite를 실제로 띄워
**문서 5개 적재 → 청크 5개 생성 → 임베딩(스텁 서버의 8차원 의사 임베딩) → Milvus
Lite 저장 → dense 검색 3건 반환**까지 확인했다. 실제 채팅·임베딩 모델 서버 대상
실행은 검증하지 않았다 — 아래 절차는 그대로 따라 하되, 실제 모델을 쓰면
`dense_dim`과 답변 문구가 예시와 달라진다.

**모델 서버 없이 확인하기**: 채팅·임베딩을 지원하는 실제 서버가 없다면
`code/_tools/fake_openai_server.py`를 띄우고 `.env`의 `OPENAI_BASE_URL`을 그
주소로, `DOCAGENT_MILVUS_URI`를 `./data/milvus_demo.db`처럼 로컬 파일 경로로
바꾼다(Milvus Lite로 동작한다). 이 조합으로 적재·검색 파이프라인 전체를 실제
서버 없이 확인할 수 있다 — 다만 임베딩이 의미 없는 해시 기반 벡터이므로 검색
결과의 **의미적** 적합성은 확인할 수 없다. `code/_tools/README.md` 참고.

```bash
# Milvus Lite로 빠르게 확인하려면:
# .env의 DOCAGENT_MILVUS_URI=./data/milvus_demo.db 로 설정한 뒤
uvicorn docagent.app:app --reload --port 8080
```

```bash
curl -X POST http://localhost:8080/api/ingest
```

스텁 서버 기준으로 실제로 확인한 응답 형태(임베딩 차원은 스텁 서버가 만드는
8차원 의사 벡터 기준이며, 실제 임베딩 모델을 쓰면 이 값이 달라진다):

```json
{"ok": true, "documents": 5, "chunks": 5, "inserted": 5, "dense_dim": 8}
```

```bash
curl -N -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "노트북 프로 15의 2분기 매출이 줄어든 이유는?"}'
```

SSE 응답은 `status` → `sources` → `status` → `token`(여러 번) → `done` 순서로
온다. `sources`의 `items`에 `q2-2026-laptop-pro#p0-c0`가 포함돼 있으면 검색이
올바른 문서를 찾은 것이고, 답변 텍스트에 "패널"이나 "공급" 같은 원인 관련 단어가
등장하는지로 근거를 실제로 썼는지 확인한다. 모델 출력 문구 자체는 매번 달라질
수 있으므로 문구를 그대로 기대하지 않는다.

정상 여부 확인 방법(구조 중심):
1. `sources.items`가 비어 있지 않다 — 검색이 뭔가 찾았다.
2. `sources.items`의 `id`가 실제 `data/docs`에 있는 `doc_id`로 시작한다 — 엉뚱한
   컬렉션을 검색하고 있지 않다.
3. `done.finish_reason`이 `stop`이다 — 오류 없이 끝났다.

## 6. 실패 상황 실습

### 6.1 임베딩 차원 불일치

컬렉션을 한 번 만든 뒤 `EMBEDDING_MODEL`을 다른 차원의 모델로 바꾸고 다시
`/api/ingest`를 호출하면, `client.insert()`가 벡터 길이와 스키마의 `dim`이
다르다는 오류를 낸다. 원인은 컬렉션이 이미 이전 모델의 차원으로 고정돼 있기
때문이다. 해결: 컬렉션을 새로 만들거나(`store.drop_collection` 뒤 재생성), 임베딩
모델을 바꿀 때마다 컬렉션 이름도 함께 바꾼다(`DOCAGENT_MILVUS_COLLECTION`).

### 6.2 문서 머리말 누락

`data/docs`에 `---`로 감싼 머리말이 없는 `.md` 파일을 넣으면 `read_document`가
`ValueError`를 낸다.

```
ValueError: data/docs/broken.md: 문서 머리말(---로 감싼 doc_id/title/source_url)이 없다.
```

원인은 `ingest.py`가 머리말 없는 파일의 `doc_id`/`source_url`을 알 방법이 없기
때문이다 — 빈 값으로 넘기면 청크 ID가 깨지고 근거 링크를 만들 수 없다. 실제로
이 오류를 재현하려면 다음처럼 확인할 수 있다.

```bash
cd code/step04_basic_rag
python3 - <<'PY'
from pathlib import Path
from docagent.rag.ingest import read_document

Path("data/docs/_broken_sample.md").write_text("머리말이 없는 문서", encoding="utf-8")
try:
    read_document(Path("data/docs/_broken_sample.md"))
except ValueError as e:
    print("예상한 오류:", e)
finally:
    Path("data/docs/_broken_sample.md").unlink()
PY
```

### 6.3 환경 변수 이름 충돌 — `import pymilvus` 자체가 실패한다

`DOCAGENT_MILVUS_URI` 대신 흔히 쓰는 이름인 `MILVUS_URI`를 그대로 환경 변수로
export한 상태에서 이 프로젝트를 실행하면, `docagent.rag.store`가 아니라 **`import
pymilvus` 시점에** 실패한다. `VERIFIED-FINDINGS.md` 4절에서 실제로 재현한 원인과
오류 메시지다.

```bash
export MILVUS_URI="./data/milvus_demo.db"   # Milvus Lite용 로컬 파일 경로라고 하자
python3 -c "import pymilvus"
```

```
ConnectionConfigException: Illegal uri: [...], expected form 'http[s]://[user:password@]example.com[:12345]'
```

원인은 `pymilvus/settings.py`가 `MILVUS_URI = str(os.getenv("MILVUS_URI", LEGACY_URI))`로
그 환경 변수 이름을 **자기 설정용으로 이미 예약**하고 있고, `pymilvus/orm/connections.py`가
모듈을 임포트하는 시점에 그 값을 `http(s)://` 형식인지 검증하기 때문이다(2.6.17, 3.0.1
동일하게 확인). Milvus Lite에 쓰는 로컬 파일 경로는 이 형식이 아니므로 여기서 즉시
실패한다 — `docagent`가 이 URI를 실제로 쓰기도 전에, 프로세스가 `pymilvus`를
가져오는 순간 죽는다. 그래서 이 프로젝트는 `DOCAGENT_MILVUS_URI`라는 접두사 붙은
이름을 쓴다(`PROJECT-SPEC.md` 2절) — 겹치는 이름을 피하면 이 오류 자체가 나지 않는다.
새 환경 변수를 만들 때는 쓰려는 라이브러리가 그 이름을 이미 읽고 있는지 먼저 확인한다.

### 6.4 검색 결과에서 `hit["id"]`를 쓰면 `KeyError`가 난다

`client.search(...)`가 돌려주는 각 결과 항목의 키는 `"id"`가 아니라 **컬렉션의
기본 키(primary key) 필드 이름**이다. 이 장의 스키마는 기본 키 필드 이름을
`pk`로 정했으므로(`PROJECT-SPEC.md` 6절), `hit["id"]`로 접근하면 실패한다.

```python
hit = results[0][0]
print(hit["id"])   # KeyError: 'id'
```

`VERIFIED-FINDINGS.md` 5절에서 실제로 확인한 결과 항목의 키 목록은
`['pk', 'distance', 'entity']`다. `store.search_dense`가 `hit["pk"]`로 읽는 이유가
이것이다 — 기본 키 필드 이름을 바꾸면(예: `id`로) 이 코드도 함께 바꿔야 한다.

### 6.5 새 프로세스에서는 컬렉션이 `released` 상태다

`/api/ingest`로 컬렉션을 만들고 데이터를 넣은 뒤, 서버 프로세스를 껐다가 다시
켜고 바로 `/api/chat`을 호출하면 검색이 실패한다. `VERIFIED-FINDINGS.md` 6절에서
실제로 재현한 오류다.

```
MilvusException: (code=101, message=Collection 'docagent_chunks' is in state 'released'; call load() before search/get/query)
```

원인은 `load_collection()`의 효과가 **프로세스를 넘어 유지되지 않기** 때문이다.
적재 스크립트(또는 `/api/ingest`를 처음 호출한 프로세스)와 재시작한 API 서버는
서로 다른 프로세스이므로, 새 프로세스는 컬렉션이 만들어져 있다는 것만 알 뿐 메모리에
올라와 있는 상태는 아니다. 해결은 검색 직전에 `load_collection()`을 매번 부르는
것이다(이미 로드돼 있으면 아무 일도 하지 않으므로 비용이 크지 않다) —
`create_chunks_collection`이 컬렉션이 이미 있을 때도 `load_collection`을 호출하는
이유가 이것이다.

### 6.6 근거 없는 질문

`RETRIEVAL_SCORE_THRESHOLD`보다 낮은 점수만 나오는 질문(예: 등록한 문서와 전혀
무관한 질문)을 보내면 `has_evidence=False`가 되어 모델을 호출하지 않고
`NO_EVIDENCE_MESSAGE`만 돌아온다. `sources.items`는 빈 배열이다. 이 동작은 이
장의 핵심 요구사항이므로, 응용 과제에서 임계값을 바꿔가며 직접 확인한다.

## 7. 응용 과제

1. **청크 크기 실험**: `CHUNK_SIZE_CHARS`/`CHUNK_OVERLAP_CHARS`를 각각
   (200, 40), (500, 100), (1200, 200)으로 바꿔가며 `/api/ingest`를 다시 호출하고,
   같은 질문에 대해 `sources.items`에 잡히는 청크 수와 내용이 어떻게 달라지는지
   비교한다. 완료 기준: 세 조합의 청크 개수와 각 청크의 첫 100자를 표로 정리하고,
   어느 조합에서 원인 문장이 한 청크 안에 온전히 들어가는지 설명할 수 있다.
2. **임계값 튜닝**: 관련성이 애매한 질문(예: "노트북 프로 15의 디자인은 어떤가?" —
   문서에 직접 언급이 없는 질문) 여러 개로 `RETRIEVAL_SCORE_THRESHOLD`를 0.2,
   0.35, 0.5로 바꿔가며 `has_evidence`가 어떻게 바뀌는지 관찰한다. 완료 기준:
   임계값이 너무 낮을 때 부적절한 근거가 채택되는 사례와, 너무 높을 때 있는 근거도
   버려지는 사례를 각각 하나씩 재현한다.
3. **top_k 실험**: `RETRIEVAL_TOP_K`를 2와 8로 바꿔 같은 질문의 `sources.items`
   개수와 답변 내용이 어떻게 달라지는지 비교한다. 힌트: top_k를 키우면 관련 없는
   청크가 [자료]에 섞여 들어갈 위험도 함께 커진다 — `RETRIEVAL_SCORE_THRESHOLD`와
   함께 조정해야 하는 이유를 설명해본다.

## 8. 핵심 정리와 확인 질문

### 핵심 정리

- RAG는 문서 전체 대신 질문과 관련된 부분만 찾아 프롬프트에 넣는 방식이고,
  애플리케이션이 문서를 읽고 자르고 저장하고 검색하는 일을 맡는다.
- 청킹은 컨텍스트 한계와 검색 정밀도 문제를 함께 푼다. 청크 크기와 중첩은
  독립적으로 조정해야 하는 서로 다른 손잡이다.
- 임베딩 차원은 모델마다 다르므로 하드코딩하지 않고 실제 호출로 확인한 뒤
  컬렉션을 만든다.
- Milvus 컬렉션 필드 이름(`pk`, `dense`, `text`, `doc_id`, `doc_title`, `page`,
  `chunk_index`, `source_url`)은 프로젝트 전체에서 고정이며, `sparse`는 5단계에서
  추가된다.
- 청크 ID(`{doc_id}#p{page}-c{chunk_index}`)는 이 장부터 쓰기 시작해 5단계의
  근거 링크로 이어진다.
- 근거 부족은 모델의 지시 준수에 기대지 않고, 애플리케이션이 점수 임계값으로
  먼저 차단한다. 모델 쪽 지시는 그 위에 얹는 보조 장치다.

### 확인 질문

1. 청크 크기를 두 배로 늘리면 검색 정밀도와 컨텍스트 사용량에 각각 어떤
   영향이 있는가?
2. 임베딩 차원을 하드코딩하면 어떤 상황에서 문제가 되는가? 이 장의 코드는
   이 문제를 어떻게 피하는가?
3. 검색 결과가 있는데도 모델이 근거 없는 내용을 답할 수 있는 이유는 무엇이고,
   이 장은 그것을 어떤 두 가지 장치로 막는가?
4. `sparse` 필드가 이 장의 스키마에 없는 이유는 무엇인가?

### 이 장의 완성 코드

`code/step04_basic_rag/` (README.md에 실행 순서 정리)

### 다음 장 연결점

5단계는 이 장의 dense 검색에 sparse(BM25 계열) 검색을 더해 하이브리드 검색으로
확장하고, 이 장에서 만든 청크 ID를 실제로 클릭 가능한 근거 링크로 바꾼다. 이
장의 `SearchHit`와 컬렉션 스키마를 그대로 확장해서 쓴다.

## 참고 문서

- [Quickstart | Milvus Documentation](https://milvus.io/docs/quickstart.md)
- [Create Collection | Milvus Documentation](https://milvus.io/docs/create-collection.md)
- [Run Milvus Lite Locally | Milvus Documentation](https://milvus.io/docs/milvus_lite.md)
- [Run Milvus in Docker (Linux) | Milvus Documentation](https://milvus.io/docs/install_standalone-docker.md)
- [create_collection() - Milvus pymilvus sdk v2.6.x/MilvusClient/Collections](https://milvus.io/api-reference/pymilvus/v2.6.x/MilvusClient/Collections/create_collection.md)
- [add_index() - Milvus pymilvus sdk v2.5.x/MilvusClient/Management](https://milvus.io/api-reference/pymilvus/v2.5.x/MilvusClient/Management/add_index.md)
- [prepare_index_params() - Milvus pymilvus sdk v2.4.x/MilvusClient/Management](https://milvus.io/api-reference/pymilvus/v2.4.x/MilvusClient/Management/prepare_index_params.md)
- [pymilvus · PyPI](https://pypi.org/project/pymilvus/) — 2026-09 시점 pymilvus 2.6.14와 3.0.1이 함께 확인됨. 이 장의 코드는 2.x 계열 `MilvusClient` API 기준이다.

> 위 milvus.io 문서는 이 세션의 네트워크 제한(WebFetch가 milvus.io를 직접 열람하지
> 못함)으로 페이지 전체를 직접 열람하지는 못했고, 검색 결과 스니펫으로 API 형태
> (메서드 이름, 파라미터, 코드 조각)를 교차 확인했다. `insert()`/`search()`의 정확한
> 반환 타입 필드명은 이후 실제로 pymilvus를 설치해 호출해 확인했다 — 6.4절 참고
> (`search()` 결과 항목의 키는 `"id"`가 아니라 기본 키 필드 이름이다).

> 검증 상태: `VERIFIED-FINDINGS.md` 8절에 따라 이 장은 아래까지 **실제로 실행해**
> 확인했다.
>
> - `scripts/make_sample_data.py` 실행으로 `data/sales.csv`와 `data/docs/*.md` 생성.
> - `docagent/rag/ingest.py`의 `load_documents`/`build_chunks`/`chunk_text`를 실제
>   샘플 문서로 호출해 청크 개수와 overlap 동작을 확인(5절 "서버 없이 확인할 수 있는
>   부분").
> - `code/_tools/fake_openai_server.py`(학습용 스텁 서버)와 Milvus Lite(로컬 파일
>   기반)를 실제로 띄워 문서 5개 적재 → 청크 5개 생성 → 임베딩(8차원 의사 벡터) →
>   Milvus Lite 저장 → `docagent.rag.search.retrieve`의 dense 검색 3건 반환까지
>   end-to-end로 확인했다.
> - 이 장의 모든 `.py` 파일이 `python3 -m py_compile`을 통과한다.
>
> **확인하지 못한 것**: 실제 임베딩·채팅 모델 서버(vLLM, Ollama, 상용 API 등)를
> 대상으로 한 실행, `RETRIEVAL_SCORE_THRESHOLD` 같은 값의 적정성, 답변 품질, 실제
> Docker standalone Milvus 서버(Milvus Lite가 아닌) 대상 실행은 검증하지 않았다.
> pymilvus API 시그니처 중 스텁 서버·Milvus Lite 조합으로 실제 호출해 본 것은
> 위 목록의 범위뿐이고, 그 밖의 API는 공식 문서·저장소 소스 비교로만 확인했다.
