"""근거 링크(인용) 정확성 평가 — 두 가지 다른 질문을 분리해서 다룬다.

(a) **집합 소속 검증**: 답변이 인용한 청크가 이번 검색에서 실제로 반환된
    청크 집합 안에 있는가? 기계적으로 100% 판정 가능하다 — 문자열 집합
    비교이므로 모호함이 없다. docagent.rag.citations.validate_and_link_citations가
    운영 경로에서 이미 이 판정을 한다(9단계 app.py에서 매 요청마다). 이
    모듈은 그 함수를 **다시 구현하지 않고 그대로 재사용**한다 — 운영과
    평가가 서로 다른 로직으로 "인용이 유효한가"를 판단하면, 평가 점수가
    좋아도 운영에서는 다르게 동작하는 사고가 날 수 있다.

(b) **근거 적합성(grounding) 검증**: 그 청크의 내용이 실제로 그 문장의
    주장을 뒷받침하는가? 이것은 문자열 비교로 판정할 수 없다 — 의미를
    이해해야 한다. 사람이 읽고 판단하거나, LLM을 심판(judge)으로 써서
    판단을 흉내 낼 수 있을 뿐이다. ``llm_judge_grounding``이 이 판정을
    맡는다.

**LLM-judge의 한계** (이 함수를 실제로 쓸 때 반드시 감안한다):

- **편향**: 심판 모델이 인용문과 스타일이 비슷한 답변을 실제 정확성과
  무관하게 더 후하게 평가하는 경향이 보고되어 있다. 심판이 곧 정답
  기준이 아니다.
- **재현성**: 같은 입력이라도 temperature>0이면 심판 판정이 매번 달라질
  수 있다. temperature=0으로 고정해도 모델 버전이 바뀌면 판정이 바뀐다 —
  평가 결과에 심판 모델 이름과 버전을 항상 같이 기록해야 시간이 지나
  숫자를 비교할 때 오해가 없다.
- **비용과 속도**: 질문마다 심판 모델을 한 번 더 호출해야 하므로, 평가
  데이터셋이 커지면 비용과 시간이 선형으로 늘어난다. 기계적으로 판정
  가능한 (a)를 먼저 걸러내고, (b)는 (a)를 통과한 인용에만 돌리는 식으로
  범위를 줄이는 것이 실용적이다.

이 한계 때문에 이 모듈의 ``llm_judge_grounding``은 실제 판정 함수를
인자로 받는 형태(의존성 주입)로 만든다 — 테스트에서는 가짜 심판을 넣어
로직만 검증하고, 실제 채점에서는 진짜 모델 호출을 넣는다. 가짜 심판으로
통과한 테스트가 "LLM-judge 자체를 검증했다"는 뜻은 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from docagent.rag.citations import RetrievedSource, validate_and_link_citations

GroundingVerdict = Literal["supported", "not_supported", "unclear"]


@dataclass(frozen=True)
class CitationSetCheck:
    """(a) 집합 소속 검증 결과. 기계적 판정이므로 신뢰도가 100%다."""

    used_labels: list[str]
    dropped_labels: list[str]

    @property
    def all_grounded_in_retrieval_set(self) -> bool:
        """인용이 하나도 안 버려졌으면 True. 답변 자체에 인용이 없는 경우도
        True다(버릴 것도 없었으므로) — 인용이 있어야 한다는 요구사항은
        answer_quality.py가 별도로 확인한다."""
        return len(self.dropped_labels) == 0


def check_citation_set_membership(
    answer_text: str,
    retrieved: list[RetrievedSource],
    *,
    app_base_url: str = "http://localhost:8080",
) -> CitationSetCheck:
    """(a) 답변의 [Sn] 인용이 전부 이번 검색 결과 집합 안에 있는지 확인한다.

    docagent.rag.citations.validate_and_link_citations를 그대로 호출한다 —
    운영 경로와 같은 함수, 같은 판정이다.
    """
    result = validate_and_link_citations(answer_text, retrieved, app_base_url=app_base_url)
    return CitationSetCheck(used_labels=result.used_labels, dropped_labels=result.dropped_labels)


JudgeFn = Callable[[str, str], GroundingVerdict]
"""심판 함수의 시그니처: (claim_sentence, chunk_text) -> verdict.

실제 구현은 LLM을 한 번 호출해 "이 청크가 이 문장을 뒷받침하는가"를
supported/not_supported/unclear 중 하나로 답하게 만드는 프롬프트를 쓴다.
이 모듈은 그 구현을 강제하지 않는다 — 어떤 모델을 심판으로 쓸지, 어떤
프롬프트를 쓸지는 호출하는 쪽(run_eval.py)이 결정한다.
"""


@dataclass(frozen=True)
class GroundingCheck:
    label: str
    claim_sentence: str
    chunk_text: str
    verdict: GroundingVerdict


def llm_judge_grounding(
    label: str,
    claim_sentence: str,
    chunk_text: str,
    *,
    judge: JudgeFn,
) -> GroundingCheck:
    """(b) 청크가 문장의 주장을 실제로 뒷받침하는지 심판 함수로 판정한다.

    이 함수 자체는 모델을 호출하지 않는다 — ``judge``를 그대로 부를
    뿐이다. 그래서 가짜 judge를 주입하면 모델 서버 없이도 이 함수의
    배관(연결이 맞는지)만 단위 테스트할 수 있다. 실제 판정 품질은
    ``judge`` 구현에 달려 있고, 그건 이 함수가 검증할 수 없다 — 위 모듈
    docstring의 한계를 참고한다.
    """
    verdict = judge(claim_sentence, chunk_text)
    return GroundingCheck(
        label=label, claim_sentence=claim_sentence, chunk_text=chunk_text, verdict=verdict
    )
