"""판매 데이터 접근 함수 (3단계 tools.py의 로직을 재사용).

이 장의 하위 에이전트는 "모델이 도구 호출을 요청하면 애플리케이션이 실행한다"는
3단계의 Tool Calling 패턴을 쓰지 않는다. 대신 각 에이전트 노드가 이 모듈의
함수를 **파이썬 함수로 직접** 부른다 — 조사·분석·검증이라는 세 노드가 이미
"누가 무엇을 하는지"를 그래프 구조로 나누고 있으므로, 그 안에서 다시 모델에게
"이 함수를 불러도 되냐"고 묻는 것은 이 장이 보이려는 것(에이전트 간 역할 분리)과
무관한 복잡도를 하나 더 얹을 뿐이다. 모델은 오직 "무엇을 조사할지", "그 결과를
어떻게 해석할지", "충분한지"를 판단하는 데만 쓰인다.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SALES_CSV_PATH = DATA_DIR / "sales.csv"


class DataError(Exception):
    """데이터 조회 중 발생한, 상위 에이전트에게 그대로 알려줘도 되는 오류."""


def load_rows() -> list[dict[str, Any]]:
    if not SALES_CSV_PATH.exists():
        raise DataError(f"판매 데이터 파일을 찾을 수 없다: {SALES_CSV_PATH}")
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
                raise DataError(f"sales.csv 형식이 예상과 다르다: {raw!r} ({exc})") from exc
    return rows


def _quarter_of(date_str: str) -> str:
    month = int(date_str.split("-")[1])
    return f"Q{(month - 1) // 3 + 1}"


def rows_for_product(product: str) -> list[dict[str, Any]]:
    """조사(investigator) 에이전트가 쓴다: 제품명으로 원본 행을 찾는다."""

    rows = load_rows()
    matched = [r for r in rows if product in r["product"]]
    return matched


def quarterly_totals(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """분석(analyst) 에이전트가 쓴다: 분기별 매출·수량 합계."""

    totals: dict[str, dict[str, int]] = {}
    for r in rows:
        q = _quarter_of(r["date"])
        bucket = totals.setdefault(q, {"units": 0, "revenue": 0})
        bucket["units"] += r["units"]
        bucket["revenue"] += r["revenue"]
    return totals


def known_products() -> list[str]:
    return sorted({r["product"] for r in load_rows()})
