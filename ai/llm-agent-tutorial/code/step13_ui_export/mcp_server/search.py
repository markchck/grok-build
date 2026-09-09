"""Dense 검색 — 이 장의 MCP 서버가 노출하는 문서 검색의 실제 구현.

임베딩은 OpenAI 호환 ``/embeddings`` 엔드포인트를 직접 호출한다(``docagent.llm``
을 재사용하지 않는다 — mcp_server는 docagent를 import하지 않는다는 원칙,
`mcp_server/__init__.py` 참고). 그래서 최소한의 임베딩 호출 함수를 이 파일에
따로 둔다.
"""

from __future__ import annotations

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from mcp_server.config import get_mcp_server_settings
from mcp_server.store import DENSE_FIELD, PK_FIELD, ensure_loaded, get_client

OUTPUT_FIELDS = ["text", "doc_id", "doc_title", "page", "chunk_index", "source_url"]


class EmbeddingError(RuntimeError):
    """임베딩 서버 호출 실패. MCP 도구 함수가 이걸 잡아서 mcp ToolError로 바꾼다."""


def embed_query(text: str) -> list[float]:
    settings = get_mcp_server_settings()
    client = OpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key, timeout=30)
    try:
        resp = client.embeddings.create(model=settings.embedding_model, input=[text])
    except APITimeoutError as exc:
        raise EmbeddingError("임베딩 서버 응답 시간 초과") from exc
    except APIConnectionError as exc:
        raise EmbeddingError(f"임베딩 서버에 연결할 수 없다: {exc}") from exc
    except APIStatusError as exc:
        raise EmbeddingError(f"임베딩 서버 오류 응답: {exc.status_code} {exc.message}") from exc
    except APIError as exc:
        raise EmbeddingError(f"임베딩 서버 호출 실패: {exc}") from exc
    return resp.data[0].embedding


def _to_hit(raw: dict) -> dict:
    entity = raw.get("entity", {})
    return {
        "pk": raw[PK_FIELD],
        "text": entity.get("text", ""),
        "doc_id": entity.get("doc_id", ""),
        "doc_title": entity.get("doc_title", ""),
        "page": entity.get("page", 0),
        "chunk_index": entity.get("chunk_index", 0),
        "source_url": entity.get("source_url", ""),
        "score": raw.get("distance", 0.0),
    }


def dense_search(query: str, *, limit: int = 5) -> list[dict]:
    settings = get_mcp_server_settings()
    client = get_client()
    ensure_loaded(client, settings.milvus_collection)
    vector = embed_query(query)
    results = client.search(
        settings.milvus_collection,
        data=[vector],
        anns_field=DENSE_FIELD,
        search_params={"metric_type": "COSINE", "params": {}},
        limit=limit,
        output_fields=OUTPUT_FIELDS,
    )
    return [_to_hit(hit) for hit in results[0]] if results else []
