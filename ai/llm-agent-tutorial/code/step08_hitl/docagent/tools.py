"""로컬 함수 도구.

``sum_sales``와 ``lookup_sales_rows``는 3단계 tools.py와 같다(그대로 가져왔다).
이 장에서 새로 추가하는 두 도구가 이 장의 주제와 직접 연결된다.

- ``archive_report``: **승인이 필요한 "중요한 작업"**의 예시다. 문서 분석
  에이전트가 오래된 리포트를 보관(삭제에 가까운 되돌리기 어려운 작업)하는
  것을 흉내 낸다. ``APPROVAL_REQUIRED_TOOLS``에 이름이 있으면 ``agent.py``가
  실행 전에 반드시 사용자 승인을 받는다.
- ``flaky_lookup``: **일부러 만든 결함 있는 도구**다. 질의(``query``)가
  달라져도 항상 같은 결과를 돌려준다. 실제 검색 서버가 캐시 버그나 인덱스
  누락으로 "다르게 물어도 같은 답만 나오는" 상황을 흉내 낸 것이다. 6절
  실패 상황 실습에서 이 도구를 계속 다른 질의로 불러도 진전이 없다는 것을
  ``agent.py``의 결과-무변화 반복 감지가 잡아내는 것을 보여준다.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SALES_CSV_PATH = DATA_DIR / "sales.csv"

# 승인 없이는 실행하지 않는 도구 이름의 집합. agent.py가 이 집합을 참조한다.
APPROVAL_REQUIRED_TOOLS: frozenset[str] = frozenset({"archive_report"})


class ToolError(Exception):
    """도구 실행 중 발생한, 모델에게 그대로 알려줘도 되는 오류."""


# ---------------------------------------------------------------------------
# 판매 데이터 도구 (3단계와 동일)
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
    limit: int = Field(default=10, ge=1, le=50, description="반환할 최대 행 수(1~50). 기본 10.")


def lookup_sales_rows(args: LookupSalesRowsArgs) -> dict[str, Any]:
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
# 승인이 필요한 도구: archive_report
# ---------------------------------------------------------------------------


class ArchiveReportArgs(BaseModel):
    report_id: str = Field(description="보관할 리포트 문서 ID. 예: 'notebook-q1-report'.")
    reason: str = Field(description="왜 이 리포트를 보관하는지에 대한 짧은 설명.")


# 실제 파일을 지우지 않는다 — 이 장의 목적은 "승인이 실행을 막는지"를 보여주는
# 것이지 실제 삭제 기능을 만드는 것이 아니다. 실행된 report_id를 메모리에 쌓아
# 실행 여부를 테스트/데모에서 확인할 수 있게 한다.
ARCHIVED_REPORTS: list[str] = []


def archive_report(args: ArchiveReportArgs) -> dict[str, Any]:
    """리포트를 보관 처리한다(시뮬레이션). 되돌리기 어려운 작업의 예시다."""

    ARCHIVED_REPORTS.append(args.report_id)
    return {"archived": True, "report_id": args.report_id, "reason": args.reason}


# ---------------------------------------------------------------------------
# 일부러 결함을 넣은 도구: flaky_lookup (6절 실패 상황 실습용)
# ---------------------------------------------------------------------------


class FlakyLookupArgs(BaseModel):
    query: str = Field(description="검색어.")


def flaky_lookup(args: FlakyLookupArgs) -> dict[str, Any]:
    """질의가 달라도 항상 같은 고정 결과를 돌려주는, 일부러 고장 낸 검색 도구.

    반환값에 ``query``를 넣지 않는 것이 핵심이다 — 그래야 결과 내용이 매번
    똑같아서 "인자는 다른데 진전이 없다"는 상황을 재현할 수 있다.
    """

    return {"matches": ["결과를 찾을 수 없음(색인 오류)"], "match_count": 0}


# ---------------------------------------------------------------------------
# 도구 등록
# ---------------------------------------------------------------------------

TOOL_SPECS: list[dict[str, Any]] = [
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
            "description": "sales.csv에서 조건에 맞는 판매 기록 원본 행을 조회한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string"},
                    "region": {"type": "string"},
                    "quarter": {"type": "string", "enum": ["Q1", "Q2"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
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
    {
        "type": "function",
        "function": {
            "name": "flaky_lookup",
            "description": "문서 색인에서 질의어로 검색한다.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "검색어."}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]

_TOOL_REGISTRY: dict[str, tuple[Callable[[BaseModel], dict[str, Any]], type[BaseModel]]] = {
    "sum_sales": (sum_sales, SumSalesArgs),
    "lookup_sales_rows": (lookup_sales_rows, LookupSalesRowsArgs),
    "archive_report": (archive_report, ArchiveReportArgs),
    "flaky_lookup": (flaky_lookup, FlakyLookupArgs),
}


class ToolRunResult(BaseModel):
    ok: bool
    content: str
    error_kind: str | None = None


def validate_args(name: str, raw_args: dict[str, Any]) -> tuple[BaseModel | None, ToolRunResult | None]:
    """인자만 검증한다(실행하지 않는다). 승인 흐름에서 "수정 요청" 인자를

    실행 전에 미리 검증할 때 쓴다. 성공하면 (parsed_args, None), 실패하면
    (None, ToolRunResult(ok=False, ...))를 돌려준다.
    """

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
    """모델이 요청한 도구를 실제로 실행한다. 실패해도 예외를 던지지 않는다."""

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
