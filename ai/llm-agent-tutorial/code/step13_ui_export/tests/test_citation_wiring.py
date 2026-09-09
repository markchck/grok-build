"""hybrid_search_docs 결과 -> run.sources -> 답변 인용 검증까지 이어지는 배선을 확인한다.

5단계 ``citations.py``의 검증 로직 자체는 5단계 테스트가 이미 검증했다(가짜
출처를 걸러내는 것 포함). 이 파일이 추가로 확인하는 것은 "이 장의 에이전트
루프가 그 로직을 실제로 호출하는가", "모델이 만든 답변의 [S1]이 이번 검색
결과에 있으면 살아남고, 없는 번호([S99])는 지워지는가"다. Milvus를 실제로
쓰지 않도록 ``hybrid_search``를 몽키패치한다(이 장의 검색 자체는 이미 실제
서버로 확인했다 — 5절 참고).
"""

from __future__ import annotations

import json

from docagent import store
from docagent.agent import advance_run, start_run


def _tool_call_message(call_id, name, arguments_json):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments_json}}],
    }


def _final_message(text):
    return {"role": "assistant", "content": text, "tool_calls": None}


def _events_by_kind(evs, kind):
    return [e for e in evs if e.kind == kind]


def test_valid_citation_survives_and_invalid_citation_is_dropped(monkeypatch):
    import docagent.agent as agent_module

    fake_hits = [
        {
            "pk": "notebook-q1-report#p0-c0",
            "text": "1분기 노트북 매출은 서울·부산 모두 증가했다.",
            "doc_id": "notebook-q1-report",
            "doc_title": "노트북 1분기 판매 리포트",
            "page": 0,
            "chunk_index": 0,
            "source_url": "data/docs/notebook-q1-report.md",
            "score": 0.9,
        }
    ]
    monkeypatch.setattr(agent_module, "hybrid_search", lambda query, limit=5: fake_hits)

    conn = store.connect(":memory:")
    calls = {"n": 0}

    def fake_chat(messages, tools=None, tool_choice=None, temperature=0.2, settings=None):
        calls["n"] += 1
        if calls["n"] == 1:
            msg = _tool_call_message("call_1", "hybrid_search_docs", '{"query": "노트북 매출 원인", "top_k": 5}')
        else:
            # S1은 실제 검색 결과에 있고, S9는 모델이 지어낸 번호다.
            msg = _final_message("서울·부산 모두 증가했다[S1]. 알 수 없는 근거[S9]도 있다.")
        return {"message": msg, "usage": {}}

    run_id = start_run(conn, "노트북 매출 원인 알려줘")
    evs = list(advance_run(conn, run_id, chat_fn=fake_chat))

    sources_events = _events_by_kind(evs, "sources")
    assert len(sources_events) == 1
    items = sources_events[0].data["items"]
    assert len(items) == 1
    assert items[0]["id"] == "S1"
    assert items[0]["url"].startswith("http")
    assert "/sources/notebook-q1-report" in items[0]["url"]

    tokens = _events_by_kind(evs, "token")
    final_text = tokens[-1].data["text"]
    assert "[S1]" in final_text
    assert "[S9]" not in final_text  # 지어낸 인용은 지워진다
    assert "제거했다" in final_text  # 5단계 규약: 버렸다는 사실을 답변에 남긴다

    done = _events_by_kind(evs, "done")
    assert done[0].data["finish_reason"] == "stop"
