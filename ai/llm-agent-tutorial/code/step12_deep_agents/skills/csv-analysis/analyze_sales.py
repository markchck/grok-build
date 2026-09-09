"""csv-analysis 스킬의 실행 스크립트.

sales.csv(컬럼: date,product,region,units,revenue)를 읽어 제품별 매출 합계와
분기 대비 증감률을 계산한다. 모델을 전혀 호출하지 않는 순수 집계 스크립트다
— 숫자는 이 스크립트가 계산하고, 모델은 그 결과를 해석해서 문장으로만 만든다.

사용법:
    python3 analyze_sales.py <csv경로> [--out <결과.md 경로>]

표준출력: 사람이 읽기 좋은 Markdown 표 + 프로그램이 다루기 쉬운 JSON(마지막 줄).
--out을 주면 Markdown 표만 그 파일에 저장한다(모델이 read_file로 다시 읽는 대상).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path


def _quarter(date_str: str) -> str:
    year, month, _ = date_str.split("-")
    q = (int(month) - 1) // 3 + 1
    return f"{year}-Q{q}"


def analyze(csv_path: Path) -> dict:
    by_product: dict[str, float] = defaultdict(float)
    by_region: dict[str, float] = defaultdict(float)
    by_quarter_product: dict[tuple[str, str], float] = defaultdict(float)
    row_count = 0

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_count += 1
            revenue = float(row["revenue"])
            product = row["product"]
            region = row["region"]
            quarter = _quarter(row["date"])
            by_product[product] += revenue
            by_region[region] += revenue
            by_quarter_product[(quarter, product)] += revenue

    quarters = sorted({q for q, _ in by_quarter_product})
    qoq: dict[str, dict[str, float | None]] = {}
    for product in by_product:
        series = [by_quarter_product.get((q, product), 0.0) for q in quarters]
        changes: dict[str, float | None] = {}
        for i, q in enumerate(quarters):
            if i == 0 or series[i - 1] == 0:
                changes[q] = None  # 비교할 직전 분기가 없거나 0이면 증감률을 낼 수 없다
            else:
                changes[q] = round((series[i] - series[i - 1]) / series[i - 1] * 100, 1)
        qoq[product] = changes

    return {
        "row_count": row_count,
        "quarters": quarters,
        "by_product": {k: round(v) for k, v in sorted(by_product.items())},
        "by_region": {k: round(v) for k, v in sorted(by_region.items())},
        "quarter_over_quarter_pct": qoq,
        "by_quarter_product": {f"{q}|{p}": round(v) for (q, p), v in by_quarter_product.items()},
    }


def render_markdown(result: dict, csv_path: Path) -> str:
    lines = [f"# CSV 분석 결과 ({csv_path.name}, {result['row_count']}행)\n"]
    lines.append("## 제품별 매출 합계\n")
    for product, total in result["by_product"].items():
        lines.append(f"- {product}: {total:,}원")
    lines.append("\n## 지역별 매출 합계\n")
    for region, total in result["by_region"].items():
        lines.append(f"- {region}: {total:,}원")
    lines.append("\n## 제품별 분기 대비 증감률(%)\n")
    for product, changes in result["quarter_over_quarter_pct"].items():
        parts = []
        for q, pct in changes.items():
            parts.append(f"{q}: {'—' if pct is None else f'{pct:+.1f}%'}")
        lines.append(f"- {product}: " + ", ".join(parts))
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="sales.csv 제품별/지역별/분기별 집계")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--out", type=Path, default=None, help="결과 Markdown을 저장할 경로")
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"파일을 찾을 수 없다: {args.csv_path}", file=sys.stderr)
        sys.exit(1)

    result = analyze(args.csv_path)
    markdown = render_markdown(result, args.csv_path)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        print(f"결과를 저장했다: {args.out}")
    else:
        print(markdown)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
