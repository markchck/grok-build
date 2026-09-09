"""compare_runs.compare()/format_report()를 저장된 결과와 비슷한 딕셔너리로 검증한다."""

from __future__ import annotations

from eval.compare_runs import compare, format_report


def _fake_result(config_name: str, top_k: int, rows: list[tuple[str, str, float, float]]) -> dict:
    return {
        "config_name": config_name,
        "top_k": top_k,
        "mean_recall": sum(r[2] for r in rows) / len(rows),
        "mean_mrr": sum(r[3] for r in rows) / len(rows),
        "hit_rate": sum(1 for r in rows if r[2] > 0) / len(rows),
        "per_question": [
            {"question_id": qid, "question": q, "recall": recall, "mrr": mrr}
            for qid, q, recall, mrr in rows
        ],
    }


def test_compare_detects_improvement():
    before = _fake_result("sparse_k1", 1, [("q1", "질문1", 0.0, 0.0), ("q2", "질문2", 1.0, 1.0)])
    after = _fake_result("sparse_k3", 3, [("q1", "질문1", 1.0, 0.5), ("q2", "질문2", 1.0, 1.0)])

    deltas = compare(before, after)

    assert len(deltas) == 2
    q1_delta = next(d for d in deltas if d.question_id == "q1")
    assert q1_delta.recall_delta == 1.0
    assert q1_delta.mrr_delta == 0.5
    assert q1_delta.changed is True

    q2_delta = next(d for d in deltas if d.question_id == "q2")
    assert q2_delta.changed is False


def test_compare_ignores_questions_missing_in_either_side():
    before = _fake_result("a", 1, [("q1", "질문1", 1.0, 1.0)])
    after = _fake_result("b", 1, [("q2", "질문2", 1.0, 1.0)])

    deltas = compare(before, after)
    assert deltas == []


def test_format_report_contains_summary_numbers():
    before = _fake_result("sparse_k1", 1, [("q1", "질문1", 0.0, 0.0)])
    after = _fake_result("sparse_k3", 3, [("q1", "질문1", 1.0, 1.0)])
    deltas = compare(before, after)

    report = format_report(before, after, deltas)

    assert "sparse_k1" in report
    assert "sparse_k3" in report
    assert "0.000 -> 1.000" in report
    assert "q1" in report
