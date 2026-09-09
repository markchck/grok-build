"""LangChain Tool — 3단계 ``docagent/tools.py``를 프레임워크로 재구성한다.

3단계에서는 아래 네 가지를 전부 손으로 만들었다.

1. OpenAI 호환 ``tools`` 배열용 JSON 스키마(``TOOL_SPECS``)를 딕셔너리로 직접 작성.
2. 모델이 만든 인자를 검증할 pydantic 모델(``SumSalesArgs`` 등)을 별도로 정의.
3. 이름 -> (함수, 인자 모델) 레지스트리(``_TOOL_REGISTRY``)와 ``run_tool()`` 디스패처.
4. 실패를 예외로 던지지 않고 ``ToolRunResult``로 감싸 모델에게 돌려주는 규약.

LangChain의 ``@tool`` 데코레이터는 1~3을 파이썬 타입 힌트와 docstring에서
**자동으로** 만든다 — 함수 시그니처가 곧 JSON 스키마의 출처이므로 스키마와
구현이 어긋날 일이 없다. 대신 "모델에게 실패를 어떻게 설명할지"(4번, 이
프로젝트의 ``ToolError`` 규약)는 LangChain이 대신 정해주지 않는다 — 그 부분은
이 파일에서도 여전히 우리가 직접 짠다. 이 차이는 이 장 6절의 비교표에서
자세히 다룬다.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import tool

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SALES_CSV_PATH = DATA_DIR / "sales.csv"


class ToolExecutionError(Exception):
    """도구 실행 중 발생한, 모델에게 그대로 설명해도 되는 오류.

    3단계의 ``ToolError``와 같은 역할이다. LangChain의 ``@tool``은 도구 함수가
    예외를 던지면 기본적으로 그 예외를 그대로 위로 전파한다 — "실패도 도구
    결과 메시지로 모델에 돌려준다"는 3단계의 규약을 그대로 지키려면, 이
    예외를 agent.py 쪽에서 잡아 ToolMessage로 바꿔주는 코드가 별도로
    필요하다(``handle_tool_errors``, agent.py 참고). LangChain이 이 규약을
    "기본으로" 만들어주지는 않는다.
    """


def _load_sales_rows() -> list[dict[str, Any]]:
    if not SALES_CSV_PATH.exists():
        raise ToolExecutionError(f"판매 데이터 파일을 찾을 수 없다: {SALES_CSV_PATH}")

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
                raise ToolExecutionError(f"sales.csv 형식이 예상과 다르다: {raw!r} ({exc})") from exc
    return rows


def _quarter_of(date_str: str) -> str:
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


@tool
def sum_sales(
    product: str | None = None,
    region: str | None = None,
    quarter: Literal["Q1", "Q2"] | None = None,
) -> dict[str, Any]:
    """sales.csv 판매 데이터에서 조건에 맞는 매출·판매수량 합계를 계산한다.

    제품별/지역별/분기별 매출 합계를 물을 때 사용한다.

    Args:
        product: 집계할 제품명. 예: '노트북'. 생략하면 전체 제품을 대상으로 한다.
        region: 집계할 지역명. 예: '서울'. 생략하면 전체 지역을 대상으로 한다.
        quarter: 집계할 분기. 'Q1' 또는 'Q2'. 생략하면 전체 기간을 대상으로 한다.
    """

    rows = _load_sales_rows()
    matched = _apply_filters(rows, product=product, region=region, quarter=quarter)
    if not matched:
        raise ToolExecutionError(
            "조건에 맞는 판매 기록이 없다. "
            f"(product={product!r}, region={region!r}, quarter={quarter!r}). "
            "제품명·지역명 철자를 확인하거나 조건을 완화해달라."
        )
    return {
        "matched_row_count": len(matched),
        "total_units": sum(r["units"] for r in matched),
        "total_revenue": sum(r["revenue"] for r in matched),
        "filters": {"product": product, "region": region, "quarter": quarter},
    }


@tool
def lookup_sales_rows(
    product: str | None = None,
    region: str | None = None,
    quarter: Literal["Q1", "Q2"] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """sales.csv에서 조건에 맞는 판매 기록 원본 행을 조회한다.

    합계가 아니라 개별 거래 내역(날짜별 수량·매출)을 확인할 때 사용한다.

    Args:
        product: 조회할 제품명. 생략하면 전체 제품.
        region: 조회할 지역명. 생략하면 전체 지역.
        quarter: 조회할 분기. 'Q1' 또는 'Q2'. 생략하면 전체 기간.
        limit: 반환할 최대 행 수(1~50). 기본 10.
    """

    if not 1 <= limit <= 50:
        raise ToolExecutionError("limit은 1 이상 50 이하여야 한다.")

    rows = _load_sales_rows()
    matched = _apply_filters(rows, product=product, region=region, quarter=quarter)
    if not matched:
        raise ToolExecutionError("조건에 맞는 판매 기록이 없다. 제품명·지역명 철자나 분기 값을 확인해달라.")
    return {
        "matched_row_count": len(matched),
        "returned_row_count": min(len(matched), limit),
        "rows": matched[:limit],
    }


# create_agent(tools=[...])에 그대로 넘길 수 있는 목록.
# TOOL_SPECS(3단계, 손으로 쓴 JSON 스키마 딕셔너리)에 대응한다 — 이 목록의
# 각 원소는 이미 실행 가능한 BaseTool 인스턴스이고, sum_sales.args_schema로
# JSON 스키마를 직접 확인할 수도 있다(README 참고).
TOOLS = [sum_sales, lookup_sales_rows]
