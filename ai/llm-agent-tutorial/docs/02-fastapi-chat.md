# 2단계. FastAPI와 대화 화면 연결하기

## 1. 학습 목표와 완성 모습

1단계(`step01_raw_api`)에서는 터미널에서 LLM 서버를 직접 호출해 일반 응답과 스트리밍
응답의 차이를 확인했다. 이번 장은 그 호출을 터미널 밖으로 꺼내 웹 서비스로 만든다.

이번 장에서 만드는 것:

- `POST /api/chat` — 사용자 메시지를 받아 답변을 실시간으로 스트리밍하는 FastAPI 엔드포인트
- 세션 ID로 구분되는 대화 기록(같은 세션이면 이전 대화를 기억한다)
- SSE(Server-Sent Events)로 전송하는 답변 토큰(`token`)과 진행 상태(`status`) 이벤트
- 브라우저 탭을 닫거나 "취소" 버튼을 눌렀을 때 서버가 모델 호출을 실제로 중단하는 처리
- 요청마다 ID를 부여하고 처리 시간을 재는 HTTP 미들웨어
- 빌드 도구 없는 순수 HTML+JS 대화 화면

완성 후 동작 예시(브라우저에서 "오늘 날씨 어때?"를 입력했을 때 서버가 보내는 SSE 프레임):

```
event: status
data: {"stage": "planning", "message": "요청을 확인하고 대화 기록을 불러오는 중"}

event: status
data: {"stage": "writing", "message": "모델 서버에 답변을 요청하는 중"}

event: token
data: {"text": "오늘"}

event: token
data: {"text": " 날씨는"}

event: token
data: {"text": " 확인할 수"}

event: token
data: {"text": " 없다."}

event: done
data: {"finish_reason": "stop"}
```

화면에는 회색 기울임 글씨로 진행 상태가 잠깐 보이고, 그 아래로 답변이 한 글자씩(정확히는
모델이 나눠 보내는 조각 단위로) 이어 붙는다. "취소" 버튼을 누르면 스트림이 즉시 멈추고
서버 로그에 취소 사실이 남는다.

## 2. 핵심 개념

### 2.1 왜 SSE인가

브라우저와 서버가 실시간으로 데이터를 주고받는 방법은 여러 가지다.

| 방식 | 방향 | 프로토콜 | 이 프로젝트에 맞는가 |
| --- | --- | --- | --- |
| 폴링(polling) | 요청마다 왕복 | HTTP | 지연이 크고 서버 부하가 늘어 스트리밍 답변에 부적합 |
| WebSocket | 양방향 | 별도 업그레이드(`ws://`) | 양방향 채팅이 필요하면 적합하지만, 이 앱은 "요청 → 스트리밍 응답" 한 방향이면 충분 |
| SSE(`text/event-stream`) | 서버 → 클라이언트 단방향 | 일반 HTTP | 이 앱에 맞는다: 클라이언트는 메시지를 한 번 POST하고, 서버가 그 응답으로 계속 이벤트를 흘려보낸다 |

모델 답변은 서버에서 클라이언트로만 흐르고, 사용자의 다음 입력은 완전히 새 요청이다.
양방향 실시간 채널(WebSocket)이 주는 이점이 필요 없으므로, 일반 HTTP 위에서 동작해
프록시·방화벽 호환성이 좋고 클라이언트 구현이 단순한 SSE를 쓴다.

### 2.2 답변 토큰과 진행 이벤트를 구분하는 이유

모델이 만드는 답변 텍스트(`token` 이벤트)와 애플리케이션이 지금 무엇을 하고 있는지
알려주는 진행 표시(`status` 이벤트)는 성격이 다르다.

- `token`: 모델이 생성한 답변 조각. 화면에 그대로 이어 붙여 보여준다.
- `status`: 애플리케이션이 **실제로 실행한 사실**을 알려준다. 이 장에서는 "요청을 확인하는
  중"(`planning`)과 "모델 서버에 답변을 요청하는 중"(`writing`) 두 단계만 있다.

`PROJECT-SPEC.md`는 이것을 요구사항의 "thinking 과정 표시"에 대응시키면서 동시에
분명한 제약을 둔다: **`status` 이벤트는 모델이 내부적으로 어떻게 추론했는지(사고 과정
원문)를 재현하지 않는다.** 대부분의 모델 서버는애초에 그런 원문을 API로 내려주지
않을뿐더러, 설령 받을 수 있어도 그걸 사용자에게 "생각의 흐름"인 것처럼 보여주는 것은
애플리케이션이 실제로 확인하지 않은 내용을 확인한 것처럼 꾸미는 일이 된다. 이 장에서
`status` 이벤트가 담는 값은 오직 애플리케이션 코드가 실제로 거친 단계뿐이다 — "지금
모델 서버에 요청을 보냈다"는 사실은 애플리케이션이 직접 알고 있는 참인 정보이지만,
"모델이 사용자의 의도를 이렇게 분석하고 있다" 같은 문장은 애플리케이션이 알 수 없는
내용이므로 만들어내지 않는다.

`status.stage` 값은 `PROJECT-SPEC.md` 4절에 다섯 개(`planning`, `retrieving`,
`analyzing`, `verifying`, `writing`)로 고정돼 있다. 이 장은 검색(`retrieving`)이나
근거 분석(`analyzing`, `verifying`) 단계가 아직 없으므로 그 세 값은 쓰지 않는다 —
4단계(RAG)부터 실제로 검색을 하게 되면 그때 `retrieving`을 채운다.

### 2.3 이번 장에서 구현하는 이벤트, 나머지는 왜 미룬다

`PROJECT-SPEC.md` 4절의 SSE 이벤트 표는 13단계까지 쓸 이벤트를 전부 미리 정해 둔
것이다. 이 장은 그중 네 개만 구현한다.

