"""병렬 조사와 결과 통합(merge_investigations 리듀서)."""

from __future__ import annotations

from docagent.multiagent.graph_parallel import build_parallel_graph, initial_parallel_state
from docagent.multiagent.state import merge_investigations


def test_parallel_investigation_merges_both_products():
    graph = build_parallel_graph()
    state = initial_parallel_state("노트북이랑 모니터 매출 비교해줘")
    result = graph.invoke(state)

    assert set(result["investigations"].keys()) == {"노트북", "모니터"}
    assert result["finish_reason"] == "stop"
    assert "노트북" in result["final_text"]
    assert "모니터" in result["final_text"]


def test_merge_investigations_reducer_keeps_both_keys():
    left = {"노트북": {"row_count": 12}}
    right = {"모니터": {"row_count": 8}}
    merged = merge_investigations(left, right)
    assert merged == {"노트북": {"row_count": 12}, "모니터": {"row_count": 8}}


def test_merge_investigations_without_reducer_would_lose_data():
    """리듀서 없이 마지막 값이 이기는 기본 동작이면 무엇이 사라지는지 보여준다."""

    left = {"노트북": {"row_count": 12}}
    right = {"모니터": {"row_count": 8}}
    last_write_wins = right  # 기본 리듀서라면 이 값만 남는다
    assert "노트북" not in last_write_wins
    assert merge_investigations(left, right) != last_write_wins
