"""모델 서버 없이, 결정적 시나리오로 Deep Agents 실행 전체를 보여준다.

이 스크립트는 8단계 ``scripts/demo_infinite_loop.py``와 같은 목적을 가진다
— 스텁 서버(``code/_tools/fake_openai_server.py``)로는 재현할 수 없는
여러 라운드짜리 흐름을, 미리 정해둔 ``ScriptedChatModel`` 응답으로 실제
``deepagents`` 그래프에 흘려보내 확인한다.

시나리오(모델이 실제로 몇 번 불리는지, 그 순서 그대로):

 1. 메인 에이전트: write_todos로 3단계 계획을 세운다.
 2. 메인 에이전트: task로 csv-analyst에게 매출 집계를 위임한다.
 3.   └ csv-analyst: execute로 analyze_sales.py를 돌린다(실제 스크립트 실행).
 4.   └ csv-analyst: 결과를 요약해 위임을 마친다.
 5. 메인 에이전트: **집계 결과(노트북 매출이 2분기에 줄었다)를 보고 계획을
    수정한다** — "부산 원인도 확인" 항목을 새로 추가한다. 이것이 이 장이
    보여주려는 "계획이 바뀐다"의 실제 지점이다.
 6. 메인 에이전트: task로 doc-researcher에게 원인 조사를 위임한다.
 7.   └ doc-researcher: execute로 find_evidence.py를 돌린다(실제 스크립트 실행).
 8.   └ doc-researcher: 근거를 요약해 위임을 마친다.
 9. 메인 에이전트: write_todos로 남은 항목을 모두 done으로 갱신한다.
10. 메인 에이전트: read_file로 csv 결과 파일을 다시 읽는다(재계산하지 않는다).
11. 메인 에이전트: read_file로 문서 조사 결과 파일을 다시 읽는다.
12. 메인 에이전트: 두 파일의 내용을 근거로 최종 답을 문장으로 만든다.

모델은 사용자 질문을 읽지 않는다 — 이 스크립트가 검증하는 것은 "이런
순서의 도구 호출이 오면 Deep Agents가 계획 갱신·위임·파일 저장/재읽기를
실제로 수행하는가"이지 "모델이 스스로 이 순서를 골랐는가"가 아니다. 후자는
실제 모델 서버가 있어야 확인할 수 있다 — 이 장 하단 "검증 상태"에 이 한계를
그대로 남긴다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import AIMessage

from docagent.bridge import stream_agent_events
from docagent.deep_agent import build_agent
from docagent.events import (
    done_event,
    status_event,
    todo_event,
    tool_call_event,
    tool_result_event,
)
from docagent.fake_model import ScriptedChatModel

RENDER = {
    "status": lambda d: status_event(d["stage"], d["message"]),
    "tool_call": lambda d: tool_call_event(d["id"], d["name"], d["args"]),
    "tool_result": lambda d: tool_result_event(d["id"], d["ok"], d["summary"]),
    "todo": lambda d: todo_event(d["items"]),
    "done": lambda d: done_event(d["finish_reason"], partial=d.get("partial")),
    "token": lambda d: f"[token] {d['text']}\n",
}


def _tc(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "name": name, "args": args, "type": "tool_call"}


def build_scenario() -> list[AIMessage]:
    return [
        # 1. 메인: 초기 계획
        AIMessage(
            content="",
            tool_calls=[
                _tc(
                    "call_1",
                    "write_todos",
                    {
                        "todos": [
                            {"content": "csv-analyst에게 매출 집계 위임", "status": "in_progress"},
                            {"content": "doc-researcher에게 원인 조사 위임", "status": "pending"},
                            {"content": "결과 종합해 답변 작성", "status": "pending"},
                        ]
                    },
                )
            ],
        ),
        # 2. 메인: csv 위임
        AIMessage(
            content="",
            tool_calls=[
                _tc(
                    "call_2",
                    "task",
                    {
                        "description": "sales.csv에서 제품별·분기별 매출을 집계하고 results/csv_summary.md에 저장하라.",
                        "subagent_type": "csv-analyst",
                    },
                )
            ],
        ),
        # 3. csv-analyst: 실제 스크립트 실행
        AIMessage(
            content="",
            tool_calls=[
                _tc(
                    "call_3",
                    "execute",
                    {"command": "python3 skills/csv-analysis/analyze_sales.py data/sales.csv --out results/csv_summary.md"},
                )
            ],
        ),
        # 4. csv-analyst: 위임 완료 보고
        AIMessage(
            content=(
                "results/csv_summary.md에 저장했다. 노트북 매출이 1분기 대비 2분기에 4.1% 줄었다. "
                "원인은 이 데이터만으로는 알 수 없다."
            ),
            tool_calls=[],
        ),
        # 5. 메인: 계획 수정 — csv 결과를 보고 새 항목을 추가한다
        AIMessage(
            content="",
            tool_calls=[
                _tc(
                    "call_5",
                    "write_todos",
                    {
                        "todos": [
                            {"content": "csv-analyst에게 매출 집계 위임", "status": "completed"},
                            {"content": "doc-researcher에게 원인 조사 위임", "status": "in_progress"},
                            {"content": "노트북 2분기 감소 원인 문서에서 확인", "status": "pending"},
                            {"content": "결과 종합해 답변 작성", "status": "pending"},
                        ]
                    },
                )
            ],
        ),
        # 6. 메인: 문서 조사 위임
        AIMessage(
            content="",
            tool_calls=[
                _tc(
                    "call_6",
                    "task",
                    {
                        "description": "노트북, 부산 키워드로 매출 감소 원인 문장을 찾아 results/doc_findings.md에 저장하라.",
                        "subagent_type": "doc-researcher",
                    },
                )
            ],
        ),
        # 7. doc-researcher: 실제 스크립트 실행
        AIMessage(
            content="",
            tool_calls=[
                _tc(
                    "call_7",
                    "execute",
                    {"command": "python3 skills/doc-research/find_evidence.py data/docs 노트북 부산 --out results/doc_findings.md"},
                )
            ],
        ),
        # 8. doc-researcher: 위임 완료 보고
        AIMessage(
            content=(
                "results/doc_findings.md에 저장했다. notebook-q1-report#p1에서 물류 센터 이전으로 "
                "부산 지역 배송이 지연돼 일부 주문이 다음 분기로 밀렸다는 근거를 찾았다."
            ),
            tool_calls=[],
        ),
        # 9. 메인: 계획 완료 갱신
        AIMessage(
            content="",
            tool_calls=[
                _tc(
                    "call_9",
                    "write_todos",
                    {
                        "todos": [
                            {"content": "csv-analyst에게 매출 집계 위임", "status": "completed"},
                            {"content": "doc-researcher에게 원인 조사 위임", "status": "completed"},
                            {"content": "노트북 2분기 감소 원인 문서에서 확인", "status": "completed"},
                            {"content": "결과 종합해 답변 작성", "status": "in_progress"},
                        ]
                    },
                )
            ],
        ),
        # 10. 메인: 저장된 csv 결과 다시 읽기(재계산하지 않는다)
        AIMessage(content="", tool_calls=[_tc("call_10", "read_file", {"file_path": "/results/csv_summary.md"})]),
        # 11. 메인: 저장된 문서 조사 결과 다시 읽기
        AIMessage(content="", tool_calls=[_tc("call_11", "read_file", {"file_path": "/results/doc_findings.md"})]),
        # 12. 메인: 최종 답변
        AIMessage(
            content=(
                "노트북 매출은 1분기 대비 2분기에 약 4.1% 줄었다(results/csv_summary.md). "
                "notebook-q1-report#p1에 따르면 부산 지역 물류 센터 이전으로 배송이 지연되면서 "
                "일부 대량 구매 고객이 다음 분기 주문으로 미룬 것이 주요 원인이다."
            ),
            tool_calls=[],
        ),
    ]


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    model = ScriptedChatModel(responses=build_scenario())
    agent = build_agent(model=model, workdir=project_root)

    print("=== Deep Agents 실행: 계획 -> 위임 -> 계획 수정 -> 위임 -> 파일 재읽기 -> 답변 ===\n")
    for name, data in stream_agent_events(agent, "제품별 매출 변화를 분석하고 원인을 문서에서 찾아줘"):
        renderer = RENDER.get(name)
        if renderer is None:
            continue
        rendered = renderer(data)
        print(rendered.strip() if not rendered.startswith("[token]") else rendered, end="\n" if rendered.startswith("[token]") else "\n\n")

    print("--- results/ 아래 실제로 저장된 파일 ---")
    for path in sorted((project_root / "results").glob("*.md")):
        print(f"\n# {path.relative_to(project_root)}")
        print(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
