"""답변 인용을 검증해 가짜 출처를 막는다 (PROJECT-SPEC.md 5장).

핵심 규칙은 하나다: **모델이 답변에 쓴 URL이나 문서명을 그대로 믿지 않는다.**
모델에게는 ``[S1]``, ``[S2]`` 형태의 인용 표시만 쓰게 하고, 애플리케이션이
"이번 검색에서 실제로 반환된 청크" 목록과 대조해서만 URL을 만든다.
목록에 없는 번호를 인용했다면 그 표시를 답변에서 지우고, 지웠다는 사실을
답변 끝에 남긴다.

이 모듈은 Milvus나 모델 서버를 호출하지 않는다. 순수 텍스트 처리이므로
외부 서버 없이 단위 테스트로 검증할 수 있다(tests/test_citations.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from docagent.tracing import SpanKind, set_output, traced_span

# "[S1]", "[S12]"처럼 S 뒤에 숫자가 오는 표시만 인용으로 인정한다.
_CITATION_PATTERN = re.compile(r"\[S(\d+)\]")


@dataclass(frozen=True)
class RetrievedSource:
    """이번 검색에서 실제로 반환된 청크 하나.

    ``label``은 프롬프트에서 모델에게 보여준 번호("S1" 등)와 검색 결과
    순서가 1:1로 대응한다는 전제를 코드로 강제하기 위한 필드다 — 순서가
    바뀌면 인용이 엉뚱한 청크를 가리키게 되므로, 검색 결과를 만들 때와
    프롬프트를 만들 때 반드시 같은 리스트, 같은 순서를 써야 한다.
    """

    label: str  # "S1", "S2", ...
    chunk_id: str  # "{doc_id}#p{page}-c{chunk_index}"
    doc_id: str
    doc_title: str
    page: int
    chunk_index: int
    text: str
    source_url: str  # 원본 문서 URL 또는 로컬 경로 (근거 링크와는 다르다)
    score: float = 0.0


@dataclass(frozen=True)
class CitationResult:
    text: str  # 검증을 마친 답변 (무효 인용 제거 + 알림 문구 포함 가능)
    sources: list[dict]  # sources 이벤트에 그대로 넣을 수 있는 형태
    used_labels: list[str]  # 실제로 답변에 남은 인용 표시
    dropped_labels: list[str]  # 버린 인용 표시 (중복 제거, 번호순)


def make_source_label(index: int) -> str:
    """검색 결과의 0-base 순번을 인용 라벨로 바꾼다. 0 -> "S1"."""
    return f"S{index + 1}"


def build_source_url(app_base_url: str, doc_id: str, page: int, chunk_index: int) -> str:
    """PROJECT-SPEC.md 5장 형식의 근거 링크 URL을 만든다.

    ``{APP_BASE_URL}/sources/{doc_id}?page={page}&chunk={chunk_index}``
    """
    base = app_base_url.rstrip("/")
    return f"{base}/sources/{doc_id}?page={page}&chunk={chunk_index}"


def build_retrieved_sources(
    hits: list[dict],
    *,
    app_base_url: str,
) -> list[RetrievedSource]:
    """검색 결과(딕셔너리 리스트)를 RetrievedSource 리스트로 바꾸며 라벨을 붙인다.

    ``hits``의 각 원소는 store.py/search.py가 돌려주는 Milvus 검색 결과
    한 건으로, 최소한 pk(청크 ID), text, doc_id, doc_title, page,
    chunk_index, source_url, score 키를 담는다.
    """
    sources: list[RetrievedSource] = []
    for i, hit in enumerate(hits):
        sources.append(
            RetrievedSource(
                label=make_source_label(i),
                chunk_id=hit["pk"],
                doc_id=hit["doc_id"],
                doc_title=hit.get("doc_title", hit["doc_id"]),
                page=int(hit.get("page", 0)),
                chunk_index=int(hit.get("chunk_index", 0)),
                text=hit.get("text", ""),
                source_url=hit.get("source_url", ""),
                score=float(hit.get("score", 0.0)),
            )
        )
    return sources


def validate_and_link_citations(
    answer_text: str,
    retrieved: list[RetrievedSource],
    *,
    app_base_url: str,
) -> CitationResult:
    """답변 속 [S1] 같은 인용 표시를 검증하고 근거 링크로 바꿀 준비를 한다.

    처리 순서:
    1. 답변 텍스트에서 ``[Sn]`` 패턴을 모두 찾는다.
    2. ``n``이 이번 검색 결과의 라벨 집합에 있으면 유효한 인용으로 남긴다.
    3. 없으면(모델이 지어낸 번호, 범위를 벗어난 번호) 그 표시를 답변에서
       지우고 dropped_labels에 기록한다.
    4. 버린 인용이 있으면 답변 끝에 알림 문구를 덧붙인다.
    5. 실제로 남은 인용만 근거 링크로 변환해 sources 이벤트용 목록을 만든다.

    이 함수는 "이번 검색에서 반환된 청크 ID 집합"만을 신뢰 가능한 근거로
    보고, 모델이 답변 본문에 직접 적은 URL이나 문서명은 절대 사용하지
    않는다 —애초에 이 함수는 그런 텍스트를 읽지 않고 [Sn] 표시만 읽는다.

    이 함수가 하는 검증은 **인용이 실제 검색 결과 집합 안에 있는가**
    (기계적으로 100% 판정 가능)뿐이다. "그 청크가 그 문장의 주장을 실제로
    뒷받침하는가"(의미적 정합성)는 이 함수의 책임이 아니다 — 그 판정은
    사람 검토 또는 LLM-judge가 필요하며 eval/citation_eval.py에서 별도로
    다룬다. app.py의 운영 경로가 이 함수에서 하는 검증과, 평가 스크립트가
    오프라인에서 하는 검증(a)이 **같은 로직**이라는 점이 이 장의 핵심이다 —
    운영에서 쓰는 판정 함수를 평가에서 다시 구현하면 둘이 몰래 어긋날 수
    있으므로, eval/citation_eval.py는 이 함수가 돌려주는 used_labels/
    dropped_labels를 그대로 재사용한다.
    """
    with traced_span(
        "citations.validate",
        kind=SpanKind.CHAIN,
        input_value=answer_text[:500],
        attributes={"citations.candidate_count": len(retrieved)},
    ) as span:
        by_label = {s.label: s for s in retrieved}

        used_labels: list[str] = []
        dropped_labels: list[str] = []

        def _replace(match: re.Match[str]) -> str:
            label = f"S{match.group(1)}"
            if label in by_label:
                if label not in used_labels:
                    used_labels.append(label)
                return match.group(0)
            dropped_labels.append(label)
            return ""

        cleaned = _CITATION_PATTERN.sub(_replace, answer_text)
        # 표시를 지운 자리에 남는 중복 공백을 정리한다.
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)

        dropped_unique = sorted(set(dropped_labels), key=lambda label: int(label[1:]))
        if dropped_unique:
            cleaned = (
                f"{cleaned.rstrip()}\n\n"
                f"[알림: 모델이 인용한 표시 중 이번 검색 결과에 없는 항목을 "
                f"제거했다: {', '.join(dropped_unique)}]"
            )

        sources_payload = [
            {
                "id": label,
                "doc": by_label[label].doc_title,
                "page": by_label[label].page,
                "url": build_source_url(
                    app_base_url,
                    by_label[label].doc_id,
                    by_label[label].page,
                    by_label[label].chunk_index,
                ),
                "snippet": by_label[label].text[:200],
            }
            for label in used_labels
        ]

        span.set_attribute("citations.used_count", len(used_labels))
        span.set_attribute("citations.dropped_count", len(dropped_unique))
        if dropped_unique:
            span.set_attribute("citations.dropped_labels", dropped_unique)
        set_output(span, f"used={used_labels} dropped={dropped_unique}")

        return CitationResult(
            text=cleaned,
            sources=sources_payload,
            used_labels=used_labels,
            dropped_labels=dropped_unique,
        )
