# 11단계. 멀티에이전트와 Agent-to-Agent

본문: `../../docs/11-multi-agent.md`

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

확인된 기준선(2026-09-09 실측): `langgraph` 1.2.11, `langchain` 1.4.0(이 장은
`langchain`을 직접 import하지 않지만 검토자 확인 버전과 나란히 기록한다),
`fastapi` 0.141.1, `openai` 3.10.0.

## 실행

### 단위 테스트 (모델 서버 없이)

```bash
PYTHONPATH=. pytest tests -q
```

### 모델 서버 없이 보는 데모

```bash
PYTHONPATH=. python scripts/demo_parallel.py     # 병렬 조사 + 결과 통합
PYTHONPATH=. python scripts/demo_handoff.py      # 핸드오프 정상 흐름 + 상호 위임 루프 차단
PYTHONPATH=. python scripts/demo_budget.py       # 협업 단위 예산 한도 확인
```

### 스텁 모델 서버 + A2A 최소 흉내 서버로 전체 흐름 실행

터미널 1(스텁 모델 서버):

```bash
python ../_tools/fake_openai_server.py --port 8291
```

터미널 2(A2A 최소 흉내 검증 에이전트, 별도 프로세스):

```bash
PYTHONPATH=. python -m docagent.a2a_min.server --port 8292
```

터미널 3(이 장의 앱):

```bash
OPENAI_BASE_URL=http://127.0.0.1:8291/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=stub-model \
A2A_VERIFIER_URL=http://127.0.0.1:8292 \
uvicorn docagent.app:app --port 8180 --app-dir .
```

터미널 4:

```bash
curl -sN http://127.0.0.1:8180/collab/supervisor -H 'content-type: application/json' \
  -d '{"message":"노트북 매출이 왜 늘었는지 조사해줘"}'

curl -sN http://127.0.0.1:8180/collab/supervisor -H 'content-type: application/json' \
  -d '{"message":"모니터 매출 추세를 검증해줘", "external_verifier": true}'
```

또는 스크립트로:

```bash
OPENAI_BASE_URL=http://127.0.0.1:8291/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=stub-model \
A2A_VERIFIER_URL=http://127.0.0.1:8292 \
PYTHONPATH=. python scripts/demo_supervisor.py
```

## 폴더 구조

```
docagent/
├── config.py           # Settings + MAX_AGENT_HANDOFFS, MAX_COLLAB_TOKENS, A2A_VERIFIER_URL
├── llm.py               # PROJECT-SPEC.md 9절 인터페이스(chat/achat/chat_json)
├── events.py             # SSE 이벤트 + finish_reason=delegation_loop 추가
├── salesdata.py           # sales.csv 접근 함수(3단계 tools.py 로직 재사용)
├── app.py                  # FastAPI: POST /collab/supervisor
├── multiagent/
│   ├── state.py             # CollabState: 공유 상태 + 에이전트별 컨텍스트
│   ├── budget.py             # 협업 전체 단위 예산 검사
│   ├── agents.py              # investigator / analyst / verifier(내부·A2A 버전) / finish
│   ├── supervisor.py           # 슈퍼바이저 노드: 위임 판단, 제어권은 항상 돌아온다
│   ├── graph_supervisor.py      # 슈퍼바이저 그래프 조립
│   ├── handoff.py                # Command(goto=...) 기반 핸드오프 + 루프 감지
│   └── graph_parallel.py          # 병렬 조사 + merge_investigations 리듀서
└── a2a_min/
    ├── server.py                   # A2A가 다루는 문제를 최소 흉내낸 별도 프로세스
    └── client.py                    # 그 서버를 호출하는 클라이언트
```

## 참고

- MCP(10단계, `code/step10_mcp/`)는 이 장과 같은 시기에 다른 필자가 병행
  집필했다. 이 장은 MCP 코드를 재사용하지 않고, 개념 비교(2-7절)로만
  다룬다.
