# step01_raw_api

1단계 완성 코드: 프레임워크 없이 OpenAI 호환 모델 서버를 호출하는 터미널 채팅 프로그램.
자세한 설명은 `../../docs/01-llm-api-direct.md`를 본다.

## 설치

```bash
cd code/step01_raw_api
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 환경 변수

```bash
cp .env.example .env
```

`.env`를 열어 아래 값을 실제 환경에 맞게 바꾼다.

| 변수 | 의미 |
| --- | --- |
| `OPENAI_BASE_URL` | 모델 서버의 OpenAI 호환 엔드포인트 (예: `http://localhost:8000/v1`) |
| `OPENAI_API_KEY` | 서버가 요구하는 API 키. 로컬 서버는 임의 문자열이어도 되는 경우가 많다 |
| `CHAT_MODEL` | 서버에 등록된 모델 이름 |
| `REQUEST_TIMEOUT_SECONDS` | 요청 타임아웃(초) |

## 실행

```bash
# 일반 응답, openai SDK 사용 (기본값)
python -m docagent.cli

# 스트리밍 응답
python -m docagent.cli --stream

# httpx로 직접 호출하는 경로로 실행
python -m docagent.cli --backend raw --stream
```

실행하면 `user>` 프롬프트가 뜬다. 질문을 입력하고 Enter를 누르면 답을 받는다.
`exit` 또는 Ctrl+D로 종료한다.

## 서버 기능 확인

```bash
python scripts/check_server_features.py
```

연결한 서버가 스트리밍 / 도구 호출(tool calling) / 구조화 출력
(`json_object`, `json_schema`)을 지원하는지 실제로 한 번씩 호출해 확인하고
결과와 실패 시 원인을 출력한다. 새 서버로 바꿀 때마다 다시 실행한다.

## 폴더 구성

```
step01_raw_api/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── __init__.py
│   ├── config.py     # 환경 변수 로딩
│   ├── llm.py         # 모델 서버 호출 (raw httpx + openai SDK 두 방식)
│   └── cli.py          # 터미널 채팅 프로그램 (실습 결과물)
└── scripts/
    └── check_server_features.py
```

`docagent/app.py`, `docagent/tools.py`, `docagent/agent.py`, `docagent/rag/`,
`static/`, `data/`는 이 단계에서 쓰지 않는다. 2단계 이후에 같은 `docagent`
패키지 아래 추가된다(`../../PROJECT-SPEC.md` 1절).

## 검증 상태

이 코드는 `python3 -m py_compile`로 문법 검사만 통과했다.
실제 모델 서버(로컬 vLLM/Ollama/LM Studio 등)에 대한 실행 검증은 하지 않았다.
