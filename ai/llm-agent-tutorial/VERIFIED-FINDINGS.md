# 실행으로 확인한 사실 (검토자 기록)

이 파일은 튜토리얼 집필 중 **실제로 실행해 확인한** 사실만 모은다.
추측이나 검색 결과만으로 얻은 내용은 여기 넣지 않는다.
각 장 본문에 반영할 때는 여기 적힌 범위를 넘어서 일반화하지 않는다.

확인 환경: Linux, Python 3.11, 2026-09-09.

## 1. 패키지 버전 (PyPI 실측 + 실제 설치)

| 패키지 | 확인한 버전 | 비고 |
| --- | --- | --- |
| `openai` | 3.10.0 | 2026-09-09 릴리스. `chat.completions.create` 인터페이스 유지 |
| `httpx` | 0.28.1 | |
| `httpx2` | 2.12.0 | **`openai` 3.x가 `httpx`가 아니라 `httpx2`(>=2.7.0,<3)에 의존한다** |
| `python-dotenv` | 1.2.3 | |
| `pymilvus` | 2.6.17 (2.x 최신), 3.0.1 (최신) | |
| `milvus-lite` | 3.2.1 | `pymilvus[milvus-lite]` extra. Windows 미지원 |
| `fastapi` | 0.141.1 | |

## 2. openai 3.10.0 API (설치한 패키지의 생성 타입 파일을 직접 열어 확인)

- `client.chat.completions.create(...)`가 그대로 존재한다.
- `max_tokens`와 `max_completion_tokens`가 **둘 다** 파라미터로 있다.
  `openai/types/chat/completion_create_params.py`의 `max_tokens` 설명:
  "This value is now deprecated in favor of `max_completion_tokens`, and is not
  compatible with o-series models."
- `response_format={"type":"json_schema", "json_schema": {...}}`의 필드
  (`openai/types/shared_params/response_format_json_schema.py`):
  - `json_schema.name` **필수**, `a-zA-Z0-9_-`만, 최대 64자
  - `json_schema.schema` JSON 스키마 객체
  - `json_schema.description` 선택
  - `json_schema.strict` 선택(`Optional[bool]`). **기본값이 타입 정의에 명시되어 있지 않다.**
    `strict=true`면 JSON 스키마의 일부 문법만 지원된다.

## 3. pymilvus 2.6.17과 3.0.1의 API 동일성 (두 휠을 모두 풀어 비교)

아래는 **두 버전에서 동일하게** 존재한다. 4·5단계 코드는 두 버전 모두에서 같은 형태로 쓸 수 있다.

- `MilvusClient.create_schema()` (classmethod, `milvus_client/base.py`)
- `MilvusClient.prepare_index_params()` (classmethod, `milvus_client/base.py`)
- `CollectionSchema.add_field()` (`orm/schema.py`)
- `IndexParams.add_index()` (`milvus_client/index.py`)
- `MilvusClient.hybrid_search(collection_name, reqs: List[AnnSearchRequest], ranker: Union[BaseRanker, Function], limit=10, output_fields=None, timeout=None, partition_names=None, **kwargs)`
- `RRFRanker`, `WeightedRanker`, `AnnSearchRequest` (`client/abstract.py`)
- `Function`, `FunctionType` (`orm/schema.py`)
- Milvus Lite는 두 버전 모두 `milvus-lite` extra로 제공된다(`sys_platform != 'win32'`).

## 4. 환경 변수 이름 충돌 — 실제로 재현한 버그

`pymilvus/settings.py`(2.6.17, 3.0.1 동일):

```python
MILVUS_URI = str(os.getenv("MILVUS_URI", LEGACY_URI))
```

이 값은 `pymilvus/orm/connections.py`가 **모듈 import 시점에** 검증한다. 따라서
환경 변수 `MILVUS_URI`가 `http(s)://` 형식이 아니면(예: Milvus Lite 파일 경로)
`import pymilvus` 자체가 다음으로 실패한다.

```
ConnectionConfigException: Illegal uri: [...], expected form 'http[s]://[user:password@]example.com[:12345]'
```

**대응**: 이 튜토리얼의 환경 변수는 `DOCAGENT_MILVUS_URI`, `DOCAGENT_MILVUS_TOKEN`,
`DOCAGENT_MILVUS_COLLECTION`을 쓴다. (PROJECT-SPEC 2절에 반영 완료)

## 5. Milvus 검색 결과의 기본 키 접근 — 실제로 재현한 버그

`client.search(...)` 결과 항목의 키는 `"id"`가 아니라 **기본 키 필드의 이름**이다.
이 프로젝트의 기본 키 필드 이름은 `pk`이므로 `hit["pk"]`다.
실측한 항목의 키: `['pk', 'distance', 'entity']`.
`hit["id"]`는 `KeyError: 'id'`가 된다. (4·5단계 코드 수정 완료)

## 6. 컬렉션 load 상태 — 실제로 재현한 버그

컬렉션을 만들고 데이터를 넣어도, **새 프로세스에서는** 검색이 다음으로 실패한다.

```
MilvusException: (code=101, message=Collection 'docagent_chunks' is in state 'released'; call load() before search/get/query)
```

