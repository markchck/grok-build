# 1단계. LLM API 직접 호출하기

## 1. 학습 목표와 완성 모습

이 장은 커리큘럼의 첫 단계다. 앞 장이 없으므로 확장할 대상은 없고, 이후 모든 장이 이 장에서 만드는
`docagent.llm` 모듈 위에 기능을 쌓는다. 2단계는 이 호출을 FastAPI 엔드포인트로 감싸고, 3단계는
같은 호출에 도구(tool) 정의를 추가해 에이전트 루프를 만든다.

이 장에서 배우는 것은 프레임워크(LangChain, LangGraph 등)를 쓰지 않고 LLM(거대 언어 모델, Large
Language Model) 서버와 직접 통신하는 방법이다. 구체적으로 다음을 다룬다.

- HTTP API와 "OpenAI 호환 API"가 무슨 관계인지
- 모델 서버와 클라이언트 SDK가 각각 무엇을 책임지는지
- `messages` 배열과 `system`/`user`/`assistant` 역할
- 모델 선택, 출력 길이 제한, `temperature`(출력의 무작위성 정도)
- 일반(비스트리밍) 응답과 스트리밍 응답의 차이
- 구조화된 JSON 출력을 요청하는 방법과 한계
- 타임아웃, API 오류, 재시도

완성 모습은 터미널에서 질문을 입력하면 답변을 받는 프로그램이다.

```
$ python -m docagent.cli --stream
모델 서버: http://localhost:8000/v1  모델: qwen2.5-7b-instruct
backend=sdk stream=True  (Ctrl+D 또는 'exit'로 종료)
user> 파이썬에서 리스트와 튜플의 차이를 한 문장으로 말해줘.
assistant> 리스트는 값을 바꿀 수 있고(mutable), 튜플은 한 번 만들면 값을 바꿀 수 없다(immutable)는 점이 다르다.
user> exit
```

같은 프로그램을 `--backend raw`로 실행하면 SDK 없이 httpx로 직접 만든 HTTP 요청으로 같은 결과를
얻는다. 두 경로를 비교하면서 SDK가 대신 해 주는 일이 무엇인지 확인하는 것이 이 장의 핵심이다.

## 2. 핵심 개념

### HTTP API와 "OpenAI 호환 API"

LLM 서버는 결국 HTTP 서버다. 클라이언트가 특정 형식의 JSON을 `POST`로 보내면, 서버는 모델을 실행해
그 결과를 JSON(또는 스트리밍의 경우 이벤트 스트림)으로 돌려준다. OpenAI가 자사 API에서 쓰는 요청·응답
형식(`POST /v1/chat/completions`, `messages` 배열, `choices[0].message.content` 등)이 사실상
업계 표준처럼 쓰이면서, 다른 회사·오픈소스 프로젝트가 만든 모델 서버들도 같은 형식을 그대로 흉내 낸
엔드포인트를 제공하기 시작했다. 이런 서버를 "OpenAI 호환(compatible) API"라고 부른다.

즉 "OpenAI 호환"은 특정 회사의 제품이 아니라 **요청·응답 형식의 약속**을 가리킨다. 이 약속을 따르는
서버라면 OpenAI의 클라우드 API든, 로컬에서 돌리는 오픈소스 모델 서버든 같은 클라이언트 코드로 호출할
수 있다. 이 튜토리얼은 `OPENAI_BASE_URL` 환경 변수로 서버 주소를 바꾸는 것만으로 어떤 OpenAI 호환
서버든 붙일 수 있게 코드를 짠다(`ai/llm-agent-tutorial/PROJECT-SPEC.md` 2절).

다만 형식이 같다고 **기능**까지 같은 것은 아니다. 요청 형식을 파싱할 수 있다는 것과, 그 요청에 담긴
모든 옵션(도구 호출, 구조화 출력, 스트리밍 등)을 실제로 구현했다는 것은 다른 문제다. 이 장 뒷부분과
3절의 `scripts/check_server_features.py`에서 이 차이를 실제로 확인한다.

### 누가 무엇을 하는가: 모델 서버 / SDK / 애플리케이션

이 장에 등장하는 세 주체의 책임을 구분한다.

| 주체 | 하는 일 | 하지 않는 일 |
| --- | --- | --- |
| 모델 서버 (vLLM, llama.cpp server, Ollama, LM Studio, OpenAI 클라우드 등) | HTTP 요청을 받아 모델을 실행하고 토큰을 생성한다. 인증, 요청 큐잉, (구현했다면) 스트리밍·도구 호출·구조화 출력을 처리한다 | 클라이언트 언어별 편의 기능은 제공하지 않는다. HTTP를 그대로 이해해야 쓸 수 있다 |
| 클라이언트 SDK (`openai` 파이썬 패키지 등) | HTTP 요청을 만들고 보내는 코드를 함수 호출로 감싼다. 요청·응답을 타입이 있는 파이썬 객체로 바꾼다. 일부 오류에 대해 자동 재시도와 타임아웃을 처리한다. 스트리밍 청크를 순회 가능한(iterable) 객체로 감싼다 | 서버가 지원하지 않는 기능을 대신 구현해 주지는 않는다. SDK가 `tools` 파라미터를 보낼 수 있다는 것과, 서버가 그 파라미터를 실제로 처리한다는 것은 별개다 |
| 애플리케이션 (이 저장소의 `docagent` 패키지) | 어떤 모델을 언제 어떤 파라미터로 호출할지 결정한다. 오류·타임아웃 발생 시 사용자에게 어떻게 보여줄지 정한다. 이후 단계에서는 도구 실행, 검색, 세션 관리도 애플리케이션의 책임이다 | 모델을 직접 실행하지 않는다. HTTP 통신의 저수준 세부 사항(TCP 연결, TLS)은 SDK나 httpx에 맡긴다 |

