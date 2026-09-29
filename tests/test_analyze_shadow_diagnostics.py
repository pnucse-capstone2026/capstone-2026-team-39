from __future__ import annotations

import copy
import subprocess
import sys
import unittest
from pathlib import Path

from scripts import analyze_shadow_diagnostics as diagnostics
from scripts import judge_service_answers as judge
from scripts.rag import grounded_claims_v2 as c2


class ShadowOfflineDiagnosticsTests(unittest.TestCase):
    """Synthetic fixtures only; never open reviewer files or call a model."""

    def notes_fixture(self):
        packet = {"items": [{"case_id": "synthetic"}]}
        notes = {
            "schema_version": "pnu.shadow-ai-observations.v1",
            "author_type": "assistant_ai", "human_review": False,
            "packet_sha256": diagnostics.PACKET_SHA,
            "items": [{"case_id": "synthetic", "run_causes": ["no_primary_gap_observed"] * 3}],
        }
        return packet, notes

    def quote_fixture(self, quote):
        return {
            "answer": "지원액은 10만원입니다.\n대상은 재학생입니다.\n지급일은 말일입니다.",
            "judge": {"score": 1, "grounded_fully_correct": False, "claim_checks": [
                {"claim_id": "c1", "status": "missing", "answer_quote": None},
            ]},
            "judge_guard": {"original_fields": {
                "score": 2, "claim_checks": [
                    {"claim_id": "c1", "status": "supported", "answer_quote": quote},
                ],
            }},
        }

    def probe_fixture(self):
        # Constructed miniature documents, not answer artifacts or human gold.
        texts = {
            "shadow_core_04": (
                "대기업 (GSAT) 과정\t프로그램운영\t2026. 8. 5.(수) ~ 8. 7.(금)\t비대면\n"
                "공기업 (NCS) 과정\t프로그램운영\t2026. 8. 24.(월) ~ 8. 28.(금)\t비대면"
            ),
            "shadow_sup_04": (
                "[성평등상담실(성희롱·성폭력)] ☎ 051-510-7890\n"
                "[인권상담실(인권침해 등)] ☎ 051-510-7942"
            ),
        }
        items = []
        for cid, text in texts.items():
            chunk_id = cid + "#0003"
            items.append({
                "case_id": cid,
                "required_claims": [{"evidence_options": [{"evidence_chunk_id": chunk_id}]}],
                "runs": [{"contexts": [{"chunk_id": chunk_id, "source_number": 1, "text": text}]}],
            })
        return {"items": items}

    def test_ai_notes_are_valid_and_not_mutated(self):
        packet, notes = self.notes_fixture()
        original = copy.deepcopy(notes)
        self.assertEqual(set(diagnostics.validate_notes(packet, notes)), {"synthetic"})
        self.assertEqual(notes, original)

    def test_human_label_impersonation_is_rejected(self):
        for field, value in (("author_type", "human"), ("human_review", True), ("human_review", None)):
            packet, notes = self.notes_fixture()
            notes[field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "AI-only"):
                diagnostics.validate_notes(packet, notes)

    def test_unbound_notes_are_rejected(self):
        packet, notes = self.notes_fixture()
        notes["packet_sha256"] = "wrong-packet"
        with self.assertRaisesRegex(ValueError, "bound"):
            diagnostics.validate_notes(packet, notes)

    def test_missing_duplicate_or_foreign_cases_are_rejected(self):
        for variant in ("missing", "duplicate", "foreign"):
            packet, notes = self.notes_fixture()
            if variant == "missing":
                notes["items"] = []
            elif variant == "duplicate":
                notes["items"] *= 2
            else:
                notes["items"][0]["case_id"] = "foreign"
            with self.subTest(variant=variant), self.assertRaisesRegex(ValueError, "exactly once"):
                diagnostics.validate_notes(packet, notes)

    def test_unknown_cause_or_missing_run_is_rejected(self):
        for causes in (["postprocessing_rejection"] * 2, ["invented"] * 3):
            packet, notes = self.notes_fixture()
            notes["items"][0]["run_causes"] = causes
            with self.subTest(causes=causes), self.assertRaisesRegex(ValueError, "per-run"):
                diagnostics.validate_notes(packet, notes)

    def test_evidence_offsets_are_exact_and_field_specific(self):
        contexts = [{"source_number": 2, "chunk_id": "synthetic#1",
                     "text": "앞말 신청 마감 7월 22일 뒷말", "source_title": "제7회 안내"}]
        for quote, field in (("신청 마감 7월 22일", "text"), ("제7회", "source_title")):
            span = diagnostics.evidence_matches(quote, contexts)[0]
            self.assertEqual(span["field"], field)
            self.assertEqual(contexts[0][field][span["start"]:span["end"]], quote)
            self.assertEqual(span["source_number"], 2)

    def test_missing_or_empty_evidence_never_becomes_an_ai_citation(self):
        contexts = [{"source_number": 1, "chunk_id": "synthetic", "text": "실제 원문"}]
        for quote in ("", "없는 원문", "실제  원문"):
            with self.subTest(quote=quote), self.assertRaisesRegex(ValueError, "does not occur"):
                diagnostics.evidence_matches(quote, contexts)

    def test_contiguous_judge_quote_passes_without_score_changes(self):
        run = self.quote_fixture("지원액은 10만원입니다.")
        original = copy.deepcopy(run)
        audit = diagnostics.quote_audit(run, judge)[0]
        self.assertTrue(audit["contiguous_under_frozen_v11"])
        self.assertTrue(audit["individual_lines_present"])
        self.assertEqual(audit["final_status"], "missing")
        self.assertEqual(run, original)

    def test_stitched_quote_is_not_misreported_as_missing_individual_facts(self):
        run = self.quote_fixture("지원액은 10만원입니다.\n지급일은 말일입니다.")
        audit = diagnostics.quote_audit(run, judge)[0]
        self.assertFalse(audit["contiguous_under_frozen_v11"])
        self.assertTrue(audit["individual_lines_present"])

    def test_quote_typo_is_distinct_from_noncontiguous_stitching(self):
        audit = diagnostics.quote_audit(self.quote_fixture("지급일은 말입입니다."), judge)[0]
        self.assertFalse(audit["contiguous_under_frozen_v11"])
        self.assertFalse(audit["individual_lines_present"])

    def test_quote_audit_without_guard_uses_saved_checks(self):
        run = self.quote_fixture("지원액은 10만원입니다.")
        run["judge"]["claim_checks"] = run.pop("judge_guard")["original_fields"]["claim_checks"]
        self.assertTrue(diagnostics.quote_audit(run, judge)[0]["contiguous_under_frozen_v11"])

    def test_c2_known_unsafe_broad_quotes_are_characterized_not_fixed(self):
        # PASS here means reproducing a KNOWN DEFECT, not a C2 adoption gate pass.
        results = diagnostics.c2_probes(self.probe_fixture(), c2)
        self.assertEqual(len(results), 6)
        unsafe = {p["probe_id"] for p in results if not p["matches_expected"]}
        self.assertEqual(unsafe, {"gsat_wrong_row_broad", "phone_wrong_subject_broad"})
        self.assertTrue(all(p["input_kind"] == "ai_constructed_verifier_probe_not_model_output" for p in results))
        for p in results:
            if p["probe_id"].endswith("_narrow"):
                self.assertFalse(p["actual_supported"])
                self.assertEqual(p["result"]["validation_reason"], "critical_value_not_in_quote")

    def test_network_guard_rejects_connect_bind_and_dns(self):
        for event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
            with self.subTest(event=event), self.assertRaisesRegex(RuntimeError, "network"):
                diagnostics.forbid_network_and_protected_files(event, ())

    def test_file_guard_rejects_protected_paths_without_opening_them(self):
        paths = [
            "config/pnu-service-answer-holdout-v2.draft.jsonl", "docs/holdout-v2-human-review.md",
            "evidence/holdout-v2-reviewer-a.json", "evidence/holdout-v2-reviewer-b.json", ".env",
        ]
        for path in paths:
            with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, "credentials/holdout"):
                diagnostics.forbid_network_and_protected_files("open", (str(diagnostics.ROOT / path), "r", 0))

    def test_file_guard_allows_synthetic_input_and_integer_descriptors(self):
        diagnostics.forbid_network_and_protected_files("open", (str(Path("synthetic-fixture.json")), "r", 0))
        diagnostics.forbid_network_and_protected_files("open", (3, "r", 0))

    def test_output_path_guard_runs_before_any_packet_access(self):
        script = diagnostics.ROOT / "scripts/analyze_shadow_diagnostics.py"
        for output in (diagnostics.ROOT / "processed/eval", diagnostics.ROOT / "docs"):
            result = subprocess.run([sys.executable, "-B", str(script), "--output-dir", str(output)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("choose a new subdirectory", result.stderr)


if __name__ == "__main__":
    unittest.main()
