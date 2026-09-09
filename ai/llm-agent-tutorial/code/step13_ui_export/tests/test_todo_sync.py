"""계획(Todo) id 안정화·상태 동기화 단위 테스트.

12단계 ``bridge.py``의 ``TodoIdAssigner``를 그대로 옮긴 로직이므로, 이 파일도
12단계 ``tests/test_bridge_todo_diff.py``와 같은 시나리오를 확인한다: 계획이
실행 중에 바뀌어도 같은 제목은 같은 id를 유지하고, 사라진 항목은 다음 목록에서
빠지고, 실패는 애플리케이션이 관측해서만 표시된다.
"""

from __future__ import annotations

from docagent.todo import TodoIdAssigner, map_todo_state


def test_map_todo_state_translates_write_plan_status():
    assert map_todo_state("pending") == "pending"
    assert map_todo_state("in_progress") == "running"
    assert map_todo_state("completed") == "done"


def test_unknown_status_falls_back_to_pending():
    assert map_todo_state("some_future_status") == "pending"


def test_same_title_keeps_same_id_across_status_changes():
    assigner = TodoIdAssigner()
    step1 = assigner.items_for([{"title": "매출 조회", "status": "pending"}])
    step2 = assigner.items_for([{"title": "매출 조회", "status": "in_progress"}])
    step3 = assigner.items_for([{"title": "매출 조회", "status": "completed"}])

    assert step1[0]["id"] == step2[0]["id"] == step3[0]["id"]
    assert step1[0]["state"] == "pending"
    assert step2[0]["state"] == "running"
    assert step3[0]["state"] == "done"


def test_plan_change_assigns_new_id_and_drops_removed_item():
    assigner = TodoIdAssigner()
    first = assigner.items_for(
        [{"title": "매출 조회", "status": "in_progress"}, {"title": "문서 검색", "status": "pending"}]
    )
    id_a, id_b = first[0]["id"], first[1]["id"]

    # 계획이 바뀐다: "매출 조회"는 끝났고, "문서 검색" 대신 "결과 검증"이 새로 생겼다.
    second = assigner.items_for([{"title": "매출 조회", "status": "completed"}, {"title": "결과 검증", "status": "pending"}])

    assert second[0]["id"] == id_a  # 살아남은 항목은 id 유지
    assert second[1]["id"] not in (id_a, id_b)  # 새 항목은 새 id
    titles = [it["title"] for it in second]
    assert "문서 검색" not in titles  # 사라진 항목은 더 이상 보이지 않는다


def test_mark_running_failed_only_affects_currently_running_item():
    assigner = TodoIdAssigner()
    assigner.items_for([{"title": "매출 조회", "status": "in_progress"}, {"title": "문서 검색", "status": "pending"}])
    assigner.mark_running_failed()  # 이 시점에 running인 "매출 조회"만 실패로 기록된다

    items = assigner.items_for([{"title": "매출 조회", "status": "in_progress"}, {"title": "문서 검색", "status": "in_progress"}])
    by_title = {it["title"]: it["state"] for it in items}
    assert by_title["매출 조회"] == "failed"
    assert by_title["문서 검색"] == "running"  # 실패 표시는 관측된 항목에만 붙는다


def test_retry_after_failure_clears_the_failed_mark():
    assigner = TodoIdAssigner()
    assigner.items_for([{"title": "매출 조회", "status": "in_progress"}])
    assigner.mark_running_failed()
    failed_items = assigner.items_for([{"title": "매출 조회", "status": "in_progress"}])
    assert failed_items[0]["state"] == "failed"

    # 모델이 다시 시도해 completed로 바꾸면 실패 표시가 풀린다.
    recovered = assigner.items_for([{"title": "매출 조회", "status": "completed"}])
    assert recovered[0]["state"] == "done"


def test_state_round_trips_through_to_state_and_from_state():
    assigner = TodoIdAssigner()
    assigner.items_for([{"title": "매출 조회", "status": "in_progress"}])
    assigner.mark_running_failed()
    snapshot = assigner.to_state()

    restored = TodoIdAssigner.from_state(snapshot)
    items = restored.items_for([{"title": "매출 조회", "status": "in_progress"}])
    assert items[0]["state"] == "failed"  # 재개(resume) 이후에도 실패 표시가 유지된다
