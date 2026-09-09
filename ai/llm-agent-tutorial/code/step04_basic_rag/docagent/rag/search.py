"""질의 임베딩 + Milvus 검색 + 근거 부족 판단 + 프롬프트 구성.

근거 부족 처리는 두 겹으로 막는다.
1) 애플리케이션 쪽 차단: 검색 결과가 비었거나, 모든 결과의 유사도 점수가
   임계값(RETRIEVAL_SCORE_THRESHOLD) 미만이면 모델을 아예 호출하지 않고
   고정된 "근거를 찾지 못했다" 응답을 돌려준다. 모델에 물어보나 마나
   지어낼 수밖에 없는 상황을 프롬프트 지시만으로 막지 않는다는 뜻이다.
2) 모델 쪽 지시: 임계값을 통과해 컨텍스트가 채워진 경우에도, 시스템 프롬프트에
   "제공된 자료에 없는 내용은 답하지 말고 모른다고 답하라"를 명시해 모델이
   컨텍스트 밖 지식으로 채워 넣지 않게 한다.
두 번째만으로는 모델이 지시를 무시하고 그럴듯한 답을 지어낼 수 있으므로
반드시 첫 번째(애플리케이션 로직)를 함께 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass

from openai import OpenAI

from docagent.llm import embed_query
from docagent.rag.store import SearchHit, search_dense
from pymilvus import MilvusClient

NO_EVIDENCE_MESSAGE = (
    "등록된 자료에서 이 질문에 대한 근거를 찾지 못했다. "
    "다른 표현으로 다시 질문하거나, 관련 문서를 먼저 등록해달라."
)

SYSTEM_PROMPT = (
    "당신은 사내 문서 검색 결과를 근거로만 답하는 어시스턴트다. "
    "아래 [자료] 안의 내용만 근거로 답하고, [자료]에 없는 내용은 "
    "일반 지식으로 채우지 말고 '제공된 자료에서 확인되지 않는다'고 답하라. "
    "[자료]의 문서명을 답변 안에서 밝혀 어떤 자료를 근거로 했는지 알 수 있게 하라."
)


@dataclass(frozen=True)
class RetrievalResult:
    hits: list[SearchHit]
    accepted: list[SearchHit]  # 임계값을 통과한 결과만
    has_evidence: bool


def retrieve(
    milvus_client: MilvusClient,
    collection_name: str,
    llm_client: OpenAI,
    embedding_model: str,
    question: str,
    top_k: int,
    score_threshold: float,
) -> RetrievalResult:
    query_vector = embed_query(llm_client, embedding_model, question)
    hits = search_dense(milvus_client, collection_name, query_vector, top_k)
    accepted = [h for h in hits if h.score >= score_threshold]
    return RetrievalResult(hits=hits, accepted=accepted, has_evidence=bool(accepted))


def build_context_block(hits: list[SearchHit]) -> str:
    """검색된 청크를 프롬프트에 넣을 [자료] 블록으로 만든다.

    각 청크 앞에 청크 ID를 붙여, 모델이 답변 안에서 어느 청크를 썼는지
    (사람이) 추적할 수 있게 한다. 5단계부터는 이 ID를 [S1] 같은 인용
    표시와 연결해 클릭 가능한 근거 링크로 바꾼다.
    """
    parts = []
    for hit in hits:
        parts.append(f"### {hit.doc_title} ({hit.pk})\n{hit.text}")
    return "\n\n".join(parts)


def build_messages(question: str, accepted_hits: list[SearchHit]) -> list[dict[str, str]]:
    context = build_context_block(accepted_hits)
    user_content = f"[자료]\n{context}\n\n[질문]\n{question}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
