from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import analyze_shadow_diagnostics as diagnostic
from evaluate_evidence_binding_experiment import payload, synthetic_cases, check_cases, counts
from rag.evidence_binding_experiment import split_preserving_dates, verify_claim, _dates
from rag.grounded_claims_v2 import GroundedClaimsError, normalize_text, sha256_text


class ScopeGateTests(unittest.TestCase):
    def test_contrastive_development_matrix(self):
        for case in synthetic_cases():
            with self.subTest(case=case["probe_id"]):
                result = verify_claim(case["claim"], case["contexts"])
                self.assertEqual(result["scope_gate_passed"], case["expected"])

    def test_baseline_really_fails_contrastive_matrix(self):
        results = check_cases(synthetic_cases())
        self.assertGreater(counts(results, "baseline_supported").get("false_accept", 0), 0)
        self.assertEqual(counts(results, "scope_gate_passed").get("false_accept", 0), 0)

    def test_original_six_reproductions(self):
        self.assertEqual(diagnostic.digest(diagnostic.PACKET), diagnostic.PACKET_SHA)
        packet = json.loads(diagnostic.PACKET.read_text())
        from rag import grounded_claims_v2
        cases = {i["case_id"]: i for i in packet["items"]}
        for probe in diagnostic.c2_probes(packet, grounded_claims_v2):
            with self.subTest(probe=probe["probe_id"]):
                result = verify_claim(probe["input"]["claims"][0], cases[probe["case_id"]]["runs"][0]["contexts"])
                self.assertEqual(result["scope_gate_passed"], probe["expected_supported_from_source"])

    def test_narrow_quote_cannot_use_unquoted_row(self):
        text = "ALPHA 모집인원: 24명\nALPHA 모집인원: 36명"
        result = verify_claim(payload("ALPHA 모집인원은 36명입니다.", "ALPHA 모집인원: 24명"), [{"text": text}])
        self.assertFalse(result["scope_gate_passed"])

    def test_quote_may_start_inside_source_row(self):
        source = "①\tALPHA 모집인원: 24명\n②\tBETA 모집인원: 36명"
        result = verify_claim(payload("ALPHA 모집인원은 24명입니다.", "ALPHA 모집인원: 24명"), [{"text": source}])
        self.assertTrue(result["scope_gate_passed"])
        for unit in result["units"]:
            self.assertEqual(normalize_text(source)[unit["normalized_start"]:unit["normalized_end"]], unit["text"])
            self.assertEqual(sha256_text(unit["text"]), unit["sha256"])

    def test_duplicate_quote_occurrences_are_audited(self):
        source = "ALPHA 모집인원: 24명\nALPHA 모집인원: 24명"
        result = verify_claim(payload("ALPHA 모집인원은 24명입니다.", "ALPHA 모집인원: 24명"), [{"text": source}])
        self.assertTrue(result["scope_gate_passed"])
        self.assertEqual(len(result["units"]), 2)

    def test_normalized_quote_restores_original_line_boundaries(self):
        source = "ALPHA 모집인원: 24명\nBETA 모집인원: 36명"
        result = verify_claim(payload("ALPHA 모집인원은 36명입니다.", normalize_text(source)), [{"text": source}])
        self.assertFalse(result["scope_gate_passed"])
        self.assertEqual(len(result["units"]), 2)

    def test_does_not_promote_baseline_rejections(self):
        result = verify_claim(payload("ALPHA 모집인원은 99명입니다.", "ALPHA 모집인원: 24명"), [{"text": "ALPHA 모집인원: 24명"}])
        self.assertFalse(result["baseline_supported"])
        self.assertFalse(result["scope_gate_passed"])

    def test_cross_source_value_pool_is_rejected(self):
        claim = payload("ALPHA 모집인원은 36명입니다.", "ALPHA 모집인원: 24명")
        claim["evidence"].append({"source_number": 2, "quote": "BETA 모집인원: 36명"})
        result = verify_claim(claim, [{"text": "ALPHA 모집인원: 24명"}, {"text": "BETA 모집인원: 36명"}])
        self.assertTrue(result["baseline_supported"])
        self.assertFalse(result["scope_gate_passed"])

    def test_unknown_source_fails_closed(self):
        result = verify_claim(payload("ALPHA 모집인원은 24명입니다.", "ALPHA 모집인원: 24명", 2), [])
        self.assertFalse(result["scope_gate_passed"])

    def test_fabricated_quote_fails_closed(self):
        result = verify_claim(payload("ALPHA 모집인원은 24명입니다.", "ALPHA 모집인원: 24명"), [{"text": "다른 문서"}])
        self.assertFalse(result["scope_gate_passed"])

    def test_schema_is_not_relaxed(self):
        with self.assertRaises(GroundedClaimsError):
            verify_claim({"text": "ALPHA 모집인원은 24명입니다.", "evidence": []}, [])

    def test_years_bound_in_sequence_not_globally(self):
        self.assertEqual(_dates("접수 2028 12.7., 발표 2029년 1월 29일"), [(2028, 12, 7), (2029, 1, 29)])

    def test_return_does_not_masquerade_as_service_answer(self):
        result = verify_claim(payload("ALPHA 모집인원은 24명입니다.", "ALPHA 모집인원: 24명"), [{"text": "ALPHA 모집인원: 24명"}])
        self.assertNotIn("supported", result)
        self.assertNotIn("answer", result)
        self.assertIn("scope_gate_passed", result)