SDK는 "더 적은 코드로 같은 HTTP 요청을 만드는 도구"에 가깝다. 이 장에서 raw httpx 구현과 SDK
구현을 나란히 두는 이유가 이것이다 — 둘이 최종적으로 서버에 보내는 내용은 같다.

### `messages`와 역할(role)

Chat Completions 형식의 요청은 대화를 하나의 배열로 표현한다.

```json
{
  "model": "qwen2.5-7b-instruct",
  "messages": [
    {"role": "system", "content": "너는 간결하게 답하는 한국어 어시스턴트다."},
    {"role": "user", "content": "리스트와 튜플의 차이는?"},
    {"role": "assistant", "content": "리스트는 값을 바꿀 수 있고..."},
    {"role": "user", "content": "그럼 성능 차이는?"}
  ]
}
```

- `system`: 모델의 행동 방식을 지시한다. 배열 맨 앞에 한 번(또는 필요하면 여러 번) 넣는다. 사용자가
  쓰는 문장이 아니라 애플리케이션이 미리 정해 넣는 문장이다.
- `user`: 사용자가 실제로 입력한 문장.
- `assistant`: 모델이 이전에 생성한 답변. 대화를 이어가려면 이전 턴의 `assistant` 메시지를 그대로
  배열에 남겨 다음 요청에 함께 보내야 한다 — 서버는 이전 요청을 기억하지 않는다. 각 요청은 그
  자체로 완결된 대화 전체를 담아야 한다(무상태, stateless).

이 장의 `docagent/cli.py`는 `history` 리스트에 `system` 메시지 하나로 시작해, 매 턴마다 `user`와
`assistant` 메시지를 순서대로 이어 붙인다.

### 모델 선택, 출력 길이, temperature

- **모델 선택**(`model` 필드): 서버에 여러 모델이 등록되어 있을 수 있다. 이름은 서버마다 다르므로
  코드에 고정하지 않고 환경 변수 `CHAT_MODEL`로 받는다.
- **출력 길이**(`max_tokens`): 모델이 생성할 수 있는 최대 토큰(token, 모델이 다루는 텍스트 조각) 수.
  이 값을 넘기면 응답이 중간에 잘리고 `finish_reason`이 `"length"`가 된다. OpenAI의 최신 공식 API는
  일부 모델에서 `max_tokens` 대신 `max_completion_tokens`를 쓰도록 안내하지만, 이 튜토리얼이
  대상으로 하는 자체 호스팅 OpenAI 호환 서버는 아직 `max_tokens`만 지원하는 경우가 많다. 어느 이름을
  받는지는 서버 문서로 확인한다.
- **temperature**: 다음 토큰을 고를 때의 무작위성 정도. 보통 0~2 범위이고, 0에 가까울수록 같은
  입력에 항상 비슷한 답을 하고, 값이 커질수록 다양하고 예측하기 어려운 답을 한다. 구조화된 데이터
  추출처럼 정답이 하나에 가까운 작업은 0에 가깝게, 아이디어를 여러 개 뽑는 작업은 높게 준다.

### 일반 응답과 스트리밍 응답

같은 요청이라도 `stream` 필드 하나로 응답 방식이 완전히 달라진다.

```
[일반 응답 (stream: false)]
클라이언트 ---요청 1회---> 서버
클라이언트 <--응답 1회(전체 텍스트)-- 서버   (모델이 답을 다 만들 때까지 기다림)

[스트리밍 응답 (stream: true)]
클라이언트 ---요청 1회---> 서버
클라이언트 <--청크 1(delta.content="안")-- 서버
클라이언트 <--청크 2(delta.content="녕")-- 서버
클라이언트 <--청크 3(delta.content="하")-- 서버
              ...
클라이언트 <--[DONE]-- 서버
```

일반 응답은 모델이 답변을 전부 만들 때까지 클라이언트가 기다린다. 스트리밍은 토큰이 만들어지는
대로 조각(청크)을 보내, 사용자가 화면에서 텍스트가 실시간으로 이어지는 것을 볼 수 있게 한다.
스트리밍은 사용자가 체감하는 첫 응답 시간을 줄여 주지만, 전체 완료까지 걸리는 시간을 줄여 주지는
않는다. 스트리밍의 실제 전송 형식(Server-Sent Events)은 4절에서 raw 레벨로 확인한다.

### 구조화된 JSON 출력

모델의 답을 사람이 읽는 문장이 아니라, 프로그램이 바로 파싱할 수 있는 JSON으로 받고 싶을 때가
있다(예: `{"city": "서울", "temperature_c": 21}`). 이를 위한 방법이 `response_format`이다.