| event | 이 장에서 구현 여부 | 실제로 필요해지는 단계 |
| --- | --- | --- |
| `token` | 구현 | — |
| `status` | 구현(`planning`, `writing`만 발생) | 4~7단계에서 나머지 stage 값 추가 |
| `error` | 구현 | — |
| `done` | 구현 | 8단계에서 `finish_reason` 값 종류가 늘어남 |
| `tool_call` / `tool_result` | 아직 없음 | 3단계 — 도구 호출이 생겨야 의미가 생긴다 |
| `todo` | 아직 없음 | 12~13단계 — 작업 계획이 생겨야 의미가 생긴다 |
| `sources` | 아직 없음 | 5단계 — 검색 근거가 생겨야 의미가 생긴다 |
| `approval_request` | 아직 없음 | 8단계 — 승인 흐름이 생겨야 의미가 생긴다 |

이벤트 이름과 `data` 안의 JSON 키는 이 장에서 한 번 정해지면 이후 장에서도 그대로다.
`docagent/events.py`가 이 포맷을 한곳에서 관리한다.

### 2.4 무엇을 누가 하는가

이 장에 등장하는 네 주체의 책임을 구분한다.

- **모델(model)**: 대화 기록을 받아 답변 텍스트 조각을 순서대로 생성한다. 스트리밍
  여부, 취소 가능 여부는 모델 서버 구현에 달려 있다.
- **애플리케이션(application, 이 장의 `docagent`)**: HTTP 요청을 받고, 세션별 대화
  기록을 관리하고, 모델이 만든 조각을 SSE 이벤트로 포장하고, 연결 종료를 감지해 모델
  호출을 취소하고, 요청 로그를 남긴다. 이 장의 코드 대부분이 여기에 속한다.
- **프레임워크(FastAPI/Starlette)**: HTTP 요청을 파이썬 코루틴으로 연결하고,
  `StreamingResponse`로 점진적 응답 전송을 처리하고, 클라이언트가 연결을 끊으면
  응답을 만드는 코루틴(태스크)을 취소하는 메커니즘을 제공한다.
- **웹 브라우저**: `fetch` + `ReadableStream`으로 SSE 프레임을 직접 파싱하고, 취소
  버튼을 누르면 `AbortController`로 연결을 끊는다.

### 2.5 EventSource 대신 fetch + ReadableStream을 쓰는 이유

브라우저 표준 `EventSource`는 SSE를 위해 만들어진 API지만 **GET 요청만 보낼 수 있고
요청 헤더나 바디를 자유롭게 설정할 수 없다.** 이 앱은 사용자 메시지(JSON 바디)를 POST로
보내야 하므로 `EventSource`를 쓸 수 없다. 대신 `fetch()`로 POST 요청을 보내고
`response.body`(`ReadableStream`)를 직접 읽어 `event: ...\ndata: ...\n\n` 형태의 SSE
프레임을 문자열로 파싱한다. 이 방식은 `EventSource`가 자동으로 해 주는 재연결 등을
직접 구현해야 하지만(이 장에서는 재연결을 만들지 않는다), POST 바디와 커스텀 헤더를
자유롭게 쓸 수 있다.

### 2.6 HTTP 미들웨어와 에이전트 미들웨어의 차이

"미들웨어"라는 같은 이름이 이 튜토리얼에서 서로 다른 두 층을 가리킨다. 혼동하지
않도록 이 장에서 먼저 구분해 둔다(에이전트 미들웨어 자체는 6단계에서 구현한다).

| | HTTP 미들웨어(이 장) | 에이전트 미들웨어(6단계) |
| --- | --- | --- |
| 개입 단위 | HTTP 요청 하나 | 모델 호출 한 번, 도구 호출 한 번 |
| 실행 시점 | 요청이 들어올 때 ~ 응답이 끝날 때 | 에이전트 루프 안에서 모델을 부르기 직전/직후, 도구를 부르기 직전/직후 |
| 누가 제공하는가 | ASGI 서버·프레임워크(Starlette) | 에이전트 프레임워크(6단계는 LangChain) |
| 무엇을 모르는가 | 요청 본문 안에 대화가 몇 턴 있는지, 모델이 도구를 부를지는 모른다 | 반대로 이 요청이 HTTP로 왔는지, 몇 번째 요청인지는 모른다 |
| 이 장에서 하는 일 | 요청 ID 부여, 처리 시간 측정, 요청 단위 로그 | (아직 없음) |
| 대표 용도 | 인증, CORS, 로깅, 레이트 리밋처럼 "어떤 요청이든" 적용할 관심사 | 프롬프트 주입 방어, 도구 호출 감사, 반복 호출 차단처럼 "모델이 무엇을 하려 하는지"에 개입하는 관심사 |

HTTP 미들웨어는 요청이 `/api/chat`이든 `/healthz`든 구분 없이 동일하게 적용된다.
반면 에이전트 미들웨어는 요청 하나 안에서 모델이 몇 번 호출되고 도구를 몇 번
부르는지(3단계부터 생기는 개념)에 개입한다 — 이 장에는 아직 도구 호출 루프 자체가
없으므로 에이전트 미들웨어도 없다.

### 2.7 요청 취소는 누가 감지하고 누가 끊는가

연결 종료 처리에는 세 단계가 있고, 각각 다른 주체가 담당한다.

```
브라우저가 연결을 끊는다 (탭 닫기, AbortController.abort())
        │
        ▼
Starlette/uvicorn이 ASGI "http.disconnect" 메시지를 받는다   ← 프레임워크
        │
        ├── (A) StreamingResponse가 응답 생성 태스크를 취소한다        ← 프레임워크가 자동으로
        │        (다음 await 지점에서 asyncio.CancelledError 발생)
        │
        └── (B) 우리 코드가 request.is_disconnected()로 직접 확인한다   ← 애플리케이션이 명시적으로
                 (반복문 안에서 다음 조각을 만들기 전에 먼저 검사)
        │
        ▼
app._chat_event_stream()의 async for piece in _stream_chat_completion_async(...)가
CancelledError를 받는다 (코루틴은 여기서 즉시 멈춘다)
        │
        ▼
그 취소는 achat_stream()이 await로 소비하던 async for chunk in stream 지점까지
그대로 전달되고, achat_stream()의 finally가 await stream.close()와
await client.close()를 실행해 모델 서버로 가는 HTTP 스트림을 실제로 닫는다
```