class DateSplitTests(unittest.TestCase):
    def test_abbreviated_month_range(self):
        text = "활동기간은 ’26. 4. ~ ’27. 2.입니다."
        self.assertEqual(split_preserving_dates(text), [text])

    def test_full_date_and_ordinary_sentence_boundary(self):
        text = "마감은 2028. 7. 22. 별도 문의가 필요합니다."
        self.assertEqual(split_preserving_dates(text), ["마감은 2028. 7. 22.", "별도 문의가 필요합니다."])

    def test_dotted_date_range_with_weekdays(self):
        text = "행사는 2028. 8. 5.(토) ~ 8. 7.(월)입니다."
        self.assertEqual(split_preserving_dates(text), [text])

    def test_month_only_range_preserved(self):
        text = "활동기간은 2028. 4. ~ 2029. 2.입니다."
        self.assertEqual(split_preserving_dates(text), [text])

    def test_short_facts_and_long_text_not_discarded(self):
        text = "무료. " + "매우 긴 설명 " * 80 + "끝."
        self.assertEqual("".join("".join(split_preserving_dates(text)).split()), "".join(text.split()))

    def test_decimal_does_not_swallow_boundary(self):
        self.assertEqual(split_preserving_dates("평점은 3.5. 제출은 별도입니다."), ["평점은 3.5.", "제출은 별도입니다."])

    def test_private_use_character_is_not_a_sentinel(self):
        text = "원문 \ue000도 보존합니다."
        self.assertEqual(split_preserving_dates(text), [text])

    def test_newlines_are_not_joined(self):
        self.assertEqual(split_preserving_dates("첫 행.\n둘째 행."), ["첫 행.", "둘째 행."])

    def test_empty(self):
        self.assertEqual(split_preserving_dates(" \n "), [])


class RunnerSafetyTests(unittest.TestCase):
    def test_network_and_protected_paths_rejected(self):
        for event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
            with self.assertRaises(RuntimeError):
                diagnostic.forbid_network_and_protected_files(event, ())
        for name in (".env", "config/pnu-service-answer-holdout-v2.draft.jsonl"):
            with self.assertRaises(RuntimeError):
                diagnostic.forbid_network_and_protected_files("open", (str(ROOT / name), "r", 0))

    def test_runner_rejects_output_outside_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/evaluate_evidence_binding_experiment.py"), "--output-dir", directory], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("output must be a new", result.stderr)

    def test_frozen_code_hashes_unchanged(self):
        for name, expected in diagnostic.CODE_PINS.items():
            with self.subTest(file=name):
                self.assertEqual(diagnostic.digest(ROOT / name), expected)


if __name__ == "__main__":
    unittest.main()