- `{"type": "json_object"}`: "유효한 JSON으로만 답하라"고 요청한다. 어떤 키가 나올지는 모델이
  자유롭게 정한다.
- `{"type": "json_schema", "json_schema": {...}}`: JSON 스키마(JSON Schema)를 함께 보내, 응답이
  그 구조를 따르도록 요청한다. `json_object`보다 강한 보장을 목표로 한다.

두 방식 모두 "요청"이지 "강제"가 아니다. 지원 수준은 서버·모델마다 다르다. `json_schema`를 아예
모르는 서버는 400 오류를 낼 수도 있고, 파라미터를 조용히 무시하고 평범한 텍스트를 돌려줄 수도
있다. 이 장에서는 두 경우 모두 실제로 호출해 확인하는 스크립트(`scripts/check_server_features.py`)를
만든다.

### 타임아웃, API 오류, 재시도

- **타임아웃**: 서버가 정해진 시간 안에 응답(또는 스트리밍의 경우 다음 청크)을 보내지 않으면
  클라이언트가 기다림을 포기하고 예외를 던진다. 네트워크 문제나 서버 과부하로 요청이 무한정
  걸리는 것을 막는다.
- **API 오류**: 서버가 명시적으로 실패를 알리는 경우다. HTTP 상태 코드로 구분한다 — 4xx는 요청
  자체의 문제(잘못된 모델 이름, 인증 실패 등), 5xx는 서버 쪽 문제, 429는 요청 빈도 제한 초과다.
- **재시도**: 같은 요청을 다시 보내면 성공할 수도 있는 오류(429, 5xx, 타임아웃)만 재시도 대상으로
  삼는다. 400처럼 요청 자체가 잘못된 오류는 몇 번을 다시 보내도 같은 결과이므로 재시도하지 않는다.
  무조건 재시도하면 이미 과부하 상태인 서버에 요청을 더 얹는 역효과가 날 수 있어, 재시도 사이에
  대기 시간을 둔다(지수 백오프, exponential backoff).

## 3. 실습 준비

### 필요한 것

- 실행 중인 OpenAI 호환 모델 서버 하나. 로컬에서 vLLM, llama.cpp server, Ollama, LM Studio 등으로
  띄운 서버, 또는 사내에 이미 있는 서버 중 아무거나 된다. 이 장의 코드는 특정 서버 구현에 의존하지
  않는다.
- Python 3.10 이상 (`openai` 패키지가 요구하는 최소 버전).

### 패키지

`code/step01_raw_api/requirements.txt`. 버전은 2026-09-09 기준으로 PyPI에서 확인한 값이다.

```
httpx>=0.28,<0.29
openai>=3.10,<4
python-dotenv>=1.0,<2
```

`httpx`는 raw HTTP 호출에, `openai`는 SDK 호출에, `python-dotenv`는 `.env` 파일에서 환경 변수를
읽어 오는 데 쓴다. 이후 장(2~5단계)의 `requirements.txt`도 같은 범위를 그대로 쓴다
(`PROJECT-SPEC.md` 10절).

`openai` 패키지 자체의 의존성 하나는 헷갈리기 쉬우니 미리 짚어 둔다. `openai` 3.x는 HTTP 전송
계층으로 `httpx`가 아니라 **`httpx2`**(`>=2.7.0,<3`)에 의존한다 — `pip install openai`를 하면
`httpx2`가 함께 설치된다. 이 장이 raw HTTP 호출에 직접 쓰는 `httpx`(0.28.1)와 `openai`가 내부적으로
쓰는 `httpx2`(2.12.0)는 이름 자체가 다른 별개의 패키지라서 같은 환경에 설치돼도 버전 충돌이
나지 않는다. 즉 이 장의 `raw_*` 함수와 `openai` SDK 경로는 겉보기엔 "같은 HTTP 라이브러리를
쓰는 것 같지만" 실제로는 서로 다른 두 패키지를 쓴다. 2026-09-09 기준 PyPI에서 실제로 설치해
확인한 값이다(`VERIFIED-FINDINGS.md` 1절).

### 환경 변수

`code/step01_raw_api/.env.example`. 이름은 `PROJECT-SPEC.md` 2절에 고정되어 있으며 이후 장에서도
그대로 쓴다.

```
OPENAI_BASE_URL=http://localhost:8000/v1
OPENAI_API_KEY=sk-local-example-key
CHAT_MODEL=your-chat-model-name
REQUEST_TIMEOUT_SECONDS=60
```

실제 키를 이 파일에 넣지 않는다. `.env`로 복사한 뒤 각자 환경에 맞는 값을 넣는다. 로컬 서버는
`OPENAI_API_KEY`를 검사하지 않는 경우가 많지만, SDK가 빈 문자열을 거부할 수 있어 예시처럼 아무
문자열이나 넣어 둔다.

### 폴더 구조

```
code/step01_raw_api/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── __init__.py
│   ├── config.py
│   ├── llm.py
│   └── cli.py
└── scripts/
    └── check_server_features.py
```

