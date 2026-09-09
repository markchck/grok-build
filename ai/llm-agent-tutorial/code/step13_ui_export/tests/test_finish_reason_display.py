"""finish_reason -> 화면 표시 결정을 파이썬으로도 검증한다.

실제 표시 로직은 ``static/app.js``의 ``FINISH_MESSAGES``/``renderFinish``에
있다(브라우저에서만 실행된다). 이 파일은 그 매핑표를 파이썬으로 그대로
옮겨 "8개 finish_reason 전부에 화면 문구가 있는가", "부분 결과가 있을 때
csv_available이 있으면 다운로드 버튼을 보여줘야 하는가"를 pytest로도
고정한다. app.js와 이 파일의 매핑이 벌어지면(문구를 한쪽만 고치면) 사람이
알아채기 어려우므로, 정확히는 "app.js가 이 목록과 같은 8개 키를 다룬다"는
것은 이 테스트가 보장하지 못한다 — 그 대응은 이 장 5절에서 실제 서버 응답과
화면 동작을 나란히 비교해 수동으로 확인했다.
"""

from __future__ import annotations

from docagent.events import FinishReason

FINISH_REASON_VALUES = list(FinishReason.__args__)

FINISH_MESSAGES = {
    "stop": "ok",
    "max_steps": "warn",
    "timeout": "warn",
    "repeated_tool_call": "warn",
    "no_progress": "warn",
    "rejected_by_user": "bad",
    "cancelled": "bad",
    "token_budget": "warn",
}


def test_every_finish_reason_has_a_display_class():
    assert set(FINISH_REASON_VALUES) == set(FINISH_MESSAGES.keys())


def test_display_class_is_one_of_three_known_values():
    assert set(FINISH_MESSAGES.values()) <= {"ok", "warn", "bad"}


def should_show_csv_button(partial: dict | None) -> bool:
    """app.js의 ``renderFinish``가 CSV 버튼을 보여주는 조건을 그대로 옮긴 것이다."""

    return bool(partial and partial.get("csv_available"))


def test_csv_button_shown_only_when_table_exists():
    assert should_show_csv_button({"csv_available": True}) is True
    assert should_show_csv_button({"csv_available": False}) is False
    assert should_show_csv_button(None) is False
    assert should_show_csv_button({}) is False
