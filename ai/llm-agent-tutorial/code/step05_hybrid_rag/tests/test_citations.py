"""citations.validate_and_link_citations 단위 테스트.

Milvus나 모델 서버를 쓰지 않는다 — RetrievedSource 리스트를 손으로 만들어
"모델이 지어낸 인용을 걸러내는지"만 검증한다.
"""

from __future__ import annotations

import unittest

from docagent.rag.citations import (
    RetrievedSource,
    build_source_url,
    validate_and_link_citations,
)

APP_BASE_URL = "http://localhost:8080"


def _source(label: str, doc_id: str = "q2-report", page: int = 3, chunk_index: int = 2) -> RetrievedSource:
    return RetrievedSource(
        label=label,
        chunk_id=f"{doc_id}#p{page}-c{chunk_index}",
        doc_id=doc_id,
        doc_title="2분기 리포트",
        page=page,
        chunk_index=chunk_index,
        text="샘플 청크 원문입니다.",
        source_url=f"data/docs/{doc_id}.md",
    )


class BuildSourceUrlTest(unittest.TestCase):
    def test_matches_project_spec_format(self):
        url = build_source_url(APP_BASE_URL, "q2-report", 3, 2)
        self.assertEqual(url, "http://localhost:8080/sources/q2-report?page=3&chunk=2")

    def test_strips_trailing_slash_on_base_url(self):
        url = build_source_url("http://localhost:8080/", "q2-report", 0, 0)
        self.assertEqual(url, "http://localhost:8080/sources/q2-report?page=0&chunk=0")


class ValidateAndLinkCitationsTest(unittest.TestCase):
    def test_valid_citation_is_kept_and_linked(self):
        retrieved = [_source("S1")]
        answer = "매출이 늘었다[S1]."
        result = validate_and_link_citations(answer, retrieved, app_base_url=APP_BASE_URL)

        self.assertIn("[S1]", result.text)
        self.assertEqual(result.used_labels, ["S1"])
        self.assertEqual(result.dropped_labels, [])
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.sources[0]["id"], "S1")
        self.assertEqual(
            result.sources[0]["url"], "http://localhost:8080/sources/q2-report?page=3&chunk=2"
        )

    def test_fake_citation_is_dropped_and_noted(self):
        # 검색 결과는 S1 하나뿐인데 모델이 존재하지 않는 S9를 인용한 경우.
        retrieved = [_source("S1")]
        answer = "매출이 늘었다[S1]. 다른 근거도 있다[S9]."
        result = validate_and_link_citations(answer, retrieved, app_base_url=APP_BASE_URL)

        self.assertNotIn("[S9]", result.text)
        self.assertIn("[S1]", result.text)
        self.assertEqual(result.dropped_labels, ["S9"])
        self.assertIn("S9", result.text)  # 알림 문구에 어떤 라벨을 버렸는지 남는다.
        self.assertEqual([s["id"] for s in result.sources], ["S1"])

    def test_all_citations_fake_when_no_sources_retrieved(self):
        result = validate_and_link_citations("근거가 있다[S1].", [], app_base_url=APP_BASE_URL)

        self.assertEqual(result.sources, [])
        self.assertEqual(result.dropped_labels, ["S1"])
        self.assertNotIn("[S1]", result.text)

    def test_duplicate_citation_of_same_label_counted_once(self):
        retrieved = [_source("S1")]
        answer = "첫 문장[S1]. 두 번째 문장도 같은 근거[S1]."
        result = validate_and_link_citations(answer, retrieved, app_base_url=APP_BASE_URL)

        self.assertEqual(result.used_labels, ["S1"])
        self.assertEqual(len(result.sources), 1)

    def test_dropped_labels_are_sorted_and_deduplicated(self):
        retrieved = [_source("S1")]
        answer = "근거 없음[S5][S9][S5]."
        result = validate_and_link_citations(answer, retrieved, app_base_url=APP_BASE_URL)

        self.assertEqual(result.dropped_labels, ["S5", "S9"])

    def test_answer_without_any_citation_is_untouched_besides_whitespace(self):
        retrieved = [_source("S1")]
        answer = "이 문장에는 인용이 없다."
        result = validate_and_link_citations(answer, retrieved, app_base_url=APP_BASE_URL)

        self.assertEqual(result.text, answer)
        self.assertEqual(result.sources, [])
        self.assertEqual(result.dropped_labels, [])

    def test_multiple_valid_sources_preserve_first_use_order(self):
        retrieved = [_source("S1", chunk_index=1), _source("S2", chunk_index=2)]
        answer = "두 번째 근거 먼저[S2]. 그다음 첫 번째[S1]."
        result = validate_and_link_citations(answer, retrieved, app_base_url=APP_BASE_URL)

        self.assertEqual(result.used_labels, ["S2", "S1"])
        self.assertEqual([s["id"] for s in result.sources], ["S2", "S1"])


if __name__ == "__main__":
    unittest.main()
