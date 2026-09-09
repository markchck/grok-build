"""CSV 생성·한글 인코딩·다운로드 헤더 단위 테스트.

이 파일은 "바이트를 직접 확인"하는 요구를 코드로 고정한 것이다 — BOM 유무에
따라 Excel이 한글을 다르게 해석한다는 사실은 이 파일이 아니라
``docs/13-ui-and-export.md`` 5절에서 실제로 두 파일을 만들어 확인한다. 여기서는
그 결과를 회귀 테스트로 지킨다.
"""

from __future__ import annotations

import csv
import io

from docagent.csv_export import build_analysis_csv, content_disposition


def test_csv_bytes_start_with_utf8_bom():
    rows = [{"date": "2026-01-05", "product": "노트북", "region": "서울", "units": 373, "revenue": 223800000}]
    data = build_analysis_csv(rows)
    assert data[:3] == b"\xef\xbb\xbf", "utf-8-sig로 인코딩했으면 BOM(EF BB BF) 세 바이트로 시작해야 한다"


def test_csv_bytes_without_bom_would_be_prefixed_differently():
    """같은 내용을 BOM 없는 utf-8로 인코딩하면 시작 바이트가 다르다는 것을 직접 비교한다
    (Excel이 이 차이로 한글 CSV를 잘못된 로캘로 해석하는 문제의 근거)."""

    rows = [{"product": "노트북"}]
    with_bom = build_analysis_csv(rows)
    without_bom = ("제품\n노트북\r\n").encode("utf-8")
    assert with_bom[:3] == b"\xef\xbb\xbf"
    assert not without_bom.startswith(b"\xef\xbb\xbf")


def test_csv_roundtrip_preserves_korean_text():
    rows = [
        {"date": "2026-01-05", "product": "노트북", "region": "서울", "units": 373, "revenue": 223800000},
        {"date": "2026-01-12", "product": "키보드", "region": "부산", "units": 60, "revenue": 3600000},
    ]
    data = build_analysis_csv(rows)
    text = data.decode("utf-8-sig")  # BOM을 인식해서 제거하는 디코딩
    reader = csv.reader(io.StringIO(text))
    parsed = list(reader)
    assert parsed[0] == ["날짜", "제품", "지역", "수량", "매출"]
    assert parsed[1] == ["2026-01-05", "노트북", "서울", "373", "223800000"]
    assert parsed[2][1] == "키보드"


def test_csv_uses_crlf_line_endings():
    data = build_analysis_csv([{"product": "노트북"}])
    text = data.decode("utf-8-sig")
    assert "\r\n" in text


def test_empty_rows_produce_header_only_csv():
    data = build_analysis_csv([])
    text = data.decode("utf-8-sig")
    lines = [l for l in text.splitlines() if l]
    assert len(lines) == 1
    assert lines[0] == "날짜,제품,지역,수량,매출"


def test_content_disposition_has_ascii_fallback_and_utf8_star():
    header = content_disposition("분석결과_run_abc123.csv")
    assert "filename*=UTF-8''" in header
    assert "filename=\"" in header
    # ascii fallback: 한글이 전부 빠지지만 확장자와 최소한의 골격은 남는다
    assert header.split('filename="')[1].split('"')[0].endswith(".csv")
    # UTF-8 퍼센트 인코딩 값을 다시 풀면 원래 한글 파일명으로 돌아온다
    import urllib.parse

    star_value = header.split("filename*=UTF-8''")[1]
    assert urllib.parse.unquote(star_value) == "분석결과_run_abc123.csv"


def test_content_disposition_fallback_for_all_korean_filename():
    """파일명이 전부 한글이라 ascii 음역이 비면 고정 폴백 이름을 쓴다."""

    header = content_disposition("한글전용파일.csv")
    ascii_part = header.split('filename="')[1].split('"')[0]
    assert ascii_part == "download.csv"
