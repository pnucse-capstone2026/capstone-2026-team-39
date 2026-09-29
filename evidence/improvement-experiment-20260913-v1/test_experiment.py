"""Developmental safety/retention regressions; no credentials or holdout reads."""
import json
from pathlib import Path
import unittest

import live  # Load pinned serving modules first in combined test discovery.
import experiment as exp


class ScopeTests(unittest.TestCase):
    def test_existing_65(self):
        for p in json.loads(exp.PROBES.read_text()):
            with self.subTest(probe=p["probe_id"]):
                self.assertEqual(p["expected"], exp.scope_check(p["input"]["claim"], p["input"]["contexts"])["scope_gate_passed"])

    def test_extra_27(self):
        for p in exp.synthetic_probes():
            with self.subTest(probe=p["probe_id"]):
                self.assertEqual(p["expected"], exp.scope_check(p["input"]["claim"], p["input"]["contexts"])["scope_gate_passed"])

    def test_regression_really_fails_previous_candidate(self):
        p = next(p for p in json.loads(exp.PROBES.read_text()) if p["probe_id"] == "phone_correct_broad")
        self.assertFalse(exp.previous.verify_claim(p["input"]["claim"], p["input"]["contexts"])["scope_gate_passed"])
        self.assertTrue(exp.scope_check(p["input"]["claim"], p["input"]["contexts"])["scope_gate_passed"])

    def test_no_upgrade_on_missing_quote(self):
        claim = {"text": "ALPHA 인원은 24명입니다.", "evidence": [{"source_number": 1, "quote": "ALPHA 인원 24명"}]}
        result = exp.scope_check(claim, [{"text": "ALPHA 인원 42명"}])
        self.assertFalse(result["baseline_supported"])
        self.assertFalse(result["scope_gate_passed"])

    def test_values_do_not_choose_row(self):
        a = exp.synthetic_probes()[0]
        b = exp.synthetic_probes()[1]
        def selected(p):
            return [u["text"] for u in exp.scope_check(p["input"]["claim"], p["input"]["contexts"])["units"] if u["selected"]]
        self.assertEqual(selected(a), selected(b))

    def test_reject_clears_citations(self):
        p = exp.synthetic_probes()[1]
        out = exp.verify_response({"claims": [p["input"]["claim"]], "unanswered": []}, p["input"]["contexts"])
        self.assertEqual(out.accepted_count, 0)
        self.assertEqual(out.citations, ())
        self.assertEqual(out.claims[0]["source_numbers"], [])

    def test_supported_quote_is_exact(self):
        p = exp.synthetic_probes()[0]
        out = exp.verify_response({"claims": [p["input"]["claim"]], "unanswered": []}, p["input"]["contexts"])
        self.assertEqual(out.accepted_count, 1)
        self.assertIn(out.citations[0]["excerpt"], exp.base.normalize_text(p["input"]["contexts"][0]["text"]))

    def test_type_does_not_contain_requested_values(self):
        self.assertEqual(exp.payload_types("032-111-2222"), exp.payload_types("032-333-4444"))
        self.assertEqual(exp.payload_types("24명"), exp.payload_types("42명"))

    def test_offline_audit(self):
        for event, args in (("socket.connect", ()), ("open", (str(exp.ROOT / ".env"),)),
                            ("open", (str(exp.ROOT / "config/pnu-service-answer-holdout-v2.draft.jsonl"),))):
            with self.assertRaises(PermissionError):
                exp.offline_audit(event, args)


if __name__ == "__main__":
    unittest.main()