(A)와 (B)는 같은 목적(빠른 취소)을 서로 다른 방식으로 달성한다. (A)는 프레임워크가
"알아서" 해 주지만 다음 `await` 지점에서만 작동하므로 반응이 한 박자 늦을 수 있다.
(B)는 우리가 반복문 안에서 명시적으로 확인해 더 빨리 멈출 수 있지만 코드를 직접
써야 한다. 이 장은 두 가지를 모두 쓴다.

취소가 모델 서버까지 실제로 도달하려면 이 취소 신호가 코루틴 체인을 따라 끊기지
않고 전달돼야 한다. `docagent/llm.py`가 `chat_stream`(동기)뿐 아니라
`achat_stream`(비동기, `AsyncOpenAI` 기반)을 같이 두고 서버 코드가 후자를 쓰는
이유가 여기 있다 — 동기 제너레이터를 별도 스레드에서 돌리는 방식이었다면, 코루틴
쪽은 즉시 멈춰도 그 스레드 자체는 밖에서 끊을 수 없어 모델 서버 응답을 끝까지
받고서야 자연히 끝났을 것이다. `achat_stream`은 스레드를 거치지 않고 이벤트 루프
안에서 직접 실행되므로, 태스크가 취소되면 그 취소가 `finally`까지 그대로 전달된다.

## 3. 실습 준비

### 3.1 패키지와 버전

이 문서 작성 시점(2026-09-09) 기준으로 PyPI에서 확인한 안정 버전이다. 상위 호환을
단정하지 않으므로 `requirements.txt`는 범위로 고정한다.

```
fastapi>=0.141.0,<0.142
uvicorn[standard]>=0.52.0,<0.53
openai>=3.10.0,<4.0
python-dotenv>=1.2.0,<2.0
```

`openai` 파이썬 SDK는 1단계(`step01_raw_api`)와 같은 버전대를 그대로 쓴다 — 모델
서버 호출 방식을 장마다 바꾸지 않는다.

### 3.2 환경 변수

`PROJECT-SPEC.md` 2절에 이름이 고정돼 있다. 이 장에서 실제로 쓰는 값만 옮기면 다음과
같다(`code/step02_fastapi_chat/.env.example`).

```
OPENAI_BASE_URL=http://localhost:8000/v1
OPENAI_API_KEY=sk-local-example-key
CHAT_MODEL=your-chat-model-name

APP_BASE_URL=http://localhost:8080
REQUEST_TIMEOUT_SECONDS=60
```

`EMBEDDING_MODEL`, `MILVUS_*`, `MAX_AGENT_*`는 4단계 이후에 쓰이므로 이 장의
`.env.example`에는 넣지 않는다(필요해지는 장에서 추가한다).

### 3.3 폴더 구조

```
code/step02_fastapi_chat/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── __init__.py
│   ├── config.py      # 환경 변수 로딩
│   ├── llm.py          # 모델 서버 스트리밍 호출
│   ├── events.py        # SSE 이벤트 포맷
│   ├── middleware.py    # 요청 ID·처리 시간 미들웨어
│   └── app.py           # FastAPI 앱, /api/chat
└── static/
    ├── index.html
    └── app.js
```

### 3.4 실행 조건

OpenAI 호환 Chat Completions API(`POST /v1/chat/completions`, `stream=true` 지원)를
제공하는 서버가 하나 필요하다. 1단계에서 만든 `scripts/check_server_features.py`로
사용하는 서버가 스트리밍을 지원하는지 먼저 확인한다. 서버가 없다면 이 장의
`README.md` 5절에 있는 최소 스텁 서버로 SSE 배관 자체는 확인할 수 있다(실제 모델
품질은 확인되지 않는다).

## 4. 단계별 구현

### 4.1 최소 예제 — SSE 프레임 하나 보내기

먼저 모델 호출 없이 SSE 응답 자체가 어떻게 생겼는지 아주 작은 예제로 확인한다.

```python
# 최소 예제 — 프로젝트 파일이 아니다. 개념 확인용.
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import asyncio, json

app = FastAPI()

@app.get("/demo")
async def demo():
    async def gen():
        for i in range(3):
            yield f"event: token\ndata: {json.dumps({'text': str(i)})}\n\n"
            await asyncio.sleep(0.5)
        yield f"event: done\ndata: {json.dumps({'finish_reason': 'stop'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
```

`StreamingResponse`는 async generator(또는 일반 generator)를 받아 그 `yield` 값을
순서대로 응답 바디에 이어 붙인다. `media_type="text/event-stream"`으로 지정하면
브라우저와 프록시가 이 응답을 스트림으로 다뤄야 한다는 것을 안다. 이 최소 예제에는
아직 취소 처리도, 실제 모델 호출도 없다 — 아래부터 실제 프로젝트에 이 뼈대를
채워 넣는다.

### 4.2 환경 변수 로딩

`code/step02_fastapi_chat/docagent/config.py`
```python
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    openai_base_url: str
    openai_api_key: str
    chat_model: str
    app_base_url: str
    request_timeout_seconds: float


def load_settings() -> Settings:
    return Settings(
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-local-example-key"),
        chat_model=os.environ.get("CHAT_MODEL", "your-chat-model-name"),
        app_base_url=os.environ.get("APP_BASE_URL", "http://localhost:8080"),
        request_timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "60")),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
```

1단계와 같은 패턴이다. `load_settings()`/`get_settings()` 두 이름은 `PROJECT-SPEC.md` 9절이
모든 단계에 고정한 것이다. `load_settings()`는 호출할 때마다 환경 변수를 새로 읽고(테스트용),
`get_settings()`는 `lru_cache`로 프로세스 안에서 한 번만 읽는다(애플리케이션 코드용).

### 4.3 SSE 이벤트 포맷을 한곳에 모으기

