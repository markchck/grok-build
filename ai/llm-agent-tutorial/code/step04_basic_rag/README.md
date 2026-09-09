# step04_basic_rag

4단계 "기본 RAG 직접 구현하기"의 완성 코드. 문서를 읽어 청킹하고, 임베딩을 만들어
Milvus에 저장한 뒤, 질문이 들어오면 검색한 청크를 근거로 답한다. 근거가 부족하면
모델을 호출하지 않고 고정 응답을 돌려준다.

본문: `../../docs/04-basic-rag.md`

## 1. 설치

```bash
cd code/step04_basic_rag
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 환경 변수

```bash
cp .env.example .env
# .env를 열어 OPENAI_BASE_URL, CHAT_MODEL, EMBEDDING_MODEL을 실제 모델 서버 값으로 바꾼다.
```

## 3. Milvus 준비

이 프로젝트의 기본값(`DOCAGENT_MILVUS_URI=http://localhost:19530`)은 **Docker standalone**을
가리킨다. 공식 문서 기준 최소 설치는 아래 스크립트다([Run Milvus in Docker (Linux)](https://milvus.io/docs/install_standalone-docker.md)).

```bash
curl -sfL https://raw.githubusercontent.com/milvus-io/milvus/master/scripts/standalone_embed.sh -o standalone_embed.sh
bash standalone_embed.sh start   # 19530 포트에 milvus-standalone 컨테이너가 뜬다
# 중지: bash standalone_embed.sh stop
# 데이터까지 삭제: bash standalone_embed.sh delete
```

서버를 띄우기 번거롭거나 빠르게 시험만 해보고 싶다면 **Milvus Lite**(같은 파이썬
프로세스 안에서 파일 하나로 동작하는 내장 모드)를 쓸 수 있다. `.env`의
`DOCAGENT_MILVUS_URI`를 로컬 파일 경로로 바꾸기만 하면 된다.

```
DOCAGENT_MILVUS_URI=./data/milvus_demo.db
```

코드는 두 경우 모두 동일하다 — `MilvusClient(uri=...)`에 준 값이 `http(s)://`면
서버에 연결하고, 로컬 경로면 Milvus Lite로 동작한다([Run Milvus Lite Locally](https://milvus.io/docs/milvus_lite.md)).
다만 Milvus Lite는 소규모 실습용이고 대량 데이터·다중 프로세스 접근에는 권장되지
않는다.

## 4. 샘플 데이터 생성

```bash
python scripts/make_sample_data.py
```

`data/sales.csv`와 `data/docs/*.md`가 생성된다(이미 이 리포지토리에는 생성된
결과물이 커밋돼 있으므로, 데이터를 초기화하고 싶을 때만 다시 실행하면 된다).

## 5. 문서 적재와 질의

```bash
uvicorn docagent.app:app --reload --port 8080
```

브라우저에서 `http://localhost:8080`을 열거나, curl로 직접 확인한다.

```bash
curl -X POST http://localhost:8080/api/ingest
curl -N -X POST http://localhost:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "노트북 프로 15의 2분기 매출이 줄어든 이유는?"}'
```

## 6. 알려진 제약

- pymilvus 최신 메이저 버전(3.0.x)이 존재할 수 있으나 이 코드는 2.5.x/2.6.x
  기준 `MilvusClient` API로 작성했다. `requirements.txt` 참고.
- 이 장에서는 검색을 dense 벡터 하나로만 한다. 키워드(sparse/BM25) 검색과
  하이브리드 결합, 근거 링크는 5단계(`step05_hybrid_rag`)에서 추가한다.
