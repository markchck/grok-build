"""Skill(12단계 skills/)이 이 장의 에이전트 루프에 실제로 연결되는지 확인한다.

``docagent/skills_runtime.py``는 실제 스크립트(``skills/csv-analysis/
analyze_sales.py``, ``skills/doc-research/find_evidence.py``, 12단계와 완전히
동일한 파일)를 subprocess로 그대로 돌린다 — 여기서도 목이 아니다. Tool
(``docagent/tools.py``)과 달리 pydantic 인자 모델이 아니라 스크립트 실행
계약(표준출력 마지막 줄 JSON, ``--out`` 파일)을 그대로 따른다는 점을 확인한다.
"""

from __future__ import annotations

import json

from docagent import skills_runtime, store
from docagent.agent import advance_run, start_run
from docagent.config import load_settings


def test_csv_analysis_skill_runs_real_script_and_writes_result_file():
    output = skills_runtime.run_csv_analysis_skill()
    assert output["row_count"] > 0
    assert "by_product" in output
    result_path = skills_runtime.BASE_DIR / output["result_file"]
    assert result_path.exists()
    assert "CSV 분석 결과" in result_path.read_text(encoding="utf-8")


def test_doc_research_skill_runs_real_script_and_finds_evidence():
    output = skills_runtime.run_doc_research_skill(["노트북"])
    assert output["result_file"]
    assert "노트북" in output["markdown"] or "찾지 못했다" in output["markdown"]


def test_doc_research_skill_empty_keywords_raises():
    import pytest

    with pytest.raises(skills_runtime.SkillRunError):
        skills_runtime.run_doc_research_skill([])


# ---------------------------------------------------------------------------
# agent.py 배선: Skill 도구가 목록에 더해지고, source="skill"로 표시되는지
# ---------------------------------------------------------------------------


def _tool_call_message(call_id: str, name: str, arguments_json: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments_json}}],
    }


def _final_message(text: str) -> dict:
    return {"role": "assistant", "content": text, "tool_calls": None}


def test_select_tools_includes_skill_tools_when_requested():
    from docagent import agent

    specs = agent._select_tools(None, include_skills=True, settings=load_settings())
    names = {s["function"]["name"] for s in specs}
    assert {"csv_analysis_skill", "doc_research_skill"} <= names
    # Tool과 공존한다.
    assert "sum_sales" in names


def test_select_tools_without_include_skills_has_no_skill_tools():
    from docagent import agent

    specs = agent._select_tools(None, include_skills=False, settings=load_settings())
    names = {s["function"]["name"] for s in specs}
    assert "csv_analysis_skill" not in names
    assert "doc_research_skill" not in names


def test_agent_loop_routes_csv_analysis_skill_and_marks_source():
    from docagent import agent

    calls = {"n": 0}

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "message": _tool_call_message("call_1", "csv_analysis_skill", "{}"),
                "usage": {},
            }
        return {"message": _final_message("집계를 완료했다."), "usage": {}}

    conn = store.connect(":memory:")
    run_id = start_run(conn, "매출을 스킬로 분석해줘")
    events = list(
        advance_run(conn, run_id, chat_fn=fake_chat, include_skills=True, settings=load_settings())
    )

    tool_calls = [e for e in events if e.kind == "tool_call"]
    assert tool_calls[0].data["name"] == "csv_analysis_skill"
    assert tool_calls[0].data["source"] == "skill"

    tool_results = [e for e in events if e.kind == "tool_result"]
    assert tool_results[0].data["ok"] is True

    done = [e for e in events if e.kind == "done"][0]
    assert done.data["finish_reason"] == "stop"


def test_agent_loop_doc_research_skill_no_keywords_fails_gracefully():
    from docagent import agent

    calls = {"n": 0}

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "message": _tool_call_message("call_1", "doc_research_skill", '{"keywords": []}'),
                "usage": {},
            }
        return {"message": _final_message("근거를 찾지 못했다."), "usage": {}}

    conn = store.connect(":memory:")
    run_id = start_run(conn, "문서에서 근거를 찾아줘")
    events = list(
        advance_run(conn, run_id, chat_fn=fake_chat, include_skills=True, settings=load_settings())
    )

    tool_results = [e for e in events if e.kind == "tool_result"]
    assert tool_results[0].data["ok"] is False

    todo_events = [e for e in events if e.kind == "todo"]
    # write_plan을 부르지 않았으므로 todo 이벤트는 없다(변경된 계획이 없다) —
    # mark_running_failed는 running 항목이 없으면 아무 것도 표시하지 않는다.
    assert todo_events == [] or all(item["state"] != "failed" for e in todo_events for item in e.data["items"])
