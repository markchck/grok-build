"""분석 결과 CSV 생성과 한글 파일명 다운로드 헤더.

## 왜 ``utf-8-sig``(UTF-8 + BOM)를 쓰는가

파이썬 표준 라이브러리 ``csv`` 모듈은 인코딩을 신경 쓰지 않는다 — 파일을 열
때 준 인코딩을 그대로 쓸 뿐이다. 문제는 그 다음이다. Windows의 Excel은
CSV를 열 때 파일 시작에 BOM(Byte Order Mark, ``EF BB BF`` 세 바이트)이 있으면
"이 파일은 UTF-8이다"라고 판단하고, BOM이 없으면 시스템 로캘의 기본 인코딩
(한국어 Windows에서는 CP949/EUC-KR 계열)으로 잘못 해석한다. 그 결과 BOM 없는
UTF-8 CSV를 Excel에서 열면 한글이 깨진다("ì œí’ˆ" 같은 글자가 보인다). 이
현상은 이 튜토리얼이 지어낸 것이 아니라 Excel의 실제 동작이다 — 아래
"실행과 결과 확인"에서 이 장은 실제로 두 파일(BOM 있음/없음)을 만들어
바이트를 직접 확인한다.

파이썬에서 이 BOM을 붙이는 가장 간단한 방법이 ``encoding="utf-8-sig"``다.
``str.encode("utf-8-sig")``는 텍스트 앞에 BOM 세 바이트를 붙인 뒤 나머지를
보통의 UTF-8로 인코딩한다.

## BOM의 대가

BOM은 "Excel을 위한 우회책"이지 정답이 아니다.
- **엄격한 파서는 깨진다.** 일부 CSV 파서·라인 기반 처리기(``pandas.read_csv``는
  기본값에서 문제없이 읽지만, 셸의 ``head -1 file.csv | grep '^date'`` 같은
  바이트 단위 비교는 첫 컬럼명 앞에 보이지 않는 3바이트가 붙어 있어 실패할 수
  있다)는 BOM을 데이터의 일부로 읽어 첫 헤더 이름이 깨진 것처럼 보인다.
- **JSON에는 BOM을 쓰지 않는다.** RFC 8259가 JSON 텍스트의 BOM을 금지하지는
  않지만 붙이지 않는 것이 관례고, 붙이면 깨지는 파서가 흔하다. 이 함수는
  CSV 전용이다.
- **macOS/Linux 도구는 BOM이 필요 없다.** 그쪽의 기본 로캘이 이미 UTF-8이므로
  BOM 없이도 한글이 정상적으로 보인다. 즉 BOM은 "모두에게 필요한 것"이 아니라
  "Excel(특히 Windows)의 자동 인코딩 추정을 상대하기 위한 것"이다.

## 대안과 이 장의 선택

- **UTF-16 + 실제 구분자(tab)**: Excel이 더 안정적으로 인식하지만 대부분의
  다른 도구·스크립트가 UTF-8을 기대하므로 범용성이 낮다.
- **사용자에게 "가져오기 마법사에서 UTF-8을 직접 선택하라"고 안내**: 맞는
  방법이지만 사용자 경험이 나쁘다(클릭 몇 단계가 늘고, 그 사실 자체를
  모르는 사용자가 더 많다).
- 이 장은 **다운로드 CSV는 ``utf-8-sig``로 만들고, 그 사실과 대가를 문서에
  분명히 적는다.** 이 프로젝트의 CSV 다운로드는 "사람이 Excel로 열어 보는
  것"이 주 용도이므로 실용적인 선택이다. 다른 프로그램이 다시 읽어야 하는
  데이터 교환용 CSV라면 BOM 없는 UTF-8을 쓰고 대상 프로그램이 인코딩을
  스스로 감지하게 하는 편이 낫다 — "정답은 용도에 따라 다르다."
"""

from __future__ import annotations

import csv
import io
import urllib.parse
from typing import Any

# sales.csv 컬럼 -> 한글 헤더. CSV 다운로드는 최종 사용자가 보는 산출물이므로
# 원본 영문 컬럼명이 아니라 한글로 보여준다.
_COLUMN_LABELS: dict[str, str] = {
    "date": "날짜",
    "product": "제품",
    "region": "지역",
    "units": "수량",
    "revenue": "매출",
}


def build_analysis_csv(rows: list[dict[str, Any]]) -> bytes:
    """분석 결과 행(표)을 한글 헤더 UTF-8(BOM 포함) CSV 바이트로 만든다.

    ``rows``가 비어 있으면 헤더만 있는 CSV를 만든다(빈 파일이 아니라 "0건"임을
    사용자가 알 수 있게).
    """

    fieldnames = list(rows[0].keys()) if rows else list(_COLUMN_LABELS.keys())
    header = [_COLUMN_LABELS.get(name, name) for name in fieldnames]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([row.get(name, "") for name in fieldnames])

    # \r\n은 csv 모듈의 기본 줄바꿈이다(RFC 4180 권장 형식이기도 하다). Excel이
    # 가장 무난하게 인식하므로 그대로 둔다.
    return buffer.getvalue().encode("utf-8-sig")


def content_disposition(filename: str) -> str:
    """한글이 포함된 파일명을 위한 ``Content-Disposition`` 헤더 값을 만든다.

    HTTP 헤더 값은 라틴-1 바이트만 담을 수 있어(RFC 7230), 한글 파일명을
    ``filename="..."``에 그대로 넣으면 브라우저마다 처리(깨짐, 거부)가 다르다.
    RFC 6266/5987이 정의한 ``filename*=UTF-8''<percent-encoded>`` 확장 문법을
    쓰면 UTF-8로 인코딩한 파일명을 퍼센트 인코딩해 명시적으로 전달할 수 있다.
    구식 클라이언트를 위해 ASCII로 음역한 ``filename=`` 폴백도 함께 준다 —
    두 값을 모두 이해하는 클라이언트는 ``filename*``를 우선한다(RFC 6266 4.3절).
    """

    ascii_fallback = filename.encode("ascii", errors="ignore").decode("ascii")
    stem = ascii_fallback[:-4] if ascii_fallback.lower().endswith(".csv") else ascii_fallback
    if not stem:
        # 파일명이 전부 한글(또는 다른 비-ASCII 문자)이라 음역하면 이름이 남지 않는
        # 경우(확장자만 남거나 완전히 비는 경우) 고정 폴백 이름을 쓴다.
        ascii_fallback = "download.csv"
    elif not ascii_fallback.lower().endswith(".csv"):
        ascii_fallback = "download.csv"
    encoded = urllib.parse.quote(filename, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"
