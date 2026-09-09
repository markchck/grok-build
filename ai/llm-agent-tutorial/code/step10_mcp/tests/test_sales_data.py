"""mcp_server/sales_data.py 단위 테스트. MCP 세션·모델 서버 없이 실행된다
(3단계 tests/test_tools.py와 같은 성격의 테스트)."""

import csv

import pytest

from mcp_server.config import get_mcp_server_settings
from mcp_server.sales_data import (
    LookupSalesRowsArgs,
    SalesDataError,
    SumSalesArgs,
    lookup_sales_rows,
    sum_sales,
)


def _load_rows():
    path = get_mcp_server_settings().sales_csv_path
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_sales_csv_has_expected_columns():
    rows = _load_rows()
    assert rows
    assert set(rows[0].keys()) == {"date", "product", "region", "units", "revenue"}


def test_sum_sales_matches_manual_sum():
    rows = _load_rows()
    laptop_seoul = [r for r in rows if r["product"] == "노트북" and r["region"] == "서울"]
    expected_units = sum(int(r["units"]) for r in laptop_seoul)
    expected_revenue = sum(int(r["revenue"]) for r in laptop_seoul)

    result = sum_sales(SumSalesArgs(product="노트북", region="서울"))
    assert result["total_units"] == expected_units
    assert result["total_revenue"] == expected_revenue
    assert result["matched_row_count"] == len(laptop_seoul)


def test_sum_sales_no_match_raises_sales_data_error():
    with pytest.raises(SalesDataError):
        sum_sales(SumSalesArgs(product="존재하지않는제품"))


def test_lookup_sales_rows_respects_limit():
    result = lookup_sales_rows(LookupSalesRowsArgs(quarter="Q1", limit=3))
    assert result["returned_row_count"] == 3
    assert len(result["rows"]) == 3
    assert result["matched_row_count"] >= 3