`PROJECT-SPEC.md` 1절의 공통 배치 중 `app.py`, `tools.py`, `agent.py`, `rag/`, `static/`, `data/`는
2단계 이후에 추가된다. 이 장에서는 만들지 않는다.

## 4. 단계별 구현

### 4.1 환경 변수 로딩

`code/step01_raw_api/docagent/config.py`

```python
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_base_url: str
    openai_api_key: str
    chat_model: str
    request_timeout_seconds: float


def _get_env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError(f"환경 변수 {name}이(가) 설정되지 않았다.")
    return value or ""


def load_settings() -> Settings:
    return Settings(
        openai_base_url=_get_env("OPENAI_BASE_URL", default="http://localhost:8000/v1", required=True),
        openai_api_key=_get_env("OPENAI_API_KEY", default="sk-local-example-key", required=True),
        chat_model=_get_env("CHAT_MODEL", required=True),
        request_timeout_seconds=float(_get_env("REQUEST_TIMEOUT_SECONDS", default="60")),
    )
```

필수 값이 비어 있으면 첫 호출이 아니라 프로그램 시작 시점에 바로 `RuntimeError`를 던진다. 모델
서버까지 요청을 보낸 뒤에야 설정 실수를 알게 되는 상황을 피하기 위해서다. 이 모듈은 이후 모든
단계가 공통으로 쓴다.

### 4.2 raw httpx로 일반 응답 호출하기

`code/step01_raw_api/docagent/llm.py` (일부)

```python
import httpx

def raw_chat_completion(settings, messages, *, temperature=0.2, max_tokens=512):
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    body = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=settings.request_timeout_seconds) as client:
        response = client.post(url, headers=headers, json=body)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]
```

이것이 OpenAI 호환 API 호출의 가장 밑바닥 형태다. `Authorization: Bearer <키>` 헤더, JSON 본문,
`POST /chat/completions`, 응답의 `choices[0].message.content` — SDK를 쓰든 안 쓰든 실제로 오가는
내용은 이것뿐이다. `httpx.Client(timeout=...)`는 연결·읽기·쓰기 타임아웃을 한 번에 지정하는
가장 단순한 형태이고, 세밀하게 나누려면 `httpx.Timeout(connect=..., read=..., write=..., pool=...)`
를 쓴다.

실제 저장소 코드(`code/step01_raw_api/docagent/llm.py`)는 여기에 429/5xx 재시도와 타임아웃 예외
처리를 더한 `raw_chat_completion`으로 구현되어 있다. 재시도 대상 상태 코드만 다시 시도하고, 400같은
클라이언트 오류는 즉시 실패로 처리한다.

### 4.3 raw httpx로 스트리밍 응답 파싱하기

스트리밍 요청의 실제 응답 본문은 아래와 같은 텍스트 줄들이다(Server-Sent Events, SSE 형식).

```
data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":"안"},"finish_reason":null}]}

data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":"녕"},"finish_reason":null}]}

data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

각 줄은 `data: `로 시작하고 그 뒤에 JSON 청크 하나가 온다(빈 줄은 이벤트 구분자). 첫 응답 조각이
아니라 델타(직전 청크 이후로 새로 생긴 부분)만 `delta.content`에 담겨 온다는 점이 일반 응답과 다르다.
마지막 줄 `data: [DONE]`은 JSON이 아니라 문자열이며, 스트림이 끝났다는 신호다.

`code/step01_raw_api/docagent/llm.py`의 `raw_chat_completion_stream`은 이 형식을 직접 파싱한다.

```python
def raw_chat_completion_stream(settings, messages, *, temperature=0.2, max_tokens=512):
    url = f"{settings.openai_base_url.rstrip('/')}/chat/completions"
    body = {"model": settings.chat_model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens, "stream": True}
    headers = {"Authorization": f"Bearer {settings.openai_api_key}",
               "Content-Type": "application/json"}
    with httpx.Client(timeout=settings.request_timeout_seconds) as client, \
         client.stream("POST", url, headers=headers, json=body) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            content = chunk["choices"][0].get("delta", {}).get("content")
            if content:
                yield content
```

`client.stream(...)`은 응답 본문을 한 번에 받지 않고 줄 단위로 흘려보낸다. `[DONE]`을 만나면
루프를 끝낸다. 서버가 SSE 대신 다른 스트리밍 방식(예: 청크마다 개행 없는 NDJSON)을 쓴다면 이
파싱 코드는 그대로 쓸 수 없다 — OpenAI 호환 API의 스트리밍은 SSE라는 것이 사실상의 표준이지만,
이 역시 "약속"이지 강제되는 규격은 아니다.

### 4.4 openai SDK로 같은 요청 보내기

`code/step01_raw_api/docagent/llm.py` (일부)

```python
from openai import OpenAI

def _make_client(settings, max_retries=2):
    return OpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        timeout=settings.request_timeout_seconds,
        max_retries=max_retries,
    )

