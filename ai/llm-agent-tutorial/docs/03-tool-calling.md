# 3단계. Tool Calling과 에이전트 반복 실행

## 1. 학습 목표와 완성 모습

2단계에서는 FastAPI 위에 SSE 스트리밍 대화 엔드포인트를 만들었다. 사용자가 메시지를
보내면 모델이 답을 만들고, 그 답을 토큰 단위로 흘려보내는 흐름이었다. 이 흐름에는
빠진 것이 하나 있다. 모델은 학습 시점에 본 지식으로만 답한다. `sales.csv`에 실제로
어떤 숫자가 들어 있는지는 모델이 알 수 없다.

이번 장에서는 모델이 필요할 때 "도구(tool)"를 호출해서 실제 데이터를 근거로 답하게
만든다. 완성하면 다음과 같이 동작한다.

```
사용자: 2분기 노트북 서울 매출 합계 알려줘

[status]  planning  1단계: 모델에게 다음 행동을 묻는다
[status]  analyzing 도구 1건 실행 준비
[tool_call]   id=call_1 name=sum_sales args={"product":"노트북","region":"서울","quarter":"Q2"}
[tool_result] id=call_1 ok=true summary={"matched_row_count":2,"total_units":...,"total_revenue":...}
[status]  planning  2단계: 모델에게 다음 행동을 묻는다
[token]   2분기 노트북 서울 매출 합계는 총 ...원이다. (판매 수량 ...개)
[done]    finish_reason=stop
```

내부적으로는 모델 호출 → 도구 실행 → 결과 전달을 필요한 만큼 반복하는 **에이전트
루프**가 이 흐름을 만든다. 이번 장은 이 루프를 라이브러리 없이 `while` 문으로 직접
구현한다. `sum_sales`(매출 합계 계산)와 `lookup_sales_rows`(판매 기록 원본 조회) 두
개의 도구를 갖춘 작은 에이전트가 실습 결과물이다.

이 장이 다루는 범위: Tool 정의와 입력 스키마, 도구 선택·인자 생성·실제 함수 실행의
역할 분담, 도구 결과를 모델에 전달하는 방법, 에이전트 루프, 입력 검증과 오류 처리,
최대 실행 횟수·시간 제한(무한루프 차단), 도구 호출 내역 표시.

## 2. 핵심 개념

### 2.1 Tool이란 무엇인가

Tool(도구)은 모델이 "이 이름의 함수를, 이런 인자로 부르고 싶다"고 요청할 수 있게
만든 명세다. OpenAI 호환 Chat Completions API에서 도구는 요청의 `tools` 배열에
아래와 같은 JSON으로 선언한다.[^openai-cookbook]

```json
{
  "type": "function",
  "function": {
    "name": "sum_sales",
    "description": "sales.csv에서 조건에 맞는 매출 합계를 계산한다.",
    "parameters": {
      "type": "object",
      "properties": {
        "product": { "type": "string", "description": "제품명" },
        "quarter": { "type": "string", "enum": ["Q1", "Q2"] }
      },
      "required": []
    }
  }
}
```

`parameters`는 JSON Schema다. 모델은 이 스키마를 읽고 "이 도구를 쓰려면 어떤 형태의
JSON을 만들어야 하는지"를 판단하는 데 쓴다. `name`과 `description`은 모델이 "이 상황에
이 도구가 맞는지"를 고르는 근거가 된다. description을 대충 쓰면 모델이 엉뚱한 도구를
고르거나, 있어야 할 도구를 안 쓴다.

### 2.2 역할 분담: 모델은 실행하지 않는다

이 장에서 가장 흔한 오해는 "모델이 함수를 실행한다"는 생각이다. **사실이 아니다.**
모델 서버는 `sales.csv`가 어디 있는지도, 파이썬 함수가 뭔지도 모른다. 모델이 실제로
하는 일은 다음 한 가지뿐이다.

> 주어진 도구 목록과 대화 내용을 보고, "이 이름의 도구를 이런 JSON 인자로 부르고
> 싶다"는 **텍스트**를 만든다.

이 텍스트가 응답의 `tool_calls` 필드로 온다. 모델은 이 시점에서 멈춘다. 그 다음은
전부 애플리케이션(이 경우 `docagent` 파이썬 프로세스) 책임이다.

| 단계 | 누가 하는가 | 실체 |
| --- | --- | --- |
| 어떤 도구를 쓸지 고른다 | 모델 | 텍스트 생성(다음 토큰 예측)의 결과 |
| 도구 인자를 만든다 | 모델 | 마찬가지로 텍스트 생성. 스키마를 "따르려고 노력"하지만 보장되지 않는다 |
| 인자가 스키마에 맞는지 검증한다 | 애플리케이션 | 이 장에서는 pydantic |
| 실제 함수를 실행한다(CSV를 읽고 합산한다) | 애플리케이션 | 순수 파이썬 코드 |
| 실행 결과를 모델이 읽을 문자열로 만든다 | 애플리케이션 | JSON 문자열 |
| 결과를 근거로 다음 행동(다른 도구 호출 또는 최종 답변)을 정한다 | 모델 | 다시 텍스트 생성 |
| 언제까지 반복할지, 반복을 멈출지 판단한다 | 애플리케이션 | 이 장의 `while` 루프 |

