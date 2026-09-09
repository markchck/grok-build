"""키워드 채점(keyword_coverage), 거절 감지(declined_to_answer),
LLM-judge 배관(llm_judge_answer)을 검증한다. 모델 서버를 쓰지 않는다.
"""

from __future__ import annotations

from eval.answer_quality import (
    AnswerVerdict,
    declined_to_answer,
    keyword_coverage,
    llm_judge_answer,
)


def test_keyword_coverage_all_present():
    answer = "2분기 노트북 매출 둔화는 경쟁사의 신모델 출시가 원인이다."
    assert keyword_coverage(answer, ["경쟁사", "신모델"]) == 1.0


def test_keyword_coverage_partial():
    answer = "2분기 노트북 매출이 둔화됐다."
    assert keyword_coverage(answer, ["경쟁사", "신모델"]) == 0.0


def test_keyword_coverage_case_insensitive():
    answer = "The Competitor launched a NEW MODEL."
    assert keyword_coverage(answer, ["competitor", "new model"]) == 1.0


def test_keyword_coverage_no_expected_keywords_is_perfect():
    assert keyword_coverage("아무 답변", []) == 1.0


def test_declined_to_answer_true_cases():
    assert declined_to_answer("등록된 문서에서 관련 내용을 찾지 못했다.") is True
    assert declined_to_answer("이 질문에 대한 근거가 없습니다.") is True


def test_declined_to_answer_false_case():
    assert declined_to_answer("2분기 매출은 경쟁사의 신모델 출시로 둔화되었다[S1].") is False


def test_llm_judge_answer_passes_through_fake_judge():
    def fake_judge(question: str, expected_summary: str, actual_answer: str) -> AnswerVerdict:
        return "correct" if expected_summary in actual_answer else "incorrect"

    result = llm_judge_answer(
        "q1",
        "노트북 2분기 매출 둔화 이유는?",
        "경쟁사 신모델",
        "경쟁사 신모델 출시 때문에 둔화되었다.",
        judge=fake_judge,
    )
    assert result.question_id == "q1"
    assert result.verdict == "correct"


def test_llm_judge_answer_incorrect_case():
    def fake_judge(question: str, expected_summary: str, actual_answer: str) -> AnswerVerdict:
        return "correct" if expected_summary in actual_answer else "incorrect"

    result = llm_judge_answer(
        "q1", "노트북 2분기 매출 둔화 이유는?", "경쟁사 신모델", "잘 모르겠습니다.", judge=fake_judge
    )
    assert result.verdict == "incorrect"
