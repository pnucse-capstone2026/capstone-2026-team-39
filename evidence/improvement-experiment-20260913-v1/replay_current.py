"""Offline validation of frozen ordinal v2 on today's 42 C1 saved drafts.

No prompt/search/security/Judge changes and no provider calls. Reproduce all
existing response fields before applying the already pinned candidate. Synthetic
semantic stress results report inherited errors separately from newly accepted
errors; passing citation invariants is not a semantic correctness judgment.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys


def offline_audit(event, args):
    if event.startswith(("socket.", "urllib.")):
        raise PermissionError("offline_network_disabled")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if path.name.startswith(".env") or ("holdout" in str(path).lower() and path.suffix != ".py"):
            raise PermissionError("protected_data_disabled")


sys.addaudithook(offline_audit)
import ordinal_scope_recovery as candidate
from rag.security.context_gate import evaluate_contexts
from rag.security.output_gate import enforce_output
from service_eval_artifacts import validate_answer_record

ROOT, api = candidate.ROOT, candidate.api
BASE = ROOT / "processed/eval/preflight-20260913/scope-bound-c3-v1"
LIVE = BASE / "live-v1"
PIN_FILE = BASE / "analysis-v1/input-sha256.json"
PIN_SHA = "b5007f347bfd4a759df8dbeb0fe60198a69c907cdc204e7b8b9869878cc38325"
CANDIDATE_SHA = "f8141d7ec9604055b0ac44ccdb1afd30b160427c9a7625c1543941228cad79e0"
ORDINAL_SHA = "ea14f35d2aec2af49410a962ad4d1a697fe9dd3380af43c33432f2c76168aa31"
PROJECTION = ("answer", "cited_answer", "claims", "citations", "postprocessing")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def project(response):
    return {key: response[key] for key in PROJECTION}


def source(title="제7회 ALPHA 공모전", text="신청 마감은 2028년 8월 5일입니다.", index=1):
    return {"source_number": index, "chunk_id": f"synthetic#{index}", "source_title": title, "text": text}


def stress_cases():
    # All expected labels are declared before any candidate invocation. These
    # are synthetic unit cases, never additions to a held-out benchmark.
    rows = []
    def add(name, text, claim, expected, title="제7회 ALPHA 공모전"):
        rows.append({"probe_id": name, "contexts": [source(title, text)], "claim": claim, "expected": expected})
    lead = "제7회 ALPHA 공모전의 "
    for name, text, clause in (
        ("date", "신청 마감은 2028년 8월 5일입니다.", "신청 마감은 2028년 8월 5일입니다."),
        ("time", "신청 마감은 2028년 8월 5일 18:00입니다.", "신청 마감은 2028년 8월 5일 18:00입니다."),
        ("amount", "참가비는 30,000원입니다.", "참가비는 30,000원입니다."),
        ("frequency", "지원 횟수는 3회입니다.", "지원 횟수는 3회입니다."),
    ):
        add(name + "_correct", text, lead + clause, True)
        add(name + "_wrong_entity", text, lead.replace("ALPHA", "BETA") + clause, False)
        add(name + "_wrong_edition", text, lead.replace("제7회", "제8회") + clause, False)
    add("wrong_date", "신청 마감은 2028년 8월 5일입니다.", lead + "신청 마감은 2028년 8월 7일입니다.", False)
    add("wrong_time", "신청 마감은 2028년 8월 5일 18:00입니다.", lead + "신청 마감은 2028년 8월 5일 10:00입니다.", False)
    add("wrong_amount", "참가비는 30,000원입니다.", lead + "참가비는 50,000원입니다.", False)
    add("title_ordinal_as_frequency", "지원 횟수는 3회입니다.", lead + "지원 횟수는 7회입니다.", False)
    add("body_other_entity", "BETA 공모전의 신청 마감은 2028년 8월 5일입니다.", lead + "신청 마감은 2028년 8월 5일입니다.", False)
    add("body_other_edition", "제8회 ALPHA 공모전의 신청 마감은 2028년 8월 5일입니다.", lead + "신청 마감은 2028년 8월 5일입니다.", False)
    add("deadline_result_swap", "접수 마감: 2028년 8월 5일\n결과 발표: 2028년 8월 7일", lead + "접수 마감은 2028년 8월 7일입니다.", False)
    add("deadline_result_correct", "접수 마감: 2028년 8월 5일\n결과 발표: 2028년 8월 7일", lead + "접수 마감은 2028년 8월 5일입니다.", True)
    for name, text, good, bad in (
        ("maximum", "참가비는 최대 30,000원입니다.", "참가비는 최대 30,000원입니다.", "참가비는 최소 30,000원입니다."),
        ("threshold", "신청 자격은 3학기 이상 등록한 학생입니다.", "신청 자격은 3학기 이상 등록한 학생입니다.", "신청 자격은 3학기 이하 등록한 학생입니다."),
        ("required", "서류 제출은 필수입니다.", "서류 제출은 필수입니다.", "서류 제출은 선택입니다."),
        ("payment", "취소자는 참가비 환수 대상입니다.", "취소자는 참가비 환수 대상입니다.", "취소자는 참가비 지급 대상입니다."),
    ):
        add(name + "_correct", text, lead + good, True)
        add(name + "_reversed", text, lead + bad, False)
    rows.append({"probe_id": "cross_document_date", "contexts": [
        source(text="신청 마감은 2028년 8월 7일입니다."),
        source(title="제7회 BETA 공모전", index=2)],
        "claim": lead + "신청 마감은 2028년 8월 5일입니다.", "expected": False})
    return rows


def run_stress():
    rows = []
    for probe in stress_cases():
        before = candidate.ORIGINAL_ATTRIBUTE(probe["claim"], probe["contexts"])
        after = candidate.attribute_claim(probe["claim"], probe["contexts"])
        # Use the real output gate on a real response, not a manually fabricated
        # citation. This distinguishes provenance validation from semantic truth.
        with candidate.enabled():
            response = api.build_rag_response("문서 내용을 확인해 주세요.", probe["contexts"], probe["claim"], "saved-synthetic-draft")
        secured = enforce_output(response, probe["contexts"])
        rows.append({**probe, "baseline_supported": before["supported"], "candidate_supported": after["supported"],
                     "baseline_reason": before["validation_reason"], "candidate_reason": after["validation_reason"],
                     "new_false_accept": not probe["expected"] and not before["supported"] and after["supported"],
                     "inherited_false_accept": not probe["expected"] and before["supported"] and after["supported"],
                     "candidate_false_reject": probe["expected"] and not after["supported"],
                     "output_gate": secured.summary(), "secured_answer": secured.response["answer"],
                     "secured_has_supported_claim": any(c["supported"] for c in secured.response["claims"])})
    summary = {"total": len(rows), "positive": sum(r["expected"] for r in rows),
               "negative": sum(not r["expected"] for r in rows),
               "new_false_accepts": sum(r["new_false_accept"] for r in rows),
               "inherited_false_accepts": sum(r["inherited_false_accept"] for r in rows),
               "false_rejects": sum(r["candidate_false_reject"] for r in rows),
               "interpretation": "Synthetic component validation, not attack success rate or service GFC"}
    return {"summary": summary, "rows": rows}


def replay_record(record):
    validate_answer_record(record)
    trace = record["evaluation_trace"]
    contexts = api.number_sources(copy.deepcopy(trace["retrieval_stages"]["final_contexts"]))
    gate = evaluate_contexts(contexts, mode="enforce")
    if gate.excluded or gate.sanitized or gate.summary() != record["security"]["context_gate"]:
        raise ValueError("context_security_drift")
    sanitized = api.strip_untrusted_citation_markers(trace["raw_draft"])
    if sanitized != trace["sanitized_draft"]:
        raise ValueError("sanitization_drift")
    before = api.build_rag_response(record["query"], contexts, sanitized, record["generator"])
    before_gate = enforce_output(before, contexts)
    if project(before_gate.response) != project(record) or before_gate.summary() != record["security"]["output_gate"]:
        raise ValueError("baseline_reproduction_mismatch:" + record["case_id"])
    with candidate.enabled():
        after = api.build_rag_response(record["query"], contexts, sanitized, record["generator"])
    after_gate = enforce_output(after, contexts)
    existing = {c["text"] for c in before_gate.response["claims"] if c["supported"]}
    recovered = [c for c in after_gate.response["claims"] if c["supported"] and c["text"] not in existing]
    retained = {c["text"] for c in after_gate.response["claims"] if c["supported"]}
    evidence = []
    for c in recovered:
        evidence.append({"text": c["text"], "original_reason": c.get("ordinal_recovery", {}).get("original_reason"),
                         "source_numbers": c["source_numbers"], "sources": [
                             {"source_number": i, "chunk_id": contexts[i - 1]["chunk_id"],
                              "source_title": contexts[i - 1].get("source_title"),
                              "source_url": contexts[i - 1].get("source_url"),
                              "text": contexts[i - 1].get("text", contexts[i - 1].get("preview"))}
                             for i in c["source_numbers"]]})
    return {"case_id": record["case_id"], "query": record["query"], "source_answer_id": record["answer_id"],
            "answer_changed": before_gate.response["answer"] != after_gate.response["answer"],
            "projection_changed": project(before_gate.response) != project(after_gate.response),
            "before": before_gate.response["cited_answer"], "after_unjudged": after_gate.response["cited_answer"],
            "added_claims": evidence, "removed_supported_claims": sorted(existing - retained),
            "before_security": before_gate.summary(), "after_security": after_gate.summary(),
            "context_security": gate.summary(), "candidate_gfc": None}, project(after_gate.response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() or out.parent != BASE or args.out.is_symlink():
        parser.error("new output directory directly under the experiment base required")
    if sha(PIN_FILE) != PIN_SHA or sha(candidate.__file__) != CANDIDATE_SHA or sha(candidate.v1.__file__) != ORDINAL_SHA:
        raise ValueError("analysis_or_candidate_pin_changed")
    pins = read(PIN_FILE)
    pins.update({str(PIN_FILE): PIN_SHA, str(Path(candidate.__file__)): CANDIDATE_SHA,
                 str(Path(candidate.v1.__file__)): sha(candidate.v1.__file__), str(Path(__file__)): sha(__file__)})
    if any(sha(p) != h for p, h in pins.items()):
        raise ValueError("input_pin_changed")
    manifest = read(BASE / "preparation-v1/manifest.json")
    policy_path = ROOT / "processed/eval/preflight-20260909/security-pilot-live-v1/run/policy.json"
    security_pin = read(policy_path)["conditions"]["c1-sec-merged"]
    if security_pin["snapshot_sha256"] != manifest["snapshot"]["snapshot_sha256"]:
        raise ValueError("security_snapshot_identity_changed")
    snapshot = Path(manifest["snapshot"]["root"])
    if not Path(api.__file__).is_relative_to(snapshot):
        raise ValueError("wrong_service_module")
    pins.update({str(snapshot / p): h for p, h in security_pin["files_sha256"].items()})
    pins[str(policy_path)] = sha(policy_path)
    if any(sha(p) != h for p, h in pins.items()):
        raise ValueError("security_source_changed")
    if len(manifest["selected_cases"]) != 42:
        raise ValueError("expected42")
    rows, responses = [], []
    for cid in manifest["selected_cases"]:
        path = LIVE / ("c1-sec-control--" + cid + ".answers.jsonl")
        row, response = replay_record(read(path))
        row["source_answer_path"] = str(path)
        rows.append(row)
        responses.append({"record_type": "offline_unjudged_response_replay", "case_id": cid,
                          "source_answer_path": str(path), "source_answer_sha256": sha(path), "response": response})
        print(json.dumps({"case_id": cid, "changed": row["answer_changed"], "added": len(row["added_claims"])}, ensure_ascii=False), flush=True)
    stress = run_stress()
    if any(sha(p) != h for p, h in pins.items()):
        raise ValueError("inputs_changed_during_replay")
    summary = {"candidate_version": candidate.VERSION, "records": len(rows), "baseline_exact": len(rows),
               "answer_changed": sum(r["answer_changed"] for r in rows),
               "projection_changed": sum(r["projection_changed"] for r in rows),
               "added_claims": sum(len(r["added_claims"]) for r in rows),
               "removed_supported_claims": sum(len(r["removed_supported_claims"]) for r in rows),
               "after_invalid_citations": sum(r["after_security"]["invalid_citations"] for r in rows),
               "semantic_stress": stress["summary"], "external_calls": 0, "candidate_gfc": None,
               "service_modified": False, "status": "NOT_ADOPTED",
               "limitations": "Known development set, same saved draft; no new generation or Judge; citation invariants do not establish semantic correctness"}
    lines = ["# 2026-09-13 C1 답변의 회차 보완 재생", "", "API 호출 0. 기존 서비스·보안·후보 코드 변경 0. 후보의 새 GFC는 미측정이다.", "",
             f"기준선 {len(rows)}/{len(rows)} 재현. 답변 변경 {summary['answer_changed']}개, 문장 추가 {summary['added_claims']}개, 기존 지원 문장 제거 {summary['removed_supported_claims']}개.", "",
             f"추가 합성 검사: {encoded(stress['summary']).strip()}", "",
             "본문은 AI 진단용 비교 자료다. 사람이 검토한 결과나 독립 홀드아웃 검증이 아니다.", ""]
    for row in rows:
        if not row["projection_changed"]:
            continue
        lines += [f"## {row['case_id']}", "", row["query"], "", "### 기존 답변", "", row["before"], "",
                  "### 보완 후 (미채점)", "", row["after_unjudged"], "", "### 추가 보존 문장과 원문", ""]
        for c in row["added_claims"]:
            lines += [c["text"], "", "기존 제외 사유: " + str(c["original_reason"]), ""]
            for src in c["sources"]:
                lines += [f"출처 {src['source_number']}: {src['source_title']} · `{src['chunk_id']}`", "", str(src["text"]), ""]
                if src["source_url"]:
                    lines += [f"[원문 페이지]({src['source_url']})", ""]
        lines += [f"[원본 답변 기록]({row['source_answer_path']})", ""]
    out.mkdir(exist_ok=False)
    values = {"summary.json": summary, "replay.json": rows, "stress.json": stress, "input-sha256.json": pins,
              "comparison.md": "\n".join(lines) + "\n",
              "responses.jsonl": "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in responses)}
    for name, value in values.items():
        with (out / name).open("x", encoding="utf-8") as handle:
            handle.write(value if isinstance(value, str) else encoded(value))
    print(encoded(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
