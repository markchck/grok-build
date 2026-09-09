"""reciprocal_rank_fusion / weighted_fusion 단위 테스트.

Milvus의 RRFRanker()/WeightedRanker()가 서버 안에서 하는 계산을 그대로
파이썬으로 옮긴 참고용 함수를 검증한다. 외부 서버가 필요 없다.
"""

from __future__ import annotations

import unittest

from docagent.rag.search import reciprocal_rank_fusion, weighted_fusion


class ReciprocalRankFusionTest(unittest.TestCase):
    def test_doc_ranked_first_in_both_lists_wins(self):
        dense = ["a", "b", "c"]
        sparse = ["a", "c", "b"]
        result = reciprocal_rank_fusion([dense, sparse], k=60)
        ranked_ids = [doc_id for doc_id, _ in result]
        self.assertEqual(ranked_ids[0], "a")

    def test_formula_matches_manual_computation(self):
        # score(d) = sum(1 / (k + rank_i(d)))
        dense = ["a", "b"]
        sparse = ["b", "a"]
        k = 60
        result = dict(reciprocal_rank_fusion([dense, sparse], k=k))

        expected_a = 1 / (k + 1) + 1 / (k + 2)  # dense 1위, sparse 2위
        expected_b = 1 / (k + 2) + 1 / (k + 1)  # dense 2위, sparse 1위
        self.assertAlmostEqual(result["a"], expected_a)
        self.assertAlmostEqual(result["b"], expected_b)
        # 대칭적으로 1위 한 번씩 나눠 가지므로 점수가 같다.
        self.assertAlmostEqual(result["a"], result["b"])

    def test_doc_present_in_only_one_list_still_scored(self):
        dense = ["a", "b"]
        sparse = ["c"]
        result = dict(reciprocal_rank_fusion([dense, sparse], k=60))
        self.assertIn("c", result)
        self.assertGreater(result["c"], 0)

    def test_limit_truncates_result(self):
        dense = ["a", "b", "c", "d"]
        result = reciprocal_rank_fusion([dense], k=60, limit=2)
        self.assertEqual(len(result), 2)

    def test_smaller_k_amplifies_rank_gap(self):
        dense = ["a", "b"]
        result_small_k = dict(reciprocal_rank_fusion([dense], k=1))
        result_large_k = dict(reciprocal_rank_fusion([dense], k=1000))

        gap_small_k = result_small_k["a"] - result_small_k["b"]
        gap_large_k = result_large_k["a"] - result_large_k["b"]
        self.assertGreater(gap_small_k, gap_large_k)


class WeightedFusionTest(unittest.TestCase):
    def test_weighted_average_matches_manual_computation(self):
        dense = [("a", 0.9), ("b", 0.4)]
        sparse = [("a", 0.2), ("b", 0.8)]
        result = dict(weighted_fusion([dense, sparse], [0.7, 0.3]))

        self.assertAlmostEqual(result["a"], 0.7 * 0.9 + 0.3 * 0.2)
        self.assertAlmostEqual(result["b"], 0.7 * 0.4 + 0.3 * 0.8)

    def test_weight_shifts_ranking(self):
        dense = [("a", 0.9), ("b", 0.1)]
        sparse = [("a", 0.1), ("b", 0.9)]

        dense_heavy = weighted_fusion([dense, sparse], [0.9, 0.1])
        sparse_heavy = weighted_fusion([dense, sparse], [0.1, 0.9])

        self.assertEqual(dense_heavy[0][0], "a")
        self.assertEqual(sparse_heavy[0][0], "b")

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(ValueError):
            weighted_fusion([[("a", 1.0)]], [0.5, 0.5])


if __name__ == "__main__":
    unittest.main()
