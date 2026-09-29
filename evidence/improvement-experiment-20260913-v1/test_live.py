"""Launcher invariants, no real provider traffic."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import live


class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = live.inputs()

    def test_core42_all_included(self):
        self.assertEqual(len(self.records), 42)
        self.assertEqual({c["shadow_bucket"] for c, _, _ in self.records}, {"simple", "multi"})

    def test_same_contexts_and_max_tokens(self):
        c, r, g = self.records[0]
        left = live.prompt_and_request(live.CONDITIONS[0], c, g)[2]
        right = live.prompt_and_request(live.CONDITIONS[1], c, g)[2]
        self.assertEqual(left["generationConfig"]["maxOutputTokens"], right["generationConfig"]["maxOutputTokens"])
        for body in (left, right):
            self.assertNotIn("temperature", body["generationConfig"])

    def test_gold_never_used_for_generation(self):
        case, r, g = self.records[0]
        poisoned = copy.deepcopy(case)
        poisoned["required_claims"] = [{"claim_id": "DO_NOT_EXPORT_GOLD", "text": "SECRET_REFERENCE_SENTINEL"}]
        for condition in live.CONDITIONS:
            original = live.prompt_and_request(condition, case, g)
            altered = live.prompt_and_request(condition, poisoned, g)
            self.assertEqual(original, altered)

    def test_baseline_safety_instruction_preserved(self):
        c, r, g = self.records[0]
        _, system, _ = live.prompt_and_request(live.CONDITIONS[1], c, g)
        self.assertTrue(system.startswith(live.gen.SYSTEM_INSTRUCTION.rsplit("출처 번호나", 1)[0]))

    def test_no_key_read_in_preparation(self):
        with tempfile.TemporaryDirectory() as d, patch.object(live, "load_key", side_effect=AssertionError("key read")):
            live.prepare(Path(d).resolve() / "prep")

    def test_quota_and_duplicate_reservation(self):
        with tempfile.TemporaryDirectory() as d:
            budget = live.Budget(Path(d))
            with patch.object(live, "CAP", 2):
                budget.reserve("a", {"x": 1})
                with self.assertRaises(Exception):
                    budget.reserve("a", {"x": 1})
                budget.reserve("b", {"x": 2})
                with self.assertRaisesRegex(ValueError, "attempt_cap"):
                    budget.reserve("c", {})
            self.assertEqual(budget.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 2)
            budget.db.close()

    def test_no_redirect(self):
        with self.assertRaises(PermissionError):
            live.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid")

    def test_network_requires_explicit_capability(self):
        with self.assertRaises(PermissionError):
            live.audit("urllib.Request", (live.API_URLS[live.MODEL],))
        with self.assertRaises(PermissionError):
            live.audit("socket.connect", (None, ("example.invalid", 443)))

    def test_citation_gate_still_runs(self):
        c, r, g = self.records[0]
        p, s, b = live.prompt_and_request(live.CONDITIONS[1], c, g)
        raw = json.dumps({"claims": [], "unanswered": ["제공된 문서에서 확인할 수 없습니다."]}, ensure_ascii=False)
        result = live.answer_record(r, c, g, live.CONDITIONS[1], raw, b, p, s, 0, "mock")
        self.assertEqual(result["security"]["output_gate"]["decision"], "abstain")
        self.assertTrue(result["experiment_id"].startswith("SYNTHETIC-"))

    def test_wrong_manifest_rejected_before_key(self):
        with tempfile.TemporaryDirectory() as d, patch.object(live, "load_key", side_effect=AssertionError("key read")):
            prep = Path(d).resolve() / "prep"
            live.prepare(prep)
            with self.assertRaisesRegex(ValueError, "manifest_hash"):
                live.run(prep, Path(d) / "run", "0" * 64, "live", live.AUTHORIZATION)


if __name__ == "__main__":
    unittest.main()
