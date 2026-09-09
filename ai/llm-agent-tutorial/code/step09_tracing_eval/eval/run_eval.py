"""고정 질문 묶음(data/eval/questions.jsonl)으로 검색 품질을 실제로 채점한다.

사용:
    python -m eval.run_eval --mode sparse --top-k 1 --out eval/results/sparse_k1.json
    python -m eval.run_eval --mode sparse --top-k 3 --out eval/results/sparse_k3.json
    python -m eval.run_eval --mode hybrid --top-k 3 --out eval/results/hybrid_k3.json

    python -m eval.compare_runs eval/results/sparse_k1.json eval/results/sparse_k3.json

Milvus·OpenAI 호환 서버가 실행 중이어야 한다(docagent.rag.search가 실제로
호출한다). 서버 없이 채점 로직만 확인하려면 tests/test_retrieval_metrics.py를
본다 — 그 테스트는 이 스크립트가 아니라 evaluate_retrieval()을 가짜
retriever로 직접 부른다.

이 스크립트 자체도 하나의 트레이스로 남는다 — 평가 실행 자체를 추적하면
"이 평가를 언제, 어떤 설정으로 돌렸는지"를 트레이스 목록에서 검색으로
찾을 수 있다(9단계 3-4절).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from docagent.config import get_settings
from docagent.rag.search import dense_search, hybrid_search, sparse_search
from docagent.tracing import SpanKind, init_tracing, set_output, traced_span
from eval.dataset import load_questions
from eval.retrieval_metrics import evaluate_retrieval

_RETRIEVERS = {
    "dense": dense_search,
    "sparse": sparse_search,
    "hybrid": hybrid_search,
}

DEFAULT_QUESTIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "eval" / "questions.jsonl"


def run(
    *,
    mode: str,
    top_k: int,
    questions_path: Path = DEFAULT_QUESTIONS_PATH,
    config_name: str | None = None,
) -> dict:
    if mode not in _RETRIEVERS:
        raise ValueError(f"알 수 없는 mode: {mode!r} (dense/sparse/hybrid 중 하나)")
    retriever = _RETRIEVERS[mode]
    config_name = config_name or f"{mode}_k{top_k}"

    questions = load_questions(questions_path)
    with traced_span(
        "eval.run_retrieval_eval",
        kind=SpanKind.CHAIN,
        input_value=config_name,
        attributes={
            "eval.config_name": config_name,
            "eval.question_count": len(questions),
            "eval.top_k": top_k,
        },
    ) as span:
        result = evaluate_retrieval(questions, retriever, top_k=top_k, config_name=config_name)
        set_output(
            span,
            f"mean_recall={result.mean_recall:.3f} mean_mrr={result.mean_mrr:.3f}",
        )
        return result.to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=sorted(_RETRIEVERS), required=True)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS_PATH))
    parser.add_argument("--config-name", default=None)
    parser.add_argument("--out", default=None, help="결과를 저장할 JSON 경로 (생략하면 표준출력만)")
    args = parser.parse_args()

    init_tracing(get_settings())

    result_dict = run(
        mode=args.mode,
        top_k=args.top_k,
        questions_path=Path(args.questions),
        config_name=args.config_name,
    )

    print(f"설정: {result_dict['config_name']} (top_k={result_dict['top_k']})")
    print(f"평균 recall@k: {result_dict['mean_recall']:.3f}")
    print(f"평균 MRR:      {result_dict['mean_mrr']:.3f}")
    print(f"hit rate:      {result_dict['hit_rate']:.3f}")
    print()
    for q in result_dict["per_question"]:
        mark = "OK" if q["hit"] else "FAIL"
        print(f"  [{mark}] {q['question_id']} recall={q['recall']:.2f} mrr={q['mrr']:.2f}  {q['question']}")
        if not q["hit"]:
            print(f"        기대: {q['expected_ids']}  실제: {q['retrieved_ids']}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result_dict, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {out_path}")


if __name__ == "__main__":
    main()
