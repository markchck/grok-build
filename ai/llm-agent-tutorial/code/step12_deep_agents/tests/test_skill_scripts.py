"""두 Skill의 실행 스크립트를 서브프로세스로 직접 돌려 확인한다.

Deep Agents 없이도 스크립트 자체가 올바른지 확인할 수 있어야 한다 — Skill의
스크립트는 "모델과 무관하게 항상 같은 입력에 같은 출력을 낸다"는 것이 이
장(2절)이 강조하는 지점이기 때문이다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_analyze_sales_script(tmp_path):
    out_path = tmp_path / "csv_summary.md"
    result = subprocess.run(
        [sys.executable, str(ROOT / "skills/csv-analysis/analyze_sales.py"), str(ROOT / "data/sales.csv"), "--out", str(out_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out_path.exists()
    text = out_path.read_text(encoding="utf-8")
    assert "노트북" in text
    assert "제품별 매출 합계" in text

    last_line = result.stdout.strip().splitlines()[-1]
    payload = json.loads(last_line)
    assert payload["row_count"] == 48
    assert "노트북" in payload["by_product"]
    assert payload["by_product"]["노트북"] > 0


def test_find_evidence_script_finds_known_paragraph(tmp_path):
    out_path = tmp_path / "doc_findings.md"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "skills/doc-research/find_evidence.py"),
            str(ROOT / "data/docs"),
            "노트북",
            "부산",
            "--out",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "근거 1건" in result.stdout or "근거" in result.stdout
    text = out_path.read_text(encoding="utf-8")
    assert "notebook-q1-report#p1" in text
    assert "신학기를 앞둔" in text


def test_find_evidence_script_reports_no_match_honestly(tmp_path):
    out_path = tmp_path / "empty.md"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "skills/doc-research/find_evidence.py"),
            str(ROOT / "data/docs"),
            "존재하지않는키워드아무거나",
            "--out",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    text = out_path.read_text(encoding="utf-8")
    assert "찾지 못했다" in text
