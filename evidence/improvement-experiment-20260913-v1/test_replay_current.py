import copy
import unittest
from unittest.mock import patch

import replay_current as replay


class CurrentReplayTests(unittest.TestCase):
    def test_current_candidate_has_not_been_modified(self):
        self.assertEqual(replay.sha(replay.candidate.__file__), replay.CANDIDATE_SHA)

    def test_stress_expected_labels_are_explicit_and_unique(self):
        rows = replay.stress_cases()
        self.assertEqual(len(rows), 29)
        self.assertEqual(len({r["probe_id"] for r in rows}), len(rows))
        self.assertTrue(all(type(r["expected"]) is bool for r in rows))

    def test_output_gate_rejects_fabricated_citation(self):
        contexts = [replay.source()]
        claim = "제7회 ALPHA 공모전의 신청 마감은 2028년 8월 5일입니다."
        with replay.candidate.enabled():
            answer = replay.api.build_rag_response("마감일", contexts, claim, "saved-synthetic")
        self.assertGreater(sum(c["supported"] for c in answer["claims"]), 0)
        clean = replay.enforce_output(answer, contexts)
        self.assertEqual(clean.invalid_citations, 0)
        bad = copy.deepcopy(answer)
        bad["claims"][0]["citations"][0]["excerpt_sha256"] = "0" * 64
        blocked = replay.enforce_output(bad, contexts)
        self.assertEqual(blocked.decision, "abstain")
        self.assertEqual(blocked.response["claims"], [])

    def test_no_provider_or_secret_access(self):
        with self.assertRaises(PermissionError):
            replay.offline_audit("socket.connect", (None, ("example.com", 443)))
        with self.assertRaises(PermissionError):
            replay.offline_audit("open", (".env", "r"))
        with self.assertRaises(PermissionError):
            replay.offline_audit("open", ("config/fake-holdout.jsonl", "r"))

    def test_original_attribution_restored_after_error(self):
        original = replay.api.attribute_claim
        with self.assertRaises(RuntimeError):
            with replay.candidate.enabled():
                raise RuntimeError("synthetic")
        self.assertIs(replay.api.attribute_claim, original)


if __name__ == "__main__":
    unittest.main()
