"""문서 읽기 → 청킹 → 임베딩 → Milvus 적재.

4단계에서 만든 파이프라인과 같은 모양이다. 이 장에서 달라지는 것은 딱 하나,
Milvus에 넣을 때 ``sparse`` 필드를 애플리케이션이 계산하지 않는다는 점이다
(store.py의 BM25 Function이 ``text``로부터 서버에서 만든다). 그래서 ingest.py
자체는 4단계와 거의 동일하게 "Dense 임베딩만 계산해서 넣는다."
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from docagent.llm import embed
from docagent.rag.store import ChunkRecord, create_collection, insert_chunks

_CHUNK_SIZE = 500  # 문자 수 기준. 토크나이저별 토큰 수와는 다르다.
_CHUNK_OVERLAP = 80
_EMBED_BATCH_SIZE = 32


@dataclass(frozen=True)
class SourceDocument:
    doc_id: str
    doc_title: str
    source_url: str
    text: str


def load_markdown_documents(docs_dir: str | Path) -> list[SourceDocument]:
    """``docs_dir`` 아래 ``*.md`` 파일을 읽는다.

    - ``doc_id``는 파일명(확장자 제외)을 쓴다. 예: ``q2-report.md`` → ``q2-report``.
    - ``doc_title``은 파일의 첫 ``# 제목`` 줄에서 뽑는다. 없으면 doc_id를 쓴다.
    - ``source_url``은 로컬 경로 문자열이다. 실제 서비스에서는 원본 문서
      저장소의 URL을 쓴다.
    """
    docs_dir = Path(docs_dir)
    documents: list[SourceDocument] = []
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        documents.append(
            SourceDocument(
                doc_id=path.stem,
                doc_title=title,
                source_url=str(path),
                text=text,
            )
        )
    return documents


def chunk_text(text: str, *, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """문단 경계를 우선 존중하는 슬라이딩 윈도 청킹.

    문단(빈 줄로 구분)을 순서대로 누적하다가 chunk_size를 넘기면 자르고,
    다음 청크의 앞부분에 이전 청크의 끝 overlap 글자를 겹쳐 넣는다. 문맥이
    청크 경계에서 완전히 끊기는 것을 줄이기 위해서다.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= chunk_size or not current:
            current = candidate
            continue
        chunks.append(current)
        tail = current[-overlap:] if overlap else ""
        current = f"{tail}\n\n{para}" if tail else para
    if current:
        chunks.append(current)
    return chunks


def ingest_directory(
    docs_dir: str | Path,
    *,
    recreate: bool = False,
) -> int:
    """``docs_dir``의 마크다운 문서를 전부 읽어 청킹·임베딩하고 Milvus에 넣는다.

    반환값은 적재한 청크 수다. 이 함수는 페이지 구분이 없는 문서를 다루므로
    ``page``는 항상 0으로 넣는다(PROJECT-SPEC.md 6장: "페이지(없으면 0)").
    페이지가 있는 PDF 등을 다루려면 문서를 읽을 때 페이지별로 SourceDocument를
    나눠 만들면 된다.
    """
    documents = load_markdown_documents(docs_dir)
    if not documents:
        raise RuntimeError(f"'{docs_dir}'에 마크다운 문서가 없다.")

    all_chunks: list[tuple[SourceDocument, int, str]] = []
    for doc in documents:
        for idx, chunk in enumerate(chunk_text(doc.text)):
            all_chunks.append((doc, idx, chunk))

    # 컬렉션을 만들려면 임베딩 차원을 알아야 하므로 먼저 한 배치를 임베딩한다.
    dense_dim: int | None = None
    records: list[ChunkRecord] = []
    for start in range(0, len(all_chunks), _EMBED_BATCH_SIZE):
        batch = all_chunks[start : start + _EMBED_BATCH_SIZE]
        vectors = embed([chunk for _, _, chunk in batch])
        if dense_dim is None and vectors:
            dense_dim = len(vectors[0])
            create_collection(dense_dim, recreate=recreate)
        for (doc, idx, chunk), vector in zip(batch, vectors):
            records.append(
                ChunkRecord(
                    doc_id=doc.doc_id,
                    doc_title=doc.doc_title,
                    page=0,
                    chunk_index=idx,
                    text=chunk,
                    source_url=doc.source_url,
                    dense=vector,
                )
            )

    return insert_chunks(records)
