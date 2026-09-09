"""승인/거절/수정 흐름과 중복 실행 방지(멱등성) 단위 테스트.

전부 모델 서버 없이(가짜 chat_fn) SQLite 파일 하나로 확인한다. "프로세스가
죽어도 상태가 살아남는다"는 각 테스트에서 ``store.connect()``를 다시 호출해
새 커넥션(=새 프로세스가 파일을 다시 여는 것과 같은 모양)을 만드는 방식으로
재현한다.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from docagent import store, tools
from docagent.agent import advance_run, start_run
from docagent.llm import ChatResult


def _tool_call_message(call_id: str, name: str, arguments_json: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments_json}}
        ],
    }


def _final_message(text: str) -> dict:
    return {"role": "assistant", "content": text, "tool_calls": None}


def _events_by_kind(events, kind):
    return [e for e in events if e.kind == kind]


@pytest.fixture()
def db_path():
    with tempfile.TemporaryDirectory() as tmp:
        yield str(Path(tmp) / "hitl_state.db")


@pytest.fixture(autouse=True)
def _reset_archived_reports():
    tools.ARCHIVED_REPORTS.clear()
    yield
    tools.ARCHIVED_REPORTS.clear()


def _to_chat_result(msg: dict, usage: dict) -> ChatResult:
    return ChatResult(
        text=msg.get("content") or "",
        finish_reason=None,
        tool_calls=msg.get("tool_calls") or [],
        raw={"usage": usage},
    )


def _archive_chat_fn(calls):
    """첫 호출에서 archive_report를 부르고, 도구 결과가 들어오면 최종 답을 낸다."""

    def fake_chat(messages, tools=None, temperature=0.2, settings=None):
        calls["n"] += 1
        already_ran = any(m.get("role") == "tool" for m in messages)
        if already_ran:
            msg = _final_message("처리를 마쳤다.")
        else:
            msg = _tool_call_message(
                "call_1", "archive_report", '{"report_id": "notebook-q1-report", "reason": "분기 종료"}'
            )
        return _to_chat_result(msg, {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})

    return fake_chat


def test_approval_request_pauses_the_run_without_a_done_event(db_path):
    conn = store.connect(db_path)
    run_id = start_run(conn, "notebook-q1-report를 보관해줘")
    evs = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 0})))

    approvals = _events_by_kind(evs, "approval_request")
    assert len(approvals) == 1
    assert approvals[0].data["action"] == "archive_report"

    # 승인 대기 중에는 done 이벤트를 내보내지 않는다 — 아직 끝난 게 아니라 멈춘 것이다.
    assert _events_by_kind(evs, "done") == []

    run = store.load_run(conn, run_id)
    assert run.status == "paused_approval"
    assert run.pending_approval_id == approvals[0].data["id"]
    assert tools.ARCHIVED_REPORTS == []  # 아직 실행되지 않았다


def test_approve_then_resume_executes_exactly_once(db_path):
    conn = store.connect(db_path)
    run_id = start_run(conn, "notebook-q1-report를 보관해줘")
    evs = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 0})))
    approval_id = _events_by_kind(evs, "approval_request")[0].data["id"]

    approval, already = store.decide_approval(conn, approval_id, status="approved", decision_args=None, mode=None)
    assert already is False

    evs2 = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 1})))
    done = _events_by_kind(evs2, "done")
    assert done[0].data["finish_reason"] == "stop"
    assert tools.ARCHIVED_REPORTS == ["notebook-q1-report"]  # 정확히 한 번 실행됐다


def test_double_click_on_decision_is_idempotent(db_path):
    """같은 승인 요청을 두 번 결정해도(더블 클릭) 두 번째는 아무것도 바꾸지 않는다."""

    conn = store.connect(db_path)
    run_id = start_run(conn, "notebook-q1-report를 보관해줘")
    evs = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 0})))
    approval_id = _events_by_kind(evs, "approval_request")[0].data["id"]

    _, first_already = store.decide_approval(
        conn, approval_id, status="approved", decision_args=None, mode=None
    )
    # 이번에는 실수로 "거절"을 다시 보냈다고 가정한다 — 그래도 반영되지 않아야 한다.
    second, second_already = store.decide_approval(
        conn, approval_id, status="rejected", decision_args={"note": "취소"}, mode="abort"
    )

    assert first_already is False
    assert second_already is True
    assert second["status"] == "approved"  # 두 번째 호출로 바뀌지 않았다


def test_resuming_a_completed_step_does_not_re_execute_the_tool(db_path):
    """멱등성 키 재사용: 승인 후 실행된 도구 호출을 다시 재개해도 재실행하지 않는다.

    resume을 두 번 부르는 상황(클라이언트 재시도, 네트워크 중복 등)을 흉내 낸다.
    """

    conn = store.connect(db_path)
    run_id = start_run(conn, "notebook-q1-report를 보관해줘")
    evs = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 0})))
    approval_id = _events_by_kind(evs, "approval_request")[0].data["id"]
    store.decide_approval(conn, approval_id, status="approved", decision_args=None, mode=None)

    # 1차 재개: 실제로 실행되고 끝난다.
    evs2 = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 1})))
    assert _events_by_kind(evs2, "done")[0].data["finish_reason"] == "stop"
    assert tools.ARCHIVED_REPORTS == ["notebook-q1-report"]

    # 2차 재개: run은 이미 done이므로 advance_run은 저장된 done을 그대로 돌려줄 뿐
    # 아무것도 다시 실행하지 않는다.
    evs3 = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 2})))
    assert _events_by_kind(evs3, "done")[0].data["finish_reason"] == "stop"
    assert tools.ARCHIVED_REPORTS == ["notebook-q1-report"]  # 여전히 한 번뿐


def test_reject_in_abort_mode_stops_the_run(db_path):
    conn = store.connect(db_path)
    run_id = start_run(conn, "notebook-q1-report를 보관해줘")
    evs = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 0})))
    approval_id = _events_by_kind(evs, "approval_request")[0].data["id"]

    store.decide_approval(
        conn, approval_id, status="rejected", decision_args={"note": "이 리포트는 아직 필요하다"}, mode="abort"
    )
    evs2 = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 1})))

    done = _events_by_kind(evs2, "done")
    assert done[0].data["finish_reason"] == "rejected_by_user"
    assert tools.ARCHIVED_REPORTS == []


def test_reject_in_alternative_mode_lets_the_model_continue(db_path):
    conn = store.connect(db_path)
    run_id = start_run(conn, "notebook-q1-report를 보관해줘")
    evs = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 0})))
    approval_id = _events_by_kind(evs, "approval_request")[0].data["id"]

    store.decide_approval(
        conn, approval_id, status="rejected", decision_args={"note": "대신 이유만 알려줘"}, mode="alternative"
    )
    evs2 = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 1})))

    done = _events_by_kind(evs2, "done")
    # 거절됐지만 실행이 중단되지 않고 모델이 최종 답으로 이어갔다.
    assert done[0].data["finish_reason"] == "stop"
    assert tools.ARCHIVED_REPORTS == []


def test_edit_runs_with_the_corrected_arguments(db_path):
    conn = store.connect(db_path)
    run_id = start_run(conn, "notebook-q1-report를 보관해줘")
    evs = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 0})))
    approval_id = _events_by_kind(evs, "approval_request")[0].data["id"]

    store.decide_approval(
        conn,
        approval_id,
        status="edited",
        decision_args={"report_id": "keyboard-q2-report", "reason": "정정된 리포트 ID로 보관"},
        mode=None,
    )
    evs2 = list(advance_run(conn, run_id, chat_fn=_archive_chat_fn({"n": 1})))

    tool_results = _events_by_kind(evs2, "tool_result")
    assert "keyboard-q2-report" in tool_results[0].data["summary"]
    assert tools.ARCHIVED_REPORTS == ["keyboard-q2-report"]  # 원래 인자가 아니라 수정된 인자로 실행됐다


def test_state_survives_reopening_the_db_file(db_path):
    """프로세스가 죽었다 살아나는 것을 '새 커넥션으로 같은 파일을 다시 여는 것'으로 흉내 낸다."""

    conn1 = store.connect(db_path)
    run_id = start_run(conn1, "notebook-q1-report를 보관해줘")
    evs = list(advance_run(conn1, run_id, chat_fn=_archive_chat_fn({"n": 0})))
    approval_id = _events_by_kind(evs, "approval_request")[0].data["id"]
    del conn1  # 이 프로세스의 커넥션을 놓아버린다(프로세스 종료를 흉내)

    # "재시작한 프로세스"가 같은 파일을 새로 연다.
    conn2 = store.connect(db_path)
    run_after_restart = store.load_run(conn2, run_id)
    assert run_after_restart is not None
    assert run_after_restart.status == "paused_approval"
    assert run_after_restart.pending_approval_id == approval_id

    store.decide_approval(conn2, approval_id, status="approved", decision_args=None, mode=None)
    evs2 = list(advance_run(conn2, run_id, chat_fn=_archive_chat_fn({"n": 1})))
    assert _events_by_kind(evs2, "done")[0].data["finish_reason"] == "stop"
    assert tools.ARCHIVED_REPORTS == ["notebook-q1-report"]
