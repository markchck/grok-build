# step02_fastapi_chat

2단계 완성 코드. `docs/02-fastapi-chat.md`와 함께 본다.

FastAPI로 대화 엔드포인트를 만들고, SSE(Server-Sent Events)로 답변 토큰과 진행 상태를
스트리밍하며, 클라이언트 연결이 끊기면 모델 호출을 실제로 취소한다.

## 1. 설치

```bash
cd code/step02_fastapi_chat
python3 -m venv .venv
source .venv/bin/activate      # Windows는 .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. 환경 변수

```bash
cp .env.example .env
```

`.env`를 열어 실제 모델 서버 값으로 채운다. `OPENAI_BASE_URL`은 OpenAI 호환
Chat Completions API를 제공하는 서버 주소(`/v1`까지 포함), `CHAT_MODEL`은 그 서버에
등록된 모델 이름이다. 로컬에서 서버가 없다면 아래 "5. 스텁 서버로 확인하기"를 먼저 본다.

## 3. 실행

```bash
uvicorn docagent.app:app --reload --port 8080
```

브라우저에서 `http://localhost:8080/`을 열면 `static/index.html`이 뜬다.
FastAPI 자동 문서는 `http://localhost:8080/docs`에서 확인한다.

## 4. 확인

1. 메시지를 입력하고 "보내기"를 누른다.
2. 회색 기울임 글씨(`status` 이벤트)가 먼저 뜨고, 이어서 초록색 답변(`token` 이벤트)이
   실시간으로 이어 붙는다.
3. 답변 도중 "취소"를 누르면 스트림이 즉시 멈추고 서버 로그에
   `stream task cancelled`가 남는다(터미널에서 uvicorn을 실행한 창을 본다).
4. 같은 세션에서 이어 질문하면 이전 대화가 반영된 답변이 온다 — `X-Session-Id` 응답
   헤더로 세션이 유지되는지 확인할 수 있다(브라우저 개발자 도구 Network 탭).

## 5. 스텁 서버로 확인하기

실제 모델 서버 없이 코드 동작만 확인하려면 최소 스텁 서버를 하나 띄워 검증할 수 있다.
이 저장소에는 포함돼 있지 않다 — 아래 스크립트를 직접 저장해서 쓴다.

```python
# stub_server.py — 이 저장소에 없는 파일이다. 검증용으로만 직접 만든다.
import json, time
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        self.rfile.read(n)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for piece in ["안녕", "하세요", "!"]:
            chunk = {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.3)
        self.wfile.write(b"data: [DONE]\n\n")

HTTPServer(("127.0.0.1", 18999), Handler).serve_forever()
```

```bash
python stub_server.py &
OPENAI_BASE_URL=http://localhost:18999/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=test-model \
  uvicorn docagent.app:app --port 8080
```

이 스텁은 실제 모델이 아니라 SSE 배관(파이프라인)이 맞물려 동작하는지만 확인한다.
이 장 본문의 코드는 이 방식으로 파이프라인 동작을 확인했고, 실제 모델 서버 대상
검증은 하지 않았다 — `docs/02-fastapi-chat.md` 맨 아래 검증 상태를 본다.

## 6. 다음 단계

3단계(`step03_tool_calling`)는 이 대화 엔드포인트에 도구 호출과 에이전트 반복 실행을
추가한다. `docagent/tools.py`, `docagent/agent.py`가 새로 생기고, SSE에 `tool_call`,
`tool_result` 이벤트가 추가된다.
