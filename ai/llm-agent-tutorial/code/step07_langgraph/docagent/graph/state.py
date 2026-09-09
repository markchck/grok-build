"""그래프 상태 타입과 리듀서(reducer).

LangGraph에서 State는 채널(channel)의 모음이다. 각 채널은 노드가 돌려준
부분 업데이트(dict)를 어떻게 이전 값과 합칠지 정하는 **리듀서**를 가진다.
``Annotated[T, reducer]``로 표시하지 않은 필드는 기본 리듀서(마지막에 쓴
값이 이전 값을 덮어쓴다, "last write wins")를 쓴다.

이 장에서 리듀서가 실제로 필요한 이유는 병렬 실행(4-4절) 때문이다.
``retrieve_dense``와 ``retrieve_sparse``는 같은 슈퍼스텝(superstep, Pregel
모델에서 "한 번에 동시 실행되는 노드들의 묶음")에서 나란히 실행되고, 둘 다
``retrieved`` 채널에 자기 검색 결과를 쓴다. 기본 리듀서(마지막 값이 이김)를
쓰면 둘 중 하나의 결과가 사라진다 — 그래서 두 업데이트를 안전하게 합치는
커스텀 리듀서(``merge_hits``)를 둔다.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_hits(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """검색 결과 두 목록을 청크 ID(``pk``) 기준으로 합친다.

    같은 청크가 dense·sparse 양쪽에서 모두 나오면(예: 정확한 키워드가
    포함된 문장은 두 검색 모두에서 상위에 오르기 쉽다) 점수(``score``)가
    더 높은 쪽을 남긴다. 두 점수는 스케일이 다르므로(자카드 유사도 vs
    카운트, 또는 실제 Milvus의 코사인 유사도 vs BM25 점수) 이 "더 큰 쪽을
    남긴다"는 규칙은 엄밀한 순위 결합이 아니다 — 엄밀한 결합(RRF, 가중치)은
    Milvus의 ``hybrid_search``(5단계, ``docagent/rag/search.py``)가 서버
    안에서 이미 하고, 그 결과를 쓸 때는 이 그래프도 ``hybrid_search`` 한
    번만 호출해 이 리듀서가 사실상 항등 함수가 된다. 이 리듀서가 실제로
    스케일이 다른 두 목록을 다루는 경우는 "dense 따로 + sparse 따로를
    병렬로 돌려 그래프 수준에서 합치는" 4-4절 실습 경로뿐이다 — 그 경로의
    목적은 정확한 순위가 아니라 "병렬 노드의 결과를 상태 충돌 없이 병합하는
    방법"을 보여주는 것이다.
    """

    by_pk: dict[str, dict[str, Any]] = {h["pk"]: h for h in left}
    for hit in right:
        prev = by_pk.get(hit["pk"])
        if prev is None or hit.get("score", 0.0) > prev.get("score", 0.0):
            by_pk[hit["pk"]] = hit
    return sorted(by_pk.values(), key=lambda h: h.get("score", 0.0), reverse=True)


class GraphState(TypedDict):
    """질문 분류 → 검색 → 근거 확인 → (부족하면) 추가 검색 → 답변 상태.

    ``Annotated[..., operator.add]``가 붙은 필드는 리스트를 이어 붙인다
    (파이썬 ``list + list``). ``Annotated[..., merge_hits]``가 붙은
    ``retrieved``는 위에서 설명한 청크 ID 기준 병합을 쓴다. 나머지 필드는
    마지막으로 쓴 값이 이전 값을 덮어쓴다.
    """

    # 입력
    question: str

    # classify 노드가 채운다
    category: str  # "factual" | "chitchat"
    category_source: str  # "model" | "heuristic" — 판단 주체 기록 (2-3절)

    # 검색에 실제로 쓴 질의문. classify가 처음 채우고 expand_query가 갱신한다.
    current_query: str

    # 검색 노드들이 채운다. 같은 슈퍼스텝에서 병렬로 쓰기 때문에 merge_hits가 필요하다.
    retrieved: Annotated[list[dict], merge_hits]

    # 실제로 실행에 쓴 질의문 이력(디버깅·표시용). 순서대로 이어 붙인다.
    queries_tried: Annotated[list[str], operator.add]

    # 몇 번째 검색 라운드인지. 마지막 값이 이긴다(리스트가 아니라 카운터라서).
    retrieval_round: int

    # verify 노드가 채운다
    sufficient: bool
    insufficiency_reason: str
    verify_source: str  # "model" | "heuristic"

    # answer 노드가 채운다
    answer_text: str
    citations: list[dict]  # sources 이벤트 payload 형태
    dropped_citations: list[str]

    # 애플리케이션이 관측한 실행 사실의 로그. SSE status 이벤트로 그대로
    # 옮길 수 있는 {"stage", "message"} 딕셔너리를 순서대로 쌓는다(PROJECT-SPEC
    # 4장). 모델의 내부 사고가 아니라 "어느 노드가 실행됐는지"만 담는다.
    trace: Annotated[list[dict], operator.add]