모델이 "실행 권한"을 가진 것처럼 보이는 이유는 애플리케이션이 모델의 요청을 거의
그대로 실행해주기 때문이다. 하지만 그 실행 여부, 실행 방법, 실행 결과의 진위를
결정하는 코드는 전부 이 파이썬 프로세스 안에 있다. 이 구분이 왜 중요한가는 6절의
"잘못된 인자" 사례와 7절의 반복 호출 차단에서 바로 드러난다 — 모델이 나쁜 인자를
만들어도, 같은 호출을 반복해도, 애플리케이션이 검증하고 한도를 두지 않으면 그대로
실행되거나 무한히 반복된다.

### 2.3 비슷한 개념과의 차이

- **함수 호출 vs. 구조화 출력(JSON mode)**: 구조화 출력은 "답변 자체를 정해진 JSON
  형태로 달라"는 요청이고, 도구 호출은 "답변하기 전에 어떤 도구를 어떤 인자로 부르고
  싶은지 알려달라"는 요청이다. 서로 다른 용도지만, 6절에서 보듯 도구 호출을 지원하지
  않는 서버에서는 구조화 출력으로 도구 호출을 흉내 낼 수 있다.
- **에이전트 루프 vs. 에이전트 프레임워크(LangChain, LangGraph 등)**: 이 장에서 만드는
  `while` 루프는 이후 6·7단계에서 배울 프레임워크가 내부적으로 하는 일과 본질적으로
  같다. 프레임워크는 이 루프에 재시도, 병렬 도구 실행, 상태 저장, 콜백 등을 얹은
  것이다. 원리를 직접 만들어봐야 프레임워크가 "무엇을 대신 해주는지"를 정확히 안다.
- **Tool과 MCP Tool의 차이**: 이 장의 도구는 같은 파이썬 프로세스 안의 함수다. 10단계의
  MCP(Model Context Protocol)는 도구를 별도 서버로 분리하고 표준 프로토콜로 호출하는
  방식이다. 인터페이스(이름, 입력 스키마)는 비슷하지만 실행 위치와 통신 방식이 다르다.

### 2.4 에이전트 루프의 흐름

```
messages = [system, user]

loop:
    응답 = 모델 호출(messages, tools=[...])       # 모델의 일: 다음 행동 결정
    messages.append(응답)

    if 응답에 tool_calls가 없음:
        최종 답변 = 응답.content
        종료

    for 각 tool_call in 응답.tool_calls:
        인자 검증(pydantic)                        # 애플리케이션의 일
        결과 = 실제 함수 실행(도구 이름, 인자)        # 애플리케이션의 일
        messages.append({"role": "tool", "tool_call_id": ..., "content": 결과})

    # 도구 결과를 반영해서 다시 모델을 부른다 (loop 처음으로)
```

이 루프는 "모델 호출 → 도구 실행 → 결과 전달"을 모델이 더 이상 도구를 요청하지 않을
때까지 반복한다. 안전장치가 없으면 모델이 같은 도구를 계속 잘못된 방식으로 부르거나,
서로 다른 도구를 무의미하게 오가며 끝나지 않을 수 있다. 그래서 4장(PROJECT-SPEC)에
정의된 실행 한도를 이 루프 안에 직접 넣는다(2.5절).

### 2.5 실행 한도(무한루프 차단)

`PROJECT-SPEC.md` 7장이 정의한 규약을 그대로 구현한다.

- 최대 단계 수 `MAX_AGENT_STEPS`를 넘으면 중단한다.
- 최대 실행 시간 `MAX_AGENT_SECONDS`를 넘으면 중단한다.
- 같은 `(도구 이름, 정규화한 인자)` 조합이 3회 반복되면 중단한다.
- 중단은 `error` 이벤트가 아니라 `done` 이벤트의 `finish_reason`으로 알린다. 이 장에서
  다루는 값은 `stop`(정상 종료), `max_steps`, `timeout`, `repeated_tool_call` 네
  가지다. 나머지 값(`no_progress`, `rejected_by_user`, `cancelled`)은 8·11단계에서
  사람 개입과 예산 관리를 다룰 때 채운다.

## 3. 실습 준비

### 3.1 패키지

이 문서 작성 시점 기준 아래 버전으로 확인했다(각 패키지의 실제 최신 버전은 설치
시점에 다를 수 있다).

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
httpx==0.27.2
pydantic==2.9.2
python-dotenv==1.0.1
pytest==8.3.3
```

```bash
cd code/step03_tool_calling
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3.2 환경 변수