`code/step02_fastapi_chat/docagent/events.py`
```python
from __future__ import annotations

import json
from typing import Any, Literal

EventName = Literal["token", "status", "error", "done"]
StatusStage = Literal["planning", "retrieving", "analyzing", "verifying", "writing"]


def sse_pack(event: EventName, data: dict[str, Any]) -> str:
    body = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {body}\n\n"


def token_event(text: str) -> str:
    return sse_pack("token", {"text": text})


def status_event(stage: StatusStage, message: str) -> str:
    return sse_pack("status", {"stage": stage, "message": message})


def error_event(code: str, message: str) -> str:
    return sse_pack("error", {"code": code, "message": message})


def done_event(finish_reason: str) -> str:
    return sse_pack("done", {"finish_reason": finish_reason})
```

이 모듈이 SSE 텍스트 포맷을 아는 유일한 곳이다. `app.py`는 `token_event(...)`처럼
이름 있는 함수만 부르므로, `PROJECT-SPEC.md`가 이벤트 스키마를 확장할 때(3, 5, 8,
12단계) 이 파일에 함수를 추가하기만 하면 된다.

### 4.4 모델 서버 스트리밍 호출과 취소 대응

`docagent/llm.py`는 `PROJECT-SPEC.md` 9절이 모든 단계에 고정한 공통 인터페이스
(`chat` / `chat_stream` / `chat_json` / `ChatResult` / `LLMError`)와, 2단계부터 추가되는
비동기 변형(`achat`, `achat_stream`)을 함께 둔다. 동기 함수(`chat`, `chat_stream`)는
CLI·스크립트·단위 테스트가 쓰기 편하도록 남겨 두고, **FastAPI 엔드포인트는 비동기 함수만
쓴다.** 왜 두 벌이 필요한지는 4.4절 끝에서 설명한다.

`code/step02_fastapi_chat/docagent/llm.py` (일부 — 비동기 스트리밍 부분)
```python
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from openai import (
    APIConnectionError, APIError, APIStatusError, AsyncOpenAI, APITimeoutError,
)

from .config import Settings, get_settings


class LLMError(RuntimeError):
    """모델 서버 호출 실패를 감싸는 공통 예외(PROJECT-SPEC.md 9절)."""


def _async_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        timeout=settings.request_timeout_seconds,
    )


async def achat_stream(
    messages: list[dict[str, Any]],
    *,
    settings: Settings | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> AsyncIterator[str]:
    """비동기 스트리밍 호출. ``chat_stream``과 같은 조각을 같은 순서로 준다.

    이 코루틴을 소비하는 태스크가 취소되면(클라이언트가 연결을 끊어
    ``asyncio.CancelledError``가 올라오면) ``finally``에서 스트림을 닫아
    모델 서버로 가는 HTTP 연결까지 함께 끊는다.
    """
    settings = settings or get_settings()
    client = _async_client(settings)
    try:
        stream = await client.chat.completions.create(
            model=settings.chat_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
    except APIConnectionError as exc:
        raise LLMError(f"모델 서버에 연결할 수 없다: {exc}") from exc
    except APITimeoutError as exc:
        raise LLMError(f"모델 서버 응답이 시간 안에 오지 않았다: {exc}") from exc
    except APIStatusError as exc:
        raise LLMError(
            f"모델 서버가 오류 상태를 반환했다 (status={exc.status_code}): {exc.message}"
        ) from exc
    except APIError as exc:
        raise LLMError(f"모델 서버 호출 중 오류가 발생했다: {exc}") from exc

    try:
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is not None and delta.content:
                yield delta.content
    except APIError as exc:
        raise LLMError(f"스트리밍 중 오류가 발생했다: {exc}") from exc
    finally:
        # 정상 종료·오류·취소 어느 경우에도 스트림과 연결을 닫는다.
        await stream.close()
        await client.close()
```

`app.py`는 이 함수를 그대로 흘려보내는 얇은 다리만 둔다.

`code/step02_fastapi_chat/docagent/app.py` (일부)
```python
async def _stream_chat_completion_async(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    """docagent.llm.achat_stream을 그대로 흘려보낸다."""
    settings = get_settings()
    async for piece in achat_stream(messages, settings=settings):
        yield piece
```

이 다리는 `queue.Queue`도 별도 스레드도 쓰지 않는다 — `achat_stream`이 이미
`AsyncIterator[str]`이므로 `async for`로 그대로 이어받기만 하면 된다. 이 얇음이
취소 처리의 핵심이다. `_chat_event_stream`이 `async for piece in
_stream_chat_completion_async(history)`를 도는 도중 태스크가 취소되면
(`StreamingResponse`가 클라이언트 종료를 감지해 취소하거나, 다음 조각을 기다리는
`await` 지점에서 `CancelledError`가 올라오면), 그 취소는 스레드 경계를 건너지 않고
`achat_stream` 안에서 `stream`을 소비하던 바로 그 `async for chunk in stream`까지
그대로 전달된다. 그래서 `achat_stream`의 `finally`가 실행돼
`await stream.close()`와 `await client.close()`로 모델 서버로 나가는 HTTP 스트림을
실제로 닫는다.

되돌아보면 이전에 이 다리를 동기 `chat_stream`을 별도 스레드에서 돌리고
`queue.Queue`로 코루틴에 전달하는 방식으로 만든 적이 있었다. 그 방식도 조각을
코루틴 쪽으로 전달하는 것 자체는 됐지만, 큐를 소비하는 코루틴이 취소돼도 그
취소는 스레드 경계를 넘지 못했다 — 스레드는 다른 스레드에서 강제로 끊을 수
없으므로, 클라이언트가 이미 떠난 뒤에도 백그라운드 스레드는 모델 서버 응답을
끝까지 받고서야 종료됐다. 이 장의 핵심 학습 내용이 "연결이 끊기면 모델 호출도
실제로 멈춘다"이므로 그 회귀는 되돌리고 비동기 다리로 다시 바꿨다.

