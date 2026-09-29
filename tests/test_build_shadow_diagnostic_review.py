from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.build_shadow_diagnostic_review import (
    ANSWER_SHAS, CASES_PATH, CASES_SHA, CONTROL_REASONS, JUDGE_SHAS,
    build_packet, label_template, project_run, render_html, render_markdown, script_json,
    select_review_rows, selection_csv,
)
from scripts.immutable_outputs import publish_immutable_texts


class ShadowDiagnosticReviewTests(unittest.TestCase):
    def rows(self):
        ids = list(CONTROL_REASONS) + [f"q{i}" for i in range(39)]
        return [{
            "case_id": cid, "bucket": "simple", "gfc_majority": i < 3,
            "gfc_run1": i < 3, "gfc_run2": i < 3, "gfc_run3": i < 3,
            "atomic_all_evidence_at_8_runs": [i < 14] * 3,
            "retrieval_identical_across_runs": True,
        } for i, cid in enumerate(ids)]

    def record(self, run_id="run1"):
        return {
            "generation_run_id": run_id, "answer_id": "answer-" + run_id,
            "answer_sha256": "a" * 64, "answer": "실제 최종 답변\n둘째 줄",
            "cited_answer": "실제 최종 답변 [1]", "judge": {"score": 1, "grounded_fully_correct": False},
            "judgment": {"judgment_id": "judge-" + run_id, "judge_config": {"version": "v11"}},
            "evaluation_trace": {
                "raw_draft": "<script>초안</script>\n" + "긴 원문" * 3000,
                "sanitized_draft": "정제 초안",
                "retrieval_stages": {"final_contexts": [{
                    "source_number": 1, "chunk_id": "doc#1", "document_id": "doc",
                    "text": "원문\n" + "A" * 15000, "source_title": "제목",
                    "text_sha256": "b" * 64, "source_url": "javascript:alert(1)",
                }]},
            },
            "claims": [{"text": "저장된 주장", "supported": False,
                        "validation_reason": "critical_value_mismatch", "source_numbers": [],
                        "citations": [{"source_number": 1, "chunk_id": "doc#1", "excerpt": "근거"}] * 2}],
        }

    def packet(self):
        row = self.rows()[0]
        cid = row["case_id"]
        case = {"query": "실제 질문 </script>", "shadow_bucket": "simple", "category": "test",
                "required_claims": [{"claim_id": "c1", "description": "필수 사실", "evidence_options": []}]}
        selected = [{**row, "selection_group": "success_control", "selection_reason": "test"}]
        runs = [{cid: self.record(f"run{i}")} for i in (1, 2, 3)]
        return build_packet({cid: case}, runs, selected, [{"path": "shadow", "sha256": "c" * 64}])

    def test_selection_is_all_eleven_failures_plus_three_fixed_controls(self):
        selected = select_review_rows(self.rows())
        self.assertEqual(len(selected), 14)
        self.assertEqual({r["case_id"] for r in selected}, set(CONTROL_REASONS) | {f"q{i}" for i in range(11)})
        self.assertEqual(sum(r["selection_group"] == "success_control" for r in selected), 3)

    def test_order_is_stable_and_not_input_order(self):
        rows = self.rows()
        self.assertEqual(select_review_rows(rows), select_review_rows(list(reversed(rows))))

    def test_duplicate_or_missing_core_rejected(self):
        rows = self.rows()
        with self.assertRaisesRegex(ValueError, "42 unique"):
            select_review_rows(rows[:-1])
        rows[-1] = rows[0]
        with self.assertRaisesRegex(ValueError, "42 unique"):
            select_review_rows(rows)

    def test_noncore_is_rejected(self):
        rows = self.rows()
        rows[-1]["bucket"] = "challenge"
        with self.assertRaisesRegex(ValueError, "core buckets"):
            select_review_rows(rows)

    def test_changed_failure_count_is_rejected(self):
        rows = self.rows()
        rows[-1]["atomic_all_evidence_at_8_runs"] = [True] * 3
        with self.assertRaisesRegex(ValueError, "all 11"):
            select_review_rows(rows)

    def test_failed_control_is_not_silently_replaced(self):
        rows = self.rows()
        rows[0]["atomic_all_evidence_at_8_runs"] = [False] * 3
        with self.assertRaisesRegex(ValueError, "fixed success control"):
            select_review_rows(rows)

    def test_retrieval_drift_is_rejected(self):
        rows = self.rows()
        rows[-1]["retrieval_identical_across_runs"] = False
        with self.assertRaisesRegex(ValueError, "retrieval changed"):
            select_review_rows(rows)

    def test_projection_keeps_exact_full_text_and_original_scores(self):
        record = self.record()
        original = copy.deepcopy(record)
        record["deterministic_guard"] = {"original_fields": {"score": 2}, "rules": ["a_guard"]}
        projected = project_run(record)
        self.assertEqual(projected["raw_draft"], original["evaluation_trace"]["raw_draft"])
        self.assertEqual(projected["contexts"][0]["text"], original["evaluation_trace"]["retrieval_stages"]["final_contexts"][0]["text"])
        self.assertEqual(projected["answer"], original["answer"])
        self.assertEqual(projected["judge"]["score"], 1)
        self.assertEqual(projected["judge_guard"]["original_fields"]["score"], 2)
        self.assertEqual(len(projected["claims"][0]["citations"]), 1)

    def test_missing_trace_rejected_not_rendered_as_empty(self):
        for field in ("raw_draft", "retrieval_stages"):
            record = self.record()
            record["evaluation_trace"].pop(field)
            with self.assertRaises((KeyError, ValueError)):
                project_run(record)
        record = self.record()
        record.pop("claims")
        with self.assertRaisesRegex(ValueError, "claim trace missing"):
            project_run(record)

    def test_label_template_is_pending_and_bound_to_each_answer(self):
        packet = self.packet()
        labels = label_template(packet, "f" * 64)
        self.assertEqual(labels["reviewer"], "")
        self.assertEqual(labels["packet_sha256"], "f" * 64)
        self.assertEqual(len(labels["reviews"]), 3)
        self.assertEqual({r["generation_run_id"] for r in labels["reviews"]}, {"run1", "run2", "run3"})
        self.assertTrue(all(r["status"] == "PENDING" and r["note"] == "" and r["judge_revealed_at"] is None for r in labels["reviews"]))
        self.assertEqual([r["answer_id"] for r in labels["reviews"]], [r["answer_id"] for r in packet["items"][0]["runs"]])

    def test_script_embedding_escapes_untrusted_document_html(self):
        raw = {"text": "</script><script>alert(1)</script>&\u2028\u2029"}
        encoded = script_json(raw)
        self.assertNotIn("<", encoded)
        self.assertNotIn("&", encoded)
        self.assertEqual(json.loads(encoded), raw)

    def test_html_has_no_external_assets_or_open_judge_panel(self):
        rendered = render_html(self.packet(), "d" * 64)
        self.assertNotIn("__PACKET_JSON__", rendered)
        self.assertEqual(rendered.count("</script>"), 1)
        self.assertNotIn("<script src=", rendered)
        self.assertNotIn("<link ", rendered)
        self.assertIn("connect-src 'none'", rendered)
        self.assertIn('<details class="inspect" id="judge-panel">', rendered)
        self.assertIn("textContent=run.answer", rendered)
        self.assertIn("labels_before_judge", rendered)

    def test_audit_selection_has_case_and_run_votes(self):
        csv_text = selection_csv(self.packet())
        self.assertIn("gfc_run1,gfc_run2,gfc_run3", csv_text)
        self.assertIn("shadow_adm_01", csv_text)

    def test_markdown_fallback_keeps_run1_and_fences_source_content(self):
        packet = self.packet()
        packet["items"][0]["runs"][0]["raw_draft"] = "```\n</details><script>원문</script>"
        packet["items"][0]["runs"][1]["raw_draft"] = "DO_NOT_INCLUDE_OPTIONAL_RUN"
        rendered = render_markdown(packet)
        self.assertIn("````text\n```\n</details><script>원문</script>\n````", rendered)
        self.assertIn(packet["items"][0]["runs"][0]["answer"], rendered)
        self.assertNotIn("DO_NOT_INCLUDE_OPTIONAL_RUN", rendered)
        self.assertIn("PENDING", rendered)

    def test_only_fixed_shadow_input_is_configurable(self):
        self.assertEqual(CASES_PATH, "config/pnu-service-shadow60-v1.jsonl")
        self.assertEqual(len(ANSWER_SHAS), 3)
        self.assertEqual(len(JUDGE_SHAS), 3)
        self.assertTrue(all(len(s) == 64 for s in (*ANSWER_SHAS, *JUDGE_SHAS, CASES_SHA)))

    def test_immutable_publication_never_overwrites(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "packet.json"
            publish_immutable_texts({target: "original"}, authoritative_path=target)
            with self.assertRaisesRegex(ValueError, "already exists"):
                publish_immutable_texts({target: "replacement"}, authoritative_path=target)
            self.assertEqual(target.read_text(), "original")

    @unittest.skipUnless(shutil.which("node"), "Node is required for offline JavaScript contract checks")
    def test_javascript_syntax_and_human_label_roundtrip_guards(self):
        packet = self.packet()
        rendered = render_html(packet, "d" * 64)
        source = rendered.split("<script>\n", 1)[1].rsplit("</script>", 1)[0]
        # This compiles the full script but executes ONLY pure label validation.
        # No browser, DOM, storage, network, or real reviewer labels are used.
        pure = source.split("const enums=", 1)[1].split("try{const saved=", 1)[0]
        payload = {"source": source, "pure": "const enums=" + pure,
                   "labels": label_template(packet, "d" * 64)}
        node_test = r'''
const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const input=JSON.parse(fs.readFileSync(0,"utf8"));
new vm.Script(input.source);
const context={EMPTY:input.labels,structuredClone};vm.createContext(context);
vm.runInContext(input.pure+";globalThis.validate=validateLabels;globalThis.reviewStatus=status;",context);
const fresh=()=>structuredClone(input.labels);
assert.deepEqual(context.validate(fresh()),input.labels);
assert.equal(context.reviewStatus(input.labels.reviews[0]),"PENDING");
let value=fresh(),r=value.reviews[0];
r.support="supported";r.completeness="complete";r.postprocessing="no_material_change";r.note="SYNTHETIC TEST ONLY";r.status="REVIEWED";
assert.equal(context.reviewStatus(r),"REVIEWED");
assert.deepEqual(context.validate(JSON.parse(JSON.stringify(value))),value);
for(const mutate of [
  v=>v.packet_sha256="bad", v=>v.purpose="holdout_signoff",
  v=>v.reviews.pop(), v=>v.reviews[1]=structuredClone(v.reviews[0]),
  v=>v.reviews[0].answer_sha256="bad", v=>v.reviews[0].case_id="bad",
  v=>v.reviews[0].support="auto_correct", v=>v.reviews[0].note=null,
  v=>v.reviews[0].status="REVIEWED", v=>v.reviews[0].judge_agreement="agree",
  v=>v.reviews[0].judge_revealed_at="invalid", v=>v.reviews[0].judge_revealed_at="2026-09-07T00:00:00Z",
]){const bad=fresh();mutate(bad);assert.throws(()=>context.validate(bad));}
value=fresh();r=value.reviews[0];r.judge_revealed_at="2026-09-07T00:00:00Z";
r.labels_before_judge={support:"",completeness:"",postprocessing:"not_checked",note:""};
r.judge_agreement="uncertain";assert.deepEqual(context.validate(value),value);
console.log("JS compilation and label contracts PASS; 12 corrupt-input cases rejected");
'''
        result = subprocess.run([shutil.which("node"), "-e", node_test],
                                input=json.dumps(payload), text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("12 corrupt-input cases rejected", result.stdout)


if __name__ == "__main__":
    unittest.main()