`PROJECT-SPEC.md` 2장에 고정된 이름만 쓴다. 이 장에서 실제로 읽는 값은
`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `CHAT_MODEL`, `REQUEST_TIMEOUT_SECONDS`,
`MAX_AGENT_STEPS`, `MAX_AGENT_SECONDS`다.

```bash
cp .env.example .env
```

```
# OpenAI 호환 모델 서버
OPENAI_BASE_URL=http://localhost:8000/v1
OPENAI_API_KEY=sk-local-example-key
CHAT_MODEL=your-chat-model-name
EMBEDDING_MODEL=your-embedding-model-name

# 애플리케이션
APP_BASE_URL=http://localhost:8080
REQUEST_TIMEOUT_SECONDS=60
MAX_AGENT_STEPS=8
MAX_AGENT_SECONDS=120
```

실제 API 키는 절대 커밋하지 않는다. 위 `sk-local-example-key`는 예시일 뿐이다.

### 3.3 폴더 구조

```
code/step03_tool_calling/
├── README.md
├── requirements.txt
├── .env.example
├── docagent/
│   ├── __init__.py
│   ├── config.py     # 환경 변수 로딩
│   ├── llm.py         # 모델 서버 호출(도구 실행은 하지 않는다)
│   ├── events.py       # SSE 이벤트 스키마
│   ├── tools.py        # 도구 함수 + 입력 스키마 + 실행 레지스트리
│   ├── agent.py         # 에이전트 루프
│   └── app.py            # FastAPI 엔드포인트
├── static/                # 단순 웹 UI
├── data/sales.csv          # 샘플 판매 데이터
└── tests/                    # 단위 테스트
```

### 3.4 샘플 데이터

`data/sales.csv`는 `PROJECT-SPEC.md` 3장이 정한 컬럼(`date,product,region,units,revenue`)을
그대로 쓴다. 제품 4종(노트북, 모니터, 키보드, 마우스), 지역 2곳(서울, 부산), 2개
분기(2026년 1~3월 = Q1, 4~6월 = Q2) 데이터를 담았다.

```csv
date,product,region,units,revenue
2026-01-05,노트북,서울,373,223800000
2026-01-05,마우스,서울,41,1230000
2026-01-05,모니터,부산,194,48500000
...
```

### 3.5 실행 조건

이 장의 코드는 도구 호출을 지원하는 OpenAI 호환 서버를 필요로 한다. 서버 실행 없이도
`tests/`의 단위 테스트는 통과한다(가짜 모델 응답으로 대체하기 때문이다). 실제 서버로
확인하는 방법은 5절에 있다.

## 4. 단계별 구현

### 4.1 계산 함수 작성

먼저 모델과 무관하게, 순수 파이썬 함수로 "매출 합계 계산"을 만든다. 이 함수는
CSV를 읽어 조건에 맞는 행을 필터링하고 합산한다.

`code/step03_tool_calling/docagent/tools.py`
```python
def _load_sales_rows() -> list[dict[str, Any]]:
    if not SALES_CSV_PATH.exists():
        raise ToolError(f"판매 데이터 파일을 찾을 수 없다: {SALES_CSV_PATH}")

    rows: list[dict[str, Any]] = []
    with SALES_CSV_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            rows.append(
                {
                    "date": raw["date"],
                    "product": raw["product"],
                    "region": raw["region"],
                    "units": int(raw["units"]),
                    "revenue": int(raw["revenue"]),
                }
            )
    return rows


def sum_sales(args: SumSalesArgs) -> dict[str, Any]:
    rows = _load_sales_rows()
    matched = _apply_filters(rows, product=args.product, region=args.region, quarter=args.quarter)
    if not matched:
        raise ToolError(
            f"조건에 맞는 판매 기록이 없다. (product={args.product!r}, "
            f"region={args.region!r}, quarter={args.quarter!r})"
        )
    return {
        "matched_row_count": len(matched),
        "total_units": sum(r["units"] for r in matched),
        "total_revenue": sum(r["revenue"] for r in matched),
        "filters": {"product": args.product, "region": args.region, "quarter": args.quarter},
    }
