"""문서 읽기 -> 청킹 -> 적재 — 5단계 ``rag/ingest.py``를 재구성한다.

문서를 읽고 제목을 뽑는 부분과 청킹 함수는 5단계와 똑같이 남겨 둔다(이
부분은 Milvus도 LangChain도 관여하지 않는 순수 텍스트 처리라 바꿀 이유가
없다). 달라지는 것은 마지막 단계 하나뿐이다: 5단계는 "임베딩을 직접
계산해서 ``insert_chunks()``로 넣었다"면, 이 장은 ``Milvus.add_texts()``에
텍스트와 메타데이터만 넘긴다 — 임베딩 호출(``build_embeddings()``로 만든
``OpenAIEmbeddings``)은 벡터 저장소 객체가 내부에서 대신 부른다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from langchain_milvus import Milvus

from docagent.rag.store import build_vector_store, make_chunk_id

_CHUNK_SIZE = 500
_CHUNK_OVERLAP = 80


@dataclass(frozen=True)
class SourceDocument:
    doc_id: str
    doc_title: str
    source_url: str
    text: str


def load_markdown_documents(docs_dir: str | Path) -> list[SourceDocument]:
    """5단계와 동일한 규칙: 파일명 -> doc_id, 첫 '# 제목' 줄 -> doc_title."""
    docs_dir = Path(docs_dir)
    documents: list[SourceDocument] = []
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        title = title_match.group(1).strip() if title_match else path.stem
        documents.append(SourceDocument(doc_id=path.stem, doc_title=title, source_url=str(path), text=text))
    return documents


def chunk_text(text: str, *, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """5단계와 완전히 같은 문단 우선 슬라이딩 윈도 청킹.

    LangChain은 ``RecursiveCharacterTextSplitter``라는 대체재를 제공하지만,
    이 장은 일부러 5단계와 같은 청크 경계를 써서 "검색 품질 차이가 청킹
    방식이 아니라 검색·결합 방식 차이에서만 나온다"는 것을 비교 스크립트에서
    보장한다. ``RecursiveCharacterTextSplitter``로 바꿔보는 것은 7절 응용
    과제로 남겨 둔다.
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


def ingest_directory(docs_dir: str | Path, *, drop_old: bool = False) -> tuple[int, Milvus]:
    """``docs_dir``의 마크다운 문서를 전부 읽어 청킹하고 벡터 저장소에 넣는다.

    반환값은 (적재한 청크 수, 사용한 벡터 저장소)다. 저장소를 함께 돌려주는
    이유는 스크립트가 적재 직후 바로 검색을 확인할 수 있게 하기 위해서다
    (``build_vector_store()``를 두 번 부르면 연결을 두 번 여는 대신, 이미
    연 것을 재사용하는 편이 낫다).
    """
    documents = load_markdown_documents(docs_dir)
    if not documents:
        raise RuntimeError(f"'{docs_dir}'에 마크다운 문서가 없다.")

    texts: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []
    for doc in documents:
        for idx, chunk in enumerate(chunk_text(doc.text)):
            texts.append(chunk)
            metadatas.append(
                {
                    "doc_id": doc.doc_id,
                    "doc_title": doc.doc_title,
                    "page": 0,  # 5단계와 동일: 페이지 구분 없는 문서는 항상 0
                    "chunk_index": idx,
                    "source_url": doc.source_url,
                }
            )
            ids.append(make_chunk_id(doc.doc_id, 0, idx))

    vector_store = build_vector_store(drop_old=drop_old)
    vector_store.add_texts(texts, metadatas=metadatas, ids=ids)
    return len(texts), vector_store
