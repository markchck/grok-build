"""로컬 함수 도구.

**역할 분담**을 코드로 그대로 드러내는 파일이다.

- 모델이 하는 일: 사용자 질문을 보고 "어떤 도구를, 어떤 인자로 부를지"를 텍스트(JSON)로
  만드는 것뿐이다. 모델은 이 파일 안의 파이썬 함수를 실행하지 않는다. 실행할 수도 없다.
  모델 서버는 이 tools.py의 존재조차 모른다 — 오직 TOOL_SPECS로 넘겨준 이름/설명/JSON
  스키마만 안다.
- 애플리케이션(agent.py)이 하는 일: 모델이 만든 이름과 인자 문자열을 받아서
  1) pydantic으로 인자를 검증하고 2) 아래 실제 함수를 호출하고 3) 결과를 다시
  모델에게 문자열로 돌려준다. "실행"은 전적으로 애플리케이션 쪽 파이썬 코드다.

이 파일은 그 실행 담당 쪽 절반(도구 함수 + 입력 스키마)만 정의한다.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SALES_CSV_PATH = DATA_DIR / "sales.csv"


class ToolError(Exception):
    """도구 실행 중 발생한, 모델에게 그대로 알려줘도 되는 오류.

    프로그램 버그가 아니라 "이 인자로는 실행할 수 없다"는 사실을 나타낸다.
    agent.py는 이 예외를 잡아서 role="tool" 메시지로 모델에 되돌려준다.
    """


# ---------------------------------------------------------------------------
# 데이터 로딩 (여러 도구가 공유)
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
    # date_str: "YYYY-MM-DD"
    month = int(date_str.split("-")[1])
    q = (month - 1) // 3 + 1
    return f"Q{q}"


def _apply_filters(
    rows: list[dict[str, Any]],
    *,
    product: str | None,
    region: str | None,
    quarter: str | None,
) -> list[dict[str, Any]]:
    result = rows
    if product is not None:
        result = [r for r in result if r["product"] == product]
    if region is not None:
        result = [r for r in result if r["region"] == region]
    if quarter is not None:
        result = [r for r in result if _quarter_of(r["date"]) == quarter]
    return result


# ---------------------------------------------------------------------------
# 도구 1: sum_sales — 매출 합계 계산 (이 장의 본문 예제)
# ---------------------------------------------------------------------------


class SumSalesArgs(BaseModel):
    """sum_sales 도구의 입력 스키마.

    모델이 만든 JSON 인자 문자열은 이 모델로 파싱·검증한다. 세 필드 모두
    생략 가능하고, 생략하면 전체 데이터를 대상으로 합계를 낸다.
    """

    product: str | None = Field(
        default=None, description="집계할 제품명. 예: '노트북'. 생략하면 전체 제품."
    )
    region: str | None = Field(
        default=None, description="집계할 지역명. 예: '서울'. 생략하면 전체 지역."
    )
    quarter: Literal["Q1", "Q2"] | None = Field(
        default=None, description="집계할 분기. 'Q1' 또는 'Q2'. 생략하면 전체 기간."
    )


def sum_sales(args: SumSalesArgs) -> dict[str, Any]:
    """조건에 맞는 판매 기록의 매출·수량 합계를 계산한다."""

    rows = _load_sales_rows()
    matched = _apply_filters(rows, product=args.product, region=args.region, quarter=args.quarter)

    if not matched:
        raise ToolError(
            "조건에 맞는 판매 기록이 없다. "
            f"(product={args.product!r}, region={args.region!r}, quarter={args.quarter!r}). "
            "제품명·지역명 철자를 확인하거나 조건을 완화해달라."
        )

    return {
        "matched_row_count": len(matched),
        "total_units": sum(r["units"] for r in matched),
        "total_revenue": sum(r["revenue"] for r in matched),
        "filters": {"product": args.product, "region": args.region, "quarter": args.quarter},
    }


# ---------------------------------------------------------------------------
# 도구 2: lookup_sales_rows — CSV 조회 (7절 응용 과제의 예시 정답)
# ---------------------------------------------------------------------------


class LookupSalesRowsArgs(BaseModel):
    """lookup_sales_rows 도구의 입력 스키마."""

    product: str | None = Field(default=None, description="조회할 제품명. 생략하면 전체 제품.")
    region: str | None = Field(default=None, description="조회할 지역명. 생략하면 전체 지역.")
    quarter: Literal["Q1", "Q2"] | None = Field(
        default=None, description="조회할 분기. 'Q1' 또는 'Q2'. 생략하면 전체 기간."
    )
    limit: int = Field(
        default=10, ge=1, le=50, description="반환할 최대 행 수(1~50). 기본 10."
    )


def lookup_sales_rows(args: LookupSalesRowsArgs) -> dict[str, Any]:
    """조건에 맞는 판매 기록 원본 행을 최대 limit개까지 반환한다."""

    rows = _load_sales_rows()
    matched = _apply_filters(rows, product=args.product, region=args.region, quarter=args.quarter)

    if not matched:
        raise ToolError(
            "조건에 맞는 판매 기록이 없다. 제품명·지역명 철자나 분기 값을 확인해달라."
        )

    return {
        "matched_row_count": len(matched),
        "returned_row_count": min(len(matched), args.limit),
        "rows": matched[: args.limit],
    }


# ---------------------------------------------------------------------------
# 도구 등록: OpenAI 호환 tools 스펙 + 실행 레지스트리
# ---------------------------------------------------------------------------

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
                    "product": {
                        "type": "string",
                        "description": "집계할 제품명. 예: '노트북'. 생략하면 전체 제품을 대상으로 한다.",
                    },
                    "region": {
                        "type": "string",
                        "description": "집계할 지역명. 예: '서울'. 생략하면 전체 지역을 대상으로 한다.",
                    },
                    "quarter": {
                        "type": "string",
                        "enum": ["Q1", "Q2"],
                        "description": "집계할 분기. 생략하면 전체 기간을 대상으로 한다.",
                    },
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
            "description": (
                "sales.csv에서 조건에 맞는 판매 기록 원본 행을 조회한다. "
                "합계가 아니라 개별 거래 내역(날짜별 수량·매출)을 확인할 때 사용한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "조회할 제품명. 생략하면 전체."},
                    "region": {"type": "string", "description": "조회할 지역명. 생략하면 전체."},
                    "quarter": {
                        "type": "string",
                        "enum": ["Q1", "Q2"],
                        "description": "조회할 분기. 생략하면 전체 기간.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "반환할 최대 행 수(1~50). 기본 10.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]

# 도구 이름 -> (파이썬 함수, 인자 검증에 쓸 pydantic 모델)
_TOOL_REGISTRY: dict[str, tuple[Callable[[BaseModel], dict[str, Any]], type[BaseModel]]] = {
    "sum_sales": (sum_sales, SumSalesArgs),
    "lookup_sales_rows": (lookup_sales_rows, LookupSalesRowsArgs),
}


class ToolRunResult(BaseModel):
    """run_tool()의 반환값. 성공/실패와 무관하게 항상 이 형태로 돌아온다."""

    ok: bool
    content: str  # 모델에게 role="tool" 메시지로 그대로 넣을 문자열
    error_kind: str | None = None  # "unknown_tool" | "invalid_args" | "tool_error" | None


def run_tool(name: str, raw_args: dict[str, Any]) -> ToolRunResult:
    """모델이 요청한 도구 이름과 원시 인자(dict)를 받아 실제로 실행한다.

    이 함수가 이 튜토리얼에서 말하는 "애플리케이션이 실행을 담당한다"의
    실체다. 실패해도 예외를 위로 던지지 않고 ToolRunResult(ok=False, ...)로
    돌려준다 — agent.py가 이걸 그대로 모델에게 도구 결과로 전달해서
    모델이 스스로 인자를 고치거나 사용자에게 설명할 기회를 주기 위해서다.
    """

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
        # 검증 실패를 여기서 죽이지 않는다. 어떤 필드가 왜 잘못됐는지를
        # 모델이 읽을 수 있는 형태로 만들어 도구 결과 메시지로 되돌린다.
        problems = [
            {"field": ".".join(str(p) for p in e["loc"]), "issue": e["msg"]} for e in exc.errors()
        ]
        return ToolRunResult(
            ok=False,
            error_kind="invalid_args",
            content=json.dumps(
                {
                    "error": "invalid_args",
                    "message": "입력 인자가 스키마와 맞지 않는다. 문제를 고쳐서 다시 호출해달라.",
                    "problems": problems,
                },
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
