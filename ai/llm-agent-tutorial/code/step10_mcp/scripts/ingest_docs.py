"""문서(``data/docs/*.md``)를 읽어 청킹·임베딩하고 Milvus에 dense 필드만
적재한다. MCP 서버(``mcp_server/server.py``)의 ``search_docs`` 도구가
검색하는 대상 컬렉션을 만드는 스크립트다.

이 스크립트는 ``mcp_server`` 패키지의 store만 쓴다 — 적재는 "서버가 검색할
데이터를 미리 준비하는 운영 작업"이라 서버 쪽 코드로 다루는 것이
자연스럽다(에이전트/클라이언트가 알 필요 없는 작업이기 때문).

실행:
    python -m scripts.ingest_docs
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from mcp_server.config import get_mcp_server_settings
from mcp_server.store import ChunkRecord, create_collection, insert_chunks

_CHUNK_SIZE = 500
_CHUNK_OVERLAP = 80
_EMBED_BATCH_SIZE = 32

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DOCS_DIR = DATA_DIR / "docs"


@dataclass(frozen=True)
class SourceDocument:
    doc_id: str
    doc_title: str
    source_url: str
    text: str


def load_markdown_documents(docs_dir: Path) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        documents.append(SourceDocument(doc_id=path.stem, doc_title=title, source_url=str(path), text=text))
    return documents


def chunk_text(text: str, *, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
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


def _embed_batch(client: OpenAI, model: str, texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=model, input=texts)
    items = sorted(resp.data, key=lambda item: item.index)
    return [item.embedding for item in items]


def ingest_directory(docs_dir: Path = DOCS_DIR, *, recreate: bool = True) -> int:
    settings = get_mcp_server_settings()
    client = OpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key, timeout=30)

    documents = load_markdown_documents(docs_dir)
    if not documents:
        raise RuntimeError(f"'{docs_dir}'에 마크다운 문서가 없다.")

    all_chunks: list[tuple[SourceDocument, int, str]] = []
    for doc in documents:
        for idx, chunk in enumerate(chunk_text(doc.text)):
            all_chunks.append((doc, idx, chunk))

    dense_dim: int | None = None
    records: list[ChunkRecord] = []
    for start in range(0, len(all_chunks), _EMBED_BATCH_SIZE):
        batch = all_chunks[start : start + _EMBED_BATCH_SIZE]
        vectors = _embed_batch(client, settings.embedding_model, [c for _, _, c in batch])
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


if __name__ == "__main__":
    count = ingest_directory()
    print(f"적재한 청크 수: {count}")
