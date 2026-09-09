#!/usr/bin/env python3
"""샘플 데이터 생성 스크립트 (PROJECT-SPEC.md 3절).

이 장에서 처음 만들고, 이후 모든 단계가 그대로 재사용한다. 실행하면 아래를 만든다.

- data/sales.csv        : date,product,region,units,revenue. 제품 4종 x 2개 분기(2026-Q1, Q2)
- data/docs/*.md         : 분기 리포트 5개. 매출 변화의 원인을 서술한 문장을 포함한다.
  각 파일은 ingest.py가 읽을 수 있도록 doc_id/title/source_url 머리말을 갖는다.

실행:
    python scripts/make_sample_data.py

기존 파일이 있으면 덮어쓴다(멱등적으로 다시 실행할 수 있다).
"""

from __future__ import annotations

import csv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOCS_DIR = DATA_DIR / "docs"

PRODUCTS = ["노트북 프로 15", "무선 이어버드 X", "스마트워치 라이트", "게이밍 마우스 G3"]
REGIONS = ["수도권", "영남권", "호남권"]

# 월별(1~6월, 2026년 1~2분기) 제품별 기준 판매량과 월간 배수.
# 배수는 아래 리포트 문서에 적는 "원인"과 방향이 맞도록 손으로 정했다.
MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]

PRODUCT_UNIT_PRICE = {
    "노트북 프로 15": 1_450_000,
    "무선 이어버드 X": 189_000,
    "스마트워치 라이트": 259_000,
    "게이밍 마우스 G3": 79_000,
}

PRODUCT_BASE_UNITS = {
    "노트북 프로 15": 40,
    "무선 이어버드 X": 120,
    "스마트워치 라이트": 60,
    "게이밍 마우스 G3": 90,
}

# 월별 배수: 1분기(1~3월), 2분기(4~6월) 흐름을 원인 문장과 맞춘다.
#  - 노트북 프로 15: 2월 대학가 할인 프로모션으로 1분기 증가, 5월 디스플레이 패널 수급 지연으로 2분기 급감
#  - 무선 이어버드 X: 1분기 인플루언서 협업으로 증가, 2분기 경쟁사 가격 인하로 감소
#  - 스마트워치 라이트: 1분기 완만, 2분기 신규 러닝 기능 마케팅으로 증가
#  - 게이밍 마우스 G3: 1분기 물류 지연으로 감소, 2분기 e스포츠 후원 노출로 회복
PRODUCT_MONTH_MULTIPLIER = {
    "노트북 프로 15": [1.0, 1.6, 1.3, 1.1, 0.5, 0.6],
    "무선 이어버드 X": [1.0, 1.2, 1.5, 1.1, 0.8, 0.7],
    "스마트워치 라이트": [0.9, 1.0, 1.0, 1.1, 1.3, 1.5],
    "게이밍 마우스 G3": [0.8, 0.7, 0.75, 1.0, 1.3, 1.4],
}

REGION_SHARE = {"수도권": 0.5, "영남권": 0.3, "호남권": 0.2}


def month_to_dates(month: str) -> str:
    """월별 대표 날짜(15일)를 그 달의 판매 집계일로 쓴다. sales.csv는 월별 집계 행이다."""
    return f"{month}-15"


def write_sales_csv() -> None:
    rows: list[dict[str, object]] = []
    for product in PRODUCTS:
        base_units = PRODUCT_BASE_UNITS[product]
        unit_price = PRODUCT_UNIT_PRICE[product]
        multipliers = PRODUCT_MONTH_MULTIPLIER[product]
        for month, mult in zip(MONTHS, multipliers):
            for region, share in REGION_SHARE.items():
                units = round(base_units * mult * share)
                revenue = units * unit_price
                rows.append(
                    {
                        "date": month_to_dates(month),
                        "product": product,
                        "region": region,
                        "units": units,
                        "revenue": revenue,
                    }
                )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "sales.csv"
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "product", "region", "units", "revenue"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"작성: {out_path} ({len(rows)}행)")


def doc(doc_id: str, title: str, body: str) -> str:
    source_url = f"data/docs/{doc_id}.md"
    header = f"---\ndoc_id: {doc_id}\ntitle: {title}\nsource_url: {source_url}\n---\n"
    return header + body.strip() + "\n"


