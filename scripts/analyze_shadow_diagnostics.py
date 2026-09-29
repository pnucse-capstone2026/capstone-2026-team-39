#!/usr/bin/env python3
"""Offline Shadow diagnostic replay and C2 safety probes. Never rescore answers."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from immutable_outputs import publish_immutable_texts, reject_symlink_inputs  # noqa: E402
from service_eval_artifacts import sha256_json  # noqa: E402

PACKET = ROOT / "processed/eval/preflight-20260907/shadow14-human-review-v1/packet.json"
PACKET_SHA = "e469fb1ccc8457c57e5626a7839470d6eed465ecc97140f6210ab9cdac178212"
NOTES = ROOT / "evidence/20260914/shadow14-ai-observations-20260907.json"
CODE_PINS = {
    "scripts/search_api.py": "9a8865db361d6d3a044d26fc57698f56840540ed573a5f37bb50ef684c1cf4a5",
    "scripts/bm25_search.py": "6c474dc83a4f27ab3172de5b839f731e9954206b1d3cb1748dc08f3848cb1ad7",
    "scripts/rag/generators.py": "67cccfd602b808929c226fcbb659635495e50e05103592664020c6c3a725166b",
    "scripts/judge_service_answers.py": "95b653d336058bc8eb8829c64472a3ad94162bc3a08d87400f0391e7b06e7414",
    "scripts/rag/grounded_claims_v2.py": "65779d9dca5ba11233d23f8082a87f605def143d3b96941ca1d2b118b149aa13",
    "scripts/evaluate_grounded_claims_v2.py": "86bf30dab92e02970475db2566c4982f41e7552e54db45738127c060028e972b",
}
CAUSES = {"postprocessing_rejection", "sentence_segmentation", "judge_quote_contract",
          "mixed_generation_and_postprocessing", "no_primary_gap_observed"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def forbid_network_and_protected_files(event: str, args: tuple) -> None:
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo"}:
        raise RuntimeError("offline diagnostic forbids network access")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(args[0].decode() if isinstance(args[0], bytes) else args[0]).resolve()
        protected = {
            ROOT / "config/pnu-service-answer-holdout-v2.draft.jsonl",
            ROOT / "docs/holdout-v2-human-review.md",
            ROOT / "evidence/holdout-v2-reviewer-a.json",
            ROOT / "evidence/holdout-v2-reviewer-b.json",
        }
        if path in protected or path.name == ".env":
            raise RuntimeError("offline diagnostic forbids credentials/holdout access")


def validate_notes(packet: dict, notes: dict) -> dict:
    if (notes.get("schema_version") != "pnu.shadow-ai-observations.v1"
            or notes.get("author_type") != "assistant_ai" or notes.get("human_review") is not False
            or notes.get("packet_sha256") != PACKET_SHA):
        raise ValueError("notes must be AI-only and bound to the frozen packet")
    by_id = {n["case_id"]: n for n in notes["items"]}
    if len(by_id) != len(notes["items"]) or set(by_id) != {i["case_id"] for i in packet["items"]}:
        raise ValueError("notes must cover every case exactly once")
    for note in by_id.values():
        if len(note["run_causes"]) != 3 or not set(note["run_causes"]) <= CAUSES:
            raise ValueError("invalid per-run cause labels")
    return by_id


def evidence_matches(quote: str, contexts: list[dict]) -> list[dict]:
    matches = []
    for c in contexts:
        for field in ("text", "source_title"):
            text = c.get(field) or ""
            start = text.find(quote)
            if start >= 0:
                matches.append({"source_number": c["source_number"], "chunk_id": c["chunk_id"],
                                "field": field, "start": start, "end": start + len(quote), "quote": quote})
    if not quote or not matches:
        raise ValueError(f"AI evidence quote does not occur in saved context: {quote}")
    return matches


def quote_audit(run: dict, judge_module) -> list[dict]:
    original = run.get("judge_guard", {}).get("original_fields", {})
    before = original.get("claim_checks", run["judge"].get("claim_checks", []))
    after = {c["claim_id"]: c for c in run["judge"].get("claim_checks", [])}
    result = []
    for check in before:
        if check["status"] not in {"supported", "partial"}:
            continue
        quote = check.get("answer_quote")
        contiguous = judge_module._quote_is_contiguous(run["answer"], quote)
        canonical_answer = judge_module._canonicalize_answer_quote_text(run["answer"])
        fragments = [judge_module._canonicalize_answer_quote_text(line)
                     for line in str(quote or "").splitlines() if line.strip()]
        result.append({
            "claim_id": check["claim_id"], "original_status": check["status"],
            "final_status": after[check["claim_id"]]["status"], "quote": quote,
            "contiguous_under_frozen_v11": contiguous,
            "individual_lines_present": bool(fragments) and all(x in canonical_answer for x in fragments),
            "diagnostic_only": True,
        })
    return result


def replay_items(packet: dict, notes: dict, originals: dict, api, judge_module) -> list[dict]:
    rows = []
    for item in packet["items"]:
        note = notes[item["case_id"]]
        gold_ids = {e["evidence_chunk_id"] for c in item["required_claims"] for e in c["evidence_options"]}
        for index, run in enumerate(item["runs"]):
            raw = originals[run["generation_run_id"]][item["case_id"]]
            contexts = raw["evaluation_trace"]["retrieval_stages"]["final_contexts"]
            if raw["answer_id"] != run["answer_id"] or raw["answer"] != run["answer"]:
                raise ValueError("packet answer binding mismatch")
            split = api.split_draft_claims(run["sanitized_draft"])
            claims = []
            for ci, saved in enumerate(run["claims"]):
                replayed = api.attribute_claim(saved["text"], contexts)
                compared = ("supported", "validation_reason", "missing_critical_values", "source_numbers")
                exact = all(replayed[k] == saved[k] for k in compared)
                checks = []
                if saved["supported"] is False:
                    for c in contexts:
                        if c["chunk_id"] not in gold_ids:
                            continue
                        good, missing = api._critical_value_support(saved["text"], api.extract_critical_values(saved["text"]), c)
                        single = api.attribute_claim(saved["text"], [c])
                        checks.append({
                            "source_number": c["source_number"], "chunk_id": c["chunk_id"],
                            "critical_values_supported": good, "missing_values": sorted(missing),
                            "semantic_relation_mismatch": api._semantic_relation_mismatch(saved["text"], c["text"], source_scope=api._result_scope_text(c)),
                            "single_source_attribution_reason": single["validation_reason"],
                            "single_source_best_score": single["best_score"],
                        })
                claims.append({"index": ci, "text": saved["text"], "saved_supported": saved["supported"],
                               "saved_reason": saved["validation_reason"], "replay_exact": exact,
                               "critical_values": sorted(api.extract_critical_values(saved["text"])),
                               "gold_context_checks": checks})
            spans = [evidence_matches(q, contexts) for q in note["evidence_quotes"]]
            rows.append({
                "review_id": item["review_id"], "case_id": item["case_id"],
                "generation_run_id": run["generation_run_id"], "answer_id": run["answer_id"],
                "answer_sha256": run["answer_sha256"], "selection_group": item["selection"]["selection_group"],
                "ai_primary_cause": note["run_causes"][index], "ai_finding": note["finding"],
                "ai_confidence": note["confidence"], "open_question": note["open_question"],
                "saved_judge_score": run["judge"]["score"], "saved_gfc": run["judge"]["grounded_fully_correct"],
                "replacement_score": None, "human_review": False,
                "split_replay_exact": split == [c["text"] for c in run["claims"]],
                "split_replay": split, "claim_replays": claims,
                "quote_audit": quote_audit(run, judge_module), "evidence_spans": spans,
            })
    return rows


def c2_probes(packet: dict, c2) -> list[dict]:
    """Hand-built verifier inputs; NOT model generations or observed error rates."""
    by_id = {i["case_id"]: i for i in packet["items"]}
    probes = []
    specs = [
        ("gsat_correct_broad", "shadow_core_04", "#0003", "대기업 GSAT 과정의 프로그램 운영일정은 2026년 8월 5일부터 8월 7일까지입니다.", None, True),
        ("gsat_wrong_row_broad", "shadow_core_04", "#0003", "대기업 GSAT 과정의 프로그램 운영일정은 2026년 8월 24일부터 8월 28일까지입니다.", None, False),
        ("gsat_wrong_row_narrow", "shadow_core_04", "#0003", "대기업 GSAT 과정의 프로그램 운영일정은 2026년 8월 24일부터 8월 28일까지입니다.", "대기업 (GSAT) 과정\t프로그램운영\t2026. 8. 5.(수) ~ 8. 7.(금)\t비대면", False),
        ("phone_correct_broad", "shadow_sup_04", "#0003", "성평등상담실의 전화번호는 051-510-7890입니다.", None, True),
        ("phone_wrong_subject_broad", "shadow_sup_04", "#0003", "성평등상담실의 전화번호는 051-510-7942입니다.", None, False),
        ("phone_wrong_subject_narrow", "shadow_sup_04", "#0003", "성평등상담실의 전화번호는 051-510-7942입니다.", "[성평등상담실(성희롱·성폭력)] ☎ 051-510-7890", False),
    ]
    for pid, cid, suffix, text, narrow, expected in specs:
        contexts = by_id[cid]["runs"][0]["contexts"]
        gold_ids = {e["evidence_chunk_id"] for c in by_id[cid]["required_claims"] for e in c["evidence_options"]}
        context = next(c for c in contexts if c["chunk_id"] in gold_ids and c["chunk_id"].endswith(suffix))
        quote = context["text"] if narrow is None else narrow
        if quote not in context["text"]:
            raise ValueError("synthetic probe quote is not in source")
        payload = {"claims": [{"text": text, "evidence": [{"source_number": context["source_number"], "quote": quote}]}], "unanswered": []}
        verified = c2.verify_response(payload, contexts)
        result = dict(verified.claims[0])
        probes.append({"probe_id": pid, "case_id": cid, "input_kind": "ai_constructed_verifier_probe_not_model_output",
                       "expected_supported_from_source": expected, "actual_supported": result["supported"],
                       "matches_expected": result["supported"] == expected,
                       "input": payload, "result": result})
    return probes


def c2_dry_plan(originals: dict, cases: list[dict], collector) -> dict:
    core_ids = [c["id"] for c in cases if c["shadow_bucket"] in {"simple", "multi"}]
    plans = []
    for run_id in ("run1", "run2", "run3"):
        records = [originals[run_id][cid] for cid in core_ids]
        planned, plan = collector.plan_collection(records, model="gemini-3.5-flash-lite", max_output_tokens=1200)
        plan["generation_run_id"] = run_id
        plan["context_visibility"] = []
        configs = []
        for record, prompt in planned:
            c1_prompt = record["evaluation_trace"]["generation_input"]["user_prompt"]
            contexts = record["evaluation_trace"]["retrieval_stages"]["final_contexts"]
            plan["context_visibility"].append({
                "case_id": record["case_id"], "c2_prompt_chars": len(prompt),
                "all_full_texts_present_c1": all(c["text"].strip() in c1_prompt for c in contexts),
                "all_full_texts_present_c2": all(c["text"].strip() in prompt for c in contexts),
                "evidence_block_bytes_equal": c1_prompt.split("<검색_근거_시작>", 1)[1] == prompt.split("<검색_근거_시작>", 1)[1],
            })
            config = record["generation"]["request_config"]["generation_config"]
            if config not in configs:
                configs.append(config)
        plan["saved_c1_generation_configs"] = configs
        plans.append(plan)
    return {
        "status": "PLAN_ONLY_NOT_AUTHORIZED_FOR_NETWORK_OR_ADOPTION",
        "scope": "all predefined core42, never only the 14 inspected cases",
        "primary_metric": "paired question-level 2/3 majority GFC; original v11 unchanged",
        "planned_generation_calls": 126, "planned_judge_calls": 126,
        "planned_logical_calls": 252, "max_generation_attempts_per_slot": 3,
        "max_judge_attempts_per_slot": 6, "max_http_attempts": 1134,
        "external_calls_executed": 0, "plans": plans,
        "interpretation": "Frozen C2 package comparison, not a single-factor quote-binding ablation. C1 has maxOutputTokens=900 with temperature unspecified; C2 requests 1200 and temperature=0. Context metadata rendering also differs.",
        "adoption_gate": "Do not adopt C2 while the synthetic wrong-row/subject probes pass. Any fix requires a separately authorized experimental version; never patch frozen C1/C2/Judge during this analysis.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    base = (ROOT / "processed/eval").resolve()
    if output.exists() or output == base or not output.is_relative_to(base):
        parser.error("choose a new subdirectory under processed/eval")
    sys.addaudithook(forbid_network_and_protected_files)
    reject_symlink_inputs([PACKET, NOTES])
    if digest(PACKET) != PACKET_SHA:
        parser.error("frozen packet SHA mismatch")
    packet = json.loads(PACKET.read_text())
    notes = validate_notes(packet, json.loads(NOTES.read_text()))
    pinned = {**CODE_PINS, **{r["path"]: r["sha256"] for r in packet["inputs"]}}
    for path, expected in pinned.items():
        if digest(ROOT / path) != expected:
            parser.error(f"input/code SHA mismatch: {path}")
    import search_api as api
    import judge_service_answers as judge_module
    from rag import grounded_claims_v2 as c2
    import evaluate_grounded_claims_v2 as collector
    originals = {}
    for row in packet["inputs"]:
        if row["path"].endswith(".answers.jsonl"):
            values = [json.loads(line) for line in (ROOT / row["path"]).read_text().splitlines()]
            originals[values[0]["generation_run_id"]] = {r["case_id"]: r for r in values}
    cases = [json.loads(line) for line in (ROOT / "config/pnu-service-shadow60-v1.jsonl").read_text().splitlines()]
    rows = replay_items(packet, notes, originals, api, judge_module)
    probes = c2_probes(packet, c2)
    plan = c2_dry_plan(originals, cases, collector)
    counts = Counter(r["ai_primary_cause"] for r in rows)
    quote_failures = [{"case_id": r["case_id"], "run": r["generation_run_id"], **q}
                      for r in rows for q in r["quote_audit"] if not q["contiguous_under_frozen_v11"]]
    summary = {
        "schema_version": "pnu.shadow-offline-diagnostics.v1", "author_type": "assistant_ai",
        "question_count": len(packet["items"]), "answer_count": len(rows),
        "primary_causes_answer_instances": dict(counts),
        "saved_gfc_count_in_purposive_sample": sum(r["saved_gfc"] for r in rows),
        "replacement_scores_computed": False, "human_labels_written": 0,
        "split_replays": len(rows), "split_mismatches": sum(not r["split_replay_exact"] for r in rows),
        "claim_replays": sum(len(r["claim_replays"]) for r in rows),
        "claim_mismatches": sum(not c["replay_exact"] for r in rows for c in r["claim_replays"]),
        "judge_quote_contract_failures": quote_failures,
        "synthetic_c2_probes": {"count": len(probes), "unexpected_acceptances": [p["probe_id"] for p in probes if p["actual_supported"] and not p["expected_supported_from_source"]]},
        "open_normative_cases": [cid for cid, n in notes.items() if n["open_question"]],
        "external_api_calls": 0, "holdout_accessed": False,
        "limitations": "AI diagnostic on purposively selected failures and controls; no population error rates, revised GFC, human calibration, or C2 generation-performance estimate.",
    }
    if summary["claim_mismatches"] or summary["split_mismatches"]:
        parser.error("frozen replay differs from saved trace; do not publish causal summary")
    table = io.StringIO(newline="")
    writer = csv.DictWriter(table, fieldnames=["review_id", "case_id", "generation_run_id", "ai_primary_cause", "saved_judge_score", "saved_gfc", "ai_confidence", "ai_finding", "open_question"])
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row[k] for k in writer.fieldnames})
    texts = {"summary.json": encoded(summary), "diagnostics.json": encoded(rows),
             "diagnostics.csv": table.getvalue(), "c2-verifier-probes.json": encoded(probes),
             "c2-core42-dry-plan.json": encoded(plan)}
    for path, expected in pinned.items():
        if digest(ROOT / path) != expected:
            parser.error("input changed during diagnosis")
    manifest = {"schema_version": "pnu.shadow-diagnostic-replay-manifest.v1", "date": "2026-09-07",
                "inputs": [{"path": p, "sha256": s} for p, s in pinned.items()],
                "packet_sha256": PACKET_SHA, "notes_sha256": digest(NOTES),
                "script_sha256": digest(Path(__file__)),
                "outputs": [{"path": n, "sha256": hashlib.sha256(t.encode()).hexdigest()} for n, t in texts.items()]}
    texts["manifest.json"] = encoded(manifest)
    publish_immutable_texts({output / name: value for name, value in texts.items()}, authoritative_path=output / "manifest.json")
    print(encoded({k: v for k, v in summary.items() if k != "judge_quote_contract_failures"}))


if __name__ == "__main__":
    main()
