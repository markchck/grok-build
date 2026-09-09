"""(a) 집합 소속 검증과 (b) LLM-judge 배관을 검증한다.

(a)는 docagent.rag.citations.validate_and_link_citations를 그대로 재사용하는
얇은 함수라서, 실제로는 tests/test_citations.py가 이미 그 로직을 자세히
검증한다. 여기서는 "eval.citation_eval이 그 함수를 올바르게 감싸는지"만
확인한다.

(b)는 가짜 judge 함수를 주입해 배관만 확인한다 — 실제 LLM-judge의 채점
품질은 검증하지 않는다(citation_eval.py 모듈 docstring의 한계 참고).
"""

from __future__ import annotations

from docagent.rag.citations import RetrievedSource
from eval.citation_eval import (
    GroundingVerdict,
    check_citation_set_membership,
    llm_judge_grounding,
)


def _source(label: str, chunk_id: str, text: str) -> RetrievedSource:
    return RetrievedSource(
        label=label,
        chunk_id=chunk_id,
        doc_id=chunk_id.split("#")[0],
        doc_title=chunk_id,
        page=0,
        chunk_index=0,
        text=text,
        source_url="",
    )


def test_check_citation_set_membership_all_valid():
    retrieved = [_source("S1", "notebook-q2-report#p0-c0", "경쟁사 신모델 출시로 매출이 둔화됐다.")]
    answer = "매출이 둔화된 이유는 경쟁사의 신모델 출시다[S1]."

    check = check_citation_set_membership(answer, retrieved)

    assert check.used_labels == ["S1"]
    assert check.dropped_labels == []
    assert check.all_grounded_in_retrieval_set is True


def test_check_citation_set_membership_drops_fabricated_label():
    retrieved = [_source("S1", "notebook-q2-report#p0-c0", "경쟁사 신모델 출시로 매출이 둔화됐다.")]
    # 검색 결과에 없는 S9를 모델이 지어낸 경우
    answer = "매출이 둔화된 이유는 경쟁사의 신모델 출시다[S9]."

    check = check_citation_set_membership(answer, retrieved)

    assert check.used_labels == []
    assert check.dropped_labels == ["S9"]
    assert check.all_grounded_in_retrieval_set is False


def test_check_citation_set_membership_no_citations_is_grounded():
    # 인용이 아예 없으면 "버릴 것도 없다" -> True. 답변에 인용이 있어야
    # 하는지는 answer_quality.py가 별도로 채점한다.
    check = check_citation_set_membership("근거가 없어 답변할 수 없다.", [])
    assert check.all_grounded_in_retrieval_set is True
    assert check.used_labels == []


def test_llm_judge_grounding_supported_case():
    def fake_judge_always_supported(claim: str, chunk: str) -> GroundingVerdict:
        return "supported"

    result = llm_judge_grounding(
        "S1",
        "경쟁사의 신모델 출시로 매출이 둔화됐다.",
        "경쟁사 신모델 출시로 매출이 둔화됐다.",
        judge=fake_judge_always_supported,
    )
    assert result.verdict == "supported"
    assert result.label == "S1"


def test_llm_judge_grounding_not_supported_case():
    # 청크 내용과 무관한 주장을 심판이 "not_supported"로 판정하는 경우를
    # 가짜 judge로 재현한다. 실제 판정 정확도는 검증하지 않는다 — 여기서는
    # judge의 반환값이 그대로 GroundingCheck에 전달되는지만 본다.
    def fake_judge_keyword_mismatch(claim: str, chunk: str) -> GroundingVerdict:
        return "supported" if "부산" in chunk and "부산" in claim else "not_supported"

    result = llm_judge_grounding(
        "S1",
        "서울 지역 매출이 늘었다.",
        "부산 지역 판매량은 200대 안팎을 유지했다.",
        judge=fake_judge_keyword_mismatch,
    )
    assert result.verdict == "not_supported"
