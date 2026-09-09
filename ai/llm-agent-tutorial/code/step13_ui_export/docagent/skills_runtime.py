"""Skill 실행 계층 — 12단계 skills/ 를 이 장의 도구 호출 에이전트 루프에 배선한다.

이 파일이 존재하는 이유는 "Tool·MCP·Skill이 서로 다른 것"이라는 최종 통합
과제 4번 항목을 코드로도 구분해서 보여주기 위해서다. 세 층의 차이:

- **Tool**(``docagent/tools.py``): 파이썬 함수 + pydantic 인자 스키마. 모델이
  호출을 요청하면 이 프로세스 안에서 함수를 직접 부른다(3단계부터).
- **MCP 도구**(``docagent/mcp_client.py``): 완전히 별도 프로세스(``mcp_server``)
  에 있는 함수를 프로토콜(stdio)로 호출한다(10단계).
- **Skill**(이 파일): 12단계 ``skills/*/SKILL.md``가 정의한 "정해진 절차 +
  스크립트" 묶음이다. Deep Agents(12단계)에서는 모델이 SKILL.md 원문을 직접
  읽고 그 지시에 따라 ``execute`` 도구로 스크립트를 돌렸다. 이 장의 에이전트
  루프는 Deep Agents가 아니라 3·8단계식 도구 호출 루프이므로, "이 스킬을
  실행하라"는 동작 자체를 하나의 도구로 노출하고, 실제 실행은 SKILL.md가
  지시하는 것과 **완전히 같은 스크립트**를 subprocess로 그대로 돌린다.
  스킬의 정체성(스크립트 파일, SKILL.md 절차, 계약)은 하나도 바꾸지 않았다
  — 이 파일이 하는 일은 "그 스크립트를 누가 어떻게 트리거하는가"뿐이다.

시스템 프롬프트와도 다르다: 시스템 프롬프트(``agent.py``의 ``SYSTEM_PROMPT``)는
모델에게 "이렇게 행동해라"라고 매 요청마다 주는 지시문이고, 대화 이력에
포함되어 모델이 직접 읽는다. Skill은 그렇지 않다 — SKILL.md는 모델이 읽는
프롬프트가 아니라(이 장에서는 아예 모델에게 전달되지 않는다), 애플리케이션이
"이 스킬을 실행하면 이 스크립트를 이 방식으로 돌린다"는 실행 절차로만 쓴다.
docs/13-ui-and-export.md 2절에서 이 세 층을 표로 비교한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = BASE_DIR / "skills"
DATA_DIR = BASE_DIR / "data"
RESULTS_DIR = BASE_DIR / "results"

CSV_ANALYSIS_SCRIPT = SKILLS_DIR / "csv-analysis" / "analyze_sales.py"
DOC_RESEARCH_SCRIPT = SKILLS_DIR / "doc-research" / "find_evidence.py"

SKILL_TOOL_NAMES: frozenset[str] = frozenset({"csv_analysis_skill", "doc_research_skill"})

SKILL_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "csv_analysis_skill",
            "description": (
                "(Skill: csv-analysis, 12단계 skills/csv-analysis/SKILL.md) sales.csv 전체를 "
                "제품별·지역별·분기별로 집계하고 직전 분기 대비 증감률을 계산한다. "
                "숫자를 스스로 어림잡지 말고 이 스킬로 계산된 값만 인용한다."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "doc_research_skill",
            "description": (
                "(Skill: doc-research, 12단계 skills/doc-research/SKILL.md) data/docs 문서에서 "
                "주어진 키워드가 전부 포함된 문단을 찾아 출처 꼬리표와 함께 돌려준다. "
                "매출 변화의 '원인' 근거가 필요할 때 쓴다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "AND로 결합할 검색 키워드 목록.",
                    }
                },
                "required": ["keywords"],
                "additionalProperties": False,
            },
        },
    },
]


class SkillRunError(RuntimeError):
    """스킬 스크립트 실행 자체가 실패했을 때(스크립트를 찾을 수 없다, 0이 아닌 종료 코드 등)."""


def _run_script(args: list[str], *, timeout: float = 30.0) -> str:
    try:
        proc = subprocess.run(
            [sys.executable, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
        )
    except subprocess.TimeoutExpired as exc:
        raise SkillRunError(f"스킬 스크립트 실행이 {timeout}초를 넘겨 시간 초과됐다: {args}") from exc
    except OSError as exc:
        raise SkillRunError(f"스킬 스크립트를 실행할 수 없다: {args} ({exc})") from exc

    if proc.returncode != 0:
        raise SkillRunError(
            f"스킬 스크립트가 0이 아닌 종료 코드({proc.returncode})로 끝났다. stderr: {proc.stderr.strip()[:500]}"
        )
    return proc.stdout


def run_csv_analysis_skill() -> dict[str, Any]:
    """SKILL.md 절차 그대로: analyze_sales.py를 data/sales.csv에 대해 돌리고
    결과를 results/csv_analysis.md에 저장한다(모델이 read_file로 다시 읽는
    대상과 같은 자리 — 이 장은 그 요약을 도구 결과로 바로 돌려준다)."""

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "csv_analysis.md"
    stdout = _run_script(
        [str(CSV_ANALYSIS_SCRIPT), str(DATA_DIR / "sales.csv"), "--out", str(out_path)]
    )
    # analyze_sales.py의 계약: --out을 주면 표준출력 마지막 줄이 JSON이다.
    last_line = [line for line in stdout.strip().splitlines() if line.strip()][-1]
    parsed = json.loads(last_line)
    return {"result_file": str(out_path.relative_to(BASE_DIR)), **parsed}


def run_doc_research_skill(keywords: list[str]) -> dict[str, Any]:
    """SKILL.md 절차 그대로: find_evidence.py를 data/docs에 대해 키워드로 돌린다."""

    if not keywords:
        raise SkillRunError("keywords가 비어 있다. 검색할 키워드를 하나 이상 준다.")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "doc_findings.md"
    _run_script(
        [str(DOC_RESEARCH_SCRIPT), str(DATA_DIR / "docs"), *keywords, "--out", str(out_path)]
    )
    markdown = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
    return {"result_file": str(out_path.relative_to(BASE_DIR)), "markdown": markdown}
