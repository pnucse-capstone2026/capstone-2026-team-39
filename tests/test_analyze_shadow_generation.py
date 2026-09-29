from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_shadow_generation import file_sha, load_bound_run, select_cases, summarize
from scripts.service_eval_artifacts import (
    ANSWER_SCHEMA_VERSION, JUDGMENT_SCHEMA_VERSION,
    build_answer_identity, build_judgment_identity, sha256_json,
)


class ShadowGenerationTests(unittest.TestCase):
    def fixture(self):
        cases = {
            "q1": {"id": "q1", "family_id": "f1", "shadow_bucket": "simple", "category": "test", "answerable": True,
                   "required_claims": [{"evidence_options": [{"document_id": "gold"}]}]},
            "q2": {"id": "q2", "family_id": "f1", "shadow_bucket": "role_variant", "category": "test", "answerable": True,
                   "required_claims": [{"evidence_options": [{"document_id": "gold"}]}]},
        }
        runs = []
        for i in range(3):
            run = {}
            for q, votes in (("q1", (True, False, True)), ("q2", (False, False, True))):
                run[q] = {
                    "experiment_id": "shadow", "condition_id": "c1", "generation_run_id": f"run{i + 1}",
                    "answer": f"answer-{i}",
                    "judge": {"score": 2 if votes[i] else 0, "grounded_fully_correct": votes[i]},
                    "evaluation_trace": {"retrieval_stages": {"final_contexts": [{"chunk_id": "gold#1", "document_id": "gold", "text": "evidence"}]}},
                    "atomic_evidence_at_k": {"8": {"available": True, "all_matched": True}},
                    "evidence_at_k": {"8": {"all_matched": False}},
                }
            runs.append(run)
        return cases, runs

    def test_majority_keeps_two_questions_and_one_family(self):
        cases, runs = self.fixture()
        report, rows = summarize(cases, runs)
        self.assertEqual(report["groups"]["all"]["questions"], 2)
        self.assertEqual(report["groups"]["all"]["question_families"], 1)
        self.assertEqual(report["groups"]["all"]["majority_gfc_count"], 1)
        self.assertEqual(report["groups"]["all"]["run_gfc_counts"], [1, 0, 2])
        self.assertEqual(rows[0]["atomic_all_evidence_at_8_runs"], [True] * 3)
        self.assertEqual(sum(r["count"] for r in report["cross_table"]), 6)
        self.assertTrue(all(r["atomic_all_evidence_at_8"] for r in report["cross_table"]))

    def test_missing_atomic_metric_is_rejected(self):
        cases, runs = self.fixture()
        runs[0]["q1"].pop("atomic_evidence_at_k")
        with self.assertRaisesRegex(ValueError, "missing atomic"):
            summarize(cases, runs)

    def test_duplicate_generation_is_rejected(self):
        cases, runs = self.fixture()
        with self.assertRaisesRegex(ValueError, "must be unique"):
            summarize(cases, [runs[0], runs[1], copy.deepcopy(runs[1])])

    def test_retrieval_drift_is_reported(self):
        cases, runs = self.fixture()
        runs[2]["q1"]["evaluation_trace"]["retrieval_stages"]["final_contexts"][0]["text"] = "changed"
        report, _ = summarize(cases, runs)
        self.assertEqual(report["retrieval_changed_case_ids"], ["q1"])

    def test_quote_guard_is_reported_without_changing_majority(self):
        cases, runs = self.fixture()
        runs[0]["q2"]["deterministic_guard"] = {
            "rules": ["unquotable_supported_claim_forces_missing"],
            "original_fields": {"grounded_fully_correct": True},
        }
        report, _ = summarize(cases, runs)
        self.assertEqual(report["judge_quote_contract_gfc_downgrades"], [{"case_id": "q2", "generation_run_id": "run1"}])
        self.assertEqual(report["groups"]["all"]["majority_gfc_count"], 1)

    def test_service_rejection_is_separate_from_judge_refusal(self):
        cases, runs = self.fixture()
        record = runs[0]["q2"]
        record["claims"] = [
            {"supported": False, "validation_reason": "critical_value_mismatch"},
            {"supported": False, "validation_reason": "semantic_relation_mismatch"},
        ]
        record["deterministic_guard"] = {"rules": ["answerable_clear_refusal_forces_score_zero"]}
        record["postprocessing"] = {"claims_truncated": True}
        report, rows = summarize(cases, runs)
        diagnostics = report["service_postprocessing"]
        self.assertEqual(diagnostics["answer_instances_with_all_claims_rejected"], 1)
        self.assertEqual(diagnostics["all_rejected_and_judge_explicit_refusal"], 1)
        self.assertEqual(diagnostics["answer_instances_with_missing_claim_trace"], 5)
        self.assertEqual(diagnostics["claim_validation_reason_counts"]["critical_value_mismatch"], 1)
        self.assertEqual(len(diagnostics["truncated_answer_instances"]), 1)
        self.assertIn("explicit_refusal_guard", rows[1]["failure_causes_runs"][0])
        self.assertEqual(report["groups"]["all"]["majority_gfc_count"], 1)

    def test_empty_and_partly_supported_claims_are_not_all_rejected(self):
        cases, runs = self.fixture()
        runs[0]["q1"]["claims"] = []
        runs[0]["q2"]["claims"] = [
            {"supported": True, "validation_reason": "supported"},
            {"supported": False, "validation_reason": "model_abstention"},
        ]
        report, _ = summarize(cases, runs)
        self.assertEqual(report["service_postprocessing"]["answer_instances_with_all_claims_rejected"], 0)
        self.assertEqual(report["service_postprocessing"]["answer_instances_with_missing_claim_trace"], 4)

    def test_core_scope_is_predefined_not_selected_by_scores(self):
        cases, runs = self.fixture()
        self.assertEqual(set(select_cases(cases, core_only=False)), {"q1", "q2"})
        core = select_cases(cases, core_only=True)
        self.assertEqual(set(core), {"q1"})
        report, _ = summarize(core, [{cid: run[cid] for cid in core} for run in runs])
        self.assertEqual(report["groups"]["all"]["questions"], 1)
        self.assertEqual(report["groups"]["all"]["majority_gfc_count"], 1)

    def artifact_fixture(self, directory):
        cases, _ = self.fixture()
        answer_path = Path(directory) / "answers.jsonl"
        judge_path = Path(directory) / "judgments.jsonl"
        answers = [build_answer_identity({
            "schema_version": ANSWER_SCHEMA_VERSION, "record_type": "answer",
            "experiment_id": "shadow", "condition_id": "c1", "generation_run_id": "run1",
            "case_id": cid, "answer": "saved answer", "case_sha256": sha256_json(case),
            "collector_config": {"cases_sha256": "cases-sha"},
            "generation": {"used": "frontier", "model": "gemini-3.5-flash-lite"},
        }) for cid, case in cases.items()]
        answer_path.write_text("".join(json.dumps(r) + "\n" for r in answers))
        judgments = [build_judgment_identity({
            "schema_version": JUDGMENT_SCHEMA_VERSION, "record_type": "judgment",
            "experiment_id": "shadow", "condition_id": "c1", "generation_run_id": "run1",
            "judge_run_id": "judge-v11-r1", "case_id": a["case_id"],
            "answer_id": a["answer_id"], "answer_sha256": a["answer_sha256"],
            "answer_record_sha256": sha256_json(a), "answers_artifact_sha256": file_sha(answer_path),
            "judge": {"score": None if a["case_id"] == "q2" else 2, "grounded_fully_correct": a["case_id"] == "q1"},
            "error": "synthetic schema failure" if a["case_id"] == "q2" else None,
        }) for a in answers]
        judge_path.write_text("".join(json.dumps(r) + "\n" for r in judgments))
        return cases, answer_path, judge_path, judgments

    def test_core_only_preserves_strict_failure_for_full_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            cases, answers, judgments, _ = self.artifact_fixture(directory)
            with self.assertRaisesRegex(ValueError, "terminal Judge error"):
                load_bound_run(answers, judgments, cases, "cases-sha")
            core = load_bound_run(answers, judgments, cases, "cases-sha", core_only=True)
            self.assertEqual(set(core), {"q1"})

    def test_core_only_still_rejects_tampered_excluded_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            cases, answers, judgments, rows = self.artifact_fixture(directory)
            rows[1]["answer_record_sha256"] = "0" * 64
            rows[1] = build_judgment_identity(rows[1])
            judgments.write_text("".join(json.dumps(r) + "\n" for r in rows))
            with self.assertRaisesRegex(ValueError, "artifact binding mismatch"):
                load_bound_run(answers, judgments, cases, "cases-sha", core_only=True)


if __name__ == "__main__":
    unittest.main()
