import unittest
from analyze_live import CONDITIONS, paired, summarize


def row(case, condition, score=None, gfc=None, status="valid"):
    return {"case_id": case, "condition_id": condition, "score": score, "gfc": gfc, "status": status}


class AnalysisTests(unittest.TestCase):
    def test_error_is_not_zero(self):
        s = summarize([row("a", CONDITIONS[0], 2, True), row("b", CONDITIONS[0], status="error")])
        self.assertEqual(s["mean_score_valid_only"], 2)
        self.assertEqual(s["gfc_rate_valid_only"], 1)
        self.assertEqual(s["observed_successes_per_scheduled"], .5)
        self.assertEqual(s["errors"], 1)

    def test_no_valid_has_no_quality_estimate(self):
        s = summarize([row("a", CONDITIONS[0], status="error")])
        self.assertIsNone(s["mean_score_valid_only"])
        self.assertIsNone(s["gfc_rate_valid_only"])

    def test_only_common_valid_questions_are_paired(self):
        p = paired([row("a", CONDITIONS[0], 2, True), row("a", CONDITIONS[1], 1, False),
                    row("b", CONDITIONS[0], 2, True), row("b", CONDITIONS[1], status="error")])
        self.assertEqual(p["common_valid_n"], 1)
        self.assertEqual(p["score_losses"], 1)
        self.assertEqual(p["gfc_loss_case_ids"], ["a"])

    def test_paired_counts(self):
        rows = [row("a", CONDITIONS[0], 0, False), row("a", CONDITIONS[1], 2, True),
                row("b", CONDITIONS[0], 2, True), row("b", CONDITIONS[1], 2, True),
                row("c", CONDITIONS[0], 1, False), row("c", CONDITIONS[1], 0, False)]
        p = paired(rows)
        self.assertEqual((p["score_wins"], p["score_losses"], p["score_ties"]), (1, 1, 1))
        self.assertEqual(p["gfc_gain_case_ids"], ["a"])
        self.assertEqual(p["mcnemar_exact_two_sided_p"], 1)


if __name__ == "__main__":
    unittest.main()
