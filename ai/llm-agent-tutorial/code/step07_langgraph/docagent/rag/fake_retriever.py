"""Milvus 없이 그래프의 검색 노드를 실행·테스트하기 위한 인메모리 검색기.

``docagent.rag.search``의 ``dense_search``/``sparse_search``와 반환 형태를
맞춘다(같은 키를 가진 딕셔너리 리스트: ``pk, text, doc_id, doc_title, page,
chunk_index, source_url, score``) — 그래프의 검색 노드는 어느 쪽을 받아도
동일하게 동작해야 한다.

이 모듈은 임베딩도, BM25도 계산하지 않는다. 단순 부분 문자열/토큰 겹침
점수로 "그럴듯한 순위"만 만든다. **검색 품질을 대표하지 않는다** — 오직
LangGraph 그래프의 상태 전이(분류→검색→근거 확인→답변)를 Milvus·임베딩
서버 없이 실행해 확인하려는 목적으로만 쓴다. 실제 검색 품질 비교는
`code/step05_hybrid_rag/`의 Milvus 기반 하이브리드 검색을 쓴다.
"""

from __future__ import annotations

import re
from pathlib import Path

from docagent.rag.store import make_chunk_id

_TOKEN_RE = re.compile(r"[\w가-힣]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _load_chunks(docs_dir: Path) -> list[dict]:
    """``docs_dir`` 아래 마크다운 파일을 문단 단위로 쪼개 청크 목록을 만든다.

    4·5단계의 진짜 ingest(``docagent.rag.ingest``)보다 훨씬 단순하다 —
    페이지 개념 없이 파일 전체를 page=0으로 두고, 빈 줄 두 개로 문단을
    나눈다. 실제 청킹 전략은 4단계 문서를 본다.
    """
    chunks: list[dict] = []
    for path in sorted(docs_dir.glob("*.md")):
        doc_id = path.stem
        title_line = path.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
        paragraphs = [p.strip() for p in path.read_text(encoding="utf-8").split("\n\n") if p.strip()]
        for idx, para in enumerate(paragraphs):
            chunks.append(
                {
                    "pk": make_chunk_id(doc_id, 0, idx),
                    "text": para,
                    "doc_id": doc_id,
                    "doc_title": title_line or doc_id,
                    "page": 0,
                    "chunk_index": idx,
                    "source_url": str(path),
                }
            )
    return chunks


class FakeRetriever:
    """``dense_search``/``sparse_search``와 같은 시그니처를 흉내내는 클래스.

    ``mode``에 따라 점수 계산만 다르게 한다.

    - ``"dense"``: 질의·청크 토큰 집합의 자카드(Jaccard) 유사도. 임베딩
      벡터 코사인 유사도의 "의미가 겹치는 정도를 본다"는 성질을 아주
      거칠게 흉내낸 것뿐이다 — 실제 의미 검색이 아니다.
    - ``"sparse"``: 질의 토큰이 청크에 그대로 등장하는 빈도(BM25를 흉내낸
      단순 카운트). 정확한 토큰 일치만 본다.
    """

    def __init__(self, docs_dir: str | Path):
        self._chunks = _load_chunks(Path(docs_dir))

    def dense_search(self, query: str, *, limit: int = 5, filter_expr: str = "") -> list[dict]:
        q_tokens = _tokenize(query)
        scored = []
        for chunk in self._chunks:
            c_tokens = _tokenize(chunk["text"])
            union = q_tokens | c_tokens
            score = len(q_tokens & c_tokens) / len(union) if union else 0.0
            if score > 0:
                scored.append({**chunk, "score": score})
        scored.sort(key=lambda h: h["score"], reverse=True)
        return scored[:limit]

    def sparse_search(self, query: str, *, limit: int = 5, filter_expr: str = "") -> list[dict]:
        q_tokens = _tokenize(query)
        scored = []
        for chunk in self._chunks:
            c_tokens = _tokenize(chunk["text"])
            hits = len(q_tokens & c_tokens)
            if hits > 0:
                scored.append({**chunk, "score": float(hits)})
        scored.sort(key=lambda h: h["score"], reverse=True)
        return scored[:limit]
