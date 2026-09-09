"""CSV 조회 로직 — 3단계 ``docagent/tools.py``의 ``sum_sales``/``lookup_sales_rows``와
같은 계산이다. 이 장에서 옮긴 것은 **누가 실행하는가**뿐이다.

3단계에서는 이 함수들이 에이전트와 같은 파이썬 프로세스 안에 있었고,
``run_tool()``이 직접 파이썬 함수 호출로 실행했다(agent.py -> tools.py, 함수
호출 한 번). 이 장에서는 이 파일이 **별도 MCP 서버 프로세스** 안에 있고,
``server.py``의 ``@mcp.tool()``이 이 함수들을 감싼다. 에이전트 프로세스는
이 함수의 이름도, 이 파일의 존재도 모른다 — MCP 세션을 통해 "이름과 JSON
인자"만 보내고 "JSON 결과"만 받는다. 프로세스 경계를 넘은 것은 정확히
"함수 호출"이 "네트워크(또는 파이프)를 통한 요청/응답"으로 바뀐 부분이다.

이 파일 자체는 3단계와 마찬가지로 순수 파이썬이고 MCP를 import하지 않는다
— MCP 연결과 무관하게 단위 테스트할 수 있다.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from mcp_server.config import get_mcp_server_settings


class SalesDataError(Exception):
    """호출자에게 그대로 알려줘도 되는 오류. server.py에서 mcp ToolError로 바꾼다."""


def _load_sales_rows() -> list[dict[str, Any]]:
    path: Path = get_mcp_server_settings().sales_csv_path
    if not path.exists():
        raise SalesDataError(f"판매 데이터 파일을 찾을 수 없다: {path}")

    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as f:
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
                raise SalesDataError(f"sales.csv 형식이 예상과 다르다: {raw!r} ({exc})") from exc
    return rows


def _quarter_of(date_str: str) -> str:
    month = int(date_str.split("-")[1])
    return f"Q{(month - 1) // 3 + 1}"


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
    quarter: Literal["Q1", "Q2"] | None = Field(default=None, description="집계할 분기. 생략하면 전체 기간.")


def sum_sales(args: SumSalesArgs) -> dict[str, Any]:
    rows = _load_sales_rows()
    matched = _apply_filters(rows, product=args.product, region=args.region, quarter=args.quarter)
    if not matched:
        raise SalesDataError(
            f"조건에 맞는 판매 기록이 없다(product={args.product!r}, region={args.region!r}, "
            f"quarter={args.quarter!r}). 제품명·지역명 철자를 확인하거나 조건을 완화해달라."
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
    quarter: Literal["Q1", "Q2"] | None = Field(default=None, description="조회할 분기. 생략하면 전체 기간.")
    limit: int = Field(default=10, ge=1, le=50, description="반환할 최대 행 수(1~50). 기본 10.")


def lookup_sales_rows(args: LookupSalesRowsArgs) -> dict[str, Any]:
    rows = _load_sales_rows()
    matched = _apply_filters(rows, product=args.product, region=args.region, quarter=args.quarter)
    if not matched:
        raise SalesDataError("조건에 맞는 판매 기록이 없다. 제품명·지역명 철자나 분기 값을 확인해달라.")
    return {
        "matched_row_count": len(matched),
        "returned_row_count": min(len(matched), args.limit),
        "rows": matched[: args.limit],
    }


def csv_schema_description() -> str:
    """CSV 스키마 설명. server.py가 Resource로 노출한다 (Tool이 아니다 —
    호출 인자가 없고, 모델이 "실행"할 대상이 아니라 참고용 정적 텍스트라서)."""
    return (
        "sales.csv 컬럼: date(YYYY-MM-DD), product(제품명: 노트북/마우스/모니터/키보드), "
        "region(지역명: 서울/부산), units(판매 수량, 정수), revenue(매출액, 원, 정수). "
        "분기는 date의 월(1~3월=Q1, 4~6월=Q2)로 계산한다."
    )
