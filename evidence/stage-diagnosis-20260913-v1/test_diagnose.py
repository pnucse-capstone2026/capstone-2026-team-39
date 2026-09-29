import copy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

import diagnose as d
import author_observations as authored
import inspect_sources


class StageDiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.claim = {"claim_index": 0, "text": "접수 마감은 7월 24일이다.",
                      "supported": False, "validation_reason": "semantic_relation_mismatch"}
        self.item = {"case_id": "synthetic", "claims": [self.claim],
                     "contexts": [{"source_number": 1, "chunk_id": "synthetic:1", "source_title": "행사 안내",
                                   "text": "접수 마감: 7월 24일. 발표: 7월 30일."}],
                     "saved_gfc": False, "split_claims": [self.claim["text"]],
                     "truncated_claims": [], "extractive_fallback": False,
                     "sanitization_changed": False, "output_gate_changed_answer": False}
        self.packet = {"items": [self.item]}
        self.notes = {"author_type": "assistant_ai", "human_review": False, "packet_sha256": "fixed",
                      "rejected_claim_observations": [{"case_id": "synthetic", "claim_index": 0,
                         "text": self.claim["text"], "status": "source_supported", "relevance": "required",
                         "quotes": [{"source_number": 1, "text": "접수 마감: 7월 24일"}], "reason": "합성 원문 대조"}]}

    def validate(self):
        return d.validate_observations(self.packet, self.notes, "fixed")

    def test_evidence_span_is_exact_unicode_offset(self):
        q = self.validate()[0]["source_spans"][0]
        self.assertEqual(self.item["contexts"][0]["text"][q["start"]:q["end"]], q["text"])
        self.assertEqual(q["sha256"], hashlib.sha256(q["text"].encode()).hexdigest())

    def test_nonexistent_quote_is_rejected(self):
        self.notes["rejected_claim_observations"][0]["quotes"][0]["text"] = "접수 마감: 7월 30일"
        with self.assertRaisesRegex(ValueError, "quote_not_in_saved_source"):
            self.validate()

    def test_quote_from_other_source_number_is_rejected(self):
        self.notes["rejected_claim_observations"][0]["quotes"][0]["source_number"] = 2
        with self.assertRaisesRegex(ValueError, "invalid_evidence_reference"):
            self.validate()

    def test_source_title_span_is_separate_from_body(self):
        quote = {"source_number": 1, "text": "행사 안내", "field": "source_title"}
        self.assertEqual(d.evidence_span(self.item, quote)["field"], "source_title")

    def test_claim_text_drift_rejected(self):
        self.notes["rejected_claim_observations"][0]["text"] += " 바뀜"
        with self.assertRaisesRegex(ValueError, "duplicate_missing_or_changed_claim"):
            self.validate()

    def test_duplicate_observation_rejected(self):
        self.notes["rejected_claim_observations"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate_missing_or_changed_claim"):
            self.validate()

    def test_missing_observation_rejected(self):
        self.notes["rejected_claim_observations"] = []
        with self.assertRaisesRegex(ValueError, "every_rejected_claim"):
            self.validate()

    def test_accepted_claim_cannot_be_annotated_as_rejected(self):
        self.claim["supported"] = True
        with self.assertRaisesRegex(ValueError, "duplicate_missing_or_changed_claim"):
            self.validate()

    def test_semantic_label_requires_quote(self):
        self.notes["rejected_claim_observations"][0]["quotes"] = []
        with self.assertRaisesRegex(ValueError, "semantic_label_requires_source_quote"):
            self.validate()

    def test_abstention_label_does_not_assert_semantic_correctness(self):
        note = self.notes["rejected_claim_observations"][0]
        note.update(status="abstention_statement", quotes=[])
        self.assertEqual(self.validate()[0]["source_spans"], [])

    def test_no_human_review_impersonation(self):
        for key, value in [("author_type", "human"), ("human_review", True), ("packet_sha256", "changed")]:
            changed = copy.deepcopy(self.notes)
            changed[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "AI_notes_identity_mismatch"):
                d.validate_observations(self.packet, changed, "fixed")

    def test_mechanics_are_not_a_raw_gfc_score(self):
        result = d.mechanical_summary([self.item])
        self.assertEqual(result["rejected_claims"], 1)
        self.assertEqual(result["questions_all_claims_rejected"], 1)
        self.assertIsNone(result["raw_gfc"])
        self.assertEqual(result["saved_gfc"], 0)

    def test_offline_and_sensitive_data_guards(self):
        for event, args in [("socket.connect", ()), ("urllib.Request", ()),
                            ("open", ("/tmp/.env.synthetic",)), ("open", ("/tmp/holdout.synthetic.json",))]:
            with self.subTest(event=event, args=args), self.assertRaises(PermissionError):
                d.offline_audit(event, args)

    def test_output_cannot_overwrite_or_escape_scope(self):
        for out in (Path(d.__file__).parent, Path("/tmp/stage-diagnosis-not-approved")):
            with self.subTest(out=out), self.assertRaisesRegex(ValueError, "new_nonsymlink_experiment_output_required"):
                d.publish(out, {})

    def test_all_48_manual_annotations_bound_to_saved_packet(self):
        notes = authored.annotations()
        packet = d.read(authored.PACKET)
        rows = d.validate_observations(packet, notes, authored.PACKET_SHA)
        self.assertEqual(len(rows), 48)
        self.assertEqual(len({(r["case_id"], r["claim_index"]) for r in rows}), 48)
        self.assertFalse(notes["human_review"])

    def test_source_mechanics_separates_aggregate_reason_from_local_reason(self):
        rows, pins = inspect_sources.inspect()
        self.assertEqual(len(rows), 14)
        self.assertEqual(len(pins), 453)
        phone = next(r for r in rows if r["case_id"] == "shadow_sup_04" and r["claim_index"] == 3)
        self.assertEqual(phone["aggregate_reason"], "critical_value_mismatch")
        self.assertTrue(phone["source_critical_ok"])
        self.assertFalse(phone["source_semantic_mismatch"])
        self.assertEqual(phone["isolated_source_reason"], "low_lexical_overlap")

    def test_changed_packet_cannot_reuse_manual_notes(self):
        with patch.object(d, "sha", return_value="changed"):
            with self.assertRaisesRegex(ValueError, "reviewed_packet_changed"):
                authored.annotations()


if __name__ == "__main__":
    unittest.main()
