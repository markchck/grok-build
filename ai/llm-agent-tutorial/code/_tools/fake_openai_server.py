"""학습·검증용 최소 OpenAI 호환 스텁 서버.

실제 모델 서버 없이 각 단계 코드가 "요청을 올바르게 만들고 응답을 올바르게 해석하는지"만
확인하기 위한 서버다. 모델이 아니므로 답변 내용은 고정 문자열이며, 답변 품질 확인에는 쓸 수 없다.

지원하는 것
  - POST /v1/chat/completions (일반 / stream=True)
  - response_format: json_object, json_schema
  - tools 파라미터가 오면 첫 응답에서 tool_call을 한 번 만들고,
    role="tool" 메시지가 들어오면 최종 답변을 낸다 (3단계 에이전트 루프 확인용)
  - POST /v1/embeddings (고정 차원 의사 임베딩, 4·5단계 확인용)
  - 오류 주입: model 이름이 "bad-model"이면 400, "slow-model"이면 응답을 지연시킨다

실행
    python code/_tools/fake_openai_server.py --port 8111

사용
    OPENAI_BASE_URL=http://127.0.0.1:8111/v1 OPENAI_API_KEY=sk-test CHAT_MODEL=stub-model \
        python -m docagent.cli

주의: 이 서버는 학습용이다. 인증을 검사하지 않고 동시성·보안 처리가 없다. 외부에 노출하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STREAM_PIECES = ["요청하신 ", "내용을 ", "확인했습니다."]
FIXED_ANSWER = "요청하신 내용을 확인했습니다."
EMBEDDING_DIM = 8


def _pseudo_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """같은 입력에 항상 같은 벡터를 주는 의사 임베딩. 의미를 담지 않는다."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [digest[i % len(digest)] / 255.0 for i in range(dim)]
    norm = sum(v * v for v in raw) ** 0.5 or 1.0
    return [v / norm for v in raw]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 요청 로그를 끈다
        pass

    # --- 응답 보조 ---------------------------------------------------------
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, err_type: str) -> None:
        self._json(status, {"error": {"message": message, "type": err_type, "code": None}})

    # --- 라우팅 ------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 규약)
        length = int(self.headers.get("content-length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._error(400, "request body is not valid JSON", "invalid_request_error")
            return

        if self.path.rstrip("/").endswith("/chat/completions"):
            self._chat(body)
        elif self.path.rstrip("/").endswith("/embeddings"):
            self._embeddings(body)
        else:
            self._error(404, f"unknown path: {self.path}", "invalid_request_error")

    # --- /v1/chat/completions ---------------------------------------------
    def _chat(self, body: dict) -> None:
        model = body.get("model", "stub-model")
        if model == "bad-model":
            self._error(400, "The model `bad-model` does not exist.", "invalid_request_error")
            return
        if model == "slow-model":
            time.sleep(float(body.get("stub_delay_seconds", 30)))

        messages = body.get("messages") or []
        tools = body.get("tools")
        already_ran_tool = any(m.get("role") == "tool" for m in messages)

        # 도구 호출 단계: tools가 있고 아직 도구 결과가 없으면 tool_call을 한 번 만든다.
        if tools and not already_ran_tool:
            first = tools[0].get("function", {})
            call = {
                "id": "call_stub_1",
                "type": "function",
                "function": {"name": first.get("name", "unknown_tool"), "arguments": "{}"},
            }
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
            self._completion(model, message, "tool_calls", stream=bool(body.get("stream")))
            return

        content = FIXED_ANSWER
        if body.get("response_format", {}).get("type") in {"json_object", "json_schema"}:
            content = json.dumps({"answer": "42", "confidence": 0.9}, ensure_ascii=False)

        message = {"role": "assistant", "content": content}
        self._completion(model, message, "stop", stream=bool(body.get("stream")))

    def _completion(self, model: str, message: dict, finish_reason: str, *, stream: bool) -> None:
        created = int(time.time())
        if not stream:
            self._json(
                200,
                {
                    "id": "chatcmpl-stub",
                    "object": "chat.completion",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
            return

        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()

        def send(delta: dict, reason=None) -> None:
            chunk = {
                "id": "chatcmpl-stub",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": reason}],
            }
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

        if message.get("tool_calls"):
            send({"role": "assistant", "tool_calls": message["tool_calls"]})
        else:
            for piece in STREAM_PIECES:
                send({"content": piece})
                time.sleep(0.05)  # 스트리밍이 실제로 나뉘어 오는 것을 눈으로 보게 한다
        send({}, finish_reason)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    # --- /v1/embeddings ----------------------------------------------------
    def _embeddings(self, body: dict) -> None:
        raw_input = body.get("input")
        items = raw_input if isinstance(raw_input, list) else [raw_input or ""]
        data = [
            {"object": "embedding", "index": i, "embedding": _pseudo_embedding(str(text))}
            for i, text in enumerate(items)
        ]
        self._json(
            200,
            {
                "object": "list",
                "data": data,
                "model": body.get("model", "stub-embedding"),
                "usage": {"prompt_tokens": 5, "total_tokens": 5},
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="학습용 OpenAI 호환 스텁 서버")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8111)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"스텁 서버 실행 중: http://{args.host}:{args.port}/v1")
    print("종료하려면 Ctrl+C를 누른다.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료한다.")


if __name__ == "__main__":
    main()