`load_collection()`의 효과는 프로세스를 넘어 유지되지 않는다. 적재 스크립트와 API 서버가
서로 다른 프로세스이므로, **검색 직전에 `load_collection()`을 매번 호출**해야 한다
(이미 로드되어 있으면 아무 일도 하지 않는다). 4·5단계 코드에 반영 완료.

## 7. 한국어 BM25 — Milvus Lite의 실제 제약 (직접 실험)

`milvus-lite` 3.2.1 + `pymilvus` 2.6.17에서 `analyzer_params`의 tokenizer를 바꿔 시도한 결과:

| 시도한 값 | 결과 |
| --- | --- |
| 지정하지 않음(기본 `standard`) | 컬렉션 생성·검색 동작 |
| `{"type": "korean"}` | `MilvusException(code=6): unknown tokenizer type: 'korean' (supported: 'standard', 'jieba')` |
| `{"tokenizer": "lindera", "dict_kind": "ko-dic"}` | 같은 오류 |
| `{"tokenizer": "icu"}` | 같은 오류 |

즉 **Milvus Lite의 BM25 tokenizer는 `standard`와 `jieba`뿐이다.**
`standard`는 한국어를 공백 단위로만 자르므로, 질의 "매출 감소"가 본문 "매출이 ... 감소했다"와
토큰이 일치하지 않아 sparse 검색의 재현율이 낮다. 실측에서 같은 질의에 대해
dense는 3건을 반환한 반면 sparse는 1건만 반환했다.

`[확인 필요: Milvus standalone(Docker)에서는 지원 tokenizer 목록이 더 넓은지]` —
Lite에서만 확인했고 standalone에서는 확인하지 않았다.

**튜토리얼에서의 대응**: 이 제약을 5단계 본문에 사실대로 적고, 한국어 문서에서는
하이브리드 검색의 이득이 주로 dense 쪽에서 나온다는 점, sparse는 제품명·모델명·숫자 같은
공백으로 분리되는 고유 토큰에서 효과가 크다는 점을 설명한다. 없는 성능을 있다고 쓰지 않는다.

## 8. 각 단계 코드의 실행 검증 결과

| 단계 | 실제로 실행해 확인한 것 |
| --- | --- |
| 1 | 의존성 설치, raw/SDK × 일반/스트리밍, json_schema 출력, 400 오류 → `LLMCallError` 변환, `check_server_features.py` 5개 항목 판정 (스텁 서버 대상) |
| 3 | pytest 16개 통과 |
| 4 | 문서 5개 적재 → 청크 5개 → 임베딩(8차원 스텁) → Milvus Lite 저장 → dense 검색 3건 반환 |
| 5 | pytest 17개 통과, Milvus Lite에 dense+sparse(BM25 Function) 컬렉션 생성, 적재, dense/sparse/RRF/weighted 4가지 검색 모두 결과 반환 |

스텁 서버는 `code/_tools/fake_openai_server.py`다. 모델이 아니므로 **답변 품질과
실제 모델 서버의 기능 지원 범위는 검증하지 않는다.**

## 9. Milvus Lite의 sparse(BM25) 컬렉션 취약성 — 부분적으로만 규명함

`milvus-lite` 3.2.1에서 sparse(BM25 Function) 컬렉션을 만들고 **다른 프로세스에서 읽으면**
다음으로 실패하는 경우가 있다.

```
MilvusException: (code=1, message=vector column must be FixedSizeList, got binary)
```

직접 실험한 결과는 다음과 같다.

| 구성 | 새 프로세스에서 sparse/hybrid 검색 |
| --- | --- |
| 5단계 코드의 스키마(pymilvus 2.6.17로 직접 선언) | **동작한다** (여러 번 재확인) |
| 최소 재현 스키마(pk/dense/text/sparse만, pymilvus 2.6.17) | 실패 |
| 같은 최소 재현 스키마, pymilvus 3.0.1 | 실패 |
| `flush()` 호출을 뺀 경우 | 여전히 실패 |
| 필드 선언 순서를 5단계와 같게 바꾼 경우 | 여전히 실패 |
| langchain-milvus 0.4.0이 만든 컬렉션(6단계) | 실패 |

**즉 이 오류는 langchain-milvus 고유의 문제가 아니다.** 순수 pymilvus로 만든 컬렉션에서도
재현된다. 반대로 5단계 구성은 같은 조건에서 안정적으로 동작한다. 두 구성의 어떤 차이가
경계를 가르는지는 **특정하지 못했다**. pymilvus 버전, flush 여부, 필드 선언 순서는
모두 원인이 아님을 실험으로 배제했다.

`[확인 필요: 이 오류를 유발하는 정확한 조건, 그리고 Milvus standalone(Docker)에서도 같은
문제가 있는지]` — Lite에서만 확인했다.

**튜토리얼에서의 대응**: 하이브리드 검색을 실제로 쓰려면 Milvus standalone을 권한다.
Milvus Lite는 5단계 구성에 한해 학습용으로 쓸 수 있다. 6장은 이 제약 때문에
적재와 서비스를 같은 프로세스에 두는 우회책을 쓰며, 그것이 우회책이라는 사실과
원인이 규명되지 않았다는 사실을 본문에 밝힌다. **원인을 아는 것처럼 쓰지 않는다.**
