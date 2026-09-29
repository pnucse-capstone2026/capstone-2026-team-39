"""Separate, API-free ordinal type diagnostic. Not installed in the service.

Only for the lifetime of an isolated process, classify explicit 제N회 as an
ordinal scope rather than a cardinal frequency. Uses the existing round type
and existing metadata/semantic guards; never broadens permitted metadata values.
This does NOT modify the concurrently frozen C1/C3 live experiment.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "processed/eval/preflight-20260909/security-live-preparation-v1/preparation-v1/snapshots/c1-sec-merged"
sys.path.insert(0, str(SNAPSHOT / "scripts"))
import search_api as api
from service_eval_artifacts import validate_answer_record

VERSION = "pnu.ordinal-type-replay.20260913-v1"
ORIGINAL = api.extract_critical_values
ORDINAL = re.compile(r"제\s*(\d+)\s*회(?!차)")


def extract(value):
    # Change the input to the VALUE extractor only. The user answer, raw source,
    # citation text, lexical and semantic guards still see their original text.
    return ORIGINAL(ORDINAL.sub(lambda m: m[1] + "차", str(value or "")))


@contextmanager
def enabled():
    prior = api.extract_critical_values
    api.extract_critical_values = extract
    try:
        yield
    finally:
        api.extract_critical_values = prior


def source(title="제7회 ALPHA 공모전", text="신청 마감은 2028년 8월 5일입니다."):
    return {"source_number": 1, "chunk_id": "synthetic#1", "source_title": title, "text": text}


class OrdinalTests(unittest.TestCase):
    def test_type_bug_reproduced(self):
        self.assertIn("quantity:7:회", ORIGINAL("제7회 공모전"))
        self.assertEqual(extract("제7회 공모전"), frozenset({"round:7"}))

    def test_frequency_remains_cardinal(self):
        self.assertEqual(extract("제7회 공모전에 3회 지원"), frozenset({"round:7", "quantity:3:회"}))

    def test_correct_title_ordinal_recovered(self):
        claim = "제7회 ALPHA 공모전의 신청 마감은 2028년 8월 5일입니다."
        self.assertFalse(api.attribute_claim(claim, [source()])["supported"])
        with enabled():
            self.assertTrue(api.attribute_claim(claim, [source()])["supported"])

    def test_wrong_edition_rejected(self):
        with enabled():
            self.assertFalse(api.attribute_claim("제8회 ALPHA 공모전의 신청 마감은 2028년 8월 5일입니다.", [source()])["supported"])

    def test_wrong_date_rejected(self):
        with enabled():
            self.assertFalse(api.attribute_claim("제7회 ALPHA 공모전의 신청 마감은 2028년 8월 7일입니다.", [source()])["supported"])

    def test_title_ordinal_cannot_supply_cardinal(self):
        with enabled():
            self.assertFalse(api.attribute_claim("ALPHA 공모전은 7회 지원할 수 있습니다.",
                [source(text="ALPHA 공모전은 지원할 수 있습니다.")])["supported"])

    def test_same_number_cardinal_is_still_checked(self):
        with enabled():
            self.assertFalse(api.attribute_claim("제7회 ALPHA 공모전은 7회 지원할 수 있습니다.",
                [source(text="ALPHA 공모전은 1회 지원할 수 있습니다.")])["supported"])

    def test_restores_original(self):
        with enabled():
            self.assertIs(api.extract_critical_values, extract)
        self.assertIs(api.extract_critical_values, ORIGINAL)


def replay(out):
    out = Path(out)
    if out.exists():
        raise ValueError("new_output_required")
    paths = [ROOT / f"processed/eval/preflight-20260905/shadow60-generation-v1/c1-run{n}.answers.jsonl" for n in (1, 2, 3)]
    cases_path = ROOT / "config/pnu-service-shadow60-v1.jsonl"
    pins = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths + [cases_path, Path(__file__), Path(api.__file__)]}
    wanted = {c["id"] for c in (json.loads(s) for s in cases_path.read_text().splitlines()) if c["shadow_bucket"] in ("simple", "multi")}
    if len(wanted) != 42:
        raise ValueError("expected_core42")
    rows, replay_exact = [], 0
    for run, path in enumerate(paths, 1):
        for r in (json.loads(s) for s in path.read_text().splitlines()):
            validate_answer_record(r)
            if r["case_id"] not in wanted:
                continue
            ctx = r["evaluation_trace"]["retrieval_stages"]["final_contexts"]
            draft = r["evaluation_trace"]["sanitized_draft"]
            before = api.build_rag_response(r["query"], ctx, draft, r["generator"])
            replay_exact += before["answer"] == r["answer"]
            with enabled():
                after = api.build_rag_response(r["query"], ctx, draft, r["generator"])
            kept = [c["text"] for c in before["claims"] if c["supported"]]
            proposed = [c["text"] for c in after["claims"] if c["supported"]]
            rows.append({"case_id": r["case_id"], "run": run, "base_answer_id": r["answer_id"],
                         "base_answer_sha256": r["answer_sha256"], "changed": before["answer"] != after["answer"],
                         "added": [s for s in proposed if s not in kept], "removed": [s for s in kept if s not in proposed],
                         "before": before["answer"], "after_unjudged": after["answer"]})
    if len(rows) != 126 or replay_exact != 126:
        raise ValueError("baseline_replay_mismatch")
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in pins.items()):
        raise ValueError("inputs_changed")
    summary = {"version": VERSION, "records": 126, "baseline_replay_exact": replay_exact,
               "changed_records": sum(r["changed"] for r in rows),
               "changed_questions": len({r["case_id"] for r in rows if r["changed"]}),
               "added_claims": sum(len(r["added"]) for r in rows), "removed_claims": sum(len(r["removed"]) for r in rows),
               "external_calls": 0, "new_gfc": None, "service_modified": False,
               "interpretation": "Same saved draft replay; no fresh generation, no rescoring, not a GFC improvement estimate"}
    out.mkdir(parents=True, exist_ok=False)
    for name, value in (("summary.json", summary), ("replay.json", rows), ("manifest.json", {"sha256": pins})):
        with (out / name).open("x", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OrdinalTests))
        raise SystemExit(not result.wasSuccessful())
    if not args.out:
        parser.error("--out or --test is required")
    replay(args.out)
