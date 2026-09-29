"""Isolated scope-bound candidate and offline gate. No service edits or API calls.

This is a DEVELOPMENT experiment, not holdout evaluation or a semantic oracle.
Source attribution is performed before comparing values: numeric values never
increase a candidate's relevance score. Unrelated headings with no payload of
the requested type cannot veto a matching payload row, but a less relevant row
cannot win merely because it contains a requested value.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
VERSION = "pnu.scope-bound-c3.20260913-v1"
PREVIOUS = ROOT / "evidence/improvement-offline-20260909-v1/candidates.py"
SNAPSHOT = ROOT / "processed/eval/preflight-20260909/security-live-preparation-v1/preparation-v1/snapshots/c1-sec-merged"
POLICY = ROOT / "processed/eval/preflight-20260909/security-pilot-live-v1/run/policy.json"
PROBES = ROOT / "processed/eval/preflight-20260908/evidence-binding-experiment-v1/probes.json"
SECURITY_RESULTS = ROOT / "processed/eval/preflight-20260909/security-continuation-v1/combined-results-v1.json"
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("_pnu_scope_previous", PREVIOUS)
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
base, v1 = previous.baseline, previous.v1


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encoded(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def offline_audit(event, args):
    if event.startswith(("socket.", "urllib.")):
        raise PermissionError("offline_network_disabled")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if path.name.startswith(".env") or ("holdout" in str(path).lower() and path.suffix != ".py"):
            raise PermissionError("protected_data_disabled")


def payload_types(text):
    kinds = set()
    if v1._PHONE.search(text):
        kinds.add("phone")
    if v1._DATE.search(text):
        kinds.add("date")
    if re.search(r"(?<!\d)\d{1,2}:\d{2}(?!\d)", text):
        kinds.add("time")
    for match in v1._VALUE.finditer(text):
        kinds.add("quantity:" + re.sub(r"[\d,.\s]", "", match.group()))
    return kinds


def scope_check(claim, contexts):
    parsed = base.parse_response({"claims": [claim], "unanswered": []})["claims"][0]
    original = base._verify_claim(parsed, contexts)
    result = {"version": VERSION, "baseline_supported": original["supported"],
              "scope_gate_passed": False, "reason": "baseline_rejected", "units": []}
    if not original["supported"]:
        return result
    text = parsed["text"]
    anchors = v1._anchors(text)
    required_types = payload_types(text)
    units = []
    for evidence in parsed["evidence"]:
        number = evidence["source_number"]
        context = contexts[number - 1]
        for unit in previous.units(base._context_text(context), evidence["quote"]):
            overlap = base._anchor_overlap(anchors, v1._anchors(unit["key"]))
            units.append({**unit, "source_number": number, "anchor_count": len(overlap),
                          "payload_types": sorted(payload_types(unit["text"]))})
    maximum = max((u["anchor_count"] for u in units), default=0)
    for unit in units:
        unit.update(selected=False, passes=False)
        unit["maximal_anchor_match"] = maximum > 0 and unit["anchor_count"] == maximum
        # Filter only among equally best lexical matches. Never search lower
        # scores for a unit whose values happen to match the generated claim.
        unit["selected"] = unit["maximal_anchor_match"] and required_types.issubset(unit["payload_types"])
        if not unit["selected"]:
            continue
        context = contexts[unit["source_number"] - 1]
        narrowed = {"text": unit["text"], "source_title": v1._safe_metadata(text, context)}
        local = base._verify_claim({"text": text, "evidence": [{"source_number": 1, "quote": unit["text"]}]}, [narrowed])
        conflicts = v1._typed_conflicts(text, unit["text"]) + previous.polarity_conflicts(text, unit["text"])
        # A flattened multi-entity phone row has no reliable ownership boundary.
        # Do not accept even the correct value in this structurally ambiguous row.
        if v1._PHONE.search(text) and len(set(v1._PHONE.findall(unit["text"]))) > 1:
            conflicts.append("multiple_phone_owners_in_one_unit")
        unit.update(local_reason=local["validation_reason"], conflicts=conflicts,
                    passes=bool(local["supported"] and not conflicts))
    selected = [u for u in units if u["selected"]]
    result.update(units=units, scope_gate_passed=bool(selected) and all(u["passes"] for u in selected))
    result["reason"] = "typed_scope_supported" if result["scope_gate_passed"] else "ambiguous_or_unsupported_scope"
    return result


def verify_response(raw, contexts):
    payload = base.parse_response(raw)
    claims = []
    for parsed in payload["claims"]:
        checked = base._verify_claim(parsed, contexts)
        scope = scope_check(parsed, contexts)
        checked["scope_verification"] = scope
        if not scope["scope_gate_passed"]:
            checked.update(supported=False, source_ids=[], source_numbers=[], citations=[],
                           validation_reason=scope["reason"])
        claims.append(checked)
    supported = [c for c in claims if c["supported"]]
    unanswered = tuple(payload["unanswered"])
    lines = [c["text"] for c in supported] + list(unanswered)
    cited = [c["text"] + " " + " ".join(f"[{n}]" for n in c["source_numbers"]) for c in supported] + list(unanswered)
    if not lines:
        lines = cited = ["제공된 검색 근거에서 질문에 답할 내용을 확인할 수 없습니다."]
    citations = tuple({**citation, "claim_index": i, "claim_text": c["text"]}
                      for i, c in enumerate(supported) for citation in c["citations"])
    return base.VerifiedResponse(answer="\n".join("- " + line for line in lines),
                                 cited_answer="\n".join("- " + line for line in cited),
                                 claims=tuple(claims), citations=citations, unanswered=unanswered)


def synthetic_probes():
    rows = []
    def add(name, source, claim, expected):
        rows.append({"probe_id": name, "expected": expected,
                     "input": {"claim": {"text": claim, "evidence": [{"source_number": 1, "quote": source}]},
                               "contexts": [{"text": source, "chunk_id": "synthetic:1", "source_title": "합성 검사"}]}})
    for a, b in (("ALPHA센터", "BETA센터"), ("푸른상담실", "노란상담실"), ("OMEGA실", "DELTA실")):
        source = f"{a} 안내\n{a} 위치: 301호\n{a} 전화번호: 032-111-2222\n{b} 전화번호: 032-333-4444"
        add(a + "-phone-preserve", source, f"{a} 전화번호는 032-111-2222입니다.", True)
        add(a + "-phone-swap", source, f"{a} 전화번호는 032-333-4444입니다.", False)
        add(a + "-tie-conflict", f"{a} 전화번호: 032-111-2222\n{a} 전화번호: 032-333-4444", f"{a} 전화번호는 032-111-2222입니다.", False)
        add(a + "-flat-ambiguous", f"{a} 전화번호 032-111-2222 {b} 전화번호 032-333-4444", f"{a} 전화번호는 032-333-4444입니다.", False)
        add(a + "-missing-owner", f"{a} 전화번호 안내\n{b} 전화번호: 032-333-4444", f"{a} 전화번호는 032-333-4444입니다.", False)
    for label in ("ALPHA", "푸른", "OMEGA"):
        source = f"{label} 접수일: 2028. 8. 5.(토) 발표일: 2028. 8. 7.(월)"
        add(label + "-date-preserve", source, f"{label} 접수일은 2028년 8월 5일입니다.", True)
        add(label + "-date-swap", source, f"{label} 접수일은 2028년 8월 7일입니다.", False)
        source = f"{label} 활동비는 평가 통과한 경우 100,000원 지급합니다."
        add(label + "-condition-preserve", source, f"{label} 활동비는 평가 통과한 경우 100,000원 지급합니다.", True)
        add(label + "-condition-reverse", source, f"{label} 활동비는 평가 미통과한 경우 100,000원 지급합니다.", False)
    return rows


def run_probes(probes):
    records, counts = [], Counter()
    for p in probes:
        out = scope_check(p["input"]["claim"], p["input"]["contexts"])
        actual = out["scope_gate_passed"]
        counts[(bool(p["expected"]), actual)] += 1
        records.append({**p, "result": out, "matches_expected": actual == p["expected"]})
    summary = {"total": len(probes), "true_accept": counts[True, True],
               "false_accept": counts[False, True], "true_reject": counts[False, False],
               "false_reject": counts[True, False]}
    summary["passed"] = summary["false_accept"] == summary["false_reject"] == 0
    return records, summary


def verify_snapshot():
    policy = json.loads(POLICY.read_text())
    condition = policy["conditions"]["c1-sec-merged"]
    actual = {name: sha(SNAPSHOT / name) for name in condition["files_sha256"]}
    if actual != condition["files_sha256"]:
        raise ValueError("security_snapshot_changed")
    return {"root": str(SNAPSHOT), "snapshot_sha256": condition["snapshot_sha256"], "verified_files": len(actual)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.addaudithook(offline_audit)
    if args.out.exists() or args.out.is_symlink():
        raise ValueError("output_must_be_new")
    snapshot = verify_snapshot()
    inputs = [PROBES, PREVIOUS, ROOT / "scripts/rag/grounded_claims_v2.py", ROOT / "scripts/rag/evidence_binding_experiment.py", POLICY]
    pins = {str(p.relative_to(ROOT)): sha(p) for p in inputs}
    old_rows, old = run_probes(json.loads(PROBES.read_text()))
    new_rows, new = run_probes(synthetic_probes())
    summary = {"version": VERSION, "status": "OFFLINE_GATE_PASS" if old["passed"] and new["passed"] else "OFFLINE_GATE_FAIL",
               "previous_probes": old, "additional_probes": new, "security_snapshot": snapshot,
               "external_llm_calls": 0, "holdout_access": False, "service_modified": False,
               "limitation": "AI-constructed developmental component probes, not semantic entailment or GFC evidence."}
    if pins != {str(p.relative_to(ROOT)): sha(p) for p in inputs}:
        raise ValueError("input_changed_during_gate")
    args.out.mkdir(parents=True, exist_ok=False)
    for name, value in (("summary.json", summary), ("probes.json", old_rows + new_rows),
                        ("manifest.json", {"input_sha256": pins, "code_sha256": {str(Path(__file__).relative_to(ROOT)): sha(__file__)}})):
        with (args.out / name).open("x", encoding="utf-8") as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
    print(encoded(summary))
    return 0 if summary["status"] == "OFFLINE_GATE_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