```

`ToolError`는 "프로그램 버그"가 아니라 "이 조건으로는 계산할 수 없다"는 사실을
나타내는 예외다. 밑에서 이 예외를 잡아 모델에게 되돌려준다(4.6절).

이 시점에서 `sum_sales`는 그냥 파이썬 함수다. 모델과 아무 관계가 없다. 터미널에서
바로 확인할 수 있다.

```python
>>> from docagent.tools import sum_sales, SumSalesArgs
>>> sum_sales(SumSalesArgs(product="노트북", quarter="Q1"))
{'matched_row_count': 6, 'total_units': 2342, 'total_revenue': 1405200000, 'filters': {...}}
```

### 4.2 입력 스키마 정의

모델이 만드는 인자는 텍스트에서 파싱한 JSON이라 형태가 항상 맞는다는 보장이 없다.
pydantic 모델로 "허용하는 입력의 모양"을 명시한다.

`code/step03_tool_calling/docagent/tools.py`
```python
class SumSalesArgs(BaseModel):
    product: str | None = Field(
        default=None, description="집계할 제품명. 예: '노트북'. 생략하면 전체 제품."
    )
    region: str | None = Field(
        default=None, description="집계할 지역명. 예: '서울'. 생략하면 전체 지역."
    )
    quarter: Literal["Q1", "Q2"] | None = Field(
        default=None, description="집계할 분기. 'Q1' 또는 'Q2'. 생략하면 전체 기간."
    )
