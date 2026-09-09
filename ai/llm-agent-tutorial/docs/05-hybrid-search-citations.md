# 5단계. 하이브리드 검색과 근거 링크

## 1. 학습 목표와 완성 모습

4단계에서는 질문을 임베딩해 Milvus에서 가장 비슷한 청크를 찾는 Dense 벡터
검색만으로 RAG를 만들었다. Dense 검색은 "의미가 비슷한 문장"은 잘 찾지만,
제품명·날짜·수치처럼 정확히 일치해야 하는 단어에는 약할 때가 있다. 이 장은
두 가지를 추가한다.

1. **하이브리드 검색**: Dense(의미) 검색과 Sparse(키워드, BM25) 검색을 함께
   돌리고 그 결과를 하나의 순위로 합친다.
2. **근거 링크와 가짜 출처 방지**: 모델 답변에 `[S1]`, `[S2]` 같은 인용
   표시만 쓰게 하고, 애플리케이션이 "이번 검색에서 실제로 반환된 청크"와
   대조해 검증한 다음에만 클릭 가능한 링크로 바꾼다. 검색 결과에 없는 번호를
   모델이 지어냈다면 그 인용을 버리고, 버렸다는 사실을 답변에 남긴다.

이번 장은 `code/step04_basic_rag/`의 Milvus 컬렉션(청크 ID, dense 벡터,
문서 메타데이터)을 확장한다. 새 코드는 `code/step05_hybrid_rag/`에 있다.

완성 모습은 이렇다. 사용자가 "2분기 노트북 매출이 둔화된 이유는?"이라고
물으면:

```
[retrieving] '2분기 노트북 매출이 둔화된 이유는?' 관련 청크를 하이브리드 검색으로 찾는 중
[analyzing]  4개 청크를 찾았다. 답변을 준비하는 중
[verifying]  답변의 인용 표시가 실제 검색 결과를 가리키는지 확인하는 중
[writing]    검증된 답변을 전달하는 중

2분기 노트북 매출은 경쟁사의 신모델 출시로 둔화되었다[S1]. 4월 중순
경쟁사가 비슷한 가격대의 신제품을 내놓으면서 일부 고객이 구매를 미루고
비교 검토에 들어간 것으로 분석된다[S1].

근거
S1 · 노트북 2분기 판매 리포트 (p0) — 2분기 노트북 매출은 1분기보다 다소...
     → http://localhost:8080/sources/notebook-q2-report?page=0&chunk=1
```

`S1` 링크를 클릭하면 `/sources/notebook-q2-report?page=0&chunk=1`이 열리고,
문서 원문에서 해당 청크가 노란색으로 강조되어 보인다. 모델이 만약
`[S9]`처럼 이번 검색에 없던 번호를 인용했다면 그 표시는 답변에서 사라지고
"모델이 인용한 표시 중 이번 검색 결과에 없는 항목을 제거했다: S9"라는
알림이 답변 끝에 붙는다.

## 2. 핵심 개념

### 2-1. Dense 벡터 검색

질문과 문서 청크를 각각 임베딩 모델(예: BGE, text-embedding 계열)로 실수
벡터(대개 수백~수천 차원)로 바꾼 뒤, 벡터 사이 거리(코사인 유사도, 내적
등)로 "의미가 가까운" 청크를 찾는다. 임베딩을 만드는 주체는 **모델
서버**이고, 벡터를 저장하고 최근접 이웃을 찾는 주체는 **Milvus(도구
서버)**다. 애플리케이션은 이 둘을 호출만 한다.

Dense 검색은 동의어·바꿔 쓴 표현("매출이 줄었다" ↔ "판매가 둔화되었다")에
강하다. 반대로 정확한 문자열 일치가 중요한 검색어(제품 코드, 정확한 날짜,
드문 고유명사)에는 종종 관련 없는 결과를 상위에 올릴 수 있다 — 벡터
공간에서는 "비슷한 뜻"이지 "같은 단어"를 우선하지 않기 때문이다.

### 2-2. Sparse 검색과 BM25의 관계

Sparse 벡터는 대부분의 차원이 0이고 실제 등장한 단어(또는 토큰)에 해당하는
차원만 값을 가진 벡터다. **BM25**는 정보 검색에서 오래 쓰인 랭킹 함수로,
"이 문서에 이 단어가 얼마나 자주 나오는지"(term frequency)와 "이 단어가
전체 문서 집합에서 얼마나 드문지"(inverse document frequency), 문서 길이를
함께 고려해 점수를 매긴다. Milvus는 BM25 점수 계산 자체를 "Sparse 벡터
검색"으로 구현한다 — 텍스트를 분석기(analyzer)로 토큰화하고, 토큰별 가중치를
Sparse 벡터의 값으로 채운 뒤, 질의와 문서 Sparse 벡터 사이의 유사도를 BM25
공식에 맞게 계산한다. 즉 "Sparse 검색"과 "BM25 검색"은 이 장에서 같은
것을 가리킨다 — Sparse는 벡터의 형태를, BM25는 그 벡터로 계산하는 점수
공식을 말한다.

