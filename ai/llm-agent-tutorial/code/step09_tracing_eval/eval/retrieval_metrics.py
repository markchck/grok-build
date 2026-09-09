"""검색 품질 평가: recall@k, MRR.

이 모듈은 답변 품질을 전혀 모른다 — 검색이 정답 청크를 찾았는지만 본다.
"검색은 맞았는데 답이 틀렸다"와 "검색부터 틀렸다"를 구분하는 것이 이 장의
핵심이므로, 검색 품질 평가는 answer_quality.py와 완전히 분리한다.

Milvus나 모델 서버를 호출하지 않는다 — ``retrieved_ids``(문자열 리스트)만
받는 순수 함수라서, 검색 결과를 실제로 만드는 코드 없이도 단위 테스트할
수 있다(tests/test_retrieval_metrics.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from eval.dataset import EvalQuestion


def recall_at_k(retrieved_ids: list[str], expected_ids: list[str]) -> float:
    """기대한 청크 중 몇 %가 top-k 검색 결과 안에 있는가.

    기대 청크가 여러 개일 수 있으므로(이 장의 데이터셋은 질문당 보통 1개만
    쓰지만, 형식은 리스트를 허용한다) "몇 개 중 몇 개를 찾았는가"의 비율이다.
    ``expected_ids``가 비어 있으면(코퍼스에 답이 없는 질문) 정의상 1.0을
    반환한다 — "찾을 게 없는데 아무것도 안 찾았다"는 성공이기 때문이다.
    이 경우는 recall이 아니라 answer_quality.py 쪽에서
    "모른다고 답했는가"로 따로 채점한다.
    """
    if not expected_ids:
        return 1.0
    expected_set = set(expected_ids)
    retrieved_set = set(retrieved_ids)
    found = expected_set & retrieved_set
    return len(found) / len(expected_set)


def mrr(retrieved_ids: list[str], expected_ids: list[str]) -> float:
    """Mean Reciprocal Rank: 기대한 청크 중 가장 먼저 등장한 것의 1/순위.

    아무 기대 청크도 top-k 안에 없으면 0.0. ``expected_ids``가 비어 있으면
    recall_at_k와 마찬가지로 1.0(정의상 "찾을 게 없으므로 만점")을 반환한다.
    """
    if not expected_ids:
        return 1.0
    expected_set = set(expected_ids)
    for rank, doc_id in enumerate(retrieved_ids, start=1):
        if doc_id in expected_set:
            return 1.0 / rank
    return 0.0


def hit_at_k(retrieved_ids: list[str], expected_ids: list[str]) -> bool:
    """기대 청크가 하나라도 top-k 안에 있으면 True. (질문 단위 성공/실패 표시용)"""
    if not expected_ids:
        return True
    return bool(set(expected_ids) & set(retrieved_ids))


class Retriever(Protocol):
    def __call__(self, query: str, *, limit: int) -> list[dict]: ...


@dataclass(frozen=True)
class QuestionResult:
    question_id: str
    question: str
    retrieved_ids: list[str]
    expected_ids: list[str]
    recall: float
    mrr: float
    hit: bool


@dataclass(frozen=True)
class RetrievalEvalResult:
    config_name: str
    top_k: int
    per_question: list[QuestionResult] = field(default_factory=list)

    @property
    def mean_recall(self) -> float:
        if not self.per_question:
            return 0.0
        return sum(q.recall for q in self.per_question) / len(self.per_question)

    @property
    def mean_mrr(self) -> float:
        if not self.per_question:
            return 0.0
        return sum(q.mrr for q in self.per_question) / len(self.per_question)

    @property
    def hit_rate(self) -> float:
        if not self.per_question:
            return 0.0
        return sum(1 for q in self.per_question if q.hit) / len(self.per_question)

    def to_dict(self) -> dict:
        return {
            "config_name": self.config_name,
            "top_k": self.top_k,
            "mean_recall": self.mean_recall,
            "mean_mrr": self.mean_mrr,
            "hit_rate": self.hit_rate,
            "per_question": [
                {
                    "question_id": q.question_id,
                    "question": q.question,
                    "retrieved_ids": q.retrieved_ids,
                    "expected_ids": q.expected_ids,
                    "recall": q.recall,
                    "mrr": q.mrr,
                    "hit": q.hit,
                }
                for q in self.per_question
            ],
        }


def evaluate_retrieval(
    questions: list[EvalQuestion],
    retriever: Retriever,
    *,
    top_k: int = 5,
    config_name: str = "default",
) -> RetrievalEvalResult:
    """질문 목록 전체에 대해 ``retriever``를 돌리고 recall@k·MRR을 집계한다.

    ``retriever``는 ``dense_search``/``sparse_search``/``hybrid_search``
    (docagent.rag.search)와 시그니처가 같은 아무 호출 가능 객체면 된다 —
    실제 Milvus 대신 고정된 결과를 돌려주는 가짜 함수를 넣으면 서버 없이도
    이 함수 자체를 테스트할 수 있다(tests/test_retrieval_metrics.py).
    """
    results: list[QuestionResult] = []
    for q in questions:
        hits = retriever(q.question, limit=top_k)
        retrieved_ids = [h["pk"] if isinstance(h, dict) else h for h in hits]
        results.append(
            QuestionResult(
                question_id=q.id,
                question=q.question,
                retrieved_ids=retrieved_ids,
                expected_ids=q.expected_chunk_ids,
                recall=recall_at_k(retrieved_ids, q.expected_chunk_ids),
                mrr=mrr(retrieved_ids, q.expected_chunk_ids),
                hit=hit_at_k(retrieved_ids, q.expected_chunk_ids),
            )
        )
    return RetrievalEvalResult(config_name=config_name, top_k=top_k, per_question=results)
