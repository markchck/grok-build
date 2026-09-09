# 검증용 보조 도구

## fake_openai_server.py — 학습용 OpenAI 호환 스텁 서버

실제 모델 서버가 없어도 각 단계의 코드가 **요청을 올바르게 만들고 응답을 올바르게 해석하는지**를
확인하기 위한 최소 서버다.

```bash
python code/_tools/fake_openai_server.py --port 8111
```

다른 터미널에서:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8111/v1
export OPENAI_API_KEY=sk-test
export CHAT_MODEL=stub-model
export EMBEDDING_MODEL=stub-embedding
python -m docagent.cli
```

지원 범위:

| 기능 | 동작 |
| --- | --- |
| `POST /v1/chat/completions` | 고정 문자열 응답 |
| `stream=true` | SSE 청크 3개 + `finish_reason` 청크 + `data: [DONE]` |
| `response_format` (`json_object`, `json_schema`) | 고정 JSON 반환 |
| `tools` | 첫 응답에서 `tool_calls` 1회 생성. `role="tool"` 메시지가 들어오면 최종 답변 |
| `POST /v1/embeddings` | 8차원 의사 임베딩(같은 입력 → 같은 벡터) |
| `model=bad-model` | HTTP 400 오류 |
| `model=slow-model` | 응답 지연(타임아웃 실습용) |

### 이 서버로 확인할 수 있는 것과 없는 것

확인할 수 있다:
- 요청 본문 형식(`messages`, `tools`, `response_format`)이 맞는지
- 스트리밍 파싱, 도구 호출 루프, 오류·타임아웃 처리 경로가 실제로 도는지

확인할 수 없다:
- 답변 품질, 모델이 실제로 어떤 도구를 고르는지, 검색 결과의 의미적 적합성
- 실제 서버의 기능 지원 범위 — 그것은 `scripts/check_server_features.py`를 **실제 서버**에 대고 실행해야 한다

임베딩은 해시 기반 의사 벡터이므로 **의미 유사도가 없다.** 4·5단계에서 검색 품질을 보려면
실제 임베딩 모델이 필요하다.

이 서버는 학습용이다. 인증을 검사하지 않으므로 외부에 노출하지 않는다.
