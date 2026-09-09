# step13_ui_export

13단계 "진행 화면과 CSV 다운로드 완성하기"의 완성 코드. 자세한 설명은
`../../docs/13-ui-and-export.md`를 본다.

이 단계는 새 SSE 이벤트를 만들지 않는다(`tool_call.source`라는 선택 필드
하나만 추가했다). 5·8·12단계가 이미 정의한
`token`/`status`/`tool_call`/`tool_result`/`todo`/`sources`/`approval_request`/
`error`/`done` 이벤트를 그대로 받아 화면이 어떻게 소비하는지가 이 장의 내용이다.
백엔드는 8단계(승인·재개·반복 감지)와 5단계(하이브리드 검색·인용 검증) 코드를
합치고, 계획(Todo)은 이 장이 로컬 도구(`write_plan`)로 직접 선언한다(12단계
`bridge.py`의 id 안정화 로직은 그대로 재사용한다). CSV 다운로드는 이 장이
새로 만든 기능이고, 이번 개정에서 **MCP(10단계)·Skill(12단계)을 같은 에이전트
루프에, 멀티에이전트 협업+A2A 최소 흉내(11단계)를 `/collab/supervisor`
라우트에, 트레이싱(9단계)을 앱 시작 시점에** 배선했다 — 자세한 설명은
`docs/13-ui-and-export.md` 4-8절, 실행 절차는 아래 6절을 본다.

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

이번 개정에서 추가한 테스트는 실제 자식 프로세스를 띄운다:

- `test_mcp_wiring.py`: `python -m mcp_server.server`를 실제로 자식
  프로세스로 실행한다. `DOCAGENT_MCP_SERVER_COMMAND`가 이 테스트를 돌리는
  가상환경의 `python`을 가리켜야 한다 — 시스템 `python`에는 `mcp` 패키지가
  없어 자식 프로세스가 즉시 죽는다. 예:
  `DOCAGENT_MCP_SERVER_COMMAND=$(which python) pytest tests/test_mcp_wiring.py`
- `test_skill_wiring.py`: `skills/*/*.py`를 실제로 subprocess로 실행한다.
- `test_collab_wiring.py`: `python -m docagent.a2a_min.server`를 임시
  포트로 실제로 띄운다(외부 포트를 열므로 샌드박스에서 네트워크 바인딩이
  막혀 있으면 이 파일만 실패할 수 있다).
- `test_tracing_wiring.py`: 기본 동작(NoOp)은 이 프로세스에서 확인하고,
  `init_tracing()`이 실제로 TracerProvider를 등록하는지는 **별도 프로세스**로
  격리해 확인한다(이유는 파일 docstring 참고 — 전역 상태 오염 방지).

```bash
DOCAGENT_MCP_SERVER_COMMAND=$(which python) pytest tests/ -q
```

## 6. 최종 통합 실행 (MCP·Skill·멀티에이전트+A2A·트레이싱을 함께)

`docs/13-ui-and-export.md` 5-9절에 실제로 실행한 전체 절차와 출력이 있다.
요약:

```bash
# 1) 문서 적재
python -c "from docagent.rag.ingest import ingest_directory; ingest_directory('data/docs', recreate=True)"

# 2) 스텁 모델 서버, Phoenix(트레이싱 UI), A2A 최소 흉내 서버를 각각 띄운다
python ../_tools/fake_openai_server.py --port 8241 &
python -m phoenix.server.main serve --port 6046 &
python -m docagent.a2a_min.server --port 8293 &

# 3) 이 장의 앱. MCP 서버는 요청이 올 때마다 docagent가 알아서 자식 프로세스로 띄운다
export OPENAI_BASE_URL=http://127.0.0.1:8241/v1 OPENAI_API_KEY=sk-test \
       CHAT_MODEL=stub-model EMBEDDING_MODEL=stub-embedding \
       DOCAGENT_TRACING_ENABLED=true DOCAGENT_PHOENIX_ENDPOINT=http://127.0.0.1:6046/v1/traces \
       DOCAGENT_MCP_SERVER_COMMAND=$(which python) A2A_VERIFIER_URL=http://127.0.0.1:8293
uvicorn docagent.app:app --port 8280 &

# 4) MCP + Skill이 같은 요청 안에서 함께 쓰이는 것 (source 필드로 구분됨)
curl -sN -X POST http://127.0.0.1:8280/chat -H 'content-type: application/json' \
  -d '{"message":"매출 원인을 MCP로, 집계는 Skill로","include_mcp":true,"include_skills":true}'

# 5) 협업(내부 검증 vs A2A 검증)
curl -sN -X POST http://127.0.0.1:8280/collab/supervisor -H 'content-type: application/json' \
  -d '{"message":"노트북 매출이 왜 늘었는지 조사해줘"}'
curl -sN -X POST http://127.0.0.1:8280/collab/supervisor -H 'content-type: application/json' \
  -d '{"message":"노트북 매출이 왜 늘었는지 조사해줘","external_verifier":true}'

# 6) Phoenix UI(http://localhost:6046)에서 스팬을 눈으로 확인하거나 GraphQL API로 조회한다
```
