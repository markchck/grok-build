"""협업 상태 타입.

7단계(``docagent/graph/state.py``)와 같은 개념(State는 채널의 모음, 리듀서로
병합 방식을 정한다)을 멀티에이전트 협업에 맞게 확장한다. 이 장에서 새로
다뤄야 하는 것은 **"공유 상태"와 "에이전트별 컨텍스트"를 분리**하는 것이다
(docs/11-multi-agent.md 2-3절).

- ``shared_notes``, ``investigation``, ``analysis``, ``verification`` — 모든
  에이전트와 슈퍼바이저가 읽을 수 있는 **공유 상태**(블랙보드). 한 에이전트가
  쓴 결과를 다른 에이전트가 그대로 읽는다.
- ``investigator_context``, ``analyst_context``, ``verifier_context`` — 그
  에이전트 **자신만** 쓰고 읽는 메시지 이력. 조사 에이전트가 데이터를 몇 번
  다시 조회하며 시행착오를 겪었는지는 분석 에이전트가 알 필요도, 봐야 할
  이유도 없다 — 컨텍스트를 나누지 않으면 모든 에이전트의 프롬프트가 매 라운드
  다른 에이전트의 시행착오까지 전부 포함하게 되어 토큰 비용이 에이전트 수만큼
  누적된다.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class CollabState(TypedDict):
    # 입력
    request: str  # 사용자 원 요청 (예: "노트북 매출이 왜 늘었는지 조사해줘")

    # --- 공유 상태(블랙보드): 누구나 읽고, 담당 에이전트만 쓴다 --------------
    investigation: dict[str, Any]  # investigator가 채운다: 찾은 원본 행 요약
    analysis: dict[str, Any]  # analyst가 채운다: 분기별 비교와 해석
    verification: dict[str, Any]  # verifier가 채운다: 재계산 결과와 판정

    shared_notes: Annotated[list[str], operator.add]  # 짧은 진행 메모(사람이 읽기 위한 것)

    # --- 에이전트별 컨텍스트: 그 에이전트만 쓰고 읽는다 ----------------------
    investigator_context: Annotated[list[dict[str, Any]], operator.add]
    analyst_context: Annotated[list[dict[str, Any]], operator.add]
    verifier_context: Annotated[list[dict[str, Any]], operator.add]

    # --- 제어 흐름 ------------------------------------------------------------
    next_agent: str  # 슈퍼바이저가 정한 다음 에이전트 이름("investigator"|"analyst"|"verifier"|"finish")
    route_source: str  # 그 판단을 누가 했는지: "model" | "heuristic"
    handoff_chain: Annotated[list[str], operator.add]  # 핸드오프 패턴에서 방문한 에이전트 순서

    # --- 실행 사실 로그(모델의 사고가 아니라 애플리케이션이 관측한 사실) -----
    trace: Annotated[list[dict[str, str]], operator.add]

    # --- 예산(협업 전체 단위, PROJECT-SPEC.md 7절을 이 장에서 확장) ----------
    hops_used: int  # 그래프에서 실행된 노드 수(슈퍼바이저 방문 포함)
    tokens_used: int  # 협업 전체 누적 토큰(추정 포함)
    tokens_estimated: bool

    # --- 결과 ------------------------------------------------------------------
    final_text: str
    finish_reason: str | None
