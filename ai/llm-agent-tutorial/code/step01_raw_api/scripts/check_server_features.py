"""연결된 OpenAI 호환 모델 서버가 어떤 기능을 지원하는지 실제로 호출해 확인한다.

PROJECT-SPEC.md 8절: 도구 호출·구조화 출력·스트리밍은 서버 구현마다 지원 범위가
다르므로, 코드에서 "지원할 것이다"라고 가정하지 않고 이 스크립트로 먼저 확인한다.

실행:
    python scripts/check_server_features.py

각 기능을 정확히 한 번씩 호출하고 성공/실패와 실패 이유를 출력한다.
이 스크립트는 판정을 위해 실제로 모델 서버에 요청을 보낸다(과금/부하가 있는
서버라면 유의한다).
"""

from __future__ import annotations

import sys
from pathlib import Path

# scripts/ 에서 실행해도 docagent 패키지를 찾을 수 있도록 상위 디렉터리를 추가한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import APIStatusError, OpenAI  # noqa: E402

from docagent.config import load_settings  # noqa: E402

CHECK_MARK = "OK"
CROSS_MARK = "FAIL"


def _client(settings) -> OpenAI:
    return OpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        timeout=settings.request_timeout_seconds,
        max_retries=0,  # 지원 여부를 판정하는 것이 목적이므로 재시도하지 않는다.
    )


def check_plain_completion(client: OpenAI, model: str) -> bool:
    """가장 기본적인 호출이 되는지 먼저 확인한다. 이후 검사들의 전제 조건이다."""
    print("[기본 응답]", end=" ")
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping이라고만 답해."}],
            max_tokens=16,
        )
        text = completion.choices[0].message.content
        print(f"{CHECK_MARK} - 응답: {text!r}")
        return True
    except APIStatusError as exc:
        print(f"{CROSS_MARK} - HTTP {exc.status_code}: {exc.message}")
        print(
            "  기본 호출이 실패하면 아래 검사들은 의미가 없다. "
            "OPENAI_BASE_URL / CHAT_MODEL / OPENAI_API_KEY를 먼저 확인한다."
        )
        return False
    except Exception as exc:  # noqa: BLE001 - 서버 연결 자체가 안 될 수도 있다.
        print(f"{CROSS_MARK} - 연결 실패: {exc}")
        return False


def check_streaming(client: OpenAI, model: str) -> None:
    print("[스트리밍]", end=" ")
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "1부터 3까지 세어줘."}],
            max_tokens=32,
            stream=True,
        )
        chunk_count = 0
        for chunk in stream:
            chunk_count += 1
            if chunk_count >= 2:
                # 여러 청크로 나뉘어 온다는 것만 확인하면 되므로 끝까지 받지 않는다.
                break
        if chunk_count >= 2:
            print(f"{CHECK_MARK} - 청크 {chunk_count}개 이상 수신")
        else:
            print(
                f"{CROSS_MARK} - 청크가 1개만 왔다(응답 전체를 한 번에 보내는 "
                "서버일 수 있다)."
            )
    except APIStatusError as exc:
        print(
            f"{CROSS_MARK} - HTTP {exc.status_code}: {exc.message}\n"
            "  전형적인 미지원 증상: stream=True를 무시하거나 400을 반환한다."
        )
    except Exception as exc:  # noqa: BLE001
        print(f"{CROSS_MARK} - {exc}")


def check_tool_calling(client: OpenAI, model: str) -> None:
    print("[도구 호출 (tool calling)]", end=" ")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "지정한 도시의 현재 날씨를 반환한다.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "도시 이름"}
                    },
                    "required": ["city"],
                },
            },
        }
    ]
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": "서울 날씨 알려줘. 반드시 도구를 써서 답해."}
            ],
            tools=tools,
            tool_choice="auto",
            max_tokens=64,
        )
        message = completion.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            call = tool_calls[0]
            print(
                f"{CHECK_MARK} - 모델이 도구 호출을 요청했다: "
                f"{call.function.name}({call.function.arguments})"
            )
        else:
            print(
                f"{CROSS_MARK} - tool_calls가 비어 있다. 서버가 tools 파라미터를 "
                f"무시했거나(400이 아니라 조용히 무시), 모델이 이번 응답에서는 "
                f"도구를 선택하지 않았을 수 있다. message.content={message.content!r}"
            )
    except APIStatusError as exc:
        print(
            f"{CROSS_MARK} - HTTP {exc.status_code}: {exc.message}\n"
            "  전형적인 미지원 증상: tools 파라미터를 모르는 필드로 취급해 400을 반환한다."
        )
    except Exception as exc:  # noqa: BLE001
        print(f"{CROSS_MARK} - {exc}")


def check_json_object(client: OpenAI, model: str) -> None:
    print("[구조화 출력: response_format=json_object]", end=" ")
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": '{"city": "서울", "summary": "..."} 형태의 JSON으로만 답해.',
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=64,
        )
        content = completion.choices[0].message.content or ""
        import json as _json

        _json.loads(content)
        print(f"{CHECK_MARK} - 유효한 JSON 수신: {content[:120]!r}")
    except APIStatusError as exc:
        print(
            f"{CROSS_MARK} - HTTP {exc.status_code}: {exc.message}\n"
            "  전형적인 미지원 증상: response_format 자체를 모르는 파라미터로 취급해 400을 반환한다."
        )
    except Exception as exc:  # noqa: BLE001
        print(f"{CROSS_MARK} - JSON 파싱 실패 또는 기타 오류: {exc}")


def check_json_schema(client: OpenAI, model: str) -> None:
    print("[구조화 출력: response_format=json_schema]", end=" ")
    schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "temperature_c": {"type": "number"},
        },
        "required": ["city", "temperature_c"],
        "additionalProperties": False,
    }
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "서울, 섭씨 21도라고 답해."}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "weather_report",
                    "schema": schema,
                    "strict": True,
                },
            },
            max_tokens=64,
        )
        content = completion.choices[0].message.content or ""
        import json as _json

        parsed = _json.loads(content)
        print(f"{CHECK_MARK} - 스키마에 맞는 JSON 수신: {parsed}")
    except APIStatusError as exc:
        print(
            f"{CROSS_MARK} - HTTP {exc.status_code}: {exc.message}\n"
            "  전형적인 미지원 증상: json_schema는 모르고 json_object만 지원하거나,\n"
            "  response_format 자체를 몰라 400을 반환한다."
        )
    except Exception as exc:  # noqa: BLE001
        print(f"{CROSS_MARK} - JSON 파싱 실패 또는 기타 오류: {exc}")


def main() -> int:
    settings = load_settings()
    client = _client(settings)
    print(f"서버: {settings.openai_base_url}  모델: {settings.chat_model}\n")

    if not check_plain_completion(client, settings.chat_model):
        return 1

    check_streaming(client, settings.chat_model)
    check_tool_calling(client, settings.chat_model)
    check_json_object(client, settings.chat_model)
    check_json_schema(client, settings.chat_model)

    print(
        "\n참고: 이 스크립트의 결과는 이 서버·이 모델 조합에서만 유효하다. "
        "모델을 바꾸거나 서버를 바꾸면 다시 실행해서 확인한다."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
