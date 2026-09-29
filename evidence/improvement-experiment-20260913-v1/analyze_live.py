"""Offline analysis of the one pinned Sep13 development trial, never holdout.

Each judgment is validated against its original one-record answer artifact via
summarize_judge_repeats. Format errors remain missing quality observations, not
fabricated score zero. Outputs must be new; no provider or service imports.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from math import comb
import os
from pathlib import Path
import sqlite3
from statistics import mean
import sys

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "processed/eval/preflight-20260913/scope-bound-c3-v1"
LIVE = BASE / "live-v1"
PREP = BASE / "preparation-v1/manifest.json"
PREP_SHA = "dad6320dc65c5416c140b20b08abc8860474bf9e293ecb44f805541ac2a0f0dc"
CONDITIONS = ("c1-sec-control", "c3-sec-scope")
sys.path.insert(0, str(ROOT / "scripts"))
from summarize_judge_repeats import aggregate_repeats


def audit(event, args):
    if event.startswith(("socket.", "urllib.")):
        raise PermissionError("offline_only")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if path.name.startswith(".env") or ("holdout" in str(path).lower() and path.suffix != ".py"):
            raise PermissionError("protected_data_disabled")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def summarize(rows):
    valid = [r for r in rows if r["status"] == "valid"]
    n = len(valid)
    gfc = sum(r["gfc"] for r in valid)
    return {"scheduled": len(rows), "valid_judgments": n,
            "errors": sum(r["status"] == "error" for r in rows),
            "unattempted": sum(r["status"] == "unattempted" for r in rows),
            "gfc_count": gfc, "gfc_rate_valid_only": gfc / n if n else None,
            "mean_score_valid_only": mean(r["score"] for r in valid) if n else None,
            "observed_successes_per_scheduled": gfc / len(rows) if rows else None,
            "score_histogram_valid_only": dict(Counter(r["score"] for r in valid))}


def paired(rows):
    groups = {c: {r["case_id"]: r for r in rows if r["condition_id"] == c and r["status"] == "valid"}
              for c in CONDITIONS}
    ids = sorted(set(groups[CONDITIONS[0]]) & set(groups[CONDITIONS[1]]))
    before = [groups[CONDITIONS[0]][cid] for cid in ids]
    after = [groups[CONDITIONS[1]][cid] for cid in ids]
    gains = [a["case_id"] for b, a in zip(before, after) if a["gfc"] and not b["gfc"]]
    losses = [a["case_id"] for b, a in zip(before, after) if b["gfc"] and not a["gfc"]]
    n_discordant = len(gains) + len(losses)
    p = min(1.0, 2 * sum(comb(n_discordant, k) for k in range(min(len(gains), len(losses)) + 1)) / 2**n_discordant)
    return {"common_valid_n": len(ids), "case_ids": ids,
            "control": summarize(before), "candidate": summarize(after),
            "score_wins": sum(a["score"] > b["score"] for b, a in zip(before, after)),
            "score_losses": sum(a["score"] < b["score"] for b, a in zip(before, after)),
            "score_ties": sum(a["score"] == b["score"] for b, a in zip(before, after)),
            "gfc_gain_case_ids": gains, "gfc_loss_case_ids": losses,
            "mcnemar_exact_two_sided_p": p,
            "inference_limit": "Post-selection known development set; n=1 generation/Judge; p is descriptive, not independent confirmation."}


def diagnose(rows):
    valid = [r for r in rows if r["status"] == "valid"]
    non = [r for r in valid if not r["gfc"]]
    causes = Counter()
    guards, rejected = Counter(), Counter()
    cross = Counter()
    for r in valid:
        cross[f"atomic_all_at8={r['atomic_all_at8']};gfc={r['gfc']}"] += 1
        rejected.update(r["rejected_claim_reasons"])
        guards.update(r["guard_rules"])
    for r in non:
        causes.update(name for name, value in r["nonexclusive_failure_flags"].items() if value)
    return {"non_gfc_valid_questions": len(non), "overlapping_question_failure_flags": dict(causes),
            "guard_rule_question_counts": dict(guards), "rejected_claim_counts_by_reason": dict(rejected),
            "cached_atomic_evidence_x_gfc": dict(cross),
            "note": "Failure flags overlap and are Judge labels, not independently verified causal attributions. Atomic evidence is inherited from the unchanged retrieval trace."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    sys.addaudithook(audit)
    out = args.out.resolve()
    if out.exists() or out.parent != BASE or args.out.is_symlink():
        parser.error("new output directly under the fixed experiment base required")
    if sha(PREP) != PREP_SHA:
        raise ValueError("prepared_manifest_changed")
    manifest = read(PREP)
    pins = {**manifest["source_pins"], str(PREP): PREP_SHA, str(Path(__file__)): sha(__file__)}
    if any(sha(p) != expected for p, expected in pins.items()):
        raise ValueError("pinned_input_changed")
    run, completion = read(LIVE / "run.json"), read(LIVE / "completion.json")
    if run["manifest"] != manifest or run["manifest_sha256"] != PREP_SHA or completion["mode"] != "live":
        raise ValueError("run_identity_mismatch")
    cases = {r["id"]: r for r in (json.loads(s) for s in (ROOT / "config/pnu-service-shadow60-v1.jsonl").read_text().splitlines())}
    finished = {(r["condition_id"], r["case_id"]): r for r in completion["cases"]}
    failures = {(r["condition_id"], r["case_id"]): r for r in completion["failures"]}
    if len(finished) != completion["completed_pairs"] or len(finished) + len(failures) > 84:
        raise ValueError("duplicate_or_invalid_slot_count")
    rows, validations = [], []
    for slot in manifest["slots"]:
        condition, cid = slot["condition_id"], slot["case_id"]
        stem, key = condition + "--" + cid, (condition, cid)
        row = {"condition_id": condition, "case_id": cid, "bucket": cases[cid]["shadow_bucket"],
               "query": cases[cid].get("query", cases[cid].get("question")), "score": None, "gfc": None}
        if key in finished:
            ap, jp = LIVE / (stem + ".answers.jsonl"), LIVE / (stem + ".judgments.jsonl")
            summary, joined = aggregate_repeats(answer_path=ap, judgment_paths=[jp])
            if len(joined) != 1 or joined[0]["case_id"] != cid:
                raise ValueError("single_slot_validation_failed")
            a, j = read(ap), read(jp)
            grade = j["judge"]
            if grade["score"] != finished[key]["score"] or grade["grounded_fully_correct"] != finished[key]["gfc"]:
                raise ValueError("completion_grade_mismatch")
            guard_rules = j["deterministic_guard"].get("rules", [])
            guard_rules = sorted({r if isinstance(r, str) else json.dumps(r, sort_keys=True) for r in guard_rules})
            row.update(status="valid", score=joined[0]["score_mean"], gfc=joined[0]["gfc_majority"],
                       answer=a["answer"], cited_answer=a["cited_answer"], query=a["query"],
                       answer_path=str(ap), judgment_path=str(jp), judge_reason=grade["reason"],
                       atomic_all_at8=a["atomic_evidence_at_k"]["8"]["all_matched"],
                       guard_rules=guard_rules,
                       accepted_claims=sum(c["supported"] for c in a["claims"]),
                       rejected_claim_reasons=[c["validation_reason"] for c in a["claims"] if not c["supported"]],
                       nonexclusive_failure_flags={"guard_applied": j["deterministic_guard"]["applied"],
                           "inappropriate_abstention": grade["abstention"] == "inappropriate",
                           "missing_required_claim": any(c["status"] == "missing" for c in grade["claim_checks"]),
                           "unsupported_fact": bool(grade["unsupported_facts"]),
                           "contradiction": bool(grade["contradictions"]),
                           "partial_citation": grade["citation_support"] == "partial"})
            validations.append(summary)
        elif key in failures:
            error_path = LIVE / (stem + ".error.json")
            if read(error_path) != failures[key]:
                raise ValueError("failure_record_mismatch")
            row.update(status="error", error=failures[key], error_path=str(error_path))
        else:
            row.update(status="unattempted")
        rows.append(row)
    db_path = LIVE / "provider-attempts.sqlite"
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as db:
        attempts = db.execute("SELECT slot,request_sha,state,http_status FROM attempts ORDER BY slot").fetchall()
    if len(attempts) != completion["provider_attempts"] or len(attempts) > 168:
        raise ValueError("attempt_accounting_mismatch")
    for slot, request_sha, state, http_status in attempts:
        if slot.endswith("--gen"):
            source_slot = next(s for s in manifest["slots"] if s["condition_id"] + "--" + s["case_id"] + "--gen" == slot)
            if request_sha != source_slot["request_sha256"]:
                raise ValueError("generation_request_changed")
    providers = sorted(LIVE.glob("*.provider.json"))
    model_versions = dict(Counter(read(p).get("modelVersion", "missing") for p in providers))
    pins.update({str(p): sha(p) for p in LIVE.iterdir() if p.is_file() and p.name != "runner.lock"})
    pair = paired(rows)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "run_status": completion["status"],
              "conditions": {c: summarize([r for r in rows if r["condition_id"] == c]) for c in CONDITIONS},
              "paired_common_valid": pair,
              "by_bucket": {c: {b: summarize([r for r in rows if r["condition_id"] == c and r["bucket"] == b]) for b in ("simple", "multi")} for c in CONDITIONS},
              "diagnostics": {c: diagnose([r for r in rows if r["condition_id"] == c]) for c in CONDITIONS},
              "provider_attempts": len(attempts),
              "provider_states": dict(Counter(f"{state};HTTP={status}" for _, _, state, status in attempts)),
              "provider_model_versions": model_versions,
              "validated_answer_judgment_pairs": len(validations),
              "candidate_adoption": "REJECTED: four additional synthetic semantic false accepts; no service integration",
              "errors_are_score_zero": False, "design": manifest["design"], "limitations": manifest["limitations"],
              "external_calls_by_analysis": 0, "rows": rows}
    lines = ["# 2026-09-13 생성·검증 후보 실측 결과", "", "이번 C3 버전은 채택하지 않는다. 추가 의미 반례 4개를 통과시켰으며, 아래 개발셋 실측 점수와 별개의 안전성 탈락 사유다.", "",
             "고정 검색 문맥·동일 보안 레이어·동일 1600토큰 한도에서 조건별 생성 1회 및 Judge v11 1회. 실제 holdout/새 검색 E2E/독립 일반화 검증이 아니다.", "",
             "| 조건 | 예정 | 유효 판정 | 오류 | GFC(유효 분모) | 평균(0–2) |", "|---|---:|---:|---:|---:|---:|"]
    for c, s in report["conditions"].items():
        lines.append(f"| {c} | {s['scheduled']} | {s['valid_judgments']} | {s['errors']} | {s['gfc_count']}/{s['valid_judgments']} | {s['mean_score_valid_only']:.4f} |")
    lines += ["", "오류는 0점으로 대체하지 않았다. 다음 비교는 양쪽 모두 유효한 동일 문항만 사용한다.", "",
              f"공통 {pair['common_valid_n']}문항: C1 GFC {pair['control']['gfc_count']}, C3 GFC {pair['candidate']['gfc_count']}; "
              f"평균 {pair['control']['mean_score_valid_only']:.4f} → {pair['candidate']['mean_score_valid_only']:.4f}.",
              f"점수 개선 {pair['score_wins']}, 악화 {pair['score_losses']}, 동점 {pair['score_ties']}. "
              f"GFC 회복 {len(pair['gfc_gain_case_ids'])}, 상실 {len(pair['gfc_loss_case_ids'])}.", "",
              f"API 시도 {len(attempts)}/168, 자동 재시도 0. 유효 answer/Judge {len(validations)}쌍을 기존 summarize_judge_repeats로 각각 해시·판정 검증했다.", "",
              "기존 n=3 majority 결과 또는 과거 900토큰 C1과 직접 비교하지 않는다. 아래 전체 답변·실패 기록을 보존한다.", "", "## 문항별 전체 비교", ""]
    for cid in manifest["selected_cases"]:
        rs = [r for r in rows if r["case_id"] == cid]
        lines += [f"### {cid}", "", rs[0]["query"] or "", ""]
        for r in rs:
            lines += [f"#### {r['condition_id']}", ""]
            if r["status"] != "valid":
                lines += [f"{r['status']}: {json.dumps(r.get('error'), ensure_ascii=False)}", ""]
            else:
                lines += [f"점수 {r['score']:g}/2 · GFC {r['gfc']}", "", r["cited_answer"], "", "Judge 근거: " + r["judge_reason"], "",
                          f"[원본 답변]({r['answer_path']}) · [원본 판정]({r['judgment_path']})", ""]
    out.mkdir(exist_ok=False)
    outputs = {"summary.json": report, "validated-single-pair-summaries.json": validations,
               "input-sha256.json": pins, "comparison.md": "\n".join(lines) + "\n"}
    for name, value in outputs.items():
        with (out / name).open("x", encoding="utf-8") as handle:
            handle.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("conditions", "paired_common_valid", "diagnostics", "provider_attempts", "provider_states", "provider_model_versions")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
