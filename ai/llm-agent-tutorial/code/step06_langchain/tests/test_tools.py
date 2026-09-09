"""LangChain @tool로 만든 sum_sales/lookup_sales_rows가 3단계와 같은 결과를 내는지 확인한다."""

from __future__ import annotations

import pytest

from docagent.tools import ToolExecutionError, lookup_sales_rows, sum_sales


def test_sum_sales_matches_filters():
    result = sum_sales.invoke({"product": "노트북", "quarter": "Q1"})
    assert result["filters"] == {"product": "노트북", "region": None, "quarter": "Q1"}
    assert result["matched_row_count"] > 0
    assert result["total_revenue"] > 0


def test_sum_sales_raises_tool_execution_error_when_no_match():
    with pytest.raises(ToolExecutionError):
        sum_sales.invoke({"product": "존재하지않는제품"})


def test_sum_sales_json_schema_has_no_required_fields():
    """모든 인자가 선택값이라는 3단계 TOOL_SPECS의 규약이 @tool에서도 유지되는지 확인한다."""
    schema = sum_sales.args_schema.model_json_schema()
    assert schema.get("required", []) == []


def test_lookup_sales_rows_limit_bounds():
    with pytest.raises(ToolExecutionError):
        lookup_sales_rows.invoke({"limit": 100})
