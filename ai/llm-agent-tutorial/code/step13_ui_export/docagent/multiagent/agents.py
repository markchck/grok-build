"""조사(investigator) · 분석(analyst) · 검증(verifier) 노드.

세 노드 모두 같은 모양을 따른다: (1) 자신의 ``*_context``에만 쓰인 이전
메시지를 읽는다, (2) 모델에게 구조화된 판단을 요청한다, (3) 모델 응답이
기대한 형태가 아니면 규칙 기반으로 대체한다(7단계 2-4절과 같은 패턴 —
"판단 주체"를 항상 기록한다), (4) 공유 상태(블랙보드)에 결과를 쓰고, 자신의
``*_context``에도 이번 라운드 메시지를 남긴다.

이 장에서 "역할 분리"가 실제로 의미하는 것: 세 노드는 서로 다른 시스템
프롬프트, 서로 다른 컨텍스트, 서로 다른 실패 처리 방식을 갖는다 — 단일
에이전트라면 이 셋을 하나의 시스템 프롬프트 안에 다 우겨넣거나, 하나의 대화
이력에 세 가지 역할의 메시지가 뒤섞였을 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from docagent import llm
from docagent import salesdata
from docagent.config import Settings
from docagent.multiagent.budget import add_usage
from docagent.multiagent.state import CollabState


@dataclass(frozen=True)
class AgentDeps:
    settings: Settings


def _trace(stage: str, message: str) -> dict[str, str]:
    return {"stage": stage, "message": message}


def _call_json(deps: AgentDeps, messages: list[dict[str, Any]], *, schema_name: str, schema: dict) -> tuple[dict, int, bool]:
    """``chat_json``을 부르고 (파싱된 dict 또는 빈 dict, 토큰 수, 추정 여부)를 돌려준다.

    모델 호출 자체가 실패하면(``LLMError``) 빈 dict를 돌려줘서 호출부가
    휴리스틱으로 넘어가게 한다. 이 장은 3·7·8단계와 같은 원칙을 따른다:
    모델 실패가 곧 애플리케이션 실패는 아니다.
    """

    try:
        # chat_json은 usage를 안 돌려주므로 raw 응답이 필요해 chat()을 직접 쓴다.
        result = llm.chat(
            messages,
            settings=deps.settings,
            temperature=0.0,
            response_format={"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
        )
    except llm.LLMError:
        return {}, 0, False

    tokens, estimated = llm.usage_tokens(result)
    import json as _json

    try:
        parsed = _json.loads(result.text or "{}")
    except ValueError:
        parsed = {}
    return parsed, tokens, estimated


# ---------------------------------------------------------------------------
# investigator: 요청에서 조사 대상 제품을 뽑고 원본 판매 행을 찾는다.
# ---------------------------------------------------------------------------

_INVESTIGATOR_SCHEMA = {
    "type": "object",
    "properties": {"product": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["product"],
}


def make_investigator_node(deps: AgentDeps) -> Callable[[CollabState], dict]:
    def investigator(state: CollabState) -> dict:
        request = state["request"]
        known = salesdata.known_products()

        parsed, tokens, estimated = _call_json(
            deps,
            [
                {
                    "role": "system",
                    "content": f"사용자 요청에서 조사할 제품명을 하나 고른다. 가능한 값: {known}. JSON으로만 답한다.",
                },
                {"role": "user", "content": request},
            ],
            schema_name="pick_product",
            schema=_INVESTIGATOR_SCHEMA,
        )
        product = parsed.get("product")
        source = "model"
        if not isinstance(product, str) or product not in known:
            # 휴리스틱: 요청 문자열에 제품명이 그대로 들어 있는지 본다.
            product = next((p for p in known if p in request), known[0] if known else "")
            source = "heuristic"

        try:
            rows = salesdata.rows_for_product(product)
        except salesdata.DataError as exc:
            rows = []
            investigation = {"product": product, "row_count": 0, "error": str(exc)}
        else:
            investigation = {
                "product": product,
                "row_count": len(rows),
                "rows": rows,
                "source": source,
            }

        note = f"[investigator] 제품 '{product}'의 판매 행 {len(rows)}건을 찾았다 (판단 주체: {source})"
        budget_update = add_usage(state, tokens, estimated)
        return {
            "investigation": investigation,
            "shared_notes": [note],
            "investigator_context": [{"role": "assistant", "content": note}],
            "hops_used": state["hops_used"] + 1,
            "trace": [_trace("retrieving", note)],
            **budget_update,
        }

    return investigator


# ---------------------------------------------------------------------------
# analyst: investigator가 찾은 원본 행으로 분기별 추세를 계산·해석한다.
# ---------------------------------------------------------------------------

_ANALYST_SCHEMA = {
    "type": "object",
    "properties": {
        "trend": {"type": "string", "enum": ["increase", "decrease", "flat", "unknown"]},
        "explanation": {"type": "string"},
    },
    "required": ["trend", "explanation"],
}


def make_analyst_node(deps: AgentDeps) -> Callable[[CollabState], dict]:
    def analyst(state: CollabState) -> dict:
        investigation = state.get("investigation") or {}
        rows = investigation.get("rows", [])
        totals = salesdata.quarterly_totals(rows)

        q1 = totals.get("Q1", {"units": 0, "revenue": 0})
        q2 = totals.get("Q2", {"units": 0, "revenue": 0})
        # 규칙 기반 1차 판정(모델이 실패해도 이 값은 항상 있다).
        if q2["revenue"] > q1["revenue"]:
            heuristic_trend = "increase"
        elif q2["revenue"] < q1["revenue"]:
            heuristic_trend = "decrease"
        else:
            heuristic_trend = "flat"

        parsed, tokens, estimated = _call_json(
            deps,
            [
                {
                    "role": "system",
                    "content": "아래 분기별 매출 집계를 보고 추세를 판단하고 한 문장으로 설명한다. JSON으로만 답한다.",
                },
                {"role": "user", "content": f"제품: {investigation.get('product')}\nQ1: {q1}\nQ2: {q2}"},
            ],
            schema_name="judge_trend",
            schema=_ANALYST_SCHEMA,
        )
        trend = parsed.get("trend")
        explanation = parsed.get("explanation")
        source = "model"
        if trend not in ("increase", "decrease", "flat", "unknown") or not explanation:
            trend = heuristic_trend
            explanation = f"Q1 매출 {q1['revenue']}에서 Q2 매출 {q2['revenue']}로 변했다"
            source = "heuristic"

        analysis = {
            "product": investigation.get("product"),
            "q1": q1,
            "q2": q2,
            "trend": trend,
            "explanation": explanation,
            "source": source,
        }
        note = f"[analyst] 추세 판정: {trend} ({explanation}, 판단 주체: {source})"
        budget_update = add_usage(state, tokens, estimated)
        return {
            "analysis": analysis,
            "shared_notes": [note],
            "analyst_context": [{"role": "assistant", "content": note}],
            "hops_used": state["hops_used"] + 1,
            "trace": [_trace("analyzing", note)],
            **budget_update,
        }

    return analyst


# ---------------------------------------------------------------------------
# verifier: analyst의 판정을 원본 데이터로 독립적으로 재계산해 검증한다.
# ---------------------------------------------------------------------------

_VERIFIER_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["ok", "reason"],
}


def make_verifier_node(deps: AgentDeps) -> Callable[[CollabState], dict]:
    def verifier(state: CollabState) -> dict:
        investigation = state.get("investigation") or {}
        analysis = state.get("analysis") or {}
        rows = investigation.get("rows", [])
        recomputed = salesdata.quarterly_totals(rows)
        r_q1 = recomputed.get("Q1", {"units": 0, "revenue": 0})
        r_q2 = recomputed.get("Q2", {"units": 0, "revenue": 0})

        # 규칙 기반 1차 판정: analyst가 보고한 숫자와 재계산 결과가 정확히 같은가.
        numbers_match = r_q1 == analysis.get("q1") and r_q2 == analysis.get("q2")

        parsed, tokens, estimated = _call_json(
            deps,
            [
                {
                    "role": "system",
                    "content": "analyst의 판정과 재계산한 숫자가 서로 맞는지 검증한다. JSON으로만 답한다.",
                },
                {
                    "role": "user",
                    "content": (
                        f"analyst 보고: trend={analysis.get('trend')}, Q1={analysis.get('q1')}, Q2={analysis.get('q2')}\n"
                        f"재계산: Q1={r_q1}, Q2={r_q2}"
                    ),
                },
            ],
            schema_name="verify_analysis",
            schema=_VERIFIER_SCHEMA,
        )
        ok = parsed.get("ok")
        reason = parsed.get("reason")
        source = "model"
        if not isinstance(ok, bool) or not reason:
            ok = numbers_match
            reason = "재계산한 분기별 합계가 analyst 보고와 일치한다" if numbers_match else "재계산한 합계가 analyst 보고와 다르다"
            source = "heuristic"

        verification = {
            "ok": ok,
            "reason": reason,
            "recomputed_q1": r_q1,
            "recomputed_q2": r_q2,
            "source": source,
        }
        note = f"[verifier] 검증 결과: {'통과' if ok else '불일치'} ({reason}, 판단 주체: {source})"
        budget_update = add_usage(state, tokens, estimated)
        return {
            "verification": verification,
            "shared_notes": [note],
            "verifier_context": [{"role": "assistant", "content": note}],
            "hops_used": state["hops_used"] + 1,
            "trace": [_trace("verifying", note)],
            **budget_update,
        }

    return verifier


# ---------------------------------------------------------------------------
# verifier의 A2A 버전: 같은 일을 하지만 별도 프로세스에 위임한다.
# ---------------------------------------------------------------------------


def make_verifier_node_a2a(deps: AgentDeps) -> Callable[[CollabState], dict]:
    """내부 verifier 노드와 판정 로직은 같지만, 계산을 이 프로세스가 하지 않고
    ``a2a_min.server``로 실행 중인 별도 에이전트에게 맡긴다.

    이 노드가 하는 일은 "결과를 어떻게 해석하는가"가 아니라 "그 결과를 어디서
    누가 만들어내는가"에서 내부 verifier와 갈린다 — docs/11-multi-agent.md
    2-7절의 비교표가 가리키는 차이를 코드로 만든 것이다.
    """

    from docagent.a2a_min import client as a2a_client

    def verifier_a2a(state: CollabState) -> dict:
        investigation = state.get("investigation") or {}
        analysis = state.get("analysis") or {}
        rows = investigation.get("rows", [])

        try:
            result = a2a_client.submit_and_wait(
                deps.settings.a2a_verifier_url,
                skill_id="verify_quarterly_analysis",
                task_input={"analysis": analysis, "rows": rows},
            )
            source = "a2a_agent"
        except a2a_client.A2AClientError as exc:
            # 외부 에이전트에 닿지 못해도 협업 전체를 실패시키지 않는다 — 검증을
            # 생략했다는 사실을 기록하고 계속 진행한다(3·7·8단계와 같은 원칙:
            # 외부 호출 실패가 곧 애플리케이션 실패는 아니다).
            result = {"ok": False, "reason": f"외부 검증 에이전트 호출 실패: {exc}"}
            source = "a2a_agent_unreachable"

        verification = {**result, "source": source}
        note = f"[verifier(A2A)] 검증 결과: {'통과' if result.get('ok') else '불일치'} ({result.get('reason')}, 판단 주체: {source})"
        return {
            "verification": verification,
            "shared_notes": [note],
            "hops_used": state["hops_used"] + 1,
            "trace": [_trace("verifying", note)],
        }

    return verifier_a2a


def make_finish_node(deps: AgentDeps) -> Callable[[CollabState], dict]:
    """세 에이전트의 결과를 하나의 답변으로 합친다. 이 노드가 "결과 통합"을 맡는다."""

    def finish(state: CollabState) -> dict:
        analysis = state.get("analysis") or {}
        verification = state.get("verification") or {}
        if not analysis:
            text = "조사 결과가 없어 답변을 만들 수 없다."
        elif verification and not verification.get("ok", True):
            text = (
                f"{analysis.get('product')}의 매출은 {analysis.get('trend')} 추세로 보이지만, "
                f"검증 단계에서 불일치가 발견됐다: {verification.get('reason')}"
            )
        else:
            text = f"{analysis.get('product')}의 매출은 {analysis.get('trend')} 추세다. {analysis.get('explanation')}"

        return {
            "final_text": text,
            "finish_reason": "stop",
            "hops_used": state["hops_used"] + 1,
            "trace": [_trace("writing", "조사·분석·검증 결과를 하나의 답변으로 합쳤다")],
        }

    return finish