**동기와 비동기를 둘 다 두는 이유**(`PROJECT-SPEC.md` 9절): CLI·스크립트·단위
테스트는 `await`를 신경 쓰지 않아도 되는 동기 함수(`chat`, `chat_stream`)가 읽기
쉽다. 반대로 서버 엔드포인트는 취소 때문에 비동기(`achat`, `achat_stream`)여야
한다 — 이벤트 루프가 열린 스트림을 직접 닫을 수 있어야 클라이언트 종료가 모델
서버 호출까지 실제로 전달되기 때문이다. 그래서 2단계 이후 서버 코드는
`achat`/`achat_stream`만 쓴다.

### 4.5 요청 ID·처리 시간 미들웨어 — 순수 ASGI 미들웨어로 만드는 이유

`code/step02_fastapi_chat/docagent/middleware.py`
```python
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("docagent.request")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        state: dict[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id

        method = scope.get("method", "")
        path = scope.get("path", "")
        start = time.perf_counter()
        status_code_holder: dict[str, int] = {}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code_holder["status"] = message["status"]
                headers = MutableHeaders(scope=message)
                headers.append("x-request-id", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            status = status_code_holder.get("status", 0)
            logger.info(
                "request_id=%s method=%s path=%s status=%s elapsed_ms=%.1f",
                request_id, method, path, status, elapsed_ms,
            )
```

