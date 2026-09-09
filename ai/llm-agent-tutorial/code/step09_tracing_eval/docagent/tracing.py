"""트레이싱 초기화 (9단계).

이 모듈이 다루는 책임은 딱 하나다: **애플리케이션 코드에서 스팬(span)을
만드는 표준화된 방법을 제공한다.** 그 스팬을 어디로 보낼지(로컬 Phoenix인지,
다른 OpenTelemetry Collector인지, 아무 데도 보내지 않을지)는 이 모듈이
결정하지 않는다 — ``init_tracing()``이 호출됐는지, 호출됐다면 어떤 엔드포인트를
줬는지에 달려 있다.

계측 라이브러리(instrumentation library)와 관측 플랫폼(observability platform)의
역할 분리:

- **계측 라이브러리** = OpenTelemetry API/SDK. "스팬을 만들고 속성을 붙이는"
  코드는 이 표준을 쓴다. 이 표준을 따르면 뒤에 어떤 플랫폼을 붙이든 코드를
  바꾸지 않아도 된다.
- **관측 플랫폼** = Phoenix, LangSmith, 또는 범용 OpenTelemetry Collector.
  스팬을 받아 저장하고 화면으로 보여주는 쪽이다. 애플리케이션 코드는 이
  플랫폼의 SDK를 직접 알 필요가 없다 — OTLP(OpenTelemetry Protocol)라는
  공통 전송 형식으로 보내기만 하면 된다.

``get_tracer()``가 돌려주는 트레이서는 ``init_tracing()``을 부르지 않아도
동작한다 — OpenTelemetry API는 전역 TracerProvider가 설정돼 있지 않으면
아무 것도 하지 않는 NoOp 스팬을 돌려준다(실제로 확인함, 장 본문 참고 문서
참고). 그래서 트레이싱을 껐을 때도 스팬 생성 코드를 if문으로 감쌀 필요가
없다 — 켜져 있으면 기록되고, 꺼져 있으면 조용히 아무 일도 하지 않는다.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from docagent.config import Settings

logger = logging.getLogger("docagent.tracing")

_TRACER_NAME = "docagent"

# OpenInference 표준 속성 이름. openinference-semantic-conventions 패키지의
# SpanAttributes/DocumentAttributes/OpenInferenceSpanKindValues에서 실제
# 문자열 값을 확인했다(장 본문 하단 "참고 문서"). 이 패키지가 설치되어
# 있지 않아도(트레이싱을 안 쓰는 실행) docagent 코드가 죽지 않도록, 문자열
# 상수를 이 모듈에 직접 복사해 둔다 — opentelemetry-api만 있으면 동작한다.
class Attr:
    OPENINFERENCE_SPAN_KIND = "openinference.span.kind"
    INPUT_VALUE = "input.value"
    OUTPUT_VALUE = "output.value"
    LLM_MODEL_NAME = "llm.model_name"
    LLM_TOKEN_COUNT_PROMPT = "llm.token_count.prompt"
    LLM_TOKEN_COUNT_COMPLETION = "llm.token_count.completion"
    LLM_TOKEN_COUNT_TOTAL = "llm.token_count.total"
    RETRIEVAL_DOCUMENTS = "retrieval.documents"


class SpanKind:
    CHAIN = "CHAIN"
    LLM = "LLM"
    RETRIEVER = "RETRIEVER"
    EMBEDDING = "EMBEDDING"


_initialized = False


def init_tracing(settings: Settings) -> bool:
    """설정에 따라 전역 TracerProvider를 등록한다.

    ``settings.tracing_enabled``가 거짓이면 아무 것도 하지 않는다(NoOp
    트레이서만 쓰인다). 참인데 ``arize-phoenix`` 패키지가 설치돼 있지
    않으면 경고를 로그로 남기고 계속 진행한다 — 트레이싱 도구가 없다고
    애플리케이션 자체가 죽으면 안 된다.

    반환값: 실제로 TracerProvider를 등록했으면 True.
    """
    global _initialized
    if not settings.tracing_enabled:
        logger.info("tracing disabled (DOCAGENT_TRACING_ENABLED=false)")
        return False
    if _initialized:
        return True

    try:
        from phoenix.otel import register
    except ImportError:
        logger.warning(
            "DOCAGENT_TRACING_ENABLED=true지만 'arize-phoenix' 패키지가 설치돼 있지 않다. "
            "트레이싱 없이 계속 진행한다. `pip install arize-phoenix`로 설치한다."
        )
        return False

    register(
        endpoint=settings.phoenix_collector_endpoint,
        project_name=settings.trace_project_name,
        # batch=False: 스팬을 만들 때마다 바로 내보낸다. 튜토리얼에서는
        # "방금 실행한 요청의 트레이스가 왜 아직 화면에 안 보이지?"라는
        # 혼란을 없애는 게 배치 처리량보다 중요하다. 운영에서는 True로
        # 바꿔 오버헤드를 줄인다.
        batch=False,
        verbose=False,
    )
    _initialized = True
    logger.info(
        "tracing enabled: endpoint=%s project=%s",
        settings.phoenix_collector_endpoint,
        settings.trace_project_name,
    )
    return True


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


@contextmanager
def traced_span(
    name: str,
    *,
    kind: str = SpanKind.CHAIN,
    input_value: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span]:
    """스팬 하나를 열고, 예외가 나면 스팬에 오류로 기록한 뒤 그대로 올린다.

    ``request_id``(2단계 HTTP 미들웨어가 붙이는 값)와 스팬은 별개의
    식별자다 — request_id는 HTTP 요청 하나를, 트레이스 ID는 그 요청 안에서
    일어난 모델 호출·검색·인용 검증까지 포함한 전체 실행 경로를 가리킨다.
    request_id를 스팬 속성(``metadata``)에 넣어 두면 로그와 트레이스를
    나중에 request_id로 서로 연결해 찾아볼 수 있다.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as span:
        span.set_attribute(Attr.OPENINFERENCE_SPAN_KIND, kind)
        if input_value is not None:
            span.set_attribute(Attr.INPUT_VALUE, input_value)
        if attributes:
            for key, value in attributes.items():
                if value is not None:
                    span.set_attribute(key, value)
        start = time.perf_counter()
        try:
            yield span
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise
        finally:
            span.set_attribute("elapsed_ms", (time.perf_counter() - start) * 1000)


