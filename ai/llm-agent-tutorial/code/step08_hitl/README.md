# step08_hitl — Human in the Loop와 무한루프 차단

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 환경 변수

```bash
cp .env.example .env
# .env를 열어 OPENAI_BASE_URL 등을 채운다. 로컬 스텁 서버를 쓰면 아래 "스텁 서버로 실행" 참고.
```

## 단위 테스트 (모델 서버 없이)

```bash
pytest tests -q
```

## 무한 루프 차단 데모 (모델 서버 없이, 스크립트로 직접 확인)

```bash
python scripts/demo_infinite_loop.py
```

## 스텁 서버로 실행

```bash
# 터미널 1: 스텁 모델 서버
python ../_tools/fake_openai_server.py --port 8141

# 터미널 2: 이 장의 앱
OPENAI_BASE_URL=http://127.0.0.1:8141/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=stub-model \
DOCAGENT_STATE_DB=./data/hitl_state.db \
uvicorn docagent.app:app --port 8180 --app-dir .
```

브라우저에서 `http://127.0.0.1:8180/static/index.html`을 연다.

승인이 필요한 요청 예시: `notebook-q1-report를 보관해줘`.
(웹 UI 예시 요청은 `tool_names` 없이 보내면 스텁 서버 특성상 항상 첫 번째
도구(sum_sales)를 부른다 — 승인 흐름을 직접 보려면 curl로 `tool_names`를
지정하거나, `docs/08-human-in-the-loop.md` 5절의 curl 예시를 따라간다.)

## 서버가 죽어도 상태가 살아남는지 확인

```bash
# 승인 대기 상태까지 진행한 뒤
kill -9 <uvicorn 프로세스 PID>
# 같은 DOCAGENT_STATE_DB로 다시 띄운다
uvicorn docagent.app:app --port 8180 --app-dir .
curl http://127.0.0.1:8180/runs/<run_id>   # 재시작 전과 같은 상태가 나온다
```

자세한 절차와 curl 전체 로그는 `docs/08-human-in-the-loop.md` 5절을 본다.
