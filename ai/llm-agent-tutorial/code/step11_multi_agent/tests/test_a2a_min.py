"""A2A 최소 흉내 서버·클라이언트.

``TestClient``로 FastAPI 앱을 직접 호출한다(실제 uvicorn 프로세스를 띄우지
않는다) — 이 테스트는 "카드 조회 -> 작업 제출 -> 폴링"이라는 흐름 자체와
판정 로직만 검증한다. 별도 프로세스로 실제 HTTP를 태우는 검증은 5절(실행과
결과 확인)에서 실제로 두 프로세스를 띄워 수행한다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from docagent.a2a_min.server import app


def test_agent_card_exposes_skill_and_disclaims_full_protocol():
    client = TestClient(app)
    resp = client.get("/agent-card")
    assert resp.status_code == 200
    card = resp.json()
    assert card["skills"][0]["id"] == "verify_quarterly_analysis"
    assert "흉내" in card["note"]  # 실제 A2A 준수를 주장하지 않는다는 사실을 카드에도 남겼다


def test_submit_and_poll_task_matches():
    client = TestClient(app)
    payload = {
        "skill_id": "verify_quarterly_analysis",
        "input": {
            "analysis": {"q1": {"units": 10, "revenue": 100}, "q2": {"units": 20, "revenue": 200}},
            "rows": [
                {"date": "2026-01-05", "units": 10, "revenue": 100},
                {"date": "2026-04-05", "units": 20, "revenue": 200},
            ],
        },
    }
    submit_resp = client.post("/tasks", json=payload)
    assert submit_resp.status_code == 200
    task_id = submit_resp.json()["task_id"]

    poll_resp = client.get(f"/tasks/{task_id}")
    assert poll_resp.status_code == 200
    body = poll_resp.json()
    assert body["status"] == "completed"
    assert body["result"]["ok"] is True


def test_submit_and_poll_task_mismatch():
    client = TestClient(app)
    payload = {
        "skill_id": "verify_quarterly_analysis",
        "input": {
            "analysis": {"q1": {"units": 999, "revenue": 999}, "q2": {"units": 20, "revenue": 200}},
            "rows": [{"date": "2026-01-05", "units": 10, "revenue": 100}],
        },
    }
    submit_resp = client.post("/tasks", json=payload)
    task_id = submit_resp.json()["task_id"]
    body = client.get(f"/tasks/{task_id}").json()
    assert body["result"]["ok"] is False


def test_unknown_skill_is_rejected():
    client = TestClient(app)
    resp = client.post("/tasks", json={"skill_id": "does_not_exist", "input": {}})
    assert resp.status_code == 400


def test_unknown_task_id_is_404():
    client = TestClient(app)
    resp = client.get("/tasks/no_such_task")
    assert resp.status_code == 404