def chat(settings, messages, *, temperature=0.2, max_tokens=512):
    client = _make_client(settings)
    completion = client.chat.completions.create(
        model=settings.chat_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return completion.choices[0].message.content

def chat_stream(settings, messages, *, temperature=0.2, max_tokens=512):
    client = _make_client(settings)
    stream = client.chat.completions.create(
        model=settings.chat_model, messages=messages,
        temperature=temperature, max_tokens=max_tokens, stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
```

4.2·4.3절과 비교하면 SDK가 대신 해 주는 일이 분명해진다.

- URL 조립(`base_url` 뒤에 `/chat/completions`를 붙이는 것), 인증 헤더 작성을 대신한다.
- 요청 본문의 JSON 직렬화와 응답의 JSON 역직렬화를 대신하고, `completion.choices[0].message.content`
  처럼 속성으로 접근할 수 있는 타입이 있는 객체를 돌려준다(raw 방식은 `data["choices"][0]...`처럼
  딕셔너리 키로 접근해야 한다).
- SSE 파싱(`data:` 접두어 제거, `[DONE]` 처리, JSON 파싱)을 대신하고 `for chunk in stream`만으로
  청크를 순회할 수 있게 한다.
- 429·5xx·타임아웃 성격의 오류에 대해 지수 백오프 재시도를 기본 제공한다(`max_retries` 파라미터로
  조정, 기본값은 2회). 이 장의 raw 구현은 재시도 로직을 직접 짰지만, SDK를 쓰면 이 부분을 다시
  구현할 필요가 없다.

SDK가 대신 해 주지 **않는** 것: 서버가 실제로 어떤 기능을 지원하는지는 SDK가 알려주지 않는다.
`tools`나 `response_format`을 파라미터로 받아 줄 뿐, 서버가 그것을 실제로 처리하는지는 호출해
봐야 안다.

### 4.5 구조화된 JSON 출력 요청하기

`code/step01_raw_api/docagent/llm.py` (일부)

```python
def chat_json(settings, messages, *, schema_name, json_schema, temperature=0.0):
    client = _make_client(settings)
    completion = client.chat.completions.create(
        model=settings.chat_model,
        messages=messages,
        temperature=temperature,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": json_schema, "strict": True},
        },
    )
    content = completion.choices[0].message.content or "{}"
    return json.loads(content)
```

`json_schema`는 표준 JSON 스키마 객체다(`type`, `properties`, `required` 등). `strict: True`는
"스키마를 정확히 따르라"는 요청이며, 이를 실제로 강제하는지는 서버 구현에 달려 있다. 이 함수를
호출하기 전에 서버가 `json_schema`를 지원하는지 반드시 확인한다(6절, `scripts/check_server_features.py`).

### 4.6 터미널 채팅 프로그램으로 합치기

`code/step01_raw_api/docagent/cli.py`는 위 함수들을 묶어 `user>` / `assistant>` 프롬프트가 있는
반복 루프를 만든다. 핵심 부분만 보면 다음과 같다.

```python
history = [{"role": "system", "content": SYSTEM_PROMPT}]

while True:
    question = input("user> ").strip()
    if question in ("exit", "quit"):
        break
    history.append({"role": "user", "content": question})

    result = chat(settings, history, temperature=args.temperature, max_tokens=args.max_tokens)
    print(f"assistant> {result.text}")
    history.append({"role": "assistant", "content": result.text})
```

매 턴마다 `history`에 메시지를 추가하고, 다음 요청에서는 `history` 전체를 다시 보낸다 — 2절에서
설명했듯 서버는 이전 요청을 기억하지 않기 때문이다. 전체 구현(오류 처리, `--stream`/`--backend`
옵션 포함)은 `code/step01_raw_api/docagent/cli.py`에 있다.

## 5. 실행과 결과 확인

```bash
cd code/step01_raw_api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # .env를 열어 서버 주소·모델 이름을 실제 값으로 바꾼다
```

먼저 서버 기능을 확인한다(6절과 이어짐).

```bash
python scripts/check_server_features.py
```

이어서 채팅 프로그램을 네 가지 조합으로 실행해 본다.

```bash
python -m docagent.cli                        # SDK, 일반 응답
python -m docagent.cli --stream                # SDK, 스트리밍
python -m docagent.cli --backend raw            # httpx, 일반 응답
python -m docagent.cli --backend raw --stream   # httpx, 스트리밍
```

**확인할 것 (모델 출력 문구 자체는 실행마다 달라질 수 있으므로 구조와 동작을 본다):**

- 일반 응답은 `assistant>` 뒤에 답이 한 번에 나타난다. 스트리밍은 글자가 순차적으로 이어져
  나타난다.
- 네 조합 모두 같은 질문에 비슷한 취지의 답을 준다 — `--backend`는 호출 방식만 바꾸고, 서버에
  전달되는 `messages`·`model`·`temperature` 등 요청 내용은 같다.
- `history`가 잘 유지되는지 확인하려면 "방금 내가 뭐라고 물었지?" 같은 후속 질문을 해 본다.
  이전 턴을 참조한 답이 나오면 `messages` 배열이 제대로 누적되고 있다는 뜻이다.
- `--max-tokens`를 아주 작게(예: `--max-tokens 8`) 주고 긴 답이 필요한 질문을 하면, 답이 중간에
  끊기고(일반 응답 기준) 내부적으로 `finish_reason`이 `"length"`로 온다.

**모델 서버 없이 확인하기**: 실제 모델 서버가 아직 없다면 `code/_tools/fake_openai_server.py`
(학습용 OpenAI 호환 스텁 서버)를 대신 띄워 위 명령을 그대로 실행할 수 있다.

```bash
python code/_tools/fake_openai_server.py --port 8111
# 다른 터미널에서 OPENAI_BASE_URL=http://127.0.0.1:8111/v1 등을 .env에 넣고 위 명령을 실행한다.
```

이 스텁 서버로 확인할 수 있는 것은 요청 형식과 응답 파싱 경로(스트리밍 조각, 도구 호출,
오류·타임아웃 처리)가 코드대로 도는지까지다. 답변 품질이나 실제 서버의 기능 지원 범위는
확인할 수 없다 — `code/_tools/README.md`에 이 서버가 확인할 수 있는 것과 없는 것이 정리돼 있다.

## 6. 실패 상황 실습

### 6.1 서버 기능 미지원 확인하기

`scripts/check_server_features.py`를 실행하면 스트리밍/도구 호출/구조화 출력(`json_object`,
`json_schema`) 각각을 실제로 한 번씩 호출해 지원 여부를 판정한다.

```bash
python scripts/check_server_features.py
```

예상 출력 형태(서버에 따라 결과는 다르다):

```
서버: http://localhost:8000/v1  모델: qwen2.5-7b-instruct

[기본 응답] OK - 응답: 'ping'
[스트리밍] OK - 청크 2개 이상 수신
[도구 호출 (tool calling)] OK - 모델이 도구 호출을 요청했다: get_weather({"city": "서울"})
[구조화 출력: response_format=json_object] OK - 유효한 JSON 수신: '{"city": "서울", "summary": "..."}'
[구조화 출력: response_format=json_schema] FAIL - HTTP 400: Unknown parameter: 'response_format.json_schema'
  전형적인 미지원 증상: json_schema는 모르고 json_object만 지원하거나,
  response_format 자체를 몰라 400을 반환한다.
```

미지원 서버에서 각 기능이 실패하는 전형적인 두 가지 패턴을 이 스크립트에서 확인할 수 있다.

1. **명시적 거부**: 서버가 모르는 파라미터를 만나면 `400 Bad Request`를 돌려준다. 이 경우
   `openai` SDK는 `APIStatusError`를 던지므로 코드에서 바로 감지할 수 있다.
2. **조용한 무시**: 서버가 파라미터를 파싱은 하되 실제로 반영하지 않는다. 예를 들어 `tools`를
   보냈는데 모델이 도구를 전혀 선택하지 않거나, `stream=True`인데 청크가 한 번에 몰려 온다. 이
   경우 HTTP 오류가 나지 않으므로, "응답이 예상한 형태인지"를 코드가 직접 확인해야 한다. 이
   스크립트가 스트리밍 검사에서 청크 개수를 세는 이유, 도구 호출 검사에서 `tool_calls`가 비어
   있는지 확인하는 이유가 이것이다.

이후 단계(3, 4, 6단계 등)에서 도구 호출이나 구조화 출력을 쓰기 전에는 먼저 이 스크립트로 대상
서버가 그 기능을 지원하는지 확인한다. 모든 OpenAI 호환 서버가 같은 기능 집합을 지원한다고
가정하지 않는다.

### 6.2 타임아웃 의도적으로 발생시키기

`.env`의 `REQUEST_TIMEOUT_SECONDS`를 아주 작은 값(예: `1`)으로 바꾸고 다시 실행한다.

```bash
REQUEST_TIMEOUT_SECONDS=1 python -m docagent.cli --backend raw
```

서버 응답이 1초 안에 오지 않으면 `httpx.TimeoutException`이 발생하고, `docagent.llm`은 이를
`LLMError`로 감싸 CLI에 `[오류] 1.0초 안에 응답이 오지 않았다 (재시도 2회 모두 실패).`처럼
표시한다. `--backend sdk`로 실행하면 `openai.APITimeoutError`가 같은 역할을 한다. 프로그램이
멈추지 않고 사용자에게 원인을 알려준 뒤 다음 질문을 받을 준비를 하는지 확인한다(`docagent/cli.py`의
`run_once` 호출을 감싼 `try/except LLMError`).

### 6.3 잘못된 모델 이름으로 400 오류 발생시키기

`.env`의 `CHAT_MODEL`을 서버에 등록되지 않은 이름(예: `no-such-model`)으로 바꾸고 실행한다.
대부분의 서버는 `404`(모델 없음) 또는 `400`(잘못된 모델 이름)을 반환한다. 이 오류는 몇 번을
재시도해도 결과가 같으므로, `raw_chat_completion`과 `chat` 모두 재시도하지 않고
즉시 `LLMError`로 실패를 알린다. 6.2절의 타임아웃(재시도 후 실패)과 이 경우(즉시 실패)를
로그 메시지로 구분할 수 있는지 확인한다.

### 6.4 API 키 누락 확인하기

`.env`에서 `OPENAI_API_KEY` 줄을 지우거나 `OPENAI_API_KEY=`로 비워 두고 실행하면, 필수 값이
비어 있으므로 서버에 요청을 보내기도 전에 `config.py`의 `load_settings()`가 `RuntimeError`를
던지고 프로그램이 바로 종료된다. 인증 자체를 요구하지 않는 로컬 서버라면 이 값이 비어 있어도
서버 쪽에서는 오류가 나지 않을 수 있지만, 이 저장소의 설정 로더는 "키 이름이 비어 있다"는 사실
자체를 실수로 보고 막는다.

## 7. 응용 과제

1. **temperature 비교**: 같은 질문("아이디어 세 개만 던져줘" 같은 열린 질문)을
   `--temperature 0`과 `--temperature 1.2`로 각각 3번씩 실행해 결과가 얼마나 달라지는지
   비교한다. 완료 기준: 낮은 temperature에서 답이 거의 똑같이 반복되고, 높은 temperature에서
   매번 다른 답이 나오는 것을 직접 확인한다.
2. **재시도 횟수 조정**: `chat`을 호출하는 부분에서 `_make_client`의
   `max_retries`를 `0`으로 바꿔 보고, 서버를 잠깐 내린 상태(또는 잘못된 포트로 `OPENAI_BASE_URL`을
   바꾼 상태)에서 실행해 오류가 얼마나 빨리 나는지 비교한다. 힌트: `max_retries=2`일 때와
   `max_retries=0`일 때 오류가 나기까지 걸리는 시간 차이를 초시계로 재 본다.
3. **json_object로 데이터 추출해 보기**: 자유 문장("서울 강남구, 3억 5천만 원짜리 24평 아파트")을
   입력받아 `{"district": "...", "price_won": ..., "area_pyeong": ...}` 형태의 JSON으로 뽑아내는
   작은 스크립트를 `docagent/llm.py`의 `chat` 대신 `response_format={"type":
   "json_object"}`를 써서 만든다. 완료 기준: 최소 5개의 서로 다른 문장에 대해 유효한 JSON이
   나오는지 `json.loads`로 검증한다. 서버가 `json_object`도 지원하지 않는다면
   `scripts/check_server_features.py` 결과를 근거로 그 사실을 기록하고, 대신 "JSON으로만
   답하라"는 지시문을 `system` 메시지에 넣는 방식(강제력은 약하다)으로 대체해 본다.

## 8. 핵심 정리와 확인 질문

### 핵심 정리

- "OpenAI 호환 API"는 특정 제품이 아니라 요청·응답 형식의 약속이다. 형식이 같다고 지원 기능까지
  같은 것은 아니다.
- 모델 서버, 클라이언트 SDK, 애플리케이션은 서로 다른 것을 책임진다. SDK는 HTTP 요청을 편하게
  만들어 줄 뿐, 서버가 구현하지 않은 기능을 대신 만들어 주지 않는다.
- `messages` 배열은 `system`/`user`/`assistant` 역할로 대화를 표현하고, 서버는 요청 사이의 대화를
  기억하지 않으므로 애플리케이션이 매번 전체 이력을 다시 보낸다.
- 일반 응답은 완성된 답을 한 번에, 스트리밍 응답은 SSE 형식의 청크를 순서대로 받는다.
- `response_format`으로 구조화된 JSON 출력을 요청할 수 있지만 서버 지원 여부를 먼저 확인해야
  한다.
- 재시도는 일시적 오류(타임아웃, 429, 5xx)에만 하고, 요청 자체가 잘못된 오류(400 등)는 재시도하지
  않는다.

### 확인 질문

1. 로컬에서 돌리는 오픈소스 모델 서버를 "OpenAI 호환"이라고 부를 수 있는 근거는 무엇인가?
2. `openai` SDK 없이 `httpx`만으로 이 장의 채팅 프로그램을 만들 수 있었던 이유를 설명해 보라.
   SDK는 정확히 무엇을 줄여 주는가?
3. 스트리밍 응답에서 `data: [DONE]`이 하는 역할은 무엇인가?
4. `response_format={"type": "json_schema", ...}`를 보냈는데 서버가 이를 지원하지 않는다면 두 가지
   서로 다른 실패 형태가 있다. 각각 무엇이고 코드에서 어떻게 구분하는가?
5. 429 오류와 400 오류를 재시도 여부에서 다르게 다루는 이유는 무엇인가?

### 이 장의 완성 코드

`code/step01_raw_api/` (`docagent/config.py`, `docagent/llm.py`, `docagent/cli.py`,
`scripts/check_server_features.py`). 실행 방법은 같은 폴더의 `README.md`를 본다.

### 다음 장 연결점

2단계는 이 장의 `docagent.llm` 호출을 FastAPI 엔드포인트 뒤에 두고, 터미널 대신 웹 화면에서
스트리밍 응답과 진행 상태를 SSE로 함께 보여준다. `docagent/llm.py`는 거의 그대로 재사용되고,
새로 추가되는 것은 HTTP 서버 계층(`docagent/app.py`)과 세션별 대화 기록 관리다.

## 참고 문서

아래 URL은 2026-09-09 기준으로 WebSearch/WebFetch로 실제 확인한 내용이다.

- openai 파이썬 SDK 사용법(클라이언트 생성, `chat.completions.create`, `stream=True`,
  `response_format`, `timeout`/`max_retries`): <https://github.com/openai/openai-python/blob/main/README.md>
- openai 파이썬 SDK 재시도 정책(기본 재시도 대상 상태 코드, `max_retries` 옵션):
  <https://github.com/openai/openai-python/discussions/795>,
  <https://community.openai.com/t/timeout-for-openai-chat-completion-in-python/411252>
- Chat Completions 엔드포인트 요청 필드(`model`, `messages`, `max_tokens`/`max_completion_tokens`,
  `temperature`, `stream`, `tools`, `tool_choice`, `response_format`) 공식 레퍼런스 페이지:
  <https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create>
  (`developers.openai.com` 원문은 이 세션의 프록시에서 열리지 않아, 대신 설치한 `openai` 3.10.0
  패키지의 생성 타입 파일 `openai/types/chat/completion_create_params.py`를 직접 열어 확인했다.
  이 파일은 OpenAI의 OpenAPI 명세에서 자동 생성된 것이다. 확인 결과: `max_tokens`와
  `max_completion_tokens`가 **둘 다 현재 파라미터로 존재**하며, `max_tokens`에는 "이 값은
  `max_completion_tokens`로 대체되어 deprecated 되었고 o 시리즈 추론 모델과는 호환되지 않는다"는
  설명이 붙어 있다. 자체 호스팅 OpenAI 호환 서버는 `max_tokens`만 받는 경우가 많으므로 이 장의
  코드는 `max_tokens`를 쓴다.)
- 스트리밍 응답 개념(청크 단위 전송, `ChatCompletionChunk`):
  <https://developers.openai.com/api/docs/guides/streaming-responses>,
  <https://deepwiki.com/openai/openai-python/4.1.2-streaming-chat-completions>
- httpx 타임아웃 종류(`connect`/`read`/`write`/`pool`)와 예외(`ConnectTimeout`, `ReadTimeout`):
  <https://www.python-httpx.org/advanced/timeouts/>,
  <https://github.com/encode/httpx/blob/master/docs/advanced/timeouts.md>
- 패키지 최신 버전 확인: `openai` 3.10.0 <https://pypi.org/project/openai/>,
  `httpx` 0.28.1(안정 버전) <https://pypi.org/project/httpx/>

`response_format`의 `json_schema` 타입 필드는 설치한 `openai` 3.10.0 패키지의 생성 타입 파일
`openai/types/shared_params/response_format_json_schema.py`를 직접 열어 확인했다. 확인된 형태는
다음과 같다.

- `type`: 필수. 항상 `"json_schema"`.
- `json_schema`: 필수. 그 안에
  - `name`: **필수**. `a-z`, `A-Z`, `0-9`, `_`, `-`만 쓸 수 있고 최대 64자.
  - `schema`: JSON 스키마 객체.
  - `description`: 이 응답 형식이 무엇을 위한 것인지 모델에게 알려주는 설명.
  - `strict`: 스키마를 엄격히 지키게 할지 여부. `true`이면 JSON 스키마의 **일부 문법만** 지원된다.

`strict`의 기본값은 이 타입 정의에 명시되어 있지 않다(`Optional[bool]`이며 생략 가능).
생략했을 때 서버가 어떻게 처리하는지는 서버 구현마다 다르므로, 이 장의 코드는 `strict`를 명시적으로
지정한다. 자체 호스팅 서버는 `json_schema`를 아예 지원하지 않는 경우가 흔하다 —
`scripts/check_server_features.py`로 먼저 확인한다.

## 검증 상태

> 이 장의 코드는 아래 범위까지 **실제로 실행해** 확인했다.
>
> - `code/step01_raw_api/requirements.txt`를 새 가상환경에 설치해 의존성이 실제로 해결되는 것을
>   확인했다(설치된 버전: `openai` 3.10.0, `httpx` 0.28.1, `httpx2` 2.12.0, `python-dotenv` 1.2.3).
> - `code/_tools/fake_openai_server.py`(학습용 OpenAI 호환 스텁 서버)를 띄우고
>   `raw_chat_completion`, `raw_chat_completion_stream`, `chat`,
>   `chat_stream`, `chat_json` 다섯 경로가 모두 정상 응답을
>   반환하는 것을 확인했다.
> - `model=bad-model`로 HTTP 400을 유발해 raw 경로와 SDK 경로가 모두 `LLMError`로 변환하는
>   것을 확인했다.
> - `scripts/check_server_features.py`를 스텁 서버에 대고 실행해 5개 항목이 모두 판정되는 것을
>   확인했다.
> - 모든 `.py` 파일이 `python3 -m py_compile`을 통과한다.
>
> **확인하지 못한 것**: 실제 모델 서버(vLLM, Ollama, TGI, OpenAI 등)를 대상으로 한 실행은 이
> 환경에 접근 가능한 서버가 없어 하지 못했다. 스텁 서버는 요청 형식과 응답 파싱 경로만
> 검증하며, 실제 서버가 각 기능을 지원하는지, 재시도·타임아웃이 실제 부하에서 어떻게 동작하는지,
> 답변 품질이 어떤지는 검증하지 않는다. 그 확인은 독자가 자신의 서버에 대고
> `scripts/check_server_features.py`를 실행해서 직접 해야 한다.
