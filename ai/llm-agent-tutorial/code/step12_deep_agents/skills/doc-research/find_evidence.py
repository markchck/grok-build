"""doc-research 스킬의 실행 스크립트.

data/docs 아래 Markdown 문서에서 주어진 키워드가 전부(AND) 포함된 문단을 찾아
`{doc_id}#p{page}` 형태의 출처 꼬리표와 함께 돌려준다. 5단계(하이브리드 검색)의
Dense/Sparse 벡터 검색이 아니라 단순 키워드 포함 검색이다 — 이 장의 학습
목표는 Skill 구성 자체이지 검색 고도화가 아니라서 의도적으로 단순하게 뒀다
(SKILL.md에 이유를 밝혔다).

문서에 페이지 구분이 없으므로(Markdown 파일 하나 = 문서 하나) 이 스크립트는
`page=1`로 고정한다. 4·5단계처럼 실제 페이지 매김이 있는 PDF를 쓰면 페이지
번호를 실제 값으로 채우면 된다.

사용법:
    python3 find_evidence.py <문서디렉터리> <키워드...> [--out <결과.md 경로>]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_PARA_SPLIT = re.compile(r"\n\s*\n")


def find_evidence(docs_dir: Path, keywords: list[str]) -> list[dict]:
    hits: list[dict] = []
    for path in sorted(docs_dir.glob("*.md")):
        doc_id = path.stem
        text = path.read_text(encoding="utf-8")
        paragraphs = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]
        for para in paragraphs:
            if all(kw in para for kw in keywords):
                hits.append({"doc_id": doc_id, "page": 1, "text": para})
    return hits


def render_markdown(hits: list[dict], keywords: list[str]) -> str:
    if not hits:
        return f"# 문서 조사 결과\n\n키워드 {keywords}로는 문서에서 근거를 찾지 못했다.\n"
    lines = [f"# 문서 조사 결과 (키워드: {', '.join(keywords)})\n"]
    for hit in hits:
        lines.append(f"## {hit['doc_id']}#p{hit['page']}\n")
        lines.append(hit["text"] + "\n")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="문서에서 키워드가 전부 포함된 문단을 찾는다")
    parser.add_argument("docs_dir", type=Path)
    parser.add_argument("keywords", nargs="+")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if not args.docs_dir.is_dir():
        print(f"디렉터리를 찾을 수 없다: {args.docs_dir}", file=sys.stderr)
        sys.exit(1)

    hits = find_evidence(args.docs_dir, args.keywords)
    markdown = render_markdown(hits, args.keywords)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown, encoding="utf-8")
        print(f"결과를 저장했다: {args.out} (근거 {len(hits)}건)")
    else:
        print(markdown)


if __name__ == "__main__":
    main()
