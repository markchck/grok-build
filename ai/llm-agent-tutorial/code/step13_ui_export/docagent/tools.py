"""로컬 함수 도구.

``sum_sales``/``lookup_sales_rows``/``archive_report``는 8단계와 같다(거의
그대로 가져왔다). 이 장이 새로 추가하는 두 도구:

- ``write_plan``: 모델이 실행 계획을 선언·수정하는 도구다. 12단계는
  Deep Agents의 ``write_todos``(langchain 내장)를 썼지만, 이 장은 도구
  호출 에이전트 루프(3·8단계 방식)를 그대로 쓰므로 계획 도구도 이 장이
  직접 정의한다. 인자 형태를 langchain의 ``write_todos``와 맞춘 이유
  (``status`` ∈ pending/in_progress/completed)는 ``docagent/todo.py``의
  변환 로직을 그대로 재사용하기 위해서다.
- ``hybrid_search_docs``: 5단계 하이브리드 검색을 도구로 노출한다. 이
  도구의 결과(어떤 청크가 실제로 반환됐는지)는 인용 검증(citations.py)의
  근거가 되므로, 실행은 ``agent.py``에서 run 상태에 접근하며 직접
  처리한다(아래 ``run_tool``을 거치지 않는다) — 5단계의 "모델이 만들어낸
  URL을 쓰지 않는다"는 규칙을 지키려면 애플리케이션이 검색 결과 자체를
  들고 있어야 하기 때문이다.

``APPROVAL_REQUIRED_TOOLS``에 이름이 있으면 ``agent.py``가 실행 전에 반드시
사용자 승인을 받는다(8단계와 동일한 메커니즘).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SALES_CSV_PATH = DATA_DIR / "sales.csv"

APPROVAL_REQUIRED_TOOLS: frozenset[str] = frozenset({"archive_report"})
# agent.py가 run 상태에 접근해 직접 처리하는 도구 이름(run_tool로 실행하지 않는다).
STATEFUL_TOOLS: frozenset[str] = frozenset({"hybrid_search_docs", "write_plan"})


class ToolError(Exception):
    """도구 실행 중 발생한, 모델에게 그대로 알려줘도 되는 오류."""


# ---------------------------------------------------------------------------
# 판매 데이터 도구 (3·8단계와 동일)
# ---------------------------------------------------------------------------


def _load_sales_rows() -> list[dict[str, Any]]:
    if not SALES_CSV_PATH.exists():
        raise ToolError(f"판매 데이터 파일을 찾을 수 없다: {SALES_CSV_PATH}")

    rows: list[dict[str, Any]] = []
    with SALES_CSV_PATH.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            try:
                rows.append(
                    {
                        "date": raw["date"],
                        "product": raw["product"],
                        "region": raw["region"],
                        "units": int(raw["units"]),
                        "revenue": int(raw["revenue"]),
                    }
                )
            except (KeyError, ValueError) as exc:
                raise ToolError(f"sales.csv 형식이 예상과 다르다: {raw!r} ({exc})") from exc
    return rows


def _quarter_of(date_str: str) -> str:
    month = int(date_str.split("-")[1])
    q = (month - 1) // 3 + 1
    return f"Q{q}"


def _apply_filters(
    rows: list[dict[str, Any]], *, product: str | None, region: str | None, quarter: str | None
) -> list[dict[str, Any]]:
    result = rows
    if product is not None:
        result = [r for r in result if r["product"] == product]
    if region is not None:
        result = [r for r in result if r["region"] == region]
    if quarter is not None:
        result = [r for r in result if _quarter_of(r["date"]) == quarter]
    return result


class SumSalesArgs(BaseModel):
    product: str | None = Field(default=None, description="집계할 제품명. 생략하면 전체 제품.")
    region: str | None = Field(default=None, description="집계할 지역명. 생략하면 전체 지역.")
    quarter: Literal["Q1", "Q2"] | None = Field(default=None, description="집계할 분기.")


def sum_sales(args: SumSalesArgs) -> dict[str, Any]:
    rows = _load_sales_rows()
    matched = _apply_filters(rows, product=args.product, region=args.region, quarter=args.quarter)
    if not matched:
        raise ToolError(
            f"조건에 맞는 판매 기록이 없다. (product={args.product!r}, region={args.region!r}, "
            f"quarter={args.quarter!r})"
        )
    return {
        "matched_row_count": len(matched),
        "total_units": sum(r["units"] for r in matched),
        "total_revenue": sum(r["revenue"] for r in matched),
        "filters": {"product": args.product, "region": args.region, "quarter": args.quarter},
    }


class LookupSalesRowsArgs(BaseModel):
    product: str | None = Field(default=None, description="조회할 제품명. 생략하면 전체.")
    region: str | None = Field(default=None, description="조회할 지역명. 생략하면 전체.")
    quarter: Literal["Q1", "Q2"] | None = Field(default=None, description="조회할 분기.")
    limit: int = Field(default=20, ge=1, le=200, description="반환할 최대 행 수(1~200). 기본 20.")


def lookup_sales_rows(args: LookupSalesRowsArgs) -> dict[str, Any]:
    """CSV 원본 행을 조회한다. 이 결과의 ``rows``가 이 장 CSV 다운로드의 원재료가 된다
    (``agent.py``가 성공한 결과를 ``run.table``에 저장한다) — "분석 결과를 표로
    구조화하기"의 표 자체다."""

    rows = _load_sales_rows()
    matched = _apply_filters(rows, product=args.product, region=args.region, quarter=args.quarter)
    if not matched:
        raise ToolError("조건에 맞는 판매 기록이 없다.")
    return {
        "matched_row_count": len(matched),
        "returned_row_count": min(len(matched), args.limit),
        "rows": matched[: args.limit],
    }


