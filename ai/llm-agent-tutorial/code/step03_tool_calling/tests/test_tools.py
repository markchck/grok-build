"""도구 함수와 입력 검증에 대한 단위 테스트.

모델 서버가 필요 없는 부분만 다룬다: run_tool()의 성공/실패 분기,
pydantic 검증 실패가 예외로 죽지 않고 ToolRunResult로 돌아오는지,
그리고 sum_sales/lookup_sales_rows의 계산 결과가 CSV 데이터와 맞는지.
"""

import csv
import json
from pathlib import Path

from docagent.tools import SALES_CSV_PATH, run_tool


def _load_rows():
    with SALES_CSV_PATH.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_sales_csv_has_expected_columns():
    rows = _load_rows()
    assert rows, "sales.csv가 비어 있으면 안 된다"
    assert set(rows[0].keys()) == {"date", "product", "region", "units", "revenue"}


def test_sum_sales_matches_manual_sum():
    rows = _load_rows()
    laptop_seoul = [r for r in rows if r["product"] == "노트북" and r["region"] == "서울"]
    expected_units = sum(int(r["units"]) for r in laptop_seoul)
    expected_revenue = sum(int(r["revenue"]) for r in laptop_seoul)

    result = run_tool("sum_sales", {"product": "노트북", "region": "서울"})
    assert result.ok
    payload = json.loads(result.content)
    assert payload["total_units"] == expected_units
    assert payload["total_revenue"] == expected_revenue
    assert payload["matched_row_count"] == len(laptop_seoul)


def test_sum_sales_quarter_filter():
    rows = _load_rows()
    q1 = [r for r in rows if int(r["date"].split("-")[1]) in (1, 2, 3)]
    result = run_tool("sum_sales", {"quarter": "Q1"})
    assert result.ok
    payload = json.loads(result.content)
    assert payload["matched_row_count"] == len(q1)


def test_sum_sales_no_match_is_tool_error_not_exception():
    result = run_tool("sum_sales", {"product": "존재하지않는제품"})
    assert result.ok is False
    assert result.error_kind == "tool_error"
    payload = json.loads(result.content)
    assert payload["error"] == "tool_error"


def test_invalid_quarter_value_returns_invalid_args_not_raise():
    # quarter는 Literal["Q1","Q2"]다. "Q9"는 스키마 위반이어야 한다.
    result = run_tool("sum_sales", {"quarter": "Q9"})
    assert result.ok is False
    assert result.error_kind == "invalid_args"
    payload = json.loads(result.content)
    assert payload["error"] == "invalid_args"
    assert payload["problems"], "어떤 필드가 잘못됐는지 problems에 담겨야 한다"


def test_invalid_type_returns_invalid_args():
    # limit은 int여야 한다. 문자열 숫자가 아닌 값을 주면 검증에 실패해야 한다.
    result = run_tool("lookup_sales_rows", {"limit": "많이"})
    assert result.ok is False
    assert result.error_kind == "invalid_args"


def test_limit_out_of_range_returns_invalid_args():
    result = run_tool("lookup_sales_rows", {"limit": 999})
    assert result.ok is False
    assert result.error_kind == "invalid_args"


def test_lookup_sales_rows_respects_limit():
    result = run_tool("lookup_sales_rows", {"quarter": "Q1", "limit": 3})
    assert result.ok
    payload = json.loads(result.content)
    assert payload["returned_row_count"] == 3
    assert len(payload["rows"]) == 3
    assert payload["matched_row_count"] >= 3


def test_unknown_tool_name():
    result = run_tool("does_not_exist", {})
    assert result.ok is False
    assert result.error_kind == "unknown_tool"


def test_extra_unexpected_field_is_ignored_or_rejected():
    # additionalProperties는 모델에게 주는 JSON 스키마 쪽 힌트다.
    # pydantic 쪽 기본 동작(추가 필드 무시)도 예외 없이 동작해야 한다.
    result = run_tool("sum_sales", {"product": "노트북", "이상한필드": 123})
    assert result.ok is True
