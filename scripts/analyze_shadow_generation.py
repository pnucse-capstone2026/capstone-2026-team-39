#!/usr/bin/env python3
"""Summarize three frozen Shadow60 generation runs with independent Judge artifacts.

No network calls. One question contributes one majority vote; legacy gold-chunk
fields are intentionally not used for this atomic-evidence question schema.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_generation_failures import failure_causes  # noqa: E402
from analyze_generation_gfc_repeats import summarize_condition  # noqa: E402
from analyze_service_ab import load_cases  # noqa: E402
from service_eval_artifacts import (  # noqa: E402
    join_answers_and_judgments, load_unique_jsonl, sha256_json,
    validate_answer_record, validate_judgment_record,
)

CASES_SHA = "0906e6c8de40040915e668e6cf61639306657e6f94f41e5bcdf62197a6d90754"


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(cases: dict, runs: list[dict]) -> tuple[dict, list[dict]]:
    if len(runs) != 3:
        raise ValueError("exactly three independent generation runs are required")
    order = list(cases)
    overall, votes = summarize_condition(order, cases, runs)
    overall.pop("effective_sample_n", None)
    overall["question_family_count"] = len({case["family_id"] for case in cases.values()})
    rows = []
    cross = Counter()
    causes = Counter()
    guards = Counter()
    contract_downgrades = []
    service_claim_reasons = Counter()
    service_diagnostics = []
    for case_id in order:
        case = cases[case_id]
        records = [run[case_id] for run in runs]
        answerable = case.get("answerable") is True
        gold_docs = {
            option["document_id"]
            for claim in case.get("required_claims", [])
            for option in claim.get("evidence_options", [])
        }
        evidence_flags = []
        source_flags = []
        signatures = []
        for record in records:
            contexts = record["evaluation_trace"]["retrieval_stages"]["final_contexts"]
            signatures.append([(c["chunk_id"], c.get("text", "")) for c in contexts])
            if answerable:
                atomic = record.get("atomic_evidence_at_k", {}).get("8", {})
                if atomic.get("available") is not True or type(atomic.get("all_matched")) is not bool:
                    raise ValueError(f"{case_id}: missing atomic evidence@8")
                evidence_flags.append(atomic["all_matched"])
                source_flags.append(bool(gold_docs & {c.get("document_id") for c in contexts[:8]}))
                cross[(source_flags[-1], evidence_flags[-1], record["judge"]["grounded_fully_correct"])] += 1
            causes.update(failure_causes(record))
            guards.update(record.get("deterministic_guard", {}).get("rules", []))
            guard = record.get("deterministic_guard", {})
            service_claims = record.get("claims")
            claims_available = isinstance(service_claims, list)
            service_claims = service_claims if claims_available else []
            service_claim_reasons.update(c.get("validation_reason", "unknown") for c in service_claims)
            all_rejected = bool(service_claims) and all(c.get("supported") is False for c in service_claims)
            service_diagnostics.append({
                "case_id": case_id,
                "generation_run_id": record["generation_run_id"],
                "claims_trace_available": claims_available,
                "all_service_claims_rejected": all_rejected if claims_available else None,
                "service_claims_truncated": record.get("postprocessing", {}).get("claims_truncated"),
                "judge_explicit_refusal_guard": "answerable_clear_refusal_forces_score_zero" in guard.get("rules", []),
            })
            if ("unquotable_supported_claim_forces_missing" in guard.get("rules", [])
                    and guard.get("original_fields", {}).get("grounded_fully_correct") is True
                    and record["judge"]["grounded_fully_correct"] is False):
                contract_downgrades.append({"case_id": case_id, "generation_run_id": record["generation_run_id"]})
        stable = all(signature == signatures[0] for signature in signatures)
        rows.append({
            "case_id": case_id,
            "family_id": case["family_id"],
            "bucket": case["shadow_bucket"],
            "category": case["category"],
            "challenge_type": case.get("challenge_type", ""),
            "answerable": answerable,
            "score_mean": mean(record["judge"]["score"] for record in records),
            "gfc_run1": votes[case_id]["votes"][0],
            "gfc_run2": votes[case_id]["votes"][1],
            "gfc_run3": votes[case_id]["votes"][2],
            "gfc_majority": votes[case_id]["majority"],
            "gfc_unanimous": len(set(votes[case_id]["votes"])) == 1,
            "source_hit_at_8_runs": source_flags,
            "atomic_all_evidence_at_8_runs": evidence_flags,
            "retrieval_identical_across_runs": stable,
            "unique_final_answers": len({record["answer"] for record in records}),
            "failure_causes_runs": [failure_causes(record) for record in records],
            "judge_guard_rules_runs": [record.get("deterministic_guard", {}).get("rules", []) for record in records],
        })
    groups = {}
    selections = {
        "core": [r for r in rows if r["bucket"] in {"simple", "multi"}],
        "simple": [r for r in rows if r["bucket"] == "simple"],
        "multi": [r for r in rows if r["bucket"] == "multi"],
        "role_variant": [r for r in rows if r["bucket"] == "role_variant"],
        "prompt_injection": [r for r in rows if r["challenge_type"] == "prompt_injection"],
        "unanswerable": [r for r in rows if r["challenge_type"] == "unanswerable"],
        "scope_version_ambiguity": [r for r in rows if r["challenge_type"] == "scope_version_ambiguity"],
        "all_answerable": [r for r in rows if r["answerable"]],
        "all": rows,
    }
    for name, selection in selections.items():
        if not selection:
            continue
        groups[name] = {
            "questions": len(selection),
            "question_families": len({r["family_id"] for r in selection}),
            "mean_score_0_to_2": mean(r["score_mean"] for r in selection),
            "run_gfc_counts": [sum(r[f"gfc_run{i}"] for r in selection) for i in (1, 2, 3)],
            "majority_gfc_count": sum(r["gfc_majority"] for r in selection),
            "majority_gfc_rate": mean(r["gfc_majority"] for r in selection),
            "gfc_unanimous_count": sum(r["gfc_unanimous"] for r in selection),
        }
    summary = {
        "schema_version": "pnu.shadow-generation-analysis.v1",
        "interpretation": "Exploratory synthetic Shadow60, not final holdout; no C0 generation comparison collected.",
        "primary_metric": "question-level strict 2/3 majority grounded_fully_correct",
        "secondary_metric": "question-level mean Judge score on 0-to-2 scale",
        "overall": overall,
        "groups": groups,
        "retrieval_changed_case_ids": [r["case_id"] for r in rows if not r["retrieval_identical_across_runs"]],
        "failure_causes_multilabel_answer_instances": dict(causes),
        "judge_guards_answer_instances": dict(guards),
        "judge_quote_contract_gfc_downgrades": contract_downgrades,
        "service_postprocessing": {
            "unit": "saved service claim or answer instance; descriptive, not a false-positive rate",
            "claim_validation_reason_counts": dict(service_claim_reasons),
            "answer_instances_with_all_claims_rejected": sum(r["all_service_claims_rejected"] is True for r in service_diagnostics),
            "answer_instances_with_missing_claim_trace": sum(not r["claims_trace_available"] for r in service_diagnostics),
            "all_rejected_and_judge_explicit_refusal": sum(r["all_service_claims_rejected"] is True and r["judge_explicit_refusal_guard"] for r in service_diagnostics),
            "truncated_answer_instances": [r for r in service_diagnostics if r["service_claims_truncated"] is True],
            "diagnostics": service_diagnostics,
            "note": "A rejected service claim may be genuinely unsupported. False-positive rejection requires source/draft inspection; no raw draft is rescored or substituted.",
        },
        "failure_label_note": "Missing-claim labels include Judge quote-contract guard downgrades and cannot all be attributed to the generator; raw Judge judgments are not replacement scores.",
        "cross_table_unit": "answerable question x generation run (descriptive only, not independent samples)",
        "cross_table": [{"source_hit_at_8": s, "atomic_all_evidence_at_8": e, "gfc": g, "count": n}
                        for (s, e, g), n in sorted(cross.items())],
    }
    return summary, rows


def select_cases(cases: dict, *, core_only: bool) -> dict:
    return {cid: case for cid, case in cases.items()
            if not core_only or case["shadow_bucket"] in {"simple", "multi"}}


def load_bound_run(answers: Path, judgments: Path, cases: dict, cases_sha: str, *, core_only: bool = False) -> dict:
    judgment_records = load_unique_jsonl(judgments, key="judgment_id")
    answer_records = load_unique_jsonl(answers, key="answer_id")
    judgments_by_case = {r["case_id"]: r for r in judgment_records}
    answer_rows = {r["case_id"]: r for r in answer_records}
    for records, by_case in ((judgment_records, judgments_by_case), (answer_records, answer_rows)):
        if len(records) != len(by_case) or set(by_case) != set(cases):
            raise ValueError("all frozen case IDs must appear exactly once in each input")
    selected = select_cases(cases, core_only=core_only)
    answers_sha = file_sha(answers)
    for case_id in cases:
        raw = answer_rows[case_id]
        judged = judgments_by_case[case_id]
        validate_answer_record(raw)
        validate_judgment_record(judged)
        if raw.get("error") or "judge" in raw:
            raise ValueError(f"{case_id}: answer error or mixed inline Judge")
        if judged.get("error") and case_id in selected:
            raise ValueError(f"{case_id}: terminal Judge error")
        if raw["case_sha256"] != sha256_json(cases[case_id]) or raw["collector_config"]["cases_sha256"] != cases_sha:
            raise ValueError(f"{case_id}: case hash mismatch")
        if judged["answers_artifact_sha256"] != answers_sha or judged["answer_record_sha256"] != sha256_json(raw):
            raise ValueError(f"{case_id}: Judge artifact binding mismatch")
        for field in ("answer_id", "answer_sha256", "experiment_id", "condition_id", "generation_run_id"):
            if judged[field] != raw[field]:
                raise ValueError(f"{case_id}: {field} mismatch")
        if raw["generation"]["used"] != "frontier" or raw["generation"]["model"] != "gemini-3.5-flash-lite":
            raise ValueError(f"{case_id}: wrong generation model/provider")
    run = join_answers_and_judgments(
        {cid: answer_rows[cid] for cid in selected},
        [judgments_by_case[cid] for cid in selected],
    )
    for case_id, record in run.items():
        record["deterministic_guard"] = judgments_by_case[case_id].get("deterministic_guard") or {}
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "config/pnu-service-shadow60-v1.jsonl")
    parser.add_argument("--answers", type=Path, nargs=3, required=True)
    parser.add_argument("--judgments", type=Path, nargs=3, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--csv-out", type=Path, required=True)
    parser.add_argument("--core-only", action="store_true",
                        help="Score only the predefined simple/multi core; still validate every input's integrity and record non-core errors")
    args = parser.parse_args()
    if file_sha(args.cases) != CASES_SHA:
        parser.error("only frozen Shadow60 v1 cases are accepted")
    if args.json_out.resolve() == args.csv_out.resolve() or args.json_out.exists() or args.csv_out.exists():
        parser.error("both output paths must be new and distinct")
    _, cases = load_cases(args.cases)
    runs = [load_bound_run(a, j, cases, CASES_SHA, core_only=args.core_only) for a, j in zip(args.answers, args.judgments)]
    judge_configs = {sha256_json(r["judgment"]["judge_config"]) for run in runs for r in run.values()}
    if len(judge_configs) != 1:
        parser.error("Judge config changed across runs")
    scoring_cases = select_cases(cases, core_only=args.core_only)
    summary, rows = summarize(scoring_cases, runs)
    summary["scope"] = {
        "core_only": args.core_only,
        "frozen_question_count": len(cases),
        "scored_question_count": len(scoring_cases),
        "excluded_by_predefined_bucket": [cid for cid in cases if cid not in scoring_cases],
        "terminal_judge_errors": [
            {"case_id": r["case_id"], "generation_run_id": r["generation_run_id"], "error": r["error"]}
            for p in args.judgments for r in load_unique_jsonl(p, key="judgment_id") if r.get("error")
        ],
        "note": "Core-only excludes ALL non-core questions regardless of score; errors are not converted to zeros or imputed. Full Shadow60 evaluation remains incomplete if any Judge error exists.",
    }
    if args.core_only:
        summary["interpretation"] = "Exploratory predefined Shadow60 core only, not a full 60-question result, final holdout, or C0 generation comparison."
    summary["inputs"] = [{"path": str(p), "sha256": file_sha(p)} for p in [args.cases, *args.answers, *args.judgments]]
    for output in (args.json_out, args.csv_out):
        output.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    with args.csv_out.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary["groups"], ensure_ascii=False))


if __name__ == "__main__":
    main()