### 2-3. 키워드 검색과 의미 검색의 차이

| | 키워드 검색 (BM25/Sparse) | 의미 검색 (Dense) |
| --- | --- | --- |
| 무엇을 비교하나 | 질의와 문서에 공통으로 등장하는 단어(토큰) | 질의와 문서 벡터 사이의 거리 |
| 강점 | 정확한 용어·코드·고유명사·숫자 | 동의어, 바꿔 쓴 표현, 문맥 |
| 약점 | 동의어를 못 찾음("판매"와 "매출"을 다른 단어로 취급) | 드문 고유명사·정확한 일치에 약함, 임베딩 품질에 좌우 |
| 계산 주체 | Milvus(analyzer + BM25 Function) | 임베딩 모델(모델 서버) + Milvus(최근접 이웃) |
| 필요한 사전 준비 | 없음(텍스트만 있으면 됨) | 임베딩 모델 호출, 벡터 차원 확정 |

### 2-4. 하이브리드 검색과 결과 결합

하이브리드 검색은 같은 질문으로 Dense 검색과 Sparse 검색을 각각 수행하고,
두 결과 목록을 하나의 순위로 합친다. 두 검색은 점수의 의미가 다르다 —
코사인 유사도는 대략 -1~1(또는 0~1) 범위지만 BM25 점수는 상한이 없고
문서 집합에 따라 스케일이 달라진다. 그래서 점수를 그대로 더하면 스케일이
큰 쪽이 결과를 사실상 독점한다. 결합 방법은 이 문제를 다루는 두 가지
전략이다.

```
질문
 ├─ 임베딩(모델 서버) → Dense 검색(Milvus, anns_field="dense")  ─┐
 └─ 원문 그대로       → Sparse/BM25 검색(Milvus, anns_field="sparse") ─┤
                                                                    ▼
                                                   결합 랭커(RRF 또는 가중치, Milvus)
                                                                    │
                                                                    ▼
                                                          하이브리드 top-K 청크
                                                                    │
                                              애플리케이션: 인용 검증([Sn] ↔ 실제 청크)
                                                                    │
                                                                    ▼
                                                        답변 + 검증된 근거 링크
```

### 2-5. RRF와 가중치 기반 결합

**RRF(Reciprocal Rank Fusion, 역순위 융합)**는 점수의 절대값을 무시하고
"각 목록에서 몇 번째 순위였는가"만 본다.

```
score(d) = Σ_i  1 / (k + rank_i(d))
```

`rank_i(d)`는 목록 i에서 문서 d의 순위(1부터 시작), `k`는 상위권 밖 순위의
영향을 완만하게 눌러주는 상수(Milvus 기본값 60)다. 예를 들어 문서 a가
Dense 목록에서 1위, Sparse 목록에서 2위라면:

```
score(a) = 1/(60+1) + 1/(60+2) = 0.016393 + 0.016129 = 0.032523
```

문서 b가 Dense 2위, Sparse 1위라면 점수는 대칭적으로 같은 0.032523이
된다 — 이렇게 RRF는 "두 검색 모두에서 상위권"인 문서를 자연스럽게 밀어
올리면서도, 점수 스케일이 다른 두 검색을 정규화 없이 바로 합칠 수 있다.
이 계산은 `code/step05_hybrid_rag/docagent/rag/search.py`의
`reciprocal_rank_fusion` 함수로 그대로 옮겨져 있고, `tests/test_rrf.py`가
이 예시 숫자를 그대로 단위 테스트로 검증한다.

**가중치 기반 결합(WeightedRanker)**은 반대로 원래 점수의 크기를 살린다.

```
score(d) = w_dense * norm(dense_score(d)) + w_sparse * norm(sparse_score(d))
```

예를 들어 정규화된 Dense 점수가 0.9, Sparse 점수가 0.2인 문서 a와, Dense
0.4, Sparse 0.8인 문서 b가 있고 가중치를 `w_dense=0.7, w_sparse=0.3`으로
주면:

```
score(a) = 0.7*0.9 + 0.3*0.2 = 0.63 + 0.06 = 0.69
score(b) = 0.7*0.4 + 0.3*0.8 = 0.28 + 0.24 = 0.52
```

Dense 가중치를 높게 주면 의미가 비슷한 문서(a)가 더 유리해진다. 이 결합은
**두 점수를 미리 같은 범위로 정규화했다는 전제**에서만 의미가 있다 — 하지
않으면 스케일이 큰 검색이 사실상 전부를 결정한다. Milvus의
`WeightedRanker`는 내부에서 점수를 정규화한 뒤 가중합을 계산한다
(`norm_score` 옵션, 기본 `True`).

