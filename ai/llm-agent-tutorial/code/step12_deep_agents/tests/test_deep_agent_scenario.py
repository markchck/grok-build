"""scripts/demo_deep_agent_run.py의 결정적 시나리오를 pytest로도 검증한다.

여기서 확인하는 것(이 장의 "완료 기준" 그대로):
- Skill이 실제로 적용된다(스크립트가 실제로 실행돼 results/ 아래 파일이 생긴다).
- 계획이 바뀐다(todo 이벤트가 3번 나가고, 두 번째 이벤트에 새 항목이 추가돼 있다).
- 중간 결과가 파일로 저장되고, 이후 단계가 그 파일을 다시 읽는다.
- 위임 내역(task 도구 호출 2건)이 tool_call 이벤트로 남는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from docagent.bridge import stream_agent_events
from docagent.deep_agent import build_agent
from docagent.fake_model import ScriptedChatModel
from demo_deep_agent_run import build_scenario


def test_deterministic_scenario_end_to_end(tmp_path):
    project_root = Path(__file__).resolve().parent.parent
    # results/를 실제 프로젝트 폴더가 아니라 임시 디렉터리에 쓰도록 워크디렉터리를
    # 통째로 복사하지는 않는다 — skills/와 data/는 읽기만 하므로 그대로 원본을
    # 가리키고, results/만 실제로 남는다(정리는 아래에서 한다).
    model = ScriptedChatModel(responses=build_scenario())
    agent = build_agent(model=model, workdir=project_root)

    events = list(stream_agent_events(agent, "제품별 매출 변화를 분석하고 원인을 문서에서 찾아줘"))
    kinds = [name for name, _ in events]

    assert kinds.count("todo") == 3
    assert kinds.count("tool_call") >= 5  # write_todos x3 + task x2 + read_file x2 이상

    todo_snapshots = [data["items"] for name, data in events if name == "todo"]
    first_titles = {item["title"] for item in todo_snapshots[0]}
    second_titles = {item["title"] for item in todo_snapshots[1]}
    # 계획 수정 지점: 두 번째 스냅샷에 첫 스냅샷에 없던 항목이 추가돼 있다.
    assert second_titles - first_titles == {"노트북 2분기 감소 원인 문서에서 확인"}

    # id는 content가 같으면 유지된다(재발급되지 않는다).
    id_by_title_1 = {item["title"]: item["id"] for item in todo_snapshots[0]}
    id_by_title_2 = {item["title"]: item["id"] for item in todo_snapshots[1]}
    for title in first_titles:
        assert id_by_title_1[title] == id_by_title_2[title]

    last_snapshot = todo_snapshots[-1]
    assert all(item["state"] in ("done", "running") for item in last_snapshot)

    task_calls = [data for name, data in events if name == "tool_call" and data["name"] == "task"]
    assert {c["args"]["subagent_type"] for c in task_calls} == {"csv-analyst", "doc-researcher"}

    done_data = next(data for name, data in events if name == "done")
    assert "4.1%" in done_data["partial"]["text"]
    assert "물류" in done_data["partial"]["text"]

    csv_result = project_root / "results" / "csv_summary.md"
    doc_result = project_root / "results" / "doc_findings.md"
    assert csv_result.exists()
    assert doc_result.exists()
    assert "노트북" in csv_result.read_text(encoding="utf-8")
    assert "notebook-q1-report#p1" in doc_result.read_text(encoding="utf-8")

    # 정리: 이 테스트가 남긴 파일을 지운다(프로젝트 데이터 폴더를 어지르지 않는다).
    csv_result.unlink(missing_ok=True)
    doc_result.unlink(missing_ok=True)
