"""협업 전체 단위의 예산 검사.

PROJECT-SPEC.md 7절의 한도(최대 단계 수, 누적 토큰 예산)를 "에이전트 하나의
run"이 아니라 "협업 전체"에 적용하도록 확장한다. 함수 하나를 여러 곳(슈퍼바이저
그래프, 핸드오프 그래프)에서 공유해서 쓴다 — 두 그래프가 서로 다른 한도 검사
로직을 갖게 되면 한쪽만 고치고 잊어버리는 사고가 나기 쉽다.
"""

from __future__ import annotations

from docagent.config import Settings
from docagent.multiagent.state import CollabState


def check_budget(state: CollabState, settings: Settings) -> str | None:
    """한도를 넘었으면 그 사유(``finish_reason`` 값)를, 아니면 ``None``을 준다.

    호출부는 이 값이 ``None``이 아니면 더 진행하지 않고 지금까지의 상태로
    마무리한다(8단계 2-6절과 같은 원칙: ``error``가 아니라 ``done`` +
    ``finish_reason``으로 끝낸다).
    """

    if state["hops_used"] > settings.max_agent_steps:
        return "max_steps"
    if state["tokens_used"] > settings.max_collab_tokens:
        return "token_budget"
    return None


def add_usage(state: CollabState, tokens: int, estimated: bool) -> dict:
    """이번 호출의 토큰 사용량을 누적한 부분 상태 업데이트를 만든다.

    ``tokens_used``는 리듀서가 없는 필드(마지막 값이 이긴다)라서 호출부가
    "이전 값 + 이번 값"을 직접 계산해서 돌려줘야 한다.
    """

    return {
        "tokens_used": state["tokens_used"] + tokens,
        "tokens_estimated": state["tokens_estimated"] or estimated,
    }