**언제 어느 쪽을 쓰나**: 어떤 검색 방식이 더 신뢰할 만한지 뚜렷한 근거가
없거나 두 방식을 비슷하게 존중하고 싶다면 RRF가 다루기 쉽다(정규화가
필요 없고 파라미터가 `k` 하나뿐이다). 특정 검색 방식(예: 이 서비스는 항상
정확한 키워드 일치가 더 중요하다)을 의도적으로 더 신뢰하고 싶다면 가중치
방식으로 그 비중을 직접 조절한다.

### 2-6. 메타데이터 필터링과 리랭킹

**필터링**은 벡터 유사도와 무관하게 스칼라 필드 조건으로 후보를 미리 줄이는
것이다. 예를 들어 `doc_id == "notebook-q2-report"`로 특정 문서 안에서만
검색하거나, `page >= 1`로 특정 페이지 이후만 본다. `AnnSearchRequest`의
`expr` 인자로 각 개별 검색(Dense/Sparse)에 같은 필터를 걸 수 있다. 필터는
**Milvus가** 벡터 검색 자체를 실행하기 전(또는 실행과 함께) 적용한다.

**리랭킹**은 1차로 넉넉히 뽑은 후보(예: 20건)를 더 정교한 기준으로 다시
정렬해 최종 K건만 남기는 단계다. 이 장에서 하이브리드 검색의 RRF/가중치
결합 자체가 1차 리랭킹 역할을 한다. 더 정교하게 하려면 Cross-Encoder
기반 리랭커 모델을 별도로 호출해 상위 후보만 재점수화하는 방법도 있는데,
이는 임베딩 모델과는 다른 별도의 모델 호출이 필요하므로 이 장에서는
다루지 않는다([확인 필요: 이 저장소가 대상으로 하는 로컬/자체 호스팅
모델 서버가 별도 리랭커 엔드포인트를 제공하는지는 서버마다 다르다]).

### 2-7. 답변 문장과 출처 ID 연결, 가짜 출처 방지

이 장에서 가장 중요한 책임 분담은 다음과 같다.

| 주체 | 하는 일 |
| --- | --- |
| 모델 | 컨텍스트로 받은 청크만 근거로 답을 만들고, 문장 끝에 `[S1]`처럼 번호만 붙인다. URL이나 파일 경로를 직접 쓰지 않는다(프롬프트로 지시). |
| 애플리케이션 | 이번 검색에서 실제로 반환된 청크 목록과 라벨(`S1`, `S2`, ...)을 1:1로 관리한다. 모델 답변에서 `[Sn]`을 찾아 그 라벨이 실제 목록에 있는지 검사한다. 없으면 지우고, 있으면 그 청크의 `doc_id/page/chunk_index`로 근거 링크 URL을 **애플리케이션이 직접 만든다.** |

모델이 만들어 낸 URL이나 문서명을 그대로 신뢰하지 않는 이유는, 모델이
컨텍스트에 없는 문서명을 그럴듯하게 지어내거나(환각), 있던 청크 번호를
착각해 다른 번호를 쓰는 경우가 실제로 있기 때문이다. 검증 로직은
`code/step05_hybrid_rag/docagent/rag/citations.py`의
`validate_and_link_citations`에 있고, 모델이 만든 텍스트에서 URL이나
문서명을 아예 읽지 않는다 — `[Sn]` 패턴만 정규식으로 찾는다. 이 함수는
Milvus나 모델 서버 없이도 동작을 확인할 수 있어서 `tests/test_citations.py`로
실제로 실행해 검증했다.

### 2-8. 원문·페이지·청크로 이동하는 링크

근거 링크 URL 형식은 PROJECT-SPEC.md 5장에 고정되어 있다.

```
{APP_BASE_URL}/sources/{doc_id}?page={page}&chunk={chunk_index}
```

이 URL은 FastAPI의 `GET /sources/{doc_id}` 라우트로 실제 구현되어 있다
(`code/step05_hybrid_rag/docagent/app.py`). 이 라우트는 Milvus에서
`pk == "{doc_id}#p{page}-c{chunk_index}"`인 청크 원문을 찾고, 로컬 문서
파일 전체 안에서 그 원문이 나오는 자리를 `<mark>`로 감싸 강조 표시한다.

## 3. 실습 준비

### 패키지와 버전

`code/step05_hybrid_rag/requirements.txt`를 그대로 쓴다. 이 문서 작성
시점(2026-09) 기준 PyPI의 pymilvus 최신은 3.0.1이다. 이 장의 pymilvus API
(`MilvusClient.create_schema`, `Function`/`FunctionType.BM25`,
`AnnSearchRequest`, `RRFRanker`, `WeightedRanker`,
`MilvusClient.hybrid_search`)는 milvus-io/pymilvus 저장소 소스와 예제
스크립트로 확인했다 — 정확한 파일과 URL은 이 문서 맨 아래 "참고 문서"에
있다. **[확인 필요: 실제 사용할 Milvus 서버가 BM25 Function을 지원하는
버전인지는 서버 쪽 릴리스 노트로 별도 확인한다.]**

### 환경 변수

