"""답변 품질 평가 — 검색 품질(retrieval_metrics.py)과 분리한다.

검색이 정답 청크를 찾아도 모델이 그걸 잘못 요약하거나, 엉뚱한 문장을
만들거나, "모른다"고 해야 할 때 지어낼 수 있다. 반대로 검색이 애초에
틀렸는데 모델이 그럴듯한 답을 만들어서 겉보기에는 그럴싸할 수도 있다.
두 실패는 원인과 고치는 방법이 다르다 — 검색 실패는 청킹·임베딩·랭커를
고치고, 답변 실패는 프롬프트나 모델을 고친다. 그래서 이 모듈은
retrieval_metrics.py의 recall/MRR을 전혀 참조하지 않는다.

이 모듈은 두 층의 채점을 제공한다.

1. ``keyword_coverage`` — 기계적 채점. 답변에 기대 키워드가 얼마나
   들어있는지 세는 것뿐이라 모델 호출이 필요 없고 100% 재현 가능하다.
   대신 "키워드가 다 있어도 문장이 앞뒤가 안 맞다"는 못 잡는다.
2. ``llm_judge_answer`` — LLM-judge 채점. 질문·기대 답변 요지·실제 답변을
   심판 모델에 주고 정확성을 점수로 매기게 한다. citation_eval.py와 같은
   한계(편향·재현성·비용)를 그대로 가진다 — 심판 함수를 주입받는 형태로
   만들어 실제 모델 호출과 로직을 분리했다.

"모른다고 답해야 하는 질문"(``expect_no_answer``)은 키워드 포함 여부로
채점할 수 없으므로 ``declined_to_answer``로 따로 채점한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

# 답변이 "모른다"는 취지로 거절했다고 볼 수 있는 표현들. 4단계에서 정한
# "근거 부족 시 답변 처리" 문구, 5단계 앱의 기본 안내 문구와 맞춘다.
_DECLINE_MARKERS = ["찾지 못했다", "근거가 없", "알 수 없", "모른다", "확인되지 않"]


def keyword_coverage(answer_text: str, expected_keywords: list[str]) -> float:
    """기대 키워드 중 답변에 실제로 등장한 비율. 대소문자 구분 없이 부분 문자열 매칭.

    ``expected_keywords``가 비어 있으면 1.0(채점할 기준이 없으므로 만점
    처리) — 이 경우는 애초에 ``expect_no_answer`` 질문일 가능성이 높고,
    그 질문은 ``declined_to_answer``로 따로 채점한다.
    """
    if not expected_keywords:
        return 1.0
    lowered = answer_text.lower()
    hit = sum(1 for kw in expected_keywords if kw.lower() in lowered)
    return hit / len(expected_keywords)


def declined_to_answer(answer_text: str) -> bool:
    """답변이 "모른다"는 취지로 거절했는지 표면적 문구로 판단한다.

    간단한 문자열 매칭이라 완벽하지 않다 — 예를 들어 "근거가 없다고
    오해할 만한 문장"을 답변이 인용하면서 설명하면 오탐할 수 있다.
    운영 품질을 정밀하게 재려면 이 부분도 LLM-judge로 바꿀 수 있지만,
    이 튜토리얼은 기계적 채점과 LLM-judge 채점을 섞지 않고 명확히
    구분해 둔다.
    """
    lowered = answer_text.lower()
    return any(marker in lowered for marker in _DECLINE_MARKERS)


AnswerVerdict = Literal["correct", "partially_correct", "incorrect"]
JudgeFn = Callable[[str, str, str], AnswerVerdict]
"""심판 함수 시그니처: (question, expected_summary, actual_answer) -> verdict."""


@dataclass(frozen=True)
class AnswerJudgeResult:
    question_id: str
    verdict: AnswerVerdict
    raw_note: str = ""


def llm_judge_answer(
    question_id: str,
    question: str,
    expected_summary: str,
    actual_answer: str,
    *,
    judge: JudgeFn,
) -> AnswerJudgeResult:
    """LLM을 심판으로 답변 정확성을 채점한다. 한계는 모듈 docstring 참고.

    ``judge``가 실제 모델을 부르지 않는 가짜 함수라도 이 함수의 배관은
    테스트할 수 있다(tests/test_answer_quality.py) — 그 테스트는 "가짜
    심판을 넣었을 때 결과가 올바르게 전달되는가"만 확인하며, 진짜
    LLM-judge의 채점 품질을 검증하지 않는다.
    """
    verdict = judge(question, expected_summary, actual_answer)
    return AnswerJudgeResult(question_id=question_id, verdict=verdict)
