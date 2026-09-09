"""평가 데이터셋(``data/eval/questions.jsonl`) 로딩.

한 줄에 질문 하나. 스키마:

    {
      "id": "q1",
      "question": "2분기 노트북 매출이 둔화된 이유는?",
      "expected_chunk_ids": ["notebook-q2-report#p0-c0"],
      "expected_keywords": ["경쟁사", "신모델"],
      "expect_no_answer": false,   # 생략 가능, 기본 false
      "note": "..."                # 생략 가능
    }

``expected_chunk_ids``는 store.py의 ``make_chunk_id(doc_id, page, chunk_index)``
형식과 정확히 같아야 한다 — 이 파일은 그 값을 검색 결과의 ``pk``와 문자열로
그대로 비교한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    question: str
    expected_chunk_ids: list[str] = field(default_factory=list)
    expected_keywords: list[str] = field(default_factory=list)
    expect_no_answer: bool = False
    note: str = ""


def load_questions(path: str | Path) -> list[EvalQuestion]:
    path = Path(path)
    questions: list[EvalQuestion] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: 잘못된 JSON 줄: {line!r}") from exc
            questions.append(
                EvalQuestion(
                    id=data["id"],
                    question=data["question"],
                    expected_chunk_ids=data.get("expected_chunk_ids", []),
                    expected_keywords=data.get("expected_keywords", []),
                    expect_no_answer=data.get("expect_no_answer", False),
                    note=data.get("note", ""),
                )
            )
    return questions