FastAPI/Starlette는 미들웨어를 만드는 두 가지 방법을 제공한다: `@app.middleware("http")`
데코레이터(또는 `BaseHTTPMiddleware` 상속)와, ASGI 세 인자(`scope, receive, send`)를
직접 다루는 순수 ASGI 미들웨어다. 대부분의 튜토리얼은 더 짧은 `BaseHTTPMiddleware`를
쓰지만, 이 프로젝트는 **순수 ASGI 미들웨어를 쓴다.** 이유는 이 장의 핵심 기능인
연결 종료 감지(`request.is_disconnected()`)가 `BaseHTTPMiddleware`로 감싼 요청에서는
항상 `False`를 반환하는, Starlette에 실제로 보고돼 있고 이 문서 작성 시점까지
해결되지 않은 문제이기 때문이다(참고 문서의 Starlette 디스커션 #2094 참고). `/api/chat`이
바로 그 감지 기능을 쓰는 엔드포인트이므로, 그 위에 이 버그가 있는 방식의 미들웨어를
얹을 수 없다. 이 문제를 실제로 재현하는 과정은 6절 "실패 상황 실습"에서 다룬다.

`scope.setdefault("state", {})`에 넣은 값은 Starlette의 `Request.state`가 그대로
노출하는 딕셔너리이므로, 라우트 핸들러에서 `request.state.request_id`로 같은 값을
읽을 수 있다.

### 4.6 FastAPI 앱 — 세션, 대화 엔드포인트, SSE 스트림

`code/step02_fastapi_chat/docagent/app.py`
```python
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import get_settings
from .events import done_event, error_event, status_event, token_event
from .llm import LLMError, achat_stream
from .middleware import RequestContextMiddleware

logger = logging.getLogger("docagent.app")

_docagent_logger = logging.getLogger("docagent")
_docagent_logger.setLevel(logging.INFO)
if not _docagent_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _docagent_logger.addHandler(_handler)

app = FastAPI(title="docagent — step02 fastapi-chat")
app.add_middleware(RequestContextMiddleware)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

SESSIONS: dict[str, list[dict[str, str]]] = {}

SYSTEM_PROMPT = {
    "role": "system",
    "content": "당신은 사용자의 질문에 한국어로 간결하게 답하는 도우미다.",
}


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None


def _get_or_create_session(session_id: str | None) -> tuple[str, list[dict[str, str]]]:
    if session_id and session_id in SESSIONS:
        return session_id, SESSIONS[session_id]
    new_id = session_id or str(uuid.uuid4())
    SESSIONS[new_id] = [SYSTEM_PROMPT]
    return new_id, SESSIONS[new_id]


async def _stream_chat_completion_async(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    """docagent.llm.achat_stream을 그대로 흘려보낸다."""
    settings = get_settings()
    async for piece in achat_stream(messages, settings=settings):
        yield piece


async def _chat_event_stream(request: Request, session_id: str, history: list[dict[str, str]]):
    request_id = request.state.request_id
    yield status_event("planning", "요청을 확인하고 대화 기록을 불러오는 중")

    collected: list[str] = []
    finish_reason: Literal["stop", "cancelled"] = "stop"

    try:
        yield status_event("writing", "모델 서버에 답변을 요청하는 중")
        async for piece in _stream_chat_completion_async(history):
            if await request.is_disconnected():
                logger.info("request_id=%s client disconnected mid-stream", request_id)
                finish_reason = "cancelled"
                break
            collected.append(piece)
            yield token_event(piece)
    except asyncio.CancelledError:
        logger.info("request_id=%s stream task cancelled", request_id)
        raise
    except LLMError as exc:
        # LLMError는 PROJECT-SPEC.md 9절 공통 인터페이스라 code 속성을 갖지 않는다
        # (1~5단계 모든 llm.py가 같은 예외 타입을 쓴다). error 이벤트의 code에는
        # 고정 문자열 "llm_error"를 넣고, 원인은 message에 그대로 담는다.
        logger.warning("request_id=%s llm_error message=%s", request_id, exc)
        yield error_event("llm_error", str(exc))
        yield done_event("stop")
        return

    if finish_reason == "stop" and collected:
        history.append({"role": "assistant", "content": "".join(collected)})

    yield done_event(finish_reason)


@app.post("/api/chat")
async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
    session_id, history = _get_or_create_session(payload.session_id)
    history.append({"role": "user", "content": payload.message})

    return StreamingResponse(
        _chat_event_stream(request, session_id, history),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
        },
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
```

몇 가지 설계 결정을 짚는다.

- **세션은 메모리 딕셔너리다.** `SESSIONS: dict[str, list[dict]]`는 프로세스가 살아있는
  동안만 유지된다. `uvicorn ... --workers 2` 이상으로 띄우거나 여러 서버 인스턴스로
  배포하면 프로세스마다 이 딕셔너리가 따로 생겨, 같은 `session_id`로 요청해도 어느
  프로세스가 받느냐에 따라 다른 대화 기록을 보게 된다. 여러 프로세스가 세션을
  공유하려면 Redis 같은 외부 저장소가 필요한데, 이는 이 장의 범위 밖이다 — 지금은
  "단일 프로세스에서만 유효하다"는 한계를 그대로 인정하고 넘어간다.
- **취소 감지는 두 겹이다.** `request.is_disconnected()`를 반복문 안에서 직접
  확인하는 것(빠른 반응)과, `except asyncio.CancelledError`로 프레임워크가 태스크를
  직접 취소하는 경우를 받는 것(4.4에서 설명한 `finally`가 실제로 실행되게 하는 경로)을
  같이 둔다. `CancelledError`를 잡고 나서 반드시 `raise`로 다시 던진다 — 취소를
  삼키면 이 태스크가 취소됐다는 사실이 상위(ASGI 서버)에 전달되지 않아 정리 과정이
  꼬일 수 있다.
- **정적 파일 마운트는 맨 마지막에 한다.** Starlette는 등록된 순서대로 경로를
  매칭하므로, `/api/chat`처럼 구체적인 경로를 먼저 등록해야 `StaticFiles`의 `"/"`
  마운트가 그 경로를 가로채지 않는다.

### 4.7 웹 화면 — fetch로 SSE 프레임 직접 파싱하기

`code/step02_fastapi_chat/static/index.html`은 입력창과 로그 영역, 취소 버튼만 있는
단순한 페이지다(전체 내용은 저장소 파일 참고). 핵심은 `app.js`의 스트림 파싱 부분이다.

`code/step02_fastapi_chat/static/app.js` (발췌)
```javascript
async function* parseSSE(reader) {
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      yield parseFrame(frame);
    }
  }
}

async function sendMessage(message) {
  currentAbortController = new AbortController();
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal: currentAbortController.signal,
  });
  const reader = response.body.getReader();
  for await (const { event, data } of parseSSE(reader)) {
    handleEvent(event, data);
  }
}
```

`fetch`는 `response.body`로 `ReadableStream`을 준다. SSE 프레임은 빈 줄(`\n\n`)로
구분되므로, 도착한 바이트를 문자열 버퍼에 계속 이어붙이면서 완성된 프레임만 잘라
꺼낸다 — 서버가 보낸 청크가 프레임 경계와 정확히 맞아떨어진다는 보장이 없기 때문에
버퍼링이 필요하다. "취소" 버튼은 `AbortController.abort()`를 부르는데, 이것이 `fetch`
요청을 즉시 실패시키고 브라우저가 실제 TCP 연결을 닫는다 — 그래서 서버의
`request.is_disconnected()`가 `True`가 된다.

## 5. 실행과 결과 확인

```bash
cd code/step02_fastapi_chat
pip install -r requirements.txt
cp .env.example .env   # 값을 실제 서버에 맞게 채운다
uvicorn docagent.app:app --reload --port 8080
```

1. `http://localhost:8080/`을 연다.
2. 메시지를 입력하고 "보내기"를 누른다. 다음이 순서대로 보이면 정상이다.
   - 회색 기울임 글씨로 `[planning] 요청을 확인하고 대화 기록을 불러오는 중`
   - 이어서 `[writing] 모델 서버에 답변을 요청하는 중`
   - 이어서 초록색 답변이 실시간으로 이어 붙는다
   - 마지막에 `종료 (finish_reason=stop)`
3. 브라우저 개발자 도구 Network 탭에서 `/api/chat` 요청의 응답 헤더에
   `X-Session-Id`와 `X-Request-Id`가 있는지 확인한다.
4. uvicorn을 실행한 터미널에 다음과 비슷한 로그 두 줄이 남는지 확인한다(값은 요청마다
   다르다).
   ```
   INFO:     127.0.0.1:53210 - "POST /api/chat HTTP/1.1" 200 OK
   2026-09-09 07:17:13,091 INFO docagent.request request_id=1cc34723-... method=POST path=/api/chat status=200 elapsed_ms=1841.1
   ```
5. 답변이 나오는 도중 "취소"를 누른다. 화면에 "요청을 취소했다"가 뜨고, 서버 터미널에
   `stream task cancelled` 로그가 남으며, `elapsed_ms`가 답변을 끝까지 받았을 때보다
   짧게 찍힌다 — 모델 호출이 끝까지 가지 않고 중간에 멈췄다는 뜻이다.

모델이 실제로 만드는 문장은 서버와 프롬프트에 따라 달라지므로, 위 확인은 "이 구조와
순서로 동작하는지"를 보는 것이지 특정 문구가 나오는지를 보는 것이 아니다.

**모델 서버 없이 확인하기**: 실제 모델 서버 대신 `code/_tools/fake_openai_server.py --port
8151`(학습용 OpenAI 호환 스텁 서버)을 띄우고 `.env`의 `OPENAI_BASE_URL`을 그 주소로
바꿔 uvicorn으로 이 장의 앱을 띄운 뒤 위 1~5번을 모두 실제로 확인했다.

- 정상 스트리밍: `curl`로 `/api/chat`을 끝까지 받으면 `status(planning)` →
  `status(writing)` → `token` 3개 → `done(finish_reason=stop)` 순서로 SSE 프레임이
  온다. 요청 로그의 `elapsed_ms`는 552.5였다.
- 스트리밍 도중 연결을 끊으면(`curl --max-time 0.08`로 강제 조기 종료) 서버 로그에
  `INFO docagent.app request_id=... stream task cancelled`가 남고, 같은 요청의
  `elapsed_ms`는 81.0으로 정상 완료 때보다 훨씬 짧게 찍혔다 — 태스크가 취소되면서
  `achat_stream`의 `finally`가 실행돼 모델 서버로 가는 스트림이 실제로 조기 종료됐다는
  뜻이다.

스텁 서버는 고정 문자열을 몇 조각으로 잘라 보내는 것뿐이므로 모델 답변의 품질은
이 확인의 대상이 아니다. 이 서버가 확인해 주는 것과 못 해주는 것은
`code/_tools/README.md`에 정리돼 있다.

## 6. 실패 상황 실습

### 6.1 BaseHTTPMiddleware로 감싸면 취소 감지가 죽는다

`middleware.py`를 순수 ASGI 대신 아래처럼 흔히 보는 `BaseHTTPMiddleware` 버전으로
바꿔 재현할 수 있다.

```python
# 재현용 — 실제 프로젝트 코드가 아니다.
from starlette.middleware.base import BaseHTTPMiddleware

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        return response
```

이렇게 바꾸고 `app.add_middleware(RequestContextMiddleware)`로 등록한 뒤, "취소" 버튼을
눌러도 서버 로그에 `stream task cancelled`도, `client disconnected mid-stream`도
남지 않는다. `request.is_disconnected()`가 클라이언트가 실제로 연결을 끊었음에도
계속 `False`를 반환하기 때문이다.

**원인**: `BaseHTTPMiddleware`는 요청을 감싸면서 응답을 다시 읽는 방식으로 동작하는데,
이 과정에서 `is_disconnected()`가 쓰는 anyio `CancelScope`의 타이밍이 어긋나
연결 종료 메시지를 받기 전에 스코프가 먼저 취소돼 버린다. Starlette 저장소의
디스커션(#2094, 참고 문서)에 이 문제가 보고돼 있고 여러 버전에 걸쳐 재현된다.

**해결**: 이 프로젝트가 실제로 쓰는 방식대로, 연결 종료 감지가 필요한 엔드포인트를
감싸는 미들웨어는 `BaseHTTPMiddleware`가 아니라 순수 ASGI 미들웨어(`__call__(scope,
receive, send)`를 직접 구현)로 만든다. `docagent/middleware.py`가 그 예다.

### 6.2 INFO 로그가 조용히 사라진다

`docagent/middleware.py`의 `logger.info(...)`를 그대로 두고 `app.py`에서 로거 설정
부분(4.6의 `_docagent_logger` 관련 코드)을 지운 뒤 실행하면, 요청은 정상적으로
처리되는데도 `docagent.request` 로그 줄이 터미널에 전혀 보이지 않는다.

**원인**: uvicorn의 기본 로깅 설정(`uvicorn.config.LOGGING_CONFIG`)은 `"uvicorn"`,
`"uvicorn.access"` 로거에만 핸들러를 붙이고, 파이썬 로깅의 루트 로거에는 핸들러를
붙이지 않는다. `docagent.request` 로거는 `propagate=True`(기본값)라서 루트까지
올라가지만, 루트에 핸들러가 없으면 파이썬 로깅 모듈의 "handler of last resort"(부모를
못 찾은 로그 레코드를 받는 마지막 핸들러, 레벨 WARNING 이상만 stderr에 출력)로
떨어진다. INFO 레벨인 요청 로그는 그 문턱을 넘지 못해 사라지고, WARNING 레벨인
`llm_error` 로그는 살아남는다 — 그래서 오류 로그는 보이는데 정상 요청 로그만 안 보이는
헷갈리는 상황이 생긴다.

**해결**: `docagent` 로거에 레벨(INFO)과 핸들러를 명시적으로 붙인다(4.6 코드의
`_docagent_logger` 부분). `logging.basicConfig(...)`만 부르는 것으로는 부족할 수
있다 — uvicorn이 앱을 임포트한 뒤 `dictConfig`를 실행하는 시점과의 순서에 따라
효과가 달라지기 때문에, 이 프로젝트는 원하는 로거에 직접 핸들러를 붙이는 방식으로
그 순서 문제를 피한다.

### 6.3 모델 서버에 연결할 수 없을 때

`.env`의 `OPENAI_BASE_URL`을 존재하지 않는 포트(예: `http://localhost:19999/v1`)로 바꾸고
메시지를 보내면, `status`(`planning`, `writing`) 이벤트까지는 정상적으로 오고 이어서

```
event: error
data: {"code": "connection_error", "message": "모델 서버에 연결할 수 없다: ..."}

event: done
data: {"finish_reason": "stop"}
```

가 온다. `llm.py`가 `APIConnectionError`를 잡아 `LLMError`로 바꾸고, `app.py`가 이를
`error` 이벤트로 옮긴 뒤 `done` 이벤트로 스트림을 정상적으로 마무리한다 — 예외가
서버를 죽이거나 클라이언트를 무한정 기다리게 하지 않는다.

## 7. 응용 과제

**과제**: 세션이 일정 시간(예: 30분) 이상 아무 요청도 없으면 자동으로 대화 기록을
비우는 기능을 추가한다.

힌트:
- `SESSIONS`의 값에 `history` 리스트뿐 아니라 `last_used_at` 시각도 같이 저장하도록
  자료구조를 바꾼다(예: `{"history": [...], "last_used_at": time.time()}`).
- `_get_or_create_session`에서 세션을 찾을 때 `last_used_at`을 확인해 너무 오래됐으면
  새 대화로 취급한다.
- "세션이 만료돼 새 대화로 시작한다"는 사실을 `status` 이벤트로 알려줄지, 조용히
  새로 시작할지는 직접 정한다 — 어느 쪽을 골랐는지 이유와 함께 남긴다.

완료 기준: 세션 만료 시각을 짧게(예: 5초) 설정해 두고, 그 시간이 지난 뒤 같은
`session_id`로 다시 질문했을 때 이전 대화 내용이 반영되지 않는 것을 확인한다.

## 8. 핵심 정리와 확인 질문

**핵심 정리**

- SSE는 서버 → 클라이언트 단방향 스트리밍에 맞는 일반 HTTP 기반 프로토콜이다.
  이 앱처럼 클라이언트가 POST로 요청을 보내고 그 응답을 스트림으로 받는 구조에는
  `EventSource`가 아니라 `fetch` + `ReadableStream`을 직접 파싱해야 한다.
- `token`(답변)과 `status`(진행 상태)는 성격이 다른 이벤트다. `status`는 애플리케이션이
  실제로 거친 단계만 담으며, 모델의 내부 사고 과정을 재현하지 않는다.
- 연결 종료 감지는 `request.is_disconnected()`(명시적 확인)와 프레임워크의 자동 태스크
  취소(`asyncio.CancelledError`) 두 경로가 있고, 실제로 모델 서버 연결을 끊는 것은
  `stream.close()`를 부르는 애플리케이션 코드다.
- HTTP 미들웨어는 요청 하나 단위로, 에이전트 미들웨어(6단계)는 모델·도구 호출 한 번
  단위로 개입한다 — 같은 "미들웨어"라는 이름이 서로 다른 두 층을 가리킨다.
- 이 장의 미들웨어는 `BaseHTTPMiddleware`가 아니라 순수 ASGI 미들웨어다. 연결 종료
  감지가 필요한 엔드포인트 위에 `BaseHTTPMiddleware`를 얹으면 감지가 깨지는 문제가
  실제로 보고돼 있기 때문이다.
- 세션은 프로세스 메모리 안에만 있다. 여러 워커·여러 인스턴스로 배포하면 깨진다.

**확인 질문**

1. `token` 이벤트와 `status` 이벤트를 하나의 `message` 이벤트로 합치지 않고 왜 따로
   두는가? 화면 쪽에서 무엇이 달라지는가?
2. 브라우저가 "취소" 버튼을 누른 순간부터 `docagent/llm.py`의 `stream.close()`가
   실행되기까지, 어떤 컴포넌트를 순서대로 거치는가?
3. `BaseHTTPMiddleware`로 미들웨어를 만들면 왜 이 프로젝트에서 문제가 되는가? 이
   문제를 몰랐다면 어떤 증상으로 처음 발견하게 될까?
4. 이 장의 세션 저장소를 그대로 두고 서버를 두 개의 워커 프로세스로 띄우면 어떤
   상황에서 사용자가 이상함을 느끼게 되는가?

**이 장의 완성 코드**: `code/step02_fastapi_chat/`

**다음 장 연결**: 3단계(`step03_tool_calling`)는 이 대화 엔드포인트 안에 도구 호출과
"모델 호출 → 도구 실행 → 결과 전달"을 반복하는 에이전트 루프를 추가한다. `status`
이벤트에 새 stage는 아직 추가되지 않지만, SSE 이벤트 목록에 `tool_call`,
`tool_result`가 새로 생기고, 이 장에서 만든 `event_stream` 함수가 도구 호출 결과를
함께 내보내도록 확장된다.

## 참고 문서

- FastAPI 미들웨어(커스텀 미들웨어, `call_next`, `X-Process-Time` 예시): https://raw.githubusercontent.com/fastapi/fastapi/master/docs/en/docs/tutorial/middleware.md
- FastAPI `StreamingResponse` 사용법과 취소 관련 유의사항(`custom-response.md`): https://raw.githubusercontent.com/fastapi/fastapi/master/docs/en/docs/advanced/custom-response.md
- Starlette `Request.is_disconnected()` 문서(원본 `requests.md`): https://raw.githubusercontent.com/encode/starlette/master/docs/requests.md
- Starlette `BaseHTTPMiddleware`에서 `is_disconnected()`가 항상 False를 반환하는 문제: https://github.com/Kludex/starlette/discussions/2094
- Starlette `Request.is_disconnected()` 관련 추가 논의(사이드 이펙트, 클라이언트 종료 재확인): https://github.com/Kludex/starlette/discussions/2271
- Starlette 미들웨어(순수 ASGI 미들웨어 작성 방식): https://starlette.dev/middleware/ (WebFetch로는 접근 차단, WebSearch 요약으로 확인)
- FastAPI/Starlette 관련 커뮤니티 논의(`StreamingResponse` 취소, `await` 필요성 PR): https://github.com/fastapi/fastapi/pull/14681
- openai 파이썬 SDK `AsyncStream`/스트리밍 구현(`close()`, `async with` 지원): https://github.com/openai/openai-python/blob/main/src/openai/_streaming.py
- openai 파이썬 SDK 비동기 스트리밍 사용법(`AsyncOpenAI`, `stream=True`): 검색 결과 종합(공식 저장소 `openai/openai-python`) 기준, 개별 페이지 URL은 `[확인 필요: 공식 문서의 정확한 "비동기 스트리밍" 절 URL]`
- uvicorn 기본 로깅 설정(`disable_existing_loggers`, 로거별 핸들러 구성)은 패키지에 설치된 `uvicorn.config.LOGGING_CONFIG` 값을 이 문서 작성 시 직접 조회해 확인했다(uvicorn 0.52.4).

> 검증 상태: 이 장의 `docagent/` 코드는 `python3 -m py_compile`로 문법 검사를 통과했다.
> 추가로 실제 모델 서버 대신 로컬 스텁 HTTP 서버(`code/_tools/fake_openai_server.py`,
> OpenAI 호환 스트리밍 응답만 흉내낸 최소 구현)와 uvicorn으로 이 장의 앱을 띄워
> `/api/chat`의 정상 스트리밍(`status(planning)` → `status(writing)` → `token` ×3 →
> `done(stop)` 순서, `elapsed_ms=552.5`), 클라이언트 조기 종료 시 취소 로그
> (`stream task cancelled`, `elapsed_ms=81.0`으로 조기 종료) 출력, 모델 서버 연결 실패 시
> `error`+`done` 이벤트 전송, 요청 ID·소요 시간 미들웨어 로그 출력을 각각 한 번씩
> 확인했다. 이는 SSE 배관과
> 취소·로깅 경로가 코드대로 동작한다는 확인이며, **실제 LLM 모델 서버를 대상으로 한
> 검증은 하지 않았다** — 모델이 만드는 답변의 품질, 실제 모델 서버 구현체별 스트리밍
> 종료 시그널의 차이는 확인되지 않았다. 웹 UI(`static/`)는 브라우저에서 직접 열어
> 확인하지 않았다 — 코드 리뷰만으로 fetch/ReadableStream 파싱 로직을 작성했다.
