"""API-free, identity-bound ordinal recovery; unintegrated development candidate.

The unrestricted ordinal type wrapper exposes an existing weak entity matcher:
an unrelated event with the same edition/date can pass. Recovery is therefore
limited to claims with an explicit possessive event name whose full normalized
identity is present in ONE edition-bearing source title. Original accepted
claims are unchanged. No title from another document can supply a body value.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata
import unittest

import ordinal_replay as v1

ROOT, api = v1.ROOT, v1.api
ORIGINAL_ATTRIBUTE = api.attribute_claim
VERSION = "pnu.ordinal-identity-recovery.20260913-v2"
_IDENTITY = re.compile(r"^\s*(제\s*\d+\s*회.{2,160}?)의\s+")


def canonical_identity(text):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", unicodedata.normalize("NFKC", text)).casefold()


def identity_matches(claim, result):
    match = _IDENTITY.match(claim)
    if not match:
        return False
    identity = match[1]
    title = str(result.get("source_title") or "")
    if len(v1.ORDINAL.findall(title)) != 1 or len(v1.ORDINAL.findall(identity)) != 1:
        return False
    return canonical_identity(identity) in canonical_identity(title)


def attribute_claim(claim, results, *, semantic_guard=True):
    original = ORIGINAL_ATTRIBUTE(claim, results, semantic_guard=semantic_guard)
    if original["supported"] or not semantic_guard:
        return original
    qualified = [(i, result) for i, result in enumerate(results, 1) if identity_matches(claim, result)]
    if not qualified:
        return original
    with v1.enabled():
        recovered = ORIGINAL_ATTRIBUTE(claim, [r for _, r in qualified], semantic_guard=True)
    if not recovered["supported"]:
        return original
    by_local = {i: original_number for i, (original_number, _) in enumerate(qualified, 1)}
    by_chunk = {r["chunk_id"]: number for number, r in qualified}
    if len(by_chunk) != len(qualified):
        return original  # Ambiguous duplicate identities: no recovery.
    recovered["source_numbers"] = [by_local[n] for n in recovered["source_numbers"]]
    recovered["citations"] = [{**c, "source_number": by_chunk[c["chunk_id"]]} for c in recovered["citations"]]
    recovered["ordinal_recovery"] = {"version": VERSION, "original_reason": original["validation_reason"],
                                      "identity_policy": "full_explicit_event_identity_in_same_single_edition_title"}
    return recovered


@contextmanager
def enabled():
    previous = api.attribute_claim
    api.attribute_claim = attribute_claim
    try:
        yield
    finally:
        api.attribute_claim = previous


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.correct = "제7회 ALPHA 공모전의 신청 마감은 2028년 8월 5일입니다."
        self.wrong = self.correct.replace("ALPHA", "BETA")

    def test_unrestricted_wrapper_counterexample_reproduced(self):
        with v1.enabled():
            self.assertTrue(ORIGINAL_ATTRIBUTE(self.wrong, [v1.source()])["supported"])
        self.assertFalse(attribute_claim(self.wrong, [v1.source()])["supported"])

    def test_correct_identity_recovered(self):
        self.assertFalse(ORIGINAL_ATTRIBUTE(self.correct, [v1.source()])["supported"])
        self.assertTrue(attribute_claim(self.correct, [v1.source()])["supported"])

    def test_wrong_edition_rejected(self):
        self.assertFalse(attribute_claim(self.correct.replace("제7회", "제8회"), [v1.source()])["supported"])

    def test_wrong_date_rejected(self):
        self.assertFalse(attribute_claim(self.correct.replace("8월 5일", "8월 7일"), [v1.source()])["supported"])

    def test_frequency_cannot_borrow_title(self):
        self.assertFalse(attribute_claim("제7회 ALPHA 공모전의 지원 횟수는 7회입니다.",
            [v1.source(text="지원 횟수는 3회입니다.")])["supported"])

    def test_no_cross_document_binding(self):
        matching_title = v1.source(text="신청 마감은 2028년 8월 7일입니다.")
        other = {**v1.source(title="제7회 BETA 공모전"), "chunk_id": "synthetic#2"}
        self.assertFalse(attribute_claim(self.correct, [matching_title, other])["supported"])

    def test_multi_edition_title_not_recovered(self):
        self.assertFalse(attribute_claim(self.correct, [v1.source(title="제7회 ALPHA 공모전 및 제8회 BETA 공모전")])["supported"])

    def test_no_possessive_not_recovered(self):
        self.assertFalse(attribute_claim(self.correct.replace("공모전의", "공모전"), [v1.source()])["supported"])

    def test_original_supported_result_unchanged(self):
        claim = "신청 마감은 2028년 8월 5일입니다."
        self.assertEqual(attribute_claim(claim, [v1.source()]), ORIGINAL_ATTRIBUTE(claim, [v1.source()]))

    def test_original_source_numbers_preserved(self):
        wrong = {**v1.source(title="제8회 BETA 공모전"), "chunk_id": "wrong#1", "source_number": 1}
        correct = {**v1.source(), "chunk_id": "right#2", "source_number": 2}
        result = attribute_claim(self.correct, [wrong, correct])
        self.assertTrue(result["supported"])
        self.assertEqual(result["source_numbers"], [2])
        self.assertEqual({c["source_number"] for c in result["citations"]}, {2})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RecoveryTests))
        return int(not result.wasSuccessful())
    if not args.out or args.out.exists():
        parser.error("a new --out directory is required")
    old = ROOT / "processed/eval/preflight-20260913/scope-bound-c3-v1/ordinal-replay-v1"
    prior_rows = json.loads((old / "replay.json").read_text())
    baseline_pins = json.loads((old / "manifest.json").read_text())["sha256"]
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in baseline_pins.items()):
        raise ValueError("original_replay_inputs_changed")
    wanted = {(r["case_id"], r["run"]): r for r in prior_rows}
    rows = []
    for run in (1, 2, 3):
        path = ROOT / f"processed/eval/preflight-20260905/shadow60-generation-v1/c1-run{run}.answers.jsonl"
        for record in (json.loads(s) for s in path.read_text().splitlines()):
            prior = wanted.get((record["case_id"], run))
            if prior is None:
                continue
            contexts = record["evaluation_trace"]["retrieval_stages"]["final_contexts"]
            before = api.build_rag_response(record["query"], contexts, record["evaluation_trace"]["sanitized_draft"], record["generator"])
            with enabled():
                after = api.build_rag_response(record["query"], contexts, record["evaluation_trace"]["sanitized_draft"], record["generator"])
            if before["answer"] != prior["before"]:
                raise ValueError("baseline_replay_mismatch")
            b = [c["text"] for c in before["claims"] if c["supported"]]
            a = [c["text"] for c in after["claims"] if c["supported"]]
            rows.append({"case_id": record["case_id"], "run": run, "changed": before["answer"] != after["answer"],
                         "added": [s for s in a if s not in b], "removed": [s for s in b if s not in a],
                         "before": before["answer"], "after_unjudged": after["answer"]})
    if len(rows) != 126:
        raise ValueError("expected126")
    summary = {"version": VERSION, "records": len(rows), "changed_records": sum(r["changed"] for r in rows),
               "changed_questions": len({r["case_id"] for r in rows if r["changed"]}),
               "added_claims": sum(len(r["added"]) for r in rows), "removed_claims": sum(len(r["removed"]) for r in rows),
               "external_calls": 0, "new_gfc": None, "service_modified": False,
               "limitations": "Conservative identity-bound saved-draft replay, not E2E generation/GFC or universal semantic verification"}
    args.out.mkdir(parents=True, exist_ok=False)
    pins = {**baseline_pins, str(Path(__file__)): hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    for name, obj in (("summary.json", summary), ("replay.json", rows), ("manifest.json", {"sha256": pins})):
        with (args.out / name).open("x", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
