"""tracing.py의 traced_span이 트레이싱 백엔드 없이도 안전하게 동작하는지 확인한다.

이 테스트는 Phoenix나 다른 OpenTelemetry Collector를 띄우지 않는다 —
전역 TracerProvider를 등록하지 않은 상태(기본값)에서 opentelemetry-api가
NoOp 스팬을 돌려준다는 사실(장 본문 2-6절, 참고 문서의 opentelemetry-api
소스 확인)에 의존한다. 즉 이 테스트는 "관측 플랫폼이 없어도 계측 코드가
죽지 않는다"는, 이 장의 핵심 설계를 검증한다.
"""

from __future__ import annotations

import pytest

from docagent.tracing import SpanKind, set_llm_usage, set_output, traced_span


def test_traced_span_yields_a_span_object_without_provider():
    with traced_span("test.span", kind=SpanKind.CHAIN, input_value="hello") as span:
        assert span is not None
        set_output(span, "world")
        span.set_attribute("custom", 1)


def test_traced_span_reraises_exceptions():
    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with traced_span("test.failing_span") as span:
            span.set_attribute("about_to_fail", True)
            raise Boom("failure inside span")


def test_set_llm_usage_skips_none_values_without_error():
    with traced_span("test.llm_usage") as span:
        # prompt_tokens만 주어짐 -> completion/total은 기록하지 않지만 오류도 나지 않는다.
        set_llm_usage(span, model="stub-model", prompt_tokens=10)
