#!/usr/bin/env python3
"""Pinned offline component evaluation. No generation, Judge, or rescoring."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import analyze_shadow_diagnostics as diagnostic  # noqa: E402
from immutable_outputs import publish_immutable_texts, require_new_outputs, reject_symlink_inputs  # noqa: E402
from rag import evidence_binding_experiment as experiment  # noqa: E402

PROBES = ROOT / "processed/eval/preflight-20260907/shadow14-ai-diagnostics-v1/c2-verifier-probes.json"
PROBES_SHA = "bec8d6dfaec86f0ae18df1371c1956d7fa0029284b3f4c8992857e84cc49f46d"


def payload(text: str, quote: str, source: int = 1) -> dict:
    return {"text": text, "evidence": [{"source_number": source, "quote": quote}]}


def synthetic_cases() -> list[dict]:
    """AI-authored contrastive development probes, not independent eval data."""
    cases = []
    for names in (("ALPHA", "BETA"), ("찬솔", "푸름")):
        a, b = names
        specs = [
            ("date", f"{a} 과정\t프로그램운영\t2028. 8. 5.(토) ~ 8. 7.(월)",
             f"{b} 과정\t프로그램운영\t2028. 8. 24.(목) ~ 8. 28.(월)",
             f"{a} 과정의 프로그램 운영일정은 2028년 8월 5일부터 8월 7일까지입니다.",
             f"{a} 과정의 프로그램 운영일정은 2028년 8월 24일부터 8월 28일까지입니다."),
            ("phone", f"{a}센터 연락처: 032-111-2222", f"{b}센터 연락처: 032-333-4444",
             f"{a}센터의 연락처는 032-111-2222입니다.", f"{a}센터의 연락처는 032-333-4444입니다."),
            ("money", f"{a} 지원금: 100,000원", f"{b} 지원금: 200,000원",
             f"{a} 지원금은 100,000원입니다.", f"{a} 지원금은 200,000원입니다."),
            ("count", f"{a} 모집인원: 24명", f"{b} 모집인원: 36명",
             f"{a} 모집인원은 24명입니다.", f"{a} 모집인원은 36명입니다."),
            ("obligation", f"{a} 사전 예약 필수", f"{b} 사전 예약 면제",
             f"{a} 사전 예약은 필수입니다.", f"{a} 사전 예약은 면제입니다."),
            ("condition", f"{a} 활동비: 평가 통과한 경우 100,000원 지급", f"{b} 활동비: 100,000원 지급",
             f"{a} 활동비는 평가 통과한 경우 100,000원 지급합니다.", f"{a} 활동비는 100,000원 지급합니다."),
        ]
        for kind, left, right, correct, wrong in specs:
            for reverse in (False, True):
                source = "\n".join((right, left) if reverse else (left, right))
                for label, text, expected in (("correct", correct, True), ("wrong", wrong, False)):
                    cases.append({"probe_id": f"{kind}-{a}-{int(reverse)}-{label}",
                                  "kind": kind, "expected": expected,
                                  "claim": payload(text, source), "contexts": [{"text": source}]})
    extra = [
        ("phone_components", "상담실 연락처: 032-111-2222 또는 051-333-4444", "상담실 연락처는 032-333-2222입니다.", False, ""),
        ("date_components", "행사 일정: 2028. 8. 5.(토), 2028. 9. 7.(목)", "행사 일정은 2028년 8월 7일입니다.", False, ""),
        ("date_year", "접수일: 2028. 12. 7.(목), 발표일: 2029. 1. 29.(월)", "접수일은 2029년 12월 7일입니다.", False, ""),
        ("value_type", "상담실 위치: 406호, 이용료: 0원", "상담실 이용료는 406원입니다.", False, ""),
        ("metadata_leak", "프로그램 모집인원 안내", "프로그램 모집인원은 24명입니다.", False, "24명 프로그램 기록"),
        ("year_scope", "학부 등록금은 동결되었습니다.", "2028학년도 학부 등록금은 동결되었습니다.", True, "2028학년도 등록금 안내"),
        ("negation", "휴학생은 지원 대상에서 제외됩니다.", "휴학생은 지원 대상에서 제외되지 않습니다.", False, ""),
    ]
    for kind, source, text, expected, title in extra:
        cases.append({"probe_id": kind, "kind": kind, "expected": expected,
                      "claim": payload(text, source), "contexts": [{"text": source, "source_title": title}]})
    return cases


def stress_cases() -> list[dict]:
    """Adversarial extension, including expected tradeoff/semantic failures.

    Never exclude these failures from the summary or service-adoption gate.
    They demonstrate why row-local lexical matching is not entailment.
    """
    specs = [
        ("flattened_subjects", "ALPHA센터 연락처: 032-111-2222 BETA센터 연락처: 032-333-4444",
         "ALPHA센터의 연락처는 032-333-4444입니다.", False),
        ("same_row_columns", "ALPHA 과정 접수일: 2028. 8. 5.(토) 발표일: 2028. 8. 7.(월)",
         "ALPHA 과정 접수일은 2028년 8월 7일입니다.", False),
        ("condition_reversal", "ALPHA 활동비는 평가 통과한 경우 100,000원 지급합니다.",
         "ALPHA 활동비는 평가 미통과한 경우 100,000원 지급합니다.", False),
        ("valid_cross_row", "ALPHA 모집인원:\n24명", "ALPHA 모집인원은 24명입니다.", True),
    ]
    return [{"probe_id": name, "kind": "stress", "expected": expected,
             "claim": payload(text, source), "contexts": [{"text": source}]}
            for name, source, text, expected in specs]


def check_cases(cases: list[dict]) -> list[dict]:
    return [{"probe_id": c["probe_id"], "kind": c["kind"], "expected": c["expected"],
             "input": {"claim": c["claim"], "contexts": c["contexts"]},
             "result": experiment.verify_claim(c["claim"], c["contexts"])} for c in cases]


def counts(rows: list[dict], field: str) -> dict:
    return dict(sorted(Counter(
        "true_accept" if r["expected"] and r["result"][field] else
        "false_reject" if r["expected"] else
        "false_accept" if r["result"][field] else "true_reject" for r in rows
    ).items()))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    sys.addaudithook(diagnostic.forbid_network_and_protected_files)
    output = args.output_dir.resolve()
    allowed = ROOT / "processed/eval/preflight-20260908"
    if not output.is_relative_to(allowed) or "holdout" in str(output).lower():
        parser.error("output must be a new offline preflight-20260908 subdirectory")
    names = ("probes.json", "probes.csv", "split-replay.json", "summary.json", "manifest.json")
    require_new_outputs([output / name for name in names])
    pins = {**diagnostic.CODE_PINS, str(diagnostic.PACKET.relative_to(ROOT)): diagnostic.PACKET_SHA,
            str(PROBES.relative_to(ROOT)): PROBES_SHA}
    reject_symlink_inputs([ROOT / path for path in pins])
    if any(diagnostic.digest(ROOT / p) != h for p, h in pins.items()):
        parser.error("frozen input/code pin mismatch")
    packet = json.loads(diagnostic.PACKET.read_text())
    by_id = {i["case_id"]: i for i in packet["items"]}
    original_probes = json.loads(PROBES.read_text())
    real_cases = [{"probe_id": p["probe_id"], "kind": "saved_source_synthetic_probe",
                   "expected": p["expected_supported_from_source"],
                   "claim": p["input"]["claims"][0],
                   "contexts": by_id[p["case_id"]]["runs"][0]["contexts"]} for p in original_probes]
    historical, synthetic = check_cases(real_cases), check_cases(synthetic_cases())
    stress = check_cases(stress_cases())
    import search_api  # noqa: E402; read-only replay of the frozen splitter
    splits = []
    for item in packet["items"]:
        for run in item["runs"]:
            draft = run["sanitized_draft"]
            before = search_api.split_draft_claims(draft)
            after = experiment.split_preserving_dates(draft)
            lossless = "".join("".join(after).split()) == "".join(draft.split())
            if not lossless:
                raise RuntimeError("experimental split lost non-whitespace input")
            splits.append({"case_id": item["case_id"], "run_id": run["generation_run_id"],
                           "before": before, "after": after, "changed": before != after,
                           "non_whitespace_preserved": lossless,
                           "source_draft_sha256": experiment.baseline.sha256_text(draft)})
    if any(diagnostic.digest(ROOT / p) != h for p, h in pins.items()):
        parser.error("frozen input/code changed during replay")
    rows = historical + synthetic + stress
    summary = {"schema_version": experiment.VERSION, "external_api_calls": 0,
               "official_scores_changed": False, "service_integrated": False,
               "evidence_type": "AI-authored component probes on inspected development data; not GFC or independent validation",
               "historical_probes": {"n": len(historical), "baseline": counts(historical, "baseline_supported"),
                                     "experiment": counts(historical, "scope_gate_passed")},
               "synthetic_probes": {"n": len(synthetic), "baseline": counts(synthetic, "baseline_supported"),
                                    "experiment": counts(synthetic, "scope_gate_passed")},
               "stress_probes": {"n": len(stress), "baseline": counts(stress, "baseline_supported"),
                                 "experiment": counts(stress, "scope_gate_passed")},
               "all_probes": {"n": len(rows), "baseline": counts(rows, "baseline_supported"),
                              "experiment": counts(rows, "scope_gate_passed")},
               "unexpected": [r["probe_id"] for r in rows if r["expected"] != r["result"]["scope_gate_passed"]],
               "service_adoption_approved": False,
               "component_gate_passed": all(r["expected"] == r["result"]["scope_gate_passed"] for r in rows),
               "split_replay": {"n": len(splits), "changed": sum(r["changed"] for r in splits),
                                "non_whitespace_preserved": sum(r["non_whitespace_preserved"] for r in splits)},
               "limits": ["Not an entailment verifier; unsupported paraphrases may still pass",
                          "A single unstructured row can contain multiple subjects/columns",
                          "Lexical unit choice can reject correct cross-row or title-inherited claims",
                          "Condition/negation presence is not logical equivalence",
                          "Date splitter does not validate calendar dates; C1 attribution is unchanged",
                          "No regenerated answers, no new Judge score, no human calibration"]}
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=["probe_id", "kind", "expected", "baseline", "experiment", "reason"])
    writer.writeheader()
    for row in rows:
        writer.writerow({"probe_id": row["probe_id"], "kind": row["kind"], "expected": row["expected"],
                         "baseline": row["result"]["baseline_supported"],
                         "experiment": row["result"]["scope_gate_passed"], "reason": row["result"]["reason"]})
    texts = {"probes.json": diagnostic.encoded(rows), "probes.csv": stream.getvalue(),
             "split-replay.json": diagnostic.encoded(splits), "summary.json": diagnostic.encoded(summary)}
    code = [Path(__file__), ROOT / "scripts/rag/evidence_binding_experiment.py",
            ROOT / "tests/test_evidence_binding_experiment.py", ROOT / "scripts/analyze_shadow_diagnostics.py",
            ROOT / "scripts/immutable_outputs.py"]
    manifest = {"version": experiment.VERSION, "inputs": pins,
                "code_sha256": {str(p.relative_to(ROOT)): diagnostic.digest(p) for p in code},
                "outputs": {name: experiment.baseline.sha256_text(value) for name, value in texts.items()}}
    texts["manifest.json"] = diagnostic.encoded(manifest)
    publish_immutable_texts({output / n: t for n, t in texts.items()}, authoritative_path=output / "manifest.json")
    print(diagnostic.encoded(summary))


if __name__ == "__main__":
    main()
