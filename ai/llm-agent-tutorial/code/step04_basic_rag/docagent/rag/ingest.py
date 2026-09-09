"""문서 읽기 + 텍스트 추출 + 청킹.

이 장에서 다루는 문서는 Markdown(.md) 텍스트 파일이다. PDF 등 다른 형식은
텍스트 추출 라이브러리(예: pypdf)가 따로 필요하고 "페이지" 개념도 파일이
갖고 있으므로, 이 장에서는 실제 페이지가 없는 형식을 골라 `page=0`으로
단순화했다. PDF를 다룰 때는 페이지별로 텍스트를 뽑아 그 페이지 번호를
`page` 필드에 그대로 넣으면 된다.

문서 하나는 아래처럼 아주 단순한 머리말을 갖는다(YAML이 아니라 직접 파싱한다).

    ---
    doc_id: q1-laptop-pro
    title: 노트북 프로 15 2026년 1분기 리포트
    source_url: data/docs/q1-laptop-pro.md
    ---
    (본문)

`doc_id`가 청크 ID(`{doc_id}#p{page}-c{chunk_index}`)의 앞부분이 되므로
파일마다 겹치지 않게 정한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_HEADER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


@dataclass(frozen=True)
class DocumentMeta:
    doc_id: str
    title: str
    source_url: str


@dataclass(frozen=True)
class RawDocument:
    meta: DocumentMeta
    text: str


@dataclass(frozen=True)
class Chunk:
    pk: str
    doc_id: str
    doc_title: str
    page: int
    chunk_index: int
    source_url: str
    text: str


def read_document(path: Path) -> RawDocument:
    """머리말을 파싱하고 본문 텍스트를 뽑는다."""
    raw = path.read_text(encoding="utf-8")
    m = _HEADER_RE.match(raw)
    if not m:
        raise ValueError(
            f"{path}: 문서 머리말(---로 감싼 doc_id/title/source_url)이 없다."
        )
    header_block, body = m.group(1), m.group(2)

    fields: dict[str, str] = {}
    for line in header_block.splitlines():
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()

    for required in ("doc_id", "title", "source_url"):
        if required not in fields:
            raise ValueError(f"{path}: 머리말에 '{required}' 항목이 없다.")

    meta = DocumentMeta(
        doc_id=fields["doc_id"], title=fields["title"], source_url=fields["source_url"]
    )
    return RawDocument(meta=meta, text=body.strip())


def chunk_text(text: str, chunk_size_chars: int, chunk_overlap_chars: int) -> list[str]:
    """텍스트를 청크 단위로 자른다.

    왜 자르는가:
    1) 컨텍스트 한계 — 모델에 문서 전체를 매번 넣을 수 없다. 토큰 수에는 상한이
       있고, 상한 안이어도 문서가 길수록 비용과 지연이 커진다.
    2) 검색 정밀도 — 문서 전체를 하나의 벡터로 만들면 "이 문서에 관련 내용이
       있는지"만 알 수 있고 "어느 부분"인지는 알 수 없다. 작은 단위로 자를수록
       질문과 실제로 관련된 부분만 골라 모델에 넘길 수 있다.

    크기와 중첩(overlap)의 효과:
    - chunk_size가 너무 작으면(예: 100자) 문장이 중간에 잘려 문맥이 끊기고,
      "매출이 늘었다"만 있고 "왜 늘었는지"는 다음 청크로 넘어가 검색에서
      두 청크를 모두 찾아야 답을 완성할 수 있다.
    - chunk_size가 너무 크면(예: 4000자) 청크 하나에 여러 주제가 섞여 임베딩이
      뭉뚱그려지고, 질문과 무관한 문장까지 함께 모델에 전달돼 정밀도가 떨어진다.
    - overlap(중첩)은 청크 경계에서 문장이 잘리는 문제를 줄인다. 앞 청크의
      끝부분을 다음 청크의 시작에 다시 포함시켜, 경계에 걸친 문장이 최소
      한 청크에는 온전히 담기게 한다. overlap이 0이면 경계에 걸린 문맥을
      영영 잃을 수 있고, overlap이 너무 크면(예: chunk_size의 절반 이상)
      같은 내용이 여러 청크에 중복 저장돼 검색 결과가 비슷한 청크로 도배된다.

    구현은 문단(빈 줄로 구분) 단위로 묶어가다가 chunk_size_chars를 넘기면
    새 청크를 시작하고, 새 청크 앞에 이전 청크의 끝 overlap_chars만큼을
    이어붙인다. 문단 하나가 chunk_size_chars보다 길면 문자 단위로 자른다.
    """
    if chunk_size_chars <= 0:
        raise ValueError("chunk_size_chars는 0보다 커야 한다.")
    if chunk_overlap_chars < 0 or chunk_overlap_chars >= chunk_size_chars:
        raise ValueError("chunk_overlap_chars는 0 이상이고 chunk_size_chars보다 작아야 한다.")

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    # 문단이 너무 길면 먼저 문자 단위로 쪼갠다.
    units: list[str] = []
    for para in paragraphs:
        if len(para) <= chunk_size_chars:
            units.append(para)
        else:
            start = 0
            while start < len(para):
                end = min(start + chunk_size_chars, len(para))
                units.append(para[start:end])
                if end == len(para):
                    break
                start = end - chunk_overlap_chars

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) <= chunk_size_chars or not current:
            current = candidate
        else:
            chunks.append(current)
            tail = current[-chunk_overlap_chars:] if chunk_overlap_chars else ""
            current = f"{tail}\n\n{unit}" if tail else unit
    if current:
        chunks.append(current)

    return chunks


def build_chunks(
    documents: list[RawDocument], chunk_size_chars: int, chunk_overlap_chars: int
) -> list[Chunk]:
    """문서 목록을 청크 목록으로 바꾼다. page는 이 장에서 항상 0이다(비-페이지 형식)."""
    result: list[Chunk] = []
    for doc in documents:
        pieces = chunk_text(doc.text, chunk_size_chars, chunk_overlap_chars)
        for idx, piece in enumerate(pieces):
            pk = f"{doc.meta.doc_id}#p0-c{idx}"
            result.append(
                Chunk(
                    pk=pk,
                    doc_id=doc.meta.doc_id,
                    doc_title=doc.meta.title,
                    page=0,
                    chunk_index=idx,
                    source_url=doc.meta.source_url,
                    text=piece,
                )
            )
    return result


def load_documents(docs_dir: Path) -> list[RawDocument]:
    return [read_document(p) for p in sorted(docs_dir.glob("*.md"))]