DOCS: dict[str, str] = {
    "q1-2026-laptop-pro": doc(
        "q1-2026-laptop-pro",
        "노트북 프로 15 2026년 1분기 리포트",
        """
# 노트북 프로 15 2026년 1분기 리포트

2026년 1분기 노트북 프로 15의 판매량은 전 분기 대비 뚜렷하게 증가했다.

가장 큰 원인은 2월에 진행한 대학가 개강 할인 프로모션이다. 수도권 주요 대학
서점과 협력해 학생 인증 시 15% 할인을 제공했고, 이 프로모션이 시작된 2월부터
판매량이 눈에 띄게 늘었다. 3월에도 프로모션 효과가 이어지며 증가세가 유지됐다.

지역별로는 수도권 비중이 가장 높았고, 이는 협력 대학 서점이 수도권에
집중돼 있었기 때문이다.
""",
    ),
    "q2-2026-laptop-pro": doc(
        "q2-2026-laptop-pro",
        "노트북 프로 15 2026년 2분기 리포트",
        """
# 노트북 프로 15 2026년 2분기 리포트

2026년 2분기 들어 노트북 프로 15의 판매량은 1분기 대비 크게 감소했다.

주된 원인은 디스플레이 패널 공급사의 생산 차질이다. 5월 초 패널 협력사 공장에서
설비 점검이 예정보다 길어지면서 부품 입고가 지연됐고, 이로 인해 5월 출하량이
계획 대비 큰 폭으로 줄었다. 6월에도 재고 회복이 더뎌 판매량 감소가 이어졌다.

프로모션은 4월까지 일부 유지됐지만 재고 부족으로 실제 판매로 이어지지 못한
사례가 많았다는 점도 감소 폭을 키운 요인이다.
""",
    ),
    "q1-2026-earbuds-x": doc(
        "q1-2026-earbuds-x",
        "무선 이어버드 X 2026년 1분기 리포트",
        """
# 무선 이어버드 X 2026년 1분기 리포트

무선 이어버드 X는 2026년 1분기 동안 꾸준한 증가세를 보였다.

증가의 주된 원인은 2월부터 시작된 인플루언서 협업 마케팅이다. 여러 유튜브
리뷰 채널과 협업해 제품 체험 영상을 배포했고, 이 영상들이 3월 초 조회수가
급증하면서 온라인 채널 판매가 함께 늘었다.

지역별로는 수도권과 영남권에서 고르게 증가했으며, 특정 지역에 편중되지 않은
성장이라는 점이 이번 분기의 특징이다.
""",
    ),
    "q2-2026-earbuds-x": doc(
        "q2-2026-earbuds-x",
        "무선 이어버드 X 2026년 2분기 리포트",
        """
# 무선 이어버드 X 2026년 2분기 리포트

2026년 2분기 무선 이어버드 X의 판매량은 1분기 대비 감소했다.

가장 큰 원인은 경쟁사가 5월에 유사 사양 제품의 가격을 20% 인하한 것이다.
가격 비교가 활발한 온라인 채널을 중심으로 고객 이탈이 나타났고, 이 영향은
6월까지 이어졌다.

같은 기간 자체 프로모션을 진행하지 않은 점도 감소 폭이 커진 배경으로
꼽힌다.
""",
    ),
    "q2-2026-watch-mouse": doc(
        "q2-2026-watch-mouse",
        "스마트워치 라이트·게이밍 마우스 G3 2026년 2분기 리포트",
        """
# 스마트워치 라이트 2026년 2분기 리포트

스마트워치 라이트는 2026년 2분기에 1분기 대비 판매량이 늘었다.

원인은 5월에 추가된 러닝 코칭 기능이다. 실외 러닝 경로를 자동으로 기록하고
페이스를 안내하는 기능이 추가되며 관련 마케팅을 함께 진행했고, 그 결과
6월 판매량이 특히 크게 늘었다.

# 게이밍 마우스 G3 2026년 2분기 리포트

게이밍 마우스 G3는 1분기 물류 지연으로 판매가 부진했으나 2분기 들어 회복했다.

회복의 원인은 4월에 시작된 지역 e스포츠 대회 후원이다. 대회 중계 화면에
제품이 노출되면서 관련 커뮤니티에서 문의가 늘었고, 이 흐름이 5~6월 판매
증가로 이어졌다.
""",
    ),
}


def write_docs() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    for doc_id, content in DOCS.items():
        out_path = DOCS_DIR / f"{doc_id}.md"
        out_path.write_text(content, encoding="utf-8")
        print(f"작성: {out_path}")


def main() -> None:
    write_sales_csv()
    write_docs()
    print("완료.")


if __name__ == "__main__":
    main()
