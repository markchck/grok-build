"""그래프 상태 전이 단위 테스트. Milvus·실제 모델 서버가 필요 없다.

``NodeDeps``에 가짜 검색·채팅 함수를 주입해(``docagent/graph/nodes.py``의
의존성 주입 설계) 그래프의 분기·반복·병렬 병합만 검증한다. 실제 검색
품질이나 모델 답변 내용은 검증하지 않는다 — 그건 이 그래프가 다루는 범위가
아니다(그 검증은 5단계 Milvus 하이브리드 검색 쪽 책임이다).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from docagent.graph.build import build_graph
from docagent.graph.nodes import NodeDeps
from docagent.llm import ChatResult, LLMError


def _fixed_chat(text: str):
    def chat_fn(messages, *, temperature: float = 0.2) -> ChatResult:
        return ChatResult(text=text, finish_reason="stop")

    return chat_fn


def _fixed_json_chat(_result: dict):
    """호출 시 항상 ``_result``를 돌려주는 가짜 json_chat.

    이 저장소의 학습용 스텁 서버(fake_openai_server.py)는 요청한 스키마와
    무관하게 고정된 JSON을 돌려준다 — 그 동작을 흉내낸 것이다. 노드는 이
    JSON이 기대하는 키를 담고 있지 않으면 규칙 기반 휴리스틱으로 넘어간다.
    """

    def json_chat_fn(messages, *, schema_name, json_schema, temperature: float = 0.0) -> dict:
        return dict(_result)

    return json_chat_fn


def _make_hit(pk: str, text: str = "본문", score: float = 1.0) -> dict:
    return {
        "pk": pk,
        "text": text,
        "doc_id": "doc",
        "doc_title": "제목",
        "page": 0,
        "chunk_index": 0,
        "source_url": "doc.md",
        "score": score,
    }


class ChitchatRoutingTest(unittest.TestCase):
    def test_chitchat_question_skips_retrieval(self):
        deps = NodeDeps(
            dense_search=lambda q, *, limit=5, filter_expr="": (_ for _ in ()).throw(AssertionError("호출되면 안 된다")),
            sparse_search=lambda q, *, limit=5, filter_expr="": (_ for _ in ()).throw(AssertionError("호출되면 안 된다")),
            chat=_fixed_chat("안녕하세요!"),
            json_chat=_fixed_json_chat({"answer": "42"}),  # category 키가 없어 휴리스틱으로 대체된다
            app_base_url="http://localhost:8080",
        )
        app = build_graph(deps)
        out = app.invoke({"question": "안녕!", "retrieved": [], "queries_tried": [], "trace": []})
        self.assertEqual(out["category"], "chitchat")
        self.assertEqual(out["answer_text"], "안녕하세요!")
        self.assertEqual(out["citations"], [])


class FactualRoutingTest(unittest.TestCase):
    def test_sufficient_evidence_answers_without_expanding(self):
        hits = [_make_hit("doc#p0-c0"), _make_hit("doc#p0-c1")]
        deps = NodeDeps(
            dense_search=lambda q, *, limit=5, filter_expr="": hits,
            sparse_search=lambda q, *, limit=5, filter_expr="": hits,
            chat=_fixed_chat("답변입니다[S1]."),
            json_chat=_fixed_json_chat({"answer": "42"}),
            app_base_url="http://localhost:8080",
            min_sufficient_hits=2,
        )
        app = build_graph(deps)
        out = app.invoke(
            {"question": "2분기 매출은?", "retrieved": [], "queries_tried": [], "trace": []}
        )
        self.assertEqual(out["category"], "factual")
        self.assertTrue(out["sufficient"])
        self.assertEqual(out["retrieval_round"], 0)  # 재검색 없이 바로 답했다
        self.assertIn("S1", out["citations"][0]["id"])

    def test_insufficient_evidence_expands_query_then_stops_at_round_limit(self):
        # 항상 청크 1건만 돌려준다 -> min_sufficient_hits(2) 미달 -> 계속 부족 판정.
        one_hit = [_make_hit("only-one")]
        deps = NodeDeps(
            dense_search=lambda q, *, limit=5, filter_expr="": one_hit,
            sparse_search=lambda q, *, limit=5, filter_expr="": one_hit,
            chat=_fixed_chat("근거가 부족하다."),
            json_chat=_fixed_json_chat({"answer": "42"}),
            app_base_url="http://localhost:8080",
            min_sufficient_hits=2,
            max_retrieval_rounds=2,
        )
        app = build_graph(deps)
        out = app.invoke(
            {"question": "존재하지 않는 제품의 매출은?", "retrieved": [], "queries_tried": [], "trace": []}
        )
        self.assertFalse(out["sufficient"])
        self.assertEqual(out["retrieval_round"], 2)  # max_retrieval_rounds에서 멈췄다
        # 검색은 (초기 1회 + 재검색 2회) x (dense+sparse) = 6번 시도된 흔적이 trace에 남는다
        retrieving_traces = [t for t in out["trace"] if t["stage"] == "retrieving"]
        self.assertGreaterEqual(len(retrieving_traces), 6)


class RetrievalDegradationTest(unittest.TestCase):
    def test_one_search_source_failing_does_not_crash_the_graph(self):
        good_hits = [_make_hit("a"), _make_hit("b")]

        def always_fails(query, *, limit=5, filter_expr=""):
            raise LLMError("검색 백엔드 장애(테스트)")

        deps = NodeDeps(
            dense_search=lambda q, *, limit=5, filter_expr="": good_hits,
            sparse_search=always_fails,
            chat=_fixed_chat("답변."),
            json_chat=_fixed_json_chat({"answer": "42"}),
            app_base_url="http://localhost:8080",
            min_sufficient_hits=2,
        )
        app = build_graph(deps)
        out = app.invoke({"question": "질문", "retrieved": [], "queries_tried": [], "trace": []})
        # sparse가 계속 실패해도 dense 결과만으로 정상 종료된다.
        self.assertTrue(out["answer_text"])
        self.assertTrue(any("실패" in t["message"] for t in out["trace"] if t["stage"] == "retrieving"))


class CheckpointResumeTest(unittest.TestCase):
    def test_resume_after_crash_does_not_rerun_completed_nodes(self):
        hits = [_make_hit("a"), _make_hit("b")]
        dense_calls = {"n": 0}

        def counting_dense(query, *, limit=5, filter_expr=""):
            dense_calls["n"] += 1
            return hits

        crash_flag = {"on": True}

        def flaky_chat(messages, *, temperature: float = 0.2) -> ChatResult:
            if crash_flag["on"]:
                raise LLMError("모델 서버 장애(테스트)")
            return ChatResult(text="복구 후 답변[S1].", finish_reason="stop")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "ckpt.sqlite")
            config = {"configurable": {"thread_id": "t1"}}

            # --- 1차 실행: answer 노드에서 실패한다 ---
            with SqliteSaver.from_conn_string(db_path) as saver:
                deps1 = NodeDeps(
                    dense_search=counting_dense,
                    sparse_search=lambda q, *, limit=5, filter_expr="": hits,
                    chat=flaky_chat,
                    json_chat=_fixed_json_chat({"answer": "42"}),
                    app_base_url="http://localhost:8080",
                    min_sufficient_hits=2,
                )
                app1 = build_graph(deps1, checkpointer=saver)
                with self.assertRaises(LLMError):
                    app1.invoke(
                        {"question": "질문", "retrieved": [], "queries_tried": [], "trace": []},
                        config=config,
                    )
                snapshot = app1.get_state(config)
                self.assertEqual(snapshot.next, ("answer",))
                self.assertEqual(dense_calls["n"], 1)

            # --- 2차 실행: 새 SqliteSaver(=새 프로세스를 흉내낸다), 장애 복구 ---
            crash_flag["on"] = False
            with SqliteSaver.from_conn_string(db_path) as saver:
                deps2 = NodeDeps(
                    dense_search=counting_dense,
                    sparse_search=lambda q, *, limit=5, filter_expr="": hits,
                    chat=flaky_chat,
                    json_chat=_fixed_json_chat({"answer": "42"}),
                    app_base_url="http://localhost:8080",
                    min_sufficient_hits=2,
                )
                app2 = build_graph(deps2, checkpointer=saver)
                result = app2.invoke(None, config=config)  # None: 중단 지점부터 재개

        self.assertIn("복구 후 답변", result["answer_text"])
        # retrieve_dense는 1차 실행에서만 실행됐어야 한다 — 재개 시 다시 실행되지 않는다.
        self.assertEqual(dense_calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
