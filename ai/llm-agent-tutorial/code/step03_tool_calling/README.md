# step03_tool_calling

3단계 "Tool Calling과 에이전트 반복 실행"의 완성 코드.
계산 도구(`sum_sales`)와 CSV 조회 도구(`lookup_sales_rows`)를 쓰는 작은 에이전트를
프레임워크 없이 `while` 루프로 구현한다.

## 1. 설치

```bash
cd code/step03_tool_calling
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 환경 변수

```bash
cp .env.example .env
# .env를 열어 OPENAI_BASE_URL / CHAT_MODEL 등을 실제 모델 서버에 맞게 수정한다.
```

이 단계는 **도구 호출(tool calling)을 지원하는 OpenAI 호환 서버**가 필요하다.
서버가 지원하지 않으면 `tools`를 넘겨도 무시되거나 오류가 난다. 장 본문 6절 "실패
상황 실습"에 확인 방법과 우회 방법을 정리했다.

## 3. 실행

```bash
uvicorn docagent.app:app --reload --port 8080
```

브라우저에서 `http://localhost:8080/static/index.html`을 연다.

또는 curl로 직접 SSE 스트림을 확인한다.

```bash
curl -N -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "2분기 노트북 서울 매출 합계 알려줘"}'
```

## 4. 확인 사항

- `tool_call` 이벤트에 모델이 고른 도구 이름과 인자가 나온다.
- `tool_result` 이벤트에 애플리케이션이 실제로 계산한 결과 요약이 나온다.
- `done` 이벤트의 `finish_reason`이 `stop`이면 정상 종료, `max_steps` /
  `timeout` / `repeated_tool_call`이면 실행 한도에 걸려 중단된 것이다.

## 5. 테스트

모델 서버 없이도 도구 함수·인자 검증·반복 호출 감지 로직은 단위 테스트로
확인할 수 있다(가짜 `chat_fn`으로 모델 호출을 대체한다).

```bash
pip install pytest
pytest tests/ -v
```

## 6. 파일 구성

```
docagent/
├── config.py   # 환경 변수 로딩
├── llm.py      # OpenAI 호환 모델 서버 호출 (도구 실행은 하지 않는다)
├── events.py   # SSE 이벤트 스키마
├── tools.py    # 도구 함수 + 입력 스키마 + 실행 레지스트리
├── agent.py    # 에이전트 루프 (while 문 직접 구현)
└── app.py      # FastAPI 엔드포인트
static/         # 단순 웹 UI
data/sales.csv  # 샘플 판매 데이터
tests/          # 도구·에이전트 루프 단위 테스트
```
