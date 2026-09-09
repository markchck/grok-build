"""FastAPI 앱. 2단계에서 정한 SSE 이벤트 스키마를 그대로 쓴다.

이 장에서 추가하는 것:
- POST /api/ingest  : data/docs 아래 문서를 읽어 청킹하고 Milvus에 저장한다.
- POST /api/chat     : 질문을 받아 검색 → (근거가 있으면) 모델 호출 → 스트리밍 응답.

3단계의 도구 호출 루프는 아직 없다. 이 장의 "에이전트"는 검색 한 번 + 답변
한 번으로 끝나는 가장 단순한 RAG 흐름이다.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from docagent import events
from docagent.config import Settings, get_settings
from docagent.llm import LLMError, chat_stream, embed
from docagent.rag.ingest import build_chunks, load_documents
from docagent.rag.search import NO_EVIDENCE_MESSAGE, build_messages, retrieve
from docagent.rag.store import (
    create_chunks_collection,
    insert_chunks,
    make_client as make_milvus_client,
)

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "data" / "docs"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="docagent - step04 basic RAG")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_settings: Settings = get_settings()
_milvus_client = make_milvus_client(_settings.milvus_uri, _settings.milvus_token)


class ChatRequest(BaseModel):
    question: str


@app.get("/")
def index():
    return StreamingResponse(
        iter([(STATIC_DIR / "index.html").read_text(encoding="utf-8")]),
        media_type="text/html",
    )


@app.post("/api/ingest")
def ingest():
    """data/docs를 읽어 청킹하고 Milvus에 저장한다.

    임베딩 차원을 하드코딩하지 않고, 첫 청크를 실제로 한 번 임베딩해서
    반환된 벡터 길이로 차원을 알아낸 뒤에 컬렉션을 만든다. 이렇게 하면
    EMBEDDING_MODEL을 다른 모델로 바꿔도 코드를 고칠 필요가 없다.
    """
    documents = load_documents(DOCS_DIR)
    if not documents:
        return {"ok": False, "message": f"{DOCS_DIR}에 문서가 없다. scripts/make_sample_data.py를 먼저 실행한다."}

    chunks = build_chunks(
        documents,
        chunk_size_chars=_settings.chunk_size_chars,
        chunk_overlap_chars=_settings.chunk_overlap_chars,
    )
    if not chunks:
        return {"ok": False, "message": "청크가 생성되지 않았다."}

    texts = [c.text for c in chunks]
    vectors = embed(texts, settings=_settings)
    dense_dim = len(vectors[0])

    create_chunks_collection(_milvus_client, _settings.milvus_collection, dense_dim)
    inserted = insert_chunks(_milvus_client, _settings.milvus_collection, chunks, vectors)

    return {
        "ok": True,
        "documents": len(documents),
        "chunks": len(chunks),
        "inserted": inserted,
        "dense_dim": dense_dim,
    }


def _chat_event_stream(question: str):
    yield events.status(events.STAGE_RETRIEVING, "관련 자료를 검색하는 중이다.")

    result = retrieve(
        milvus_client=_milvus_client,
        collection_name=_settings.milvus_collection,
        question=question,
        top_k=_settings.retrieval_top_k,
        score_threshold=_settings.retrieval_score_threshold,
    )

    source_items = [
        {
            "id": hit.pk,
            "doc": hit.doc_title,
            "page": hit.page,
            "url": hit.source_url,
            "snippet": hit.text[:200],
        }
        for hit in result.accepted
    ]
    yield events.sources(source_items)

    if not result.has_evidence:
        # 애플리케이션 쪽 차단: 근거가 없으면 모델을 호출하지 않는다.
        yield events.status(events.STAGE_VERIFYING, "임계값을 넘는 근거를 찾지 못했다.")
        yield events.token(NO_EVIDENCE_MESSAGE)
        yield events.done(events.FINISH_STOP)
        return

    yield events.status(events.STAGE_WRITING, "검색된 자료를 근거로 답변을 작성하는 중이다.")
    messages = build_messages(question, result.accepted)

    try:
        for piece in chat_stream(messages, settings=_settings):
            yield events.token(piece)
    except LLMError as exc:
        yield events.error("model_call_failed", str(exc))
        yield events.done(events.FINISH_ERROR)
        return

    yield events.done(events.FINISH_STOP)


@app.post("/api/chat")
def chat(req: ChatRequest):
    return StreamingResponse(_chat_event_stream(req.question), media_type="text/event-stream")
