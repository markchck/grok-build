"""recall@k, MRR, evaluate_retrieval()을 가짜 retriever로 검증한다.

Milvus·모델 서버를 전혀 쓰지 않는다 — eval.retrieval_metrics는 순수 함수와
호출 가능 객체(retriever)만으로 이루어져 있어서, 진짜 검색 결과 대신
미리 정해 둔 딕셔너리를 돌려주는 가짜 함수를 넣으면 로직만 정확히
검증할 수 있다.
"""

from __future__ import annotations

from eval.dataset import EvalQuestion
from eval.retrieval_metrics import evaluate_retrieval, hit_at_k, mrr, recall_at_k


def _hit(pk: str) -> dict:
    return {"pk": pk, "text": "", "score": 0.0}


def test_recall_at_k_perfect_match():
    assert recall_at_k(["a", "b", "c"], ["a"]) == 1.0


def test_recall_at_k_miss():
    assert recall_at_k(["a", "b", "c"], ["z"]) == 0.0


def test_recall_at_k_partial_multi_expected():
    # 기대 청크 2개 중 1개만 찾음 -> 0.5
    assert recall_at_k(["a", "x"], ["a", "b"]) == 0.5


def test_recall_at_k_no_expected_is_perfect():
    # "찾을 게 없는 질문"은 뭘 반환해도 recall=1.0 (정의상)
    assert recall_at_k(["a", "b"], []) == 1.0
    assert recall_at_k([], []) == 1.0


def test_mrr_first_rank():
    assert mrr(["a", "b", "c"], ["a"]) == 1.0


def test_mrr_second_rank():
    assert mrr(["x", "a", "c"], ["a"]) == 0.5


def test_mrr_not_found():
    assert mrr(["x", "y"], ["a"]) == 0.0


def test_mrr_picks_earliest_of_multiple_expected():
    # 기대 청크가 여러 개면 그중 가장 먼저 나온 것의 순위를 쓴다.
    assert mrr(["x", "b", "a"], ["a", "b"]) == 0.5  # b가 2위, a가 3위 -> 1/2


def test_hit_at_k():
    assert hit_at_k(["a", "b"], ["b"]) is True
    assert hit_at_k(["a", "b"], ["z"]) is False
    assert hit_at_k(["a", "b"], []) is True


def test_evaluate_retrieval_with_fake_retriever():
    questions = [
        EvalQuestion(id="q1", question="노트북 매출?", expected_chunk_ids=["notebook#p0-c0"]),
        EvalQuestion(id="q2", question="키보드 매출?", expected_chunk_ids=["keyboard#p0-c0"]),
        EvalQuestion(id="q3", question="근거 없는 질문", expected_chunk_ids=[]),
    ]

    # 가짜 retriever: 질문 텍스트로 미리 정해 둔 결과를 돌려준다. dense_search/
    # sparse_search/hybrid_search와 시그니처(query, *, limit)만 맞으면 된다.
    fixed_results = {
        "노트북 매출?": [_hit("keyboard#p0-c0"), _hit("notebook#p0-c0")],  # 2위에 있음
        "키보드 매출?": [_hit("monitor#p0-c0")],  # 못 찾음
        "근거 없는 질문": [_hit("keyboard#p0-c0")],
    }

    def fake_retriever(query: str, *, limit: int) -> list[dict]:
        return fixed_results[query][:limit]

    result = evaluate_retrieval(questions, fake_retriever, top_k=2, config_name="fake")

    assert result.config_name == "fake"
    assert result.top_k == 2
    assert len(result.per_question) == 3

    q1 = result.per_question[0]
    assert q1.recall == 1.0
    assert q1.mrr == 0.5  # 2위
    assert q1.hit is True

    q2 = result.per_question[1]
    assert q2.recall == 0.0
    assert q2.mrr == 0.0
    assert q2.hit is False

    q3 = result.per_question[2]
    assert q3.recall == 1.0  # 기대가 없으므로 만점
    assert q3.hit is True

    # 집계: (1.0 + 0.0 + 1.0) / 3
    assert abs(result.mean_recall - (2 / 3)) < 1e-9
    assert abs(result.hit_rate - (2 / 3)) < 1e-9


def test_to_dict_roundtrip_keys():
    questions = [EvalQuestion(id="q1", question="q", expected_chunk_ids=["a"])]

    def fake_retriever(query: str, *, limit: int) -> list[dict]:
        return [_hit("a")]

    result = evaluate_retrieval(questions, fake_retriever, top_k=1, config_name="cfg")
    d = result.to_dict()
    assert d["config_name"] == "cfg"
    assert d["top_k"] == 1
    assert d["mean_recall"] == 1.0
    assert d["per_question"][0]["question_id"] == "q1"
