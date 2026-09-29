"""Frozen C1 stage lineage and separately labelled AI evidence review, offline.

Never score raw drafts with the final-answer GFC rubric. A rejected claim is not
automatically a correct claim lost. New observations require exact source quotes
and stay AI diagnostics, not human review, gold edits, or replacement judgments.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys


def offline_audit(event, args):
    if event.startswith(("socket.", "urllib.")):
        raise PermissionError("offline_only")
    if event == "open" and isinstance(args[0], (str, bytes)):
        p = Path(os.fsdecode(args[0]))
        if p.name.startswith(".env") or ("holdout" in str(p).lower() and p.suffix != ".py"):
            raise PermissionError("protected_data_disabled")


sys.addaudithook(offline_audit)
ROOT = Path(__file__).resolve().parents[2]
PRIOR = ROOT / "evidence/improvement-experiment-20260913-v1"
sys.path.insert(0, str(PRIOR))
import replay_current as frozen
from summarize_judge_repeats import aggregate_repeats

BASE = ROOT / "processed/eval/preflight-20260913/stage-diagnosis-v1"
ORIGIN = frozen.BASE
PIN_FILE = ORIGIN / "ordinal-current-v1/input-sha256.json"
PIN_SHA = "37e116e0573b7e6acd7c0bb2540325e76248240f94d5de6d913b151695ca0a36"
STATUS = {"source_supported", "partially_supported", "source_contradicted", "not_established", "abstention_statement", "uncertain"}
RELEVANCE = {"required", "optional", "irrelevant", "mixed", "uncertain"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def publish(out, values):
    out = Path(out)
    if out.exists() or out.resolve().parent != BASE or any(p.is_symlink() for p in (out, *out.parents)):
        raise ValueError("new_nonsymlink_experiment_output_required")
    out.mkdir(parents=True, exist_ok=False)
    for name, value in values.items():
        with (out / name).open("x", encoding="utf-8") as handle:
            handle.write(value if isinstance(value, str) else encode(value))


def checked_inputs():
    if sha(PIN_FILE) != PIN_SHA:
        raise ValueError("prior_pin_manifest_changed")
    pins = read(PIN_FILE)
    pins.update({str(PIN_FILE): PIN_SHA, str(Path(__file__)): sha(__file__)})
    if any(sha(p) != h for p, h in pins.items()):
        raise ValueError("frozen_input_changed")
    return pins


def lineage(answer):
    api = frozen.api
    frozen.validate_answer_record(answer)
    contexts = api.number_sources(answer["evaluation_trace"]["retrieval_stages"]["final_contexts"])
    cg = frozen.evaluate_contexts(contexts, mode="enforce")
    if cg.summary() != answer["security"]["context_gate"] or cg.excluded or cg.sanitized:
        raise ValueError("context_gate_drift")
    raw = answer["evaluation_trace"]["raw_draft"]
    sanitized = api.strip_untrusted_citation_markers(raw)
    if sanitized != answer["evaluation_trace"]["sanitized_draft"]:
        raise ValueError("sanitization_drift")
    split = api.split_draft_claims(sanitized)
    fallback = not split
    inputs = split if split else api.select_answer_claims(answer["query"], contexts)
    replay = api.build_rag_response(answer["query"], contexts, sanitized, answer["generator"])
    secured = frozen.enforce_output(replay, contexts)
    if frozen.project(secured.response) != frozen.project(answer) or secured.summary() != answer["security"]["output_gate"]:
        raise ValueError("baseline_replay_mismatch")
    if inputs[:api.MAX_CLAIMS] != [c["text"] for c in replay["claims"]]:
        raise ValueError("split_lineage_mismatch")
    claims = [{"claim_index": i, **{k: c[k] for k in ("text", "supported", "validation_reason", "missing_critical_values", "source_numbers", "best_score")}}
              for i, c in enumerate(replay["claims"])]
    return {"raw_draft": raw, "sanitized_draft": sanitized, "sanitization_changed": raw != sanitized,
            "split_claims": split, "extractive_fallback": fallback, "truncated_claims": inputs[api.MAX_CLAIMS:],
            "claims": claims, "pre_security_answer": replay["answer"], "final_answer": answer["answer"],
            "cited_answer": answer["cited_answer"], "output_gate_changed_answer": replay["answer"] != secured.response["answer"],
            "context_gate": cg.summary(), "output_gate": secured.summary(),
            "contexts": [{"source_number": c["source_number"], "chunk_id": c["chunk_id"], "document_id": c.get("document_id"),
                          "source_title": c.get("source_title", ""), "source_url": c.get("source_url"), "text": c.get("text", c.get("preview", ""))} for c in contexts]}


def mechanical_summary(items):
    claims = [c for r in items for c in r["claims"]]
    rejected = [c for c in claims if not c["supported"]]
    return {"questions": len(items), "baseline_exact": len(items), "split_claims": sum(len(r["split_claims"]) for r in items),
            "processed_claims": len(claims), "supported_claims": sum(c["supported"] for c in claims), "rejected_claims": len(rejected),
            "rejection_reasons": dict(Counter(c["validation_reason"] for c in rejected)),
            "questions_with_rejection": sum(any(not c["supported"] for c in r["claims"]) for r in items),
            "questions_all_claims_rejected": sum(bool(r["claims"]) and not any(c["supported"] for c in r["claims"]) for r in items),
            "non_gfc_questions_with_rejection": sum(not r["saved_gfc"] and any(not c["supported"] for c in r["claims"]) for r in items),
            "non_gfc_questions_without_rejection": sum(not r["saved_gfc"] and not any(not c["supported"] for c in r["claims"]) for r in items),
            "truncated_claims": sum(len(r["truncated_claims"]) for r in items),
            "extractive_fallback_questions": sum(r["extractive_fallback"] for r in items),
            "sanitization_changed_questions": sum(r["sanitization_changed"] for r in items),
            "output_gate_changed_answers": sum(r["output_gate_changed_answer"] for r in items),
            "saved_gfc": sum(r["saved_gfc"] for r in items), "raw_gfc": None,
            "note": "Exact stage counts, not semantic correctness or causal upper bounds. Rejection is not necessarily harmful."}


def build(out):
    pins = checked_inputs()
    manifest = read(ORIGIN / "preparation-v1/manifest.json")
    cases = {c["id"]: c for c in (json.loads(s) for s in (ROOT / "config/pnu-service-shadow60-v1.jsonl").read_text().splitlines())}
    items = []
    for cid in manifest["selected_cases"]:
        stem = "c1-sec-control--" + cid
        ap = ORIGIN / "live-v1" / (stem + ".answers.jsonl")
        jp = ORIGIN / "live-v1" / (stem + ".judgments.jsonl")
        a, j = read(ap), read(jp)
        _, validated = aggregate_repeats(answer_path=ap, judgment_paths=[jp])
        if len(validated) != 1 or validated[0]["case_id"] != cid:
            raise ValueError("judgment_binding_mismatch")
        items.append({"case_id": cid, "query": a["query"], "bucket": cases[cid]["shadow_bucket"],
                      "source_answer_path": str(ap), "source_answer_id": a["answer_id"],
                      "saved_score": j["judge"]["score"], "saved_gfc": j["judge"]["grounded_fully_correct"],
                      "saved_judge": j["judge"], "saved_guard": j["deterministic_guard"],
                      "atomic_evidence_at8": a["atomic_evidence_at_k"]["8"], "required_claims": cases[cid]["required_claims"],
                      **lineage(a)})
    if len(items) != 42 or any(sha(p) != h for p, h in pins.items()):
        raise ValueError("input_or_sample_drift")
    packet = {"schema_version": "pnu.stage-diagnosis.v1", "diagnostic_only": True, "human_review": False,
              "external_calls": 0, "items": items}
    template = {"author_type": "assistant_ai", "human_review": False, "packet_sha256": hashlib.sha256(encode(packet).encode()).hexdigest(),
                "rejected_claim_observations": [{"case_id": r["case_id"], "claim_index": c["claim_index"],
                    "text": c["text"], "status": "uncertain", "relevance": "uncertain", "quotes": [], "reason": "未検討"}
                    for r in items for c in r["claims"] if not c["supported"]]}
    summary = mechanical_summary(items)
    publish(out, {"packet.json": packet, "mechanical-summary.json": summary, "input-sha256.json": pins,
                  "ai-observations.template.json": template})
    print(encode(summary))


def evidence_span(item, quote):
    source = next((c for c in item["contexts"] if c["source_number"] == quote["source_number"]), None)
    field = quote.get("field", "text")
    if source is None or field not in {"text", "source_title"} or not quote["text"]:
        raise ValueError("invalid_evidence_reference")
    text = source.get(field) or ""
    start = text.find(quote["text"])
    if start < 0:
        raise ValueError("quote_not_in_saved_source")
    return {**quote, "field": field, "chunk_id": source["chunk_id"], "start": start,
            "end": start + len(quote["text"]), "sha256": hashlib.sha256(quote["text"].encode()).hexdigest()}


def validate_observations(packet, notes, packet_sha):
    if notes.get("author_type") != "assistant_ai" or notes.get("human_review") is not False or notes.get("packet_sha256") != packet_sha:
        raise ValueError("AI_notes_identity_mismatch")
    by_id = {r["case_id"]: r for r in packet["items"]}
    expected = {(r["case_id"], c["claim_index"]): c for r in packet["items"] for c in r["claims"] if not c["supported"]}
    rows, seen = [], set()
    for note in notes["rejected_claim_observations"]:
        key = (note["case_id"], note["claim_index"])
        if key in seen or key not in expected or note["text"] != expected[key]["text"]:
            raise ValueError("duplicate_missing_or_changed_claim")
        seen.add(key)
        if note["status"] not in STATUS or note["relevance"] not in RELEVANCE or not note["reason"].strip():
            raise ValueError("invalid_observation")
        if note["status"] in {"source_supported", "partially_supported", "source_contradicted"} and not note["quotes"]:
            raise ValueError("semantic_label_requires_source_quote")
        rows.append({**note, "source_spans": [evidence_span(by_id[key[0]], q) for q in note["quotes"]]})
    if seen != set(expected):
        raise ValueError("every_rejected_claim_requires_an_observation")
    return rows


def analyze(packet_path, notes_path, out):
    packet_path, notes_path = Path(packet_path), Path(notes_path)
    packet_sha, notes_sha = sha(packet_path), sha(notes_path)
    packet, notes = read(packet_path), read(notes_path)
    rows = validate_observations(packet, notes, packet_sha)
    supported = [r for r in rows if r["status"] == "source_supported" and r["relevance"] in {"required", "mixed"}]
    summary = {"human_review": False, "raw_gfc": None, "replacement_final_gfc": None, "external_calls": 0,
               "reviewed_rejected_claims": len(rows), "ai_status_counts": dict(Counter(r["status"] for r in rows)),
               "ai_relevance_counts": dict(Counter(r["relevance"] for r in rows)),
               "ai_source_supported_required_rejected_claims": len(supported),
               "ai_source_supported_required_rejected_questions": len({r["case_id"] for r in supported}),
               "supported_required_case_ids": sorted({r["case_id"] for r in supported}),
               "limitations": "AI diagnostic annotations after observing known development failures. Not human calibration, gold, GFC, or an independent causal estimate."}
    lines = ["# 生成초안→최종답변 분리 진단", "", "AI 원문 대조이며 사람 검수·새 Judge 판정이 아니다. 원시 초안의 GFC는 계산하지 않는다.", "",
             "## 기계적으로 재현한 단계별 수치", "", "```json", encode(mechanical_summary(packet["items"])).strip(), "```", "",
             "## 삭제 문장의 AI 근거 대조", "", "```json", encode(summary).strip(), "```", ""]
    for item in packet["items"]:
        lines += [f"## {item['case_id']}", "", item["query"], "", "### 생성 초안", "", item["raw_draft"], "",
                  "### 최종 답변", "", item["cited_answer"], "",
                  f"기존 Judge v11: {item['saved_score']}/2, GFC {item['saved_gfc']}. 원시 초안 점수로 재사용하지 않는다.", ""]
        for note in (r for r in rows if r["case_id"] == item["case_id"]):
            lines += [f"### 삭제 문장 {note['claim_index']}", "", note["text"], "",
                      f"AI 대조: {note['status']} / {note['relevance']}", "", note["reason"], ""]
            for q in note["source_spans"]:
                lines += [f"Source {q['source_number']} · {q['field']} · `{q['chunk_id']}`", "", q["text"], ""]
    if sha(packet_path) != packet_sha or sha(notes_path) != notes_sha:
        raise ValueError("analysis_inputs_changed")
    publish(out, {"summary.json": summary, "observations-with-spans.json": rows, "comparison.md": "\n".join(lines) + "\n",
                  "input-sha256.json": {str(packet_path): packet_sha, str(notes_path): notes_sha, str(Path(__file__)): sha(__file__)}})
    print(encode(summary))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("build", "analyze"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--notes", type=Path)
    args = parser.parse_args()
    if args.mode == "build":
        build(args.out)
    else:
        if not args.packet or not args.notes:
            parser.error("--packet and --notes required")
        analyze(args.packet, args.notes, args.out)


if __name__ == "__main__":
    main()