```

`Literal["Q1", "Q2"]`는 "Q1", "Q2" 외의 값이 오면 검증에 실패하게 만든다. 뒤에서
모델이 "Q9" 같은 값을 만드는 상황을 일부러 재현해서 이 검증이 실제로 동작하는지
확인한다(6절).

### 4.3 도구 등록: OpenAI 호환 tools 스펙 만들기

pydantic 모델과 별개로, 모델에게 넘길 JSON Schema를 `TOOL_SPECS`로 정의한다. 두
정의(파이썬 검증용 pydantic, 모델에게 보여줄 JSON Schema)가 필드 이름과 의미에서
어긋나지 않게 유지하는 것이 중요하다. 이 튜토리얼에서는 손으로 맞춰 쓰지만, 도구
수가 늘어나면 pydantic 모델의 `model_json_schema()`로 자동 생성하는 방법도 있다
(`parameters`에 필요한 `type: object`, `properties`, `required` 형태와 완전히
일치하지는 않으므로 그대로 쓰려면 후처리가 필요하다. `[확인 필요: 사용 중인 pydantic
버전의 model_json_schema() 출력이 OpenAI tools 스키마 요구사항과 100% 호환되는지는
버전마다 다를 수 있어 프로젝트별로 직접 확인해야 한다]`).

`code/step03_tool_calling/docagent/tools.py`
```python
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "sum_sales",
            "description": (
                "sales.csv 판매 데이터에서 조건에 맞는 매출·판매수량 합계를 계산한다. "
                "제품별/지역별/분기별 매출 합계를 물을 때 사용한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "집계할 제품명. 생략하면 전체."},
                    "region": {"type": "string", "description": "집계할 지역명. 생략하면 전체."},
                    "quarter": {
                        "type": "string",
                        "enum": ["Q1", "Q2"],
                        "description": "집계할 분기. 생략하면 전체 기간.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    # lookup_sales_rows는 7절 응용 과제에서 다룬다. 완성된 정의는 tools.py에 있다.
]
```

`description`은 장식이 아니다. 모델이 여러 도구 중 무엇을 고를지, 언제 도구를
아예 쓰지 않을지를 판단하는 유일한 근거가 이 텍스트다.

### 4.4 모델 호출

`llm.py`에 도구 목록을 함께 보내는 호출 함수를 추가한다. 이 함수는 모델의 응답을
그대로 반환할 뿐, 응답 안의 `tool_calls`를 해석하거나 실행하지 않는다.

`code/step03_tool_calling/docagent/llm.py`
```python
def chat_completion(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    temperature: float = 0.2,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": settings.chat_model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice

    resp = httpx.post(
        f"{settings.openai_base_url.rstrip('/')}/chat/completions",
        headers=_headers(),
        json=payload,
        timeout=settings.request_timeout_seconds,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]
```

반환값은 `{"role": "assistant", "content": ..., "tool_calls": [...] | None}` 형태다.
`tool_calls`가 있으면 모델이 도구를 요청한 것이고, 없으면 `content`가 최종 답변이다.
(실제 오류 처리를 포함한 전체 코드는 `docagent/llm.py`를 본다.)

### 4.5 도구 실행: 애플리케이션이 함수를 부른다

`run_tool()`이 "모델의 요청 텍스트"와 "실제 파이썬 함수 실행" 사이를 잇는다. 이름과
원시 인자(dict)를 받아서, 이 프로세스 안에서 직접 함수를 호출한다.

`code/step03_tool_calling/docagent/tools.py`
```python
def run_tool(name: str, raw_args: dict[str, Any]) -> ToolRunResult:
    entry = _TOOL_REGISTRY.get(name)
    if entry is None:
        return ToolRunResult(ok=False, error_kind="unknown_tool", content=json.dumps({...}))

    func, args_model = entry
    try:
        parsed_args = args_model.model_validate(raw_args)
    except ValidationError as exc:
        problems = [{"field": ".".join(str(p) for p in e["loc"]), "issue": e["msg"]} for e in exc.errors()]
        return ToolRunResult(ok=False, error_kind="invalid_args", content=json.dumps({
            "error": "invalid_args", "message": "...", "problems": problems,
        }))

    try:
        output = func(parsed_args)
    except ToolError as exc:
        return ToolRunResult(ok=False, error_kind="tool_error", content=json.dumps({...}))

    return ToolRunResult(ok=True, content=json.dumps(output, ensure_ascii=False))
```

여기서 `func(parsed_args)`가 이 튜토리얼에서 말하는 "실제 함수 실행"의 정확한 위치다.
모델은 이 줄이 실행되는 것을 모른다. 결과가 다시 모델에게 전달될 때만(4.6절) 무슨
일이 있었는지 "듣게" 된다.

### 4.6 결과 전달: role="tool" 메시지로 되돌리기

도구 실행 결과는 `role: "tool"`, `tool_call_id: <모델이 준 id>`, `content: <문자열>`
형태의 메시지로 대화에 추가한 뒤, 그 메시지를 포함해서 모델을 다시 호출한다.[^cookbook-toolmsg]

`code/step03_tool_calling/docagent/agent.py`
```python
result = run_tool(name, args_dict)
messages.append(
    {"role": "tool", "tool_call_id": call_id, "name": name, "content": result.content}
)
```

`tool_call_id`가 어떤 도구 호출에 대한 결과인지를 모델에게 알려주는 유일한 연결
고리다. 모델이 한 응답에서 도구를 여러 개 동시에 요청할 수 있으므로(`tool_calls`가
배열이다), 각 결과를 정확한 id에 맞춰 돌려줘야 한다.

### 4.7 에이전트 루프로 반복하기

이제 4.4~4.6을 하나의 `while` 루프로 묶는다. 프레임워크를 쓰지 않고 직접 쓴다.

`code/step03_tool_calling/docagent/agent.py`
```python
while True:
    step += 1
    if step > max_steps:
        yield AgentEvent("done", {"finish_reason": "max_steps"})
        return
    if time.monotonic() - start > max_seconds:
        yield AgentEvent("done", {"finish_reason": "timeout"})
        return

    assistant_message = chat_fn(messages, tools=TOOL_SPECS, tool_choice="auto")
    messages.append(assistant_message)
    tool_calls = assistant_message.get("tool_calls")

    if not tool_calls:
        yield AgentEvent("token", {"text": assistant_message.get("content") or ""})
        yield AgentEvent("done", {"finish_reason": "stop"})
        return

    for call in tool_calls:
        # ... 인자 파싱, 반복 감지, run_tool 호출, 결과를 messages에 추가 ...
        pass
    # 도구 결과를 반영해서 다음 반복에서 모델을 다시 부른다
```

`chat_fn`을 매개변수로 받는 이유는 테스트 때문이다. 실제 서버 대신 가짜 함수를
주입하면 모델 서버 없이도 루프의 반복·중단 로직을 확인할 수 있다(5.3절, 6절).

### 4.8 최종 답변까지: SSE로 내보내기

`app.py`는 `run_agent()`가 만드는 이벤트를 그대로 SSE로 흘려보낸다. 2단계의 `/chat`
엔드포인트와 라우팅 모양은 같고, 내부 구현만 에이전트 루프로 바뀌었다.

`code/step03_tool_calling/docagent/app.py`
```python
@app.post("/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    def event_stream():
        for agent_event in run_agent(req.message):
            yield _render_event(agent_event.kind, agent_event.data)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

`_render_event()`는 `docagent.events`의 함수만 써서 `event:`/`data:` 문자열을
만든다 — 이벤트 이름과 JSON 키가 `PROJECT-SPEC.md` 4장과 어긋나지 않게 한 군데서만
관리하기 위해서다.

## 5. 실행과 결과 확인

### 5.1 서버 실행

```bash
cd code/step03_tool_calling
uvicorn docagent.app:app --reload --port 8080
```

`http://localhost:8080/static/index.html`을 열면 입력창과 진행 로그가 보인다.

### 5.2 요청 예시

```bash
curl -N -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "2분기 노트북 서울 매출 합계 알려줘"}'
```

정상 동작이라면 아래 순서로 이벤트가 온다(모델 출력 문구는 실행마다 달라질 수 있으니
구조로 확인한다).

1. `event: status` (`stage: planning`) — 모델에게 다음 행동을 물었다는 표시.
2. `event: tool_call` — `name: "sum_sales"`, `args`에 모델이 만든 인자.
3. `event: tool_result` — `ok: true`와 계산 결과 요약.
4. `event: status` (`stage: planning`) — 도구 결과를 반영해 모델을 다시 불렀다는 표시.
5. `event: token` — 최종 답변 텍스트.
6. `event: done` — `finish_reason: "stop"`.

### 5.3 정상 여부 확인 방법

- `tool_call`의 `args`가 사용자 질문과 맞는 값인지 본다(제품명·지역명·분기가 질문과
  일치하는가).
- `tool_result`의 `ok`가 `true`이고 `summary` 안의 숫자가 `data/sales.csv`를 직접
  계산한 값과 같은지 대조한다.
- `done`의 `finish_reason`이 `stop`인지 확인한다. `max_steps`/`timeout`/
  `repeated_tool_call`이 나오면 6절에서 다루는 상황 중 하나에 걸린 것이다.
- 서버 없이 로직만 확인하려면 단위 테스트를 돌린다.

```bash
pytest tests/ -v
```

`tests/test_tools.py`는 도구 함수와 pydantic 검증을, `tests/test_agent.py`는 가짜
모델 응답으로 에이전트 루프의 반복·중단 조건을 확인한다.

## 6. 실패 상황 실습

### 6.1 모델이 스키마에 없는 값을 만드는 경우

모델이 `quarter`에 `"Q9"`처럼 스키마에 없는 값을 넣는 상황은 실제로 일어난다.
`run_tool()`이 이를 예외로 죽이지 않고 도구 결과 메시지로 되돌리는지 확인한다.

```python
from docagent.tools import run_tool
result = run_tool("sum_sales", {"quarter": "Q9"})
print(result.ok)          # False
print(result.error_kind)  # "invalid_args"
print(result.content)     # {"error": "invalid_args", "problems": [{"field": "quarter", "issue": "..."}]}
```

이 `content`가 그대로 `role: "tool"` 메시지로 모델에게 전달된다. 실제 서버와의
대화라면 모델이 이 오류 메시지를 읽고 다음 턴에 `"Q1"`이나 `"Q2"`로 고쳐서 다시
호출하는 것을 기대할 수 있다 — `tests/test_agent.py::test_invalid_args_are_fed_back_and_model_fixes_them`가
이 흐름(잘못된 인자 → 오류 결과 전달 → 모델이 고쳐서 재호출 → 성공)을 가짜 모델
응답으로 재현해서 확인한다.

### 6.2 존재하지 않는 조건으로 조회하는 경우

`sum_sales(product="존재하지않는제품")`처럼 조건에 맞는 행이 없으면 `ToolError`가
발생하고, `run_tool()`은 이를 `error_kind: "tool_error"`로 잡아 모델에게 "조건을
완화해달라"는 메시지를 돌려준다. 프로그램이 죽지 않고, 사용자에게도 스택 트레이스가
아니라 이해할 수 있는 문장이 전달된다.

### 6.3 같은 도구를 반복 호출하는 경우

모델이 같은 `(도구, 인자)` 조합을 계속 요청하는 상황(예: 도구 결과를 잘못 이해하고
같은 질문을 반복하는 경우)을 가짜 모델로 재현한다.

```python
# tests/test_agent.py 발췌
def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2):
    n = sum(1 for m in messages if m.get("role") == "assistant")
    return _tool_call_message(f"call_{n}", "sum_sales", '{"product": "노트북"}')

events = list(run_agent("같은 걸 계속 불러라", chat_fn=fake_chat, max_steps=10, max_seconds=30))
# tool_call은 4번 발생(3번은 실행되고, 4번째 시도에서 차단)
# done.finish_reason == "repeated_tool_call"
```

정규화 방식은 `json.dumps(args, sort_keys=True)`다. 키 순서만 다른 같은 값은 같은
호출로 취급하지만, 값 자체가 다르면(예: `product`가 다름) 별개의 호출로 센다.

### 6.4 최대 단계·시간 초과

`max_steps`나 `max_seconds`를 의도적으로 작게 주면 정상적으로 진행되던 대화도
중단된다.

```python
events = list(run_agent("계속 도구를 불러라", chat_fn=fake_chat, max_steps=2, max_seconds=30))
# done.finish_reason == "max_steps"
```

세 상황(잘못된 인자, 반복 호출, 한도 초과) 모두 `error` 이벤트가 아니라 `done`
이벤트의 `finish_reason`으로 알린다는 점을 확인한다. `error` 이벤트는 모델 서버
연결 실패처럼 애플리케이션이 계속 진행할 수 없는 상황을 위해 남겨둔다.

### 6.5 도구 호출을 지원하지 않는 서버

모든 OpenAI 호환 서버가 `tools`/`tool_choice`를 지원하지는 않는다. 지원 여부를
먼저 확인해야 한다.

**확인 방법**: 아주 단순한 도구 하나를 붙여 `tool_choice: "required"`(반드시 도구를
쓰게 강제)로 호출해본다.[^tool-choice-required] 서버가 도구 호출을 지원하면 응답의
`message.tool_calls`에 실제로 함수 호출이 담겨 온다. 지원하지 않으면 다음 중 하나가
일어난다: `tools` 필드를 무시하고 일반 텍스트로 답하거나, 400 계열 오류를 반환하거나,
`tool_calls` 없이 `content`만 채워서 응답한다.

**미지원 시 우회**: 도구 목록과 사용 규칙을 시스템 프롬프트에 텍스트로 적고, "도구를
쓰고 싶으면 다른 말 없이 `{\"tool\": \"이름\", \"args\": {...}}` 형태의 JSON만 출력하라"고
지시한 뒤, 응답을 애플리케이션이 직접 파싱해서 `assistant_message.tool_calls`와 같은
모양으로 바꿔 이후 로직(4.5~4.7절)에 그대로 태운다.

**이 우회의 한계**:
- 모델이 JSON 앞뒤에 설명 문장을 덧붙이면 파싱이 깨진다. 파싱 실패를 6.1절처럼
  오류로 되돌리는 로직이 반드시 필요하다.
- 병렬 도구 호출(한 응답에 여러 `tool_calls`)을 프롬프트만으로 안정적으로 받기
  어렵다.
- 서버가 실제로 구조화 출력(JSON mode)을 지원하는지도 별도로 확인해야 한다.
  `[확인 필요: 사용 중인 서버 구현이 JSON mode·도구 호출·둘 다 미지원인 경우의
  정확한 동작은 구현체마다 다르므로 실제 서버 문서를 확인해야 한다]`
- 이 방식은 임시 우회다. 도구 호출을 지원하는 서버로 옮기는 것이 근본적인 해결책이다.

## 7. 응용 과제

**과제: CSV 조회 도구 추가**

지금까지 만든 `sum_sales`는 "합계"만 알려준다. 사용자가 "1분기에 노트북이 서울에서
며칠에 얼마나 팔렸는지 하나씩 보여줘"처럼 개별 거래 내역을 물으면 답할 수 없다.

`lookup_sales_rows`라는 새 도구를 추가해서 조건에 맞는 원본 행을 반환하게 만들어라.

**요구사항**:
1. 입력 스키마: `product`, `region`, `quarter`(모두 생략 가능)에 더해 `limit`(1~50,
   기본 10)을 추가한다. `limit`으로 반환 행 수를 제한하는 이유를 생각해본다(모델에게
   너무 큰 결과를 한 번에 넘기면 무슨 문제가 생기는가?).
2. `sum_sales`와 마찬가지로 조건에 맞는 행이 없으면 `ToolError`를 던진다.
3. `TOOL_SPECS`에 두 번째 도구로 등록한다. `description`을 `sum_sales`와 명확히
   구분되게 쓴다(모델이 "합계가 필요한지 원본이 필요한지" 구분할 수 있어야 한다).
4. `run_tool()`의 레지스트리(`_TOOL_REGISTRY`)에 추가한다.
5. 가짜 모델 응답으로 `lookup_sales_rows` 호출이 `limit`을 지키는지, 잘못된 `limit`
   값(예: 999)이 `invalid_args`로 처리되는지 테스트를 추가한다.

**완료 기준**: "1분기 노트북 부산 판매 내역 5건만 보여줘" 같은 질문에 모델이
`lookup_sales_rows(product="노트북", region="부산", quarter="Q1", limit=5)`를
호출하고, 애플리케이션이 실제로 5건 이하의 행을 반환하는 것을 SSE 로그로 확인한다.

**힌트**: 이 과제의 참고 정답은 이미 `code/step03_tool_calling/docagent/tools.py`
안에 `lookup_sales_rows`로 구현돼 있고, `tests/test_tools.py`에 관련 테스트도 있다.
먼저 직접 만들어본 뒤에 비교해본다.

## 8. 핵심 정리와 확인 질문

### 핵심 정리

- Tool은 모델에게 "이런 이름과 입력 형태의 함수를 부를 수 있다"고 알려주는 명세일
  뿐이다. 모델은 도구를 실행하지 않는다. 어떤 도구를, 어떤 인자로 부를지 텍스트로
  요청할 뿐이며, 실제 실행은 애플리케이션이 한다.
- 도구 실행 결과는 `role: "tool"`, `tool_call_id`, `content`를 가진 메시지로 대화에
  추가한 뒤 모델을 다시 불러야 모델이 그 결과를 "본다".
- 에이전트 루프는 "모델 호출 → 도구 실행 → 결과 전달"을 모델이 더 이상 도구를
  요청하지 않을 때까지 반복하는 `while` 문이다. 프레임워크가 없어도 직접 만들 수
  있다.
- 모델이 만든 인자는 신뢰할 수 없다. pydantic으로 검증하고, 실패해도 예외로 죽이지
  않고 도구 결과 메시지로 모델에게 되돌려 스스로 고칠 기회를 준다.
- 무한루프는 실제로 일어날 수 있는 문제다. 최대 단계 수, 최대 시간, 반복 호출 감지
  세 가지를 애플리케이션이 직접 강제해야 한다. 중단은 오류가 아니라 `finish_reason`이
  붙은 정상적인 종료로 다룬다.

### 확인 질문

1. "모델이 함수를 실행한다"는 문장이 왜 부정확한지, 실제로 실행이 일어나는 코드
   위치(파일과 함수 이름)를 짚어서 설명할 수 있는가?
2. `tool_call_id`가 없다면 모델이 도구 결과 여러 개를 어떻게 잘못 해석할 수 있는가?
3. pydantic 검증 실패를 예외로 바로 던지지 않고 도구 결과 메시지로 돌려주는 이유는
   무엇인가? 예외로 죽였을 때와 무엇이 달라지는가?
4. 같은 `(도구, 인자)` 반복을 3회로 제한하는 대신 "완전히 같은 응답이 2번 연속
   나오면 중단"으로 바꾸면 어떤 경우를 놓치게 되는가?
5. 도구 호출을 지원하지 않는 서버에서 프롬프트 기반 JSON 출력으로 우회할 때, 이
   장의 `run_tool()` 이후 로직을 얼마나 그대로 재사용할 수 있는가?

### 이 장의 완성 코드

`code/step03_tool_calling/`

### 다음 장

4단계에서는 이번 장의 도구 실행 패턴과 별개로, 문서를 읽고 청크로 나누고 임베딩을
만들어 Milvus에 저장한 뒤 검색하는 RAG(Retrieval-Augmented Generation)를 직접
구현한다. 이후 5단계에서 이번 장에서 다루지 않은 "검색 도구"까지 에이전트에 연결하며
Tool과 RAG 검색이 어떻게 만나는지 다시 다룬다.

---

## 참고 문서

- OpenAI Cookbook, "How to call functions with chat models" — `tools`/`function`
  스펙 구조, 응답의 `tool_calls`(id, function.name, function.arguments) 형태,
  `role: "tool"` 결과 메시지(`tool_call_id`, `name`, `content`) 형태를 확인.
  https://github.com/openai/openai-cookbook/blob/main/examples/How_to_call_functions_with_chat_models.ipynb
- OpenAI Developer Community, "New API feature: forcing function calling via
  `tool_choice: \"required\"`" — `tool_choice`가 `"auto"`/`"none"`/`"required"`/특정
  함수 지정을 지원한다는 내용 확인.
  https://community.openai.com/t/new-api-feature-forcing-function-calling-via-tool-choice-required/731488

`[확인 필요]`: 이번 집필 시점에 `platform.openai.com`과 `developers.openai.com`의
공식 API 레퍼런스 페이지에는 네트워크 접근 제한으로 직접 접속하지 못했다. 위 두
출처(OpenAI 공식 GitHub 저장소의 쿡북, OpenAI 개발자 커뮤니티 공지)로 핵심 스키마를
교차 확인했지만, `parameters`의 JSON Schema 세부 제약(예: 중첩 객체·배열 지원 범위,
`strict` 모드 옵션 등)은 공식 API 레퍼런스 원문으로 별도 확인이 필요하다.

> 검증 상태: 이 장의 `docagent/` 코드는 전부 `python3 -m py_compile`을 통과했고,
> FastAPI 앱(`docagent.app:app`)은 실제로 임포트해서 라우트가 정상 등록되는 것을
> 확인했다. `tools.py`(도구 함수, pydantic 인자 검증)와 `agent.py`(에이전트 루프의
> 반복·중단 로직)는 `tests/`의 pytest 단위 테스트 16개로 실제로 실행해 통과시켰다 —
> 가짜 `chat_fn`으로 모델 응답을 대체해서 모델 서버 없이 확인했다. **실제 OpenAI
> 호환 모델 서버를 대상으로 한 end-to-end 실행 검증(5절의 curl 예시, 6.5절의 도구
> 호출 미지원 서버 우회)은 하지 않았다.** 이 장에 나온 모델 응답 예시 문구는 실제
> 실행 결과가 아니라 설명을 위해 만든 예시다.

[^openai-cookbook]: OpenAI Cookbook 저장소의 함수 호출 예제. `tools` 배열의
    `type`/`function.name`/`function.description`/`function.parameters` 구조를
    그대로 인용했다.
[^cookbook-toolmsg]: 같은 문서에서 `messages.append({"role": "tool", "tool_call_id":
    tool_call_id, "name": tool_function_name, "content": results})` 형태를 확인했다.
[^tool-choice-required]: `tool_choice: "required"`는 OpenAI 개발자 커뮤니티 공지로
    확인했다. 모든 OpenAI 호환 서버가 `"required"` 값까지 지원하는지는 서버 구현마다
    다르므로, 지원하지 않으면 `tool_choice`를 생략(기본값 `"auto"`)한 뒤 시스템
    프롬프트로 "도구가 필요하면 반드시 사용하라"고 유도하는 방법도 함께 고려한다.
