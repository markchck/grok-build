"""터미널에서 질문하고 답변을 받는 프로그램 (1단계 실습 결과).

사용 예:
    python -m docagent.cli
    python -m docagent.cli --stream
    python -m docagent.cli --backend raw --stream

--backend sdk (기본값): openai SDK로 호출한다.
--backend raw          : httpx로 /v1/chat/completions를 직접 호출한다.
--stream               : 스트리밍 응답으로 받는다. 기본은 일반(비스트리밍) 응답이다.
"""

from __future__ import annotations

import argparse
import sys

from docagent.config import load_settings
from docagent.llm import (
    LLMCallError,
    raw_chat_completion,
    raw_chat_completion_stream,
    sdk_chat_completion,
    sdk_chat_completion_stream,
)

SYSTEM_PROMPT = (
    "너는 간결하게 답하는 한국어 어시스턴트다. 모르는 내용은 모른다고 말한다."
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=["sdk", "raw"],
        default="sdk",
        help="sdk: openai SDK 사용 (기본값), raw: httpx로 HTTP 직접 호출",
    )
    parser.add_argument(
        "--stream", action="store_true", help="스트리밍 응답으로 받는다."
    )
    parser.add_argument(
        "--temperature", type=float, default=0.2, help="출력의 무작위성 (기본 0.2)"
    )
    parser.add_argument(
        "--max-tokens", type=int, default=512, help="답변 최대 길이 (기본 512 토큰)"
    )
    return parser.parse_args(argv)


def run_once(
    settings, history: list[dict[str, str]], args: argparse.Namespace
) -> None:
    """질문 한 번을 처리하고 답변을 표준 출력에 찍는다."""
    if args.backend == "sdk":
        chat_fn, stream_fn = sdk_chat_completion, sdk_chat_completion_stream
    else:
        chat_fn, stream_fn = raw_chat_completion, raw_chat_completion_stream

    if args.stream:
        print("assistant> ", end="", flush=True)
        pieces: list[str] = []
        for piece in stream_fn(
            settings,
            history,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        ):
            print(piece, end="", flush=True)
            pieces.append(piece)
        print()
        history.append({"role": "assistant", "content": "".join(pieces)})
    else:
        result = chat_fn(
            settings,
            history,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
        print(f"assistant> {result.text}")
        if result.finish_reason not in (None, "stop"):
            print(f"  (finish_reason={result.finish_reason})")
        history.append({"role": "assistant", "content": result.text})


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    try:
        settings = load_settings()
    except RuntimeError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    print(f"모델 서버: {settings.openai_base_url}  모델: {settings.chat_model}")
    print(f"backend={args.backend} stream={args.stream}  (Ctrl+D 또는 'exit'로 종료)")

    history: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        try:
            question = input("user> ").strip()
        except EOFError:
            print()
            break

        if not question:
            continue
        if question in ("exit", "quit"):
            break

        history.append({"role": "user", "content": question})

        try:
            run_once(settings, history, args)
        except LLMCallError as exc:
            # 모델 호출이 최종적으로 실패해도 대화 자체는 계속할 수 있게 한다.
            # 실패한 질문을 history에 남겨 두면 다음 요청에서도 계속 실패하는
            # 경우가 있어(예: 너무 긴 컨텍스트) 되돌린다.
            history.pop()
            print(f"[오류] {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
