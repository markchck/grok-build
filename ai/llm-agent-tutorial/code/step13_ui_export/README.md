# step13_ui_export

13단계 "진행 화면과 CSV 다운로드 완성하기"의 완성 코드. 자세한 설명은
`../../docs/13-ui-and-export.md`를 본다.

이 단계는 새 SSE 이벤트를 만들지 않는다. 5·8·12단계가 이미 정의한
`token`/`status`/`tool_call`/`tool_result`/`todo`/`sources`/`approval_request`/
`error`/`done` 이벤트를 그대로 받아 화면이 어떻게 소비하는지가 이 장의 내용이다.
백엔드는 8단계(승인·재개·반복 감지)와 5단계(하이브리드 검색·인용 검증) 코드를
합치고, 계획(Todo)은 이 장이 로컬 도구(`write_plan`)로 직접 선언한다(12단계
`bridge.py`의 id 안정화 로직은 그대로 재사용한다). 이 장이 새로 만드는 것은
CSV 다운로드뿐이다.

## 1. 설치

```bash
cd code/step13_ui_export
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 환경 변수

```bash
cp .env.example .env
```

Milvus Lite로 학습하려면 `DOCAGENT_MILVUS_URI`를 로컬 파일 경로로 준다(예:
`./data/milvus_demo.db`). 4·5단계와 같은 이유로 `MILVUS_URI`(접두사 없는 이름)는
쓰지 않는다 — VERIFIED-FINDINGS.md 4절 참고.

## 3. 문서 적재 (5단계와 동일한 절차)

```bash
python -c "from docagent.rag.ingest import ingest_directory; ingest_directory('data/docs', recreate=True)"
```

## 4. 실행

```bash
uvicorn docagent.app:app --reload --port 8080
```

`http://localhost:8080/`에 접속한다. 실제 모델 서버 없이 시험하려면
`code/_tools/fake_openai_server.py`를 띄우고 `.env`의 `OPENAI_BASE_URL`을
그 주소로 맞춘다. 그 스텁은 모델이 아니므로 `tools` 목록의 첫 항목을 항상
고른다 — 특정 도구 흐름(승인, 검색, CSV)만 시험하려면 `/chat` 요청에
`"tool_names": ["hybrid_search_docs"]`처럼 원하는 도구 하나만 넘긴다.

## 5. 테스트

```bash
pytest tests/ -q
```

모델 서버·Milvus 없이 도는 것: `test_csv_export.py`(CSV 인코딩),
`test_todo_sync.py`(계획 id 안정화), `test_finish_reason_display.py`(종료
사유별 화면 표시), `test_agent_limits.py`·`test_citation_wiring.py`(가짜
`chat_fn`을 주입해 실행 한도·인용 검증까지 실제로 루프를 돌린다).
