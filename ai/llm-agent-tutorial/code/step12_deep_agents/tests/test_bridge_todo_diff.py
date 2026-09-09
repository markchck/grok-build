"""docagent.bridge의 상태 매핑·id 안정성 단위 테스트(모델·에이전트 불필요)."""

from docagent.bridge import TodoIdAssigner, is_error_result, map_todo_state, stage_for_tool


def test_map_todo_state_known_values():
    assert map_todo_state("pending") == "pending"
    assert map_todo_state("in_progress") == "running"
    assert map_todo_state("completed") == "done"


def test_map_todo_state_unknown_falls_back_to_pending():
    # langchain이 앞으로 상태를 추가해도 "완료"나 "실패"로 함부로 단정하지 않는다.
    assert map_todo_state("blocked") == "pending"


def test_stage_for_tool_known_and_unknown():
    assert stage_for_tool("write_todos") == "planning"
    assert stage_for_tool("task") == "analyzing"
    assert stage_for_tool("read_file") == "retrieving"
    assert stage_for_tool("write_file") == "writing"
    assert stage_for_tool("이상한_도구") == "analyzing"


def test_is_error_result():
    assert is_error_result("Error invoking tool 'ls' ...") is True
    assert is_error_result("Updated file /results/x.md") is False


def test_todo_id_assigner_keeps_stable_id_across_status_changes():
    assigner = TodoIdAssigner()
    first = assigner.items_for([{"content": "A", "status": "pending"}, {"content": "B", "status": "pending"}])
    assert first == [
        {"id": "t1", "title": "A", "state": "pending"},
        {"id": "t2", "title": "B", "state": "pending"},
    ]

    second = assigner.items_for([{"content": "A", "status": "in_progress"}, {"content": "B", "status": "pending"}])
    assert second[0]["id"] == "t1"  # 같은 content는 같은 id를 유지한다
    assert second[0]["state"] == "running"


def test_todo_id_assigner_gives_new_id_when_plan_changes():
    assigner = TodoIdAssigner()
    assigner.items_for([{"content": "A", "status": "pending"}])
    revised = assigner.items_for(
        [
            {"content": "A", "status": "completed"},
            {"content": "C(새로 추가된 항목)", "status": "pending"},
        ]
    )
    assert revised[0]["id"] == "t1"
    assert revised[1]["id"] == "t2"  # 새 content는 새 id
    assert revised[1]["state"] == "pending"


def test_todo_id_assigner_dropped_item_disappears_from_next_snapshot():
    assigner = TodoIdAssigner()
    assigner.items_for([{"content": "A", "status": "pending"}, {"content": "B", "status": "pending"}])
    only_a = assigner.items_for([{"content": "A", "status": "completed"}])
    assert [item["title"] for item in only_a] == ["A"]