def set_output(span: Span, value: str) -> None:
    span.set_attribute(Attr.OUTPUT_VALUE, value)


def set_llm_usage(
    span: Span,
    *,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    """모델 응답의 ``usage`` 필드를 스팬 속성으로 기록한다.

    OpenAI 호환 서버가 모두 ``usage``를 채워 준다고 가정하지 않는다 —
    값이 없으면(None) 그 속성은 아예 기록하지 않는다. app.py에서 이 함수를
    부를 때 실제로 서버 응답에 usage가 있는지 먼저 확인한다.
    """
    if model is not None:
        span.set_attribute(Attr.LLM_MODEL_NAME, model)
    if prompt_tokens is not None:
        span.set_attribute(Attr.LLM_TOKEN_COUNT_PROMPT, prompt_tokens)
    if completion_tokens is not None:
        span.set_attribute(Attr.LLM_TOKEN_COUNT_COMPLETION, completion_tokens)
    if total_tokens is not None:
        span.set_attribute(Attr.LLM_TOKEN_COUNT_TOTAL, total_tokens)


def set_retrieval_documents(span: Span, hits: list[dict]) -> None:
    """검색 결과를 OpenInference 표준 형태(document.id/content/score)로 기록한다."""
    for i, hit in enumerate(hits):
        prefix = f"{Attr.RETRIEVAL_DOCUMENTS}.{i}"
        span.set_attribute(f"{prefix}.document.id", hit.get("pk", ""))
        span.set_attribute(f"{prefix}.document.content", (hit.get("text", "") or "")[:500])
        span.set_attribute(f"{prefix}.document.score", float(hit.get("score", 0.0)))