# ---------------------------------------------------------------------------
# 승인이 필요한 도구: archive_report (8단계와 동일)
# ---------------------------------------------------------------------------


class ArchiveReportArgs(BaseModel):
    report_id: str = Field(description="보관할 리포트 문서 ID. 예: 'notebook-q1-report'.")
    reason: str = Field(description="왜 이 리포트를 보관하는지에 대한 짧은 설명.")


ARCHIVED_REPORTS: list[str] = []


def archive_report(args: ArchiveReportArgs) -> dict[str, Any]:
    """리포트를 보관 처리한다(시뮬레이션). 되돌리기 어려운 작업의 예시다."""

    ARCHIVED_REPORTS.append(args.report_id)
    return {"archived": True, "report_id": args.report_id, "reason": args.reason}


# ---------------------------------------------------------------------------
# 도구 등록 (write_plan/hybrid_search_docs는 스키마만 여기 있고 실행은
# agent.py가 STATEFUL_TOOLS 분기에서 직접 한다)
# ---------------------------------------------------------------------------

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "write_plan",
            "description": (
                "이번 요청을 처리할 작업 계획을 선언하거나 갱신한다. 항목이 여러 개면 "
                "순서대로 items에 나열한다. 이미 선언한 계획을 다시 부르면 그 시점의 "
                "전체 계획으로 대체된다(이전 계획에 덧붙이지 않는다)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "작업 항목 설명."},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                            },
                            "required": ["title", "status"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["items"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hybrid_search_docs",
            "description": "제품 분기 리포트 문서에서 Dense+Sparse 하이브리드 검색으로 근거를 찾는다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색 질의."},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "description": "반환할 청크 수."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sum_sales",
            "description": "sales.csv에서 조건에 맞는 매출·판매수량 합계를 계산한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "집계할 제품명."},
                    "region": {"type": "string", "description": "집계할 지역명."},
                    "quarter": {"type": "string", "enum": ["Q1", "Q2"], "description": "집계할 분기."},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_sales_rows",
            "description": "sales.csv에서 조건에 맞는 판매 기록 원본 행을 조회한다. 이 결과가 CSV 다운로드의 표가 된다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string"},
                    "region": {"type": "string"},
                    "quarter": {"type": "string", "enum": ["Q1", "Q2"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_report",
            "description": (
                "리포트 문서를 보관 처리한다. 되돌리기 어려운 작업이므로 실행 전에 "
                "반드시 사용자 승인을 받는다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_id": {"type": "string", "description": "보관할 리포트 ID."},
                    "reason": {"type": "string", "description": "보관 사유."},
                },
                "required": ["report_id", "reason"],
                "additionalProperties": False,
            },
        },
    },
]

_TOOL_REGISTRY: dict[str, tuple[Callable[[BaseModel], dict[str, Any]], type[BaseModel]]] = {
    "sum_sales": (sum_sales, SumSalesArgs),
    "lookup_sales_rows": (lookup_sales_rows, LookupSalesRowsArgs),
    "archive_report": (archive_report, ArchiveReportArgs),
}


class ToolRunResult(BaseModel):
    ok: bool
    content: str
    error_kind: str | None = None


def validate_args(name: str, raw_args: dict[str, Any]) -> tuple[BaseModel | None, ToolRunResult | None]:
    entry = _TOOL_REGISTRY.get(name)
    if entry is None:
        return None, ToolRunResult(
            ok=False,
            error_kind="unknown_tool",
            content=json.dumps(
                {"error": "unknown_tool", "message": f"'{name}'이라는 도구는 없다."}, ensure_ascii=False
            ),
        )
    _, args_model = entry
    try:
        return args_model.model_validate(raw_args), None
    except ValidationError as exc:
        problems = [{"field": ".".join(str(p) for p in e["loc"]), "issue": e["msg"]} for e in exc.errors()]
        return None, ToolRunResult(
            ok=False,
            error_kind="invalid_args",
            content=json.dumps(
                {"error": "invalid_args", "message": "입력 인자가 스키마와 맞지 않는다.", "problems": problems},
                ensure_ascii=False,
            ),
        )


def run_tool(name: str, raw_args: dict[str, Any]) -> ToolRunResult:
    """``STATEFUL_TOOLS``가 아닌 도구를 실제로 실행한다. 실패해도 예외를 던지지 않는다."""

    entry = _TOOL_REGISTRY.get(name)
    if entry is None:
        return ToolRunResult(
            ok=False,
            error_kind="unknown_tool",
            content=json.dumps(
                {
                    "error": "unknown_tool",
                    "message": f"'{name}'이라는 도구는 없다. 사용 가능한 도구: {sorted(_TOOL_REGISTRY)}",
                },
                ensure_ascii=False,
            ),
        )

    func, args_model = entry
    try:
        parsed_args = args_model.model_validate(raw_args)
    except ValidationError as exc:
        problems = [{"field": ".".join(str(p) for p in e["loc"]), "issue": e["msg"]} for e in exc.errors()]
        return ToolRunResult(
            ok=False,
            error_kind="invalid_args",
            content=json.dumps(
                {"error": "invalid_args", "message": "입력 인자가 스키마와 맞지 않는다.", "problems": problems},
                ensure_ascii=False,
            ),
        )

    try:
        output = func(parsed_args)
    except ToolError as exc:
        return ToolRunResult(
            ok=False,
            error_kind="tool_error",
            content=json.dumps({"error": "tool_error", "message": str(exc)}, ensure_ascii=False),
        )

    return ToolRunResult(ok=True, content=json.dumps(output, ensure_ascii=False))