`code/step05_hybrid_rag/.env.example`을 복사해 쓴다. 5단계에서 새로 쓰는
이름은 없다 — `MILVUS_URI`, `MILVUS_TOKEN`, `MILVUS_COLLECTION`은
PROJECT-SPEC.md에 이미 4단계용으로 고정돼 있던 이름을 그대로 가져다 쓴다.

### 폴더 구조

```
code/step05_hybrid_rag/
├── docagent/
│   ├── config.py, events.py, llm.py, app.py
│   └── rag/{ingest,store,search,citations}.py
├── static/{index.html, app.js}
├── data/{docs/*.md, sales.csv}
├── scripts/{make_sample_data, migrate_add_sparse, compare_search}.py
└── tests/{test_citations, test_rrf}.py
```

### 샘플 데이터

```bash
cd code/step05_hybrid_rag
python -m scripts.make_sample_data
```

제품별 분기 리포트 4개(`notebook-q1-report.md`, `notebook-q2-report.md`,
`monitor-q1-report.md`, `keyboard-q2-report.md`)와 `sales.csv`를 만든다.
각 리포트는 매출 변화의 원인을 서술한 문장을 포함한다(예: "경쟁사의 신모델
출시로 분석된다").

## 4. 단계별 구현

### 4-1. 최소 예제: BM25 Function으로 Sparse 필드 만들기

먼저 4단계 스키마에 없던 것 — "텍스트만 넣으면 Milvus가 Sparse 벡터를
알아서 만들어 준다" — 를 아주 작은 컬렉션으로 확인한다. 이것이 이 장에서
Sparse 임베딩을 만드는 방법이다: **별도의 Sparse 임베딩 함수를 애플리케이션이
호출하지 않는다.** 대신 Milvus 컬렉션 스키마에 BM25 Function을 등록해
서버가 만들게 한다.

이렇게 고른 이유와 대안:

- **대안 1**: `pymilvus.model.sparse`의 `BM25EmbeddingFunction`으로
  애플리케이션이 직접 Sparse 벡터를 계산해서 insert하는 방법도 있다. 이
  방법은 코퍼스 통계(IDF 등)를 애플리케이션이 직접 학습(`fit`)해서
  들고 있어야 하고, 질의 시점에도 같은 통계로 인코딩해야 한다.
- **이 장에서 고른 방법(서버 내장 BM25 Function)**: Milvus가 analyzer로
  토큰화하고 통계를 관리하므로 애플리케이션 코드가 더 단순하고, 문서를
  추가할 때마다 통계를 다시 학습할 필요가 없다. 대신 analyzer 설정(언어,
  불용어 등)을 바꾸고 싶으면 Milvus 쪽 컬렉션 스키마를 바꿔야 한다.
- **대안 2**: 별도의 검색 엔진(Elasticsearch/OpenSearch 등)에서 BM25를
  계산하고 Milvus는 Dense 검색만 맡긴 뒤 애플리케이션이 두 시스템의
  결과를 합치는 구성도 가능하다. 이 장은 하나의 저장소(Milvus)로 구성을
  단순하게 유지하는 쪽을 택했다.

```python
# code/step05_hybrid_rag/docagent/rag/store.py (발췌)
from pymilvus import DataType, Function, FunctionType, MilvusClient

schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
schema.add_field("pk", DataType.VARCHAR, is_primary=True, max_length=300)
schema.add_field("dense", DataType.FLOAT_VECTOR, dim=dense_dim)
schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
schema.add_field("text", DataType.VARCHAR, max_length=8192, enable_analyzer=True)
# ... doc_id, doc_title, page, chunk_index, source_url ...

bm25_function = Function(
    name="text_bm25",
    function_type=FunctionType.BM25,
    input_field_names=["text"],
    output_field_names="sparse",
)
schema.add_function(bm25_function)
```

`text` 필드에 `enable_analyzer=True`를 주는 것이 핵심이다 — analyzer가
원문을 토큰으로 쪼갠 결과 위에서 BM25 Function이 동작한다. insert할 때는
`sparse` 필드에 아무 값도 넣지 않는다. 넣으려고 하면 오류가 난다(Function이
만드는 출력 필드이기 때문이다).

### 4-2. 4단계 컬렉션 마이그레이션

이미 4단계에서 dense 필드만 있는 컬렉션에 데이터를 넣어 두었다면, 그
컬렉션을 그 자리에서 바로 "sparse 필드 추가"로 바꾸는 것은 안전하게
보장된 경로가 아니다 — Function이 만드는 벡터 출력 필드는 컬렉션을 새로
만들 때 스키마에 포함하는 방식이 여러 Milvus 버전에서 공통으로 동작한다.
그래서 이 장은 "내보내기 → 새 컬렉션 생성 → 다시 넣기 → 이름 바꾸기"
경로로 마이그레이션한다.

```python
# code/step05_hybrid_rag/docagent/rag/store.py (발췌)
def migrate_add_sparse(dense_dim: int, *, batch_size: int = 200) -> int:
    old_name = settings.milvus_collection
    tmp_name = f"{old_name}__migrating"

    client = get_client()
    schema = _build_schema(client, dense_dim)          # sparse 필드 + BM25 Function 포함
    index_params = _build_index_params(client)
    client.create_collection(tmp_name, schema=schema, index_params=index_params,
                              consistency_level="Strong")

    iterator = client.query_iterator(old_name, batch_size=batch_size,
                                      output_fields=_EXPORT_FIELDS)
    moved = 0
    try:
        while True:
            batch = iterator.next()
            if not batch:
                break
            client.insert(tmp_name, batch)   # text만 넣으면 sparse는 BM25 Function이 채운다
            moved += len(batch)
    finally:
        iterator.close()

    client.flush(tmp_name)
    client.load_collection(tmp_name)
    client.drop_collection(old_name)
    client.rename_collection(tmp_name, old_name)
    return moved
```

`query_iterator`를 쓰는 이유는 컬렉션이 커도 한 번에 다 불러오지 않고
배치 단위로 안전하게 순회하기 위해서다. 실행은 스크립트로 한다.

```bash
python -m scripts.migrate_add_sparse --dense-dim 1024
```

`[확인 필요: pymilvus 최신 개발 소스에는 MilvusClient.add_function_field라는,
기존 컬렉션에 Function 출력 필드를 그 자리에서 추가하는 API가 보인다. 이
API가 정확히 어느 Milvus 서버 버전부터 지원되는지, 기존 행의 sparse 값을
자동으로 채워 주는지(백필)는 이 문서 작성 시점에 공식 문서로 확인하지
못했다. 그 API를 쓰려면 최신 공식 문서와 서버 버전을 먼저 확인한다.]`
`docagent/rag/store.py`의 `migrate_add_sparse` docstring에도 같은 내용을
남겨 두었다.

### 4-3. Dense / Sparse 개별 검색

```python
# code/step05_hybrid_rag/docagent/rag/search.py (발췌)
def sparse_search(query: str, *, limit: int = 5, filter_expr: str = "") -> list[dict]:
    client = get_client()
    results = client.search(
        settings.milvus_collection,
        data=[query],                      # 원문 문자열을 그대로 넘긴다. 임베딩하지 않는다.
        anns_field="sparse",
        search_params={"metric_type": "BM25", "params": {}},
        limit=limit,
        filter=filter_expr,
        output_fields=OUTPUT_FIELDS,
    )
    return [_to_hit(hit) for hit in results[0]] if results else []
```

Sparse 검색은 질의를 임베딩하지 않는다 — BM25 Function이 질의 원문도
analyzer로 토큰화해서 바로 점수를 계산하기 때문이다. Dense 검색은 4단계와
같은 방식으로 `embed_texts`로 질의를 임베딩한 뒤 `anns_field="dense"`,
`metric_type="COSINE"`으로 검색한다(`dense_search` 함수, 코드 생략 —
`search.py` 전체를 본다).

### 4-4. 하이브리드 검색: AnnSearchRequest + 랭커

```python
# code/step05_hybrid_rag/docagent/rag/search.py (발췌)
from pymilvus import AnnSearchRequest, RRFRanker, WeightedRanker

def hybrid_search(query: str, *, limit: int = 5, filter_expr: str = "",
                   ranker: str = "rrf", dense_weight: float = 0.5,
                   sparse_weight: float = 0.5, rrf_k: int = 60,
                   candidate_limit: int | None = None) -> list[dict]:
    client = get_client()
    candidate_limit = candidate_limit or max(limit * 4, 20)

    dense_req = AnnSearchRequest(
        data=embed_texts([query]), anns_field="dense",
        param={"metric_type": "COSINE", "params": {}},
        limit=candidate_limit, expr=filter_expr,
    )
    sparse_req = AnnSearchRequest(
        data=[query], anns_field="sparse",
        param={"metric_type": "BM25", "params": {}},
        limit=candidate_limit, expr=filter_expr,
    )

    rerank = RRFRanker(k=rrf_k) if ranker == "rrf" else WeightedRanker(dense_weight, sparse_weight)

    results = client.hybrid_search(
        settings.milvus_collection, [dense_req, sparse_req], rerank,
        limit=limit, output_fields=OUTPUT_FIELDS,
    )
    return [_to_hit(hit) for hit in results[0]] if results else []
```

각 `AnnSearchRequest`는 독립적인 벡터 검색 요청이고, `ranker`(RRFRanker
또는 WeightedRanker)가 그 결과들을 하나로 합친다. `candidate_limit`을
최종 `limit`보다 넉넉하게 잡는 이유는, 예를 들어 Dense에서 6위였던 문서가
Sparse에서 1위라면 결합 후에는 상위로 올라올 수 있는데, 애초에 Dense
쪽에서 top-5만 가져오면 그 문서 자체가 후보에 없어서 결합이 무의미해지기
때문이다.

RRF와 WeightedRanker의 실제 계산은 서버(Milvus) 안에서 일어난다.
"서버가 정확히 무슨 계산을 하는지" 눈으로 확인하고 싶으면 같은 파일에 있는
`reciprocal_rank_fusion`/`weighted_fusion` 순수 파이썬 함수(2-5절의 공식을
그대로 옮긴 것)를 본다 — 이 두 함수는 Milvus를 호출하지 않고, 이미
`tests/test_rrf.py`로 검증했다.

### 4-5. 인용 검증: 가짜 출처 방지

```python
# code/step05_hybrid_rag/docagent/rag/citations.py (발췌)
_CITATION_PATTERN = re.compile(r"\[S(\d+)\]")

def validate_and_link_citations(answer_text, retrieved, *, app_base_url):
    by_label = {s.label: s for s in retrieved}     # 이번 검색에서 실제로 반환된 것만
    used_labels, dropped_labels = [], []

    def _replace(match):
        label = f"S{match.group(1)}"
        if label in by_label:
            if label not in used_labels:
                used_labels.append(label)
            return match.group(0)          # 유효하면 그대로 둔다
        dropped_labels.append(label)       # 무효하면 버린다
        return ""

    cleaned = _CITATION_PATTERN.sub(_replace, answer_text)
    dropped_unique = sorted(set(dropped_labels), key=lambda l: int(l[1:]))
    if dropped_unique:
        cleaned += f"\n\n[알림: 모델이 인용한 표시 중 이번 검색 결과에 없는 항목을 제거했다: {', '.join(dropped_unique)}]"

    sources_payload = [
        {"id": label, "doc": by_label[label].doc_title, "page": by_label[label].page,
         "url": build_source_url(app_base_url, by_label[label].doc_id,
                                  by_label[label].page, by_label[label].chunk_index),
         "snippet": by_label[label].text[:200]}
        for label in used_labels
    ]
    return CitationResult(text=cleaned, sources=sources_payload,
                           used_labels=used_labels, dropped_labels=dropped_unique)
```

이 함수는 답변 텍스트에서 `[Sn]` 패턴만 읽는다. 모델이 답변 본문에 직접
문서명이나 URL을 적더라도 이 함수는 그 텍스트를 근거 URL로 쓰지 않는다 —
URL은 오직 `by_label[label]`(이번 검색 결과)에서 가져온
`doc_id/page/chunk_index`로 애플리케이션이 새로 조립한다.

### 4-6. FastAPI에 연결하기: `/chat`과 `/sources/{doc_id}`

```python
# code/step05_hybrid_rag/docagent/app.py (발췌)
@app.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(_chat_stream(req), media_type="text/event-stream")
```

`_chat_stream`은 `status(retrieving)` → 하이브리드 검색 → `status(analyzing)`
→ 모델 호출(비-스트리밍) → `status(verifying)` + 인용 검증 →
`status(writing)` + 검증된 텍스트를 `token` 이벤트로 잘라 보내기 →
`sources` 이벤트 → `done` 순서로 SSE 이벤트를 만든다.

여기서 2단계와 다른 설계를 하나 선택했다: **모델 응답을 스트리밍으로 바로
클라이언트에 보내지 않는다.** 어떤 `[Sn]` 인용이 유효한지는 답변을 끝까지
받아야 검증할 수 있기 때문이다. 토큰이 하나씩 도착하는 도중에는 아직 이
답변에 가짜 인용이 있는지 알 수 없다 — 먼저 다 받고 검증한 뒤, 검증이
끝난 텍스트를 다시 잘라서 `token` 이벤트로 흘려보낸다("검증 후 재생"
방식). 실시간성은 약간 떨어지지만 가짜 인용이 화면에 잠깐이라도 노출되는
상황을 막을 수 있다.

```python
@app.get("/sources/{doc_id}", response_class=HTMLResponse)
async def view_source(doc_id: str, page: int = 0, chunk: int = 0) -> HTMLResponse:
    chunk_id = make_chunk_id(doc_id, page, chunk)
    rows = get_client().query(
        settings.milvus_collection, filter=f'pk == "{chunk_id}"',
        output_fields=["text", "doc_title"],
    )
    chunk_text = rows[0]["text"] if rows else ""
    doc_text = _load_source_document(doc_id)
    # doc_text 안에서 chunk_text가 나오는 자리를 <mark>로 감싸 강조한다.
    ...
```

이 라우트가 PROJECT-SPEC.md 5장 URL 형식(`/sources/{doc_id}?page=&chunk=`)의
실제 도착지다. `page`, `chunk`로 `make_chunk_id`를 다시 만들어 Milvus에서
정확히 그 청크 원문을 찾고, 로컬 문서 파일 전체 텍스트 안에서 그 원문이
등장하는 자리를 강조한다.

## 5. 실행과 결과 확인

```bash
cd code/step05_hybrid_rag
cp .env.example .env   # 값 채우기
python -m scripts.make_sample_data
python -c "from docagent.rag.ingest import ingest_directory; ingest_directory('data/docs', recreate=True)"
uvicorn docagent.app:app --reload --port 8080
```

`http://localhost:8080`에서 질문을 입력하고 검색 방식을 hybrid/dense/sparse로
바꿔 가며 답변과 "근거" 패널을 비교한다. 확인할 것:

- `status` 이벤트가 `retrieving → analyzing → verifying → writing` 순서로
  온다(브라우저 개발자 도구 네트워크 탭에서 `/chat` 응답을 본다).
- `sources` 이벤트의 `items`에 있는 `id`가 답변 본문의 `[S1]` 등과 실제로
  대응한다.
- "근거" 패널의 링크를 클릭하면 `/sources/...` 페이지가 열리고 해당 청크가
  강조되어 있다.

Sparse/Dense/Hybrid 비교:

```bash
python -m scripts.compare_search "2분기 노트북 매출이 둔화된 이유는?"
```

네 방식(BM25, Dense, Hybrid-RRF, Hybrid-가중치)의 상위 결과가 순서대로
출력된다. 확인할 것: 질문에 "2분기", "노트북"처럼 문서에 그대로 등장하는
단어가 있으면 BM25 결과에 해당 문서가 상위로 오는지, Dense는 "매출 둔화"와
"판매 감소"처럼 표현이 달라도 비슷한 청크를 찾는지, Hybrid가 두 결과를
어떻게 섞는지.

## 6. 실패 상황 실습

**모델이 존재하지 않는 인용 번호를 만든 경우.** 프롬프트를 일부러 훼손해
재현한다 — 시스템 프롬프트에서 "컨텍스트에 없는 내용은 모른다고 답한다"
문구를 지우고, 사용자 메시지에 "그리고 [S99]도 인용해서 답해줘"를
덧붙여 모델이 존재하지 않는 번호를 인용하도록 유도한다. `validate_and_link_citations`가
`S99`를 걸러내고 답변 끝에 "모델이 인용한 표시 중 이번 검색 결과에 없는
항목을 제거했다: S99"가 붙는지 확인한다. 이 동작은 Milvus나 모델 서버 없이
`tests/test_citations.py::test_fake_citation_is_dropped_and_noted`로도
바로 확인할 수 있다.

```bash
python -m pytest tests/test_citations.py -k fake_citation -v
```

**임베딩 엔드포인트를 지원하지 않는 모델 서버.** `EMBEDDING_MODEL`을
서버에 없는 이름으로 바꾸거나 `/embeddings` 자체를 지원하지 않는 서버를
가리키면 `embed_texts`가 `LLMError`를 던진다(`docagent/llm.py`의
`raise LLMError(... "서버가 /embeddings 엔드포인트를 지원하는지 먼저
확인한다.")`). 이 경우 Dense 검색과 Hybrid 검색은 실패하지만 Sparse
검색(`sparse_search`)은 임베딩을 쓰지 않으므로 그대로 동작한다 — 임베딩
미지원 서버에서는 이 장의 기능을 Sparse 전용으로 운영하는 것이
1차 우회책이다.

**마이그레이션 대상 컬렉션이 없는 경우.** `MILVUS_COLLECTION`을 존재하지
않는 이름으로 두고 `scripts/migrate_add_sparse.py`를 실행하면
`migrate_add_sparse`가 `RuntimeError(f"마이그레이션할 컬렉션 '{old_name}'이(가)
없다.")`를 던진다 — 임시 컬렉션을 만들기 전에 원본 존재 여부를 먼저
검사하므로, 실패해도 기존 데이터나 컬렉션 이름에는 영향이 없다.

## 7. 응용 과제

1. **가중치 바꿔 보기**: `scripts/compare_search.py`의 `_MODES`에서
   `dense_weight`/`sparse_weight` 조합을 여러 개 추가하고, 제품명이 정확히
   들어간 질문과 "매출이 왜 줄었어?"처럼 두루뭉술한 질문 각각에 대해 어느
   가중치가 더 관련 있는 결과를 상위에 두는지 비교한다. 완료 기준: 서로
   다른 두 질문 유형에서 최적 가중치가 다르다는 것을 결과로 보인다.
   힌트: 정확한 고유명사가 있는 질문은 `sparse_weight`를 높여 본다.
2. **메타데이터 필터 추가**: `ChatRequest`에 `doc_id` 필드를 추가하고,
   있으면 `hybrid_search`의 `filter_expr`에 `f'doc_id == "{doc_id}"'`를
   넘기도록 `app.py`를 고친다. 완료 기준: 특정 문서를 지정했을 때 다른
   문서의 청크가 검색 결과에 전혀 나오지 않는다.
3. **dropped_labels를 사용자에게 별도로 보여주기**: 지금은 dropped 정보가
   답변 텍스트 끝에 문구로만 붙는다. `sources_event`와 별도로 `dropped`
   필드를 SSE로 추가 전송하도록 `events.py`와 `app.py`를 고치고, 프런트에서
   경고 배지로 표시한다. 완료 기준: 가짜 인용이 있을 때만 배지가 뜨고 없을
   때는 뜨지 않는다.

## 8. 핵심 정리와 확인 질문

- Dense 검색은 임베딩 벡터 사이 거리로 의미가 비슷한 청크를 찾고, Sparse
  검색은 BM25로 단어 일치·빈도를 본다. 이 장은 Sparse 벡터를 애플리케이션이
  직접 계산하지 않고 Milvus의 BM25 Function이 `text` 필드로부터 만들게
  했다.
- 하이브리드 검색은 두 검색을 각각 수행한 뒤 RRF(순위 기반) 또는
  가중치(점수 기반, 정규화 필요) 랭커로 결합한다. `AnnSearchRequest` +
  `RRFRanker`/`WeightedRanker` + `MilvusClient.hybrid_search`로 구현했다.
- 가짜 출처 방지의 핵심은 "모델은 번호만 인용하고, 애플리케이션은 그 번호를
  이번 검색 결과와 대조해서만 URL을 만든다"는 책임 분담이다. 모델이 답변에
  적은 URL이나 문서명은 절대 신뢰하지 않는다.
- 근거 링크는 `/sources/{doc_id}?page=&chunk=`로 실제 청크 원문까지
  연결된다.

확인 질문:
1. 같은 질문을 Dense로만 검색했을 때와 Sparse로만 검색했을 때 결과가 왜
   달라질 수 있는지, 자신의 말로 설명할 수 있는가?
2. RRF 점수 공식에서 `k`를 아주 크게(예: 10000) 잡으면 결합 결과가 어떻게
   달라지는지 설명할 수 있는가? (힌트: `tests/test_rrf.py`의
   `test_smaller_k_amplifies_rank_gap`을 본다.)
3. 모델이 `[S3]`를 인용했는데 이번 검색 결과가 `S1`, `S2` 두 개뿐이라면
   애플리케이션은 정확히 무엇을 하는가?
4. 4단계 컬렉션을 그 자리에서 바로 고치지 않고 "내보내기 → 새로 만들기 →
   다시 넣기 → 이름 바꾸기" 순서로 마이그레이션한 이유를 설명할 수 있는가?

이 장의 완성 코드는 `code/step05_hybrid_rag/`에 있다. 다음 장(6단계)은 이
RAG 파이프라인과 3단계의 도구 호출 에이전트를 LangChain으로 재구성하며
하나로 합친다.

## 참고 문서

- pymilvus API 확인용 소스(milvus-io/pymilvus 저장소, 이 문서 작성 시점
  기준 기본 브랜치):
  - <https://github.com/milvus-io/pymilvus/blob/master/examples/full_text_search/bm25.py> — BM25 Function, sparse 필드 스키마, 인덱스 파라미터
  - <https://github.com/milvus-io/pymilvus/blob/master/examples/orm_deprecated/hybrid_search/hello_hybrid_bm25.py> — `AnnSearchRequest` + `RRFRanker`로 하는 Dense+BM25 하이브리드 검색 전체 예제
  - <https://github.com/milvus-io/pymilvus/blob/master/examples/hybrid_search.py> — `MilvusClient.hybrid_search(collection_name, req_list, ranker, limit, output_fields=...)` 시그니처
  - `pymilvus/milvus_client/milvus_client.py`(같은 저장소)의 `hybrid_search`, `search`, `query_iterator`, `rename_collection`, `add_function_field` 정의
  - <https://pypi.org/project/pymilvus/> — 이 문서 작성 시점 최신 배포 버전(3.0.1) 확인
- milvus.io 공식 문서 포인터(이 작업 환경에서 `milvus.io` 직접 접속이
  네트워크 정책으로 차단되어 있어 아래 페이지의 전체 원문은 확인하지
  못했다. 검색 결과 요약으로 존재와 주제만 확인했고, 정확한 시그니처는
  위 GitHub 소스로 확인했다):
  - <https://milvus.io/docs/multi-vector-search.md>
  - <https://milvus.io/docs/reranking.md>
  - <https://milvus.io/docs/bm25-function.md>
  - <https://milvus.io/docs/full-text-search.md>

> 검증 상태: 이 장의 코드는 `python3 -m py_compile`로 전체 문법을
> 확인했다. `code/step05_hybrid_rag/tests/test_citations.py`(인용 검증)와
> `tests/test_rrf.py`(RRF·가중치 결합 함수)는 Milvus·모델 서버 없이 실제로
> 실행해 17개 테스트가 모두 통과하는 것을 확인했다. 그 외
> Milvus 컬렉션 생성·마이그레이션·검색, `/chat`·`/sources` 엔드포인트의
> 실제 서버 대상 실행은 검증하지 않았다. pymilvus API 시그니처는 위
> "참고 문서"의 소스 코드로 확인했지만 실제 Milvus 서버 버전별 동작
> 차이는 확인하지 못했다.
