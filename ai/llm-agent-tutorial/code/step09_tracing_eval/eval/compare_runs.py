"""저장된 평가 결과 두 개(개선 전/후)를 비교한다.

``run_eval.py``가 만든 JSON 파일(retrieval_metrics.RetrievalEvalResult.to_dict()
형태) 두 개를 받아 질문별 recall/MRR 변화와 전체 평균 변화를 출력한다.
파일을 읽고 숫자를 비교하는 순수 로직이라 Milvus·모델 서버 없이 단위
테스트할 수 있다(tests/test_compare_runs.py).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QuestionDelta:
    question_id: str
    question: str
    before_recall: float
    after_recall: float
    before_mrr: float
    after_mrr: float

    @property
    def recall_delta(self) -> float:
        return self.after_recall - self.before_recall

    @property
    def mrr_delta(self) -> float:
        return self.after_mrr - self.before_mrr

    @property
    def changed(self) -> bool:
        return abs(self.recall_delta) > 1e-9 or abs(self.mrr_delta) > 1e-9


def load_result(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(before: dict, after: dict) -> list[QuestionDelta]:
    """두 RetrievalEvalResult.to_dict() 결과를 question_id 기준으로 짝지어 비교한다.

    한쪽에만 있는 질문 ID는 건너뛴다(``run_eval.py``가 항상 같은
    questions.jsonl로 두 실행을 만들게 하는 것이 정상 사용법이다).
    """
    before_by_id = {q["question_id"]: q for q in before["per_question"]}
    after_by_id = {q["question_id"]: q for q in after["per_question"]}
    deltas = []
    for qid in before_by_id:
        if qid not in after_by_id:
            continue
        b, a = before_by_id[qid], after_by_id[qid]
        deltas.append(
            QuestionDelta(
                question_id=qid,
                question=b["question"],
                before_recall=b["recall"],
                after_recall=a["recall"],
                before_mrr=b["mrr"],
                after_mrr=a["mrr"],
            )
        )
    return deltas


def format_report(before: dict, after: dict, deltas: list[QuestionDelta]) -> str:
    lines = [
        f"설정: {before['config_name']}(top_k={before['top_k']}) -> "
        f"{after['config_name']}(top_k={after['top_k']})",
        f"평균 recall@k: {before['mean_recall']:.3f} -> {after['mean_recall']:.3f} "
        f"({after['mean_recall'] - before['mean_recall']:+.3f})",
        f"평균 MRR:      {before['mean_mrr']:.3f} -> {after['mean_mrr']:.3f} "
        f"({after['mean_mrr'] - before['mean_mrr']:+.3f})",
        f"hit rate:      {before['hit_rate']:.3f} -> {after['hit_rate']:.3f} "
        f"({after['hit_rate'] - before['hit_rate']:+.3f})",
        "",
        "질문별 변화(달라진 것만):",
    ]
    changed = [d for d in deltas if d.changed]
    if not changed:
        lines.append("  (변화 없음)")
    for d in changed:
        lines.append(
            f"  [{d.question_id}] {d.question}\n"
            f"    recall {d.before_recall:.2f} -> {d.after_recall:.2f} "
            f"({d.recall_delta:+.2f})  "
            f"MRR {d.before_mrr:.2f} -> {d.after_mrr:.2f} ({d.mrr_delta:+.2f})"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", help="개선 전 결과 JSON 경로")
    parser.add_argument("after", help="개선 후 결과 JSON 경로")
    args = parser.parse_args()

    before = load_result(args.before)
    after = load_result(args.after)
    deltas = compare(before, after)
    print(format_report(before, after, deltas))


if __name__ == "__main__":
    main()
