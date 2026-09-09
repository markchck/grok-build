"""GraphState 리듀서(merge_hits) 단위 테스트. Milvus·모델 서버가 필요 없다."""

from __future__ import annotations

import unittest

from docagent.graph.state import merge_hits


class MergeHitsTest(unittest.TestCase):
    def test_disjoint_lists_are_concatenated_and_sorted_by_score(self):
        left = [{"pk": "a", "score": 0.5}]
        right = [{"pk": "b", "score": 0.9}]
        merged = merge_hits(left, right)
        self.assertEqual([h["pk"] for h in merged], ["b", "a"])

    def test_duplicate_pk_keeps_higher_score(self):
        left = [{"pk": "x", "score": 0.3}, {"pk": "y", "score": 0.9}]
        right = [{"pk": "x", "score": 0.7}, {"pk": "z", "score": 0.1}]
        merged = merge_hits(left, right)
        by_pk = {h["pk"]: h["score"] for h in merged}
        self.assertEqual(by_pk["x"], 0.7)  # 더 높은 쪽이 남는다
        self.assertEqual(set(by_pk), {"x", "y", "z"})

    def test_empty_inputs(self):
        self.assertEqual(merge_hits([], []), [])
        self.assertEqual(merge_hits([{"pk": "a", "score": 1.0}], []), [{"pk": "a", "score": 1.0}])


if __name__ == "__main__":
    unittest.main()
