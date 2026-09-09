"""data/eval/questions.jsonl이 실제로 파싱되는지, 스키마가 맞는지 확인한다."""

from __future__ import annotations

from pathlib import Path

from eval.dataset import load_questions

QUESTIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "eval" / "questions.jsonl"


def test_load_questions_real_dataset():
    questions = load_questions(QUESTIONS_PATH)
    assert len(questions) == 8
    ids = [q.id for q in questions]
    assert ids == [f"q{i}" for i in range(1, 9)]


def test_expect_no_answer_question_has_empty_expected_ids():
    questions = load_questions(QUESTIONS_PATH)
    no_answer = [q for q in questions if q.expect_no_answer]
    assert len(no_answer) == 1
    assert no_answer[0].expected_chunk_ids == []


def test_every_expected_chunk_id_matches_pk_format():
    # store.make_chunk_id 형식: "{doc_id}#p{page}-c{chunk_index}"
    questions = load_questions(QUESTIONS_PATH)
    for q in questions:
        for chunk_id in q.expected_chunk_ids:
            assert "#p" in chunk_id and "-c" in chunk_id, chunk_id
