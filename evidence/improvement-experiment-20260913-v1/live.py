"""Pinned, single-stream core42 generation-boundary experiment (not holdout).

prepare/mock are offline. Live requires the prepared manifest SHA and explicit
authorization. 168 provider attempts maximum, one per stage, no retries/model
switching. Requests are reserved durably BEFORE sending. Existing run roots are
never restarted automatically: interrupted/failed attempts remain consumed.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, timezone
import fcntl
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SNAPSHOT = ROOT / "processed/eval/preflight-20260909/security-live-preparation-v1/preparation-v1/snapshots/c1-sec-merged"
sys.path.insert(0, str(SNAPSHOT / "scripts"))
import search_api as api
from rag import generators as gen
from rag.security.context_gate import evaluate_contexts
from rag.security.output_gate import enforce_output
import rag
rag.__path__.append(str(ROOT / "scripts/rag"))  # experimental module only
import experiment as exp
import judge_service_answers as judge
import evaluate_grounded_claims_v2 as c2_collector
from service_eval_artifacts import build_answer_identity, canonical_json, sha256_json, validate_answer_record

CASES = ROOT / "config/pnu-service-shadow60-v1.jsonl"
ANSWERS = ROOT / "processed/eval/preflight-20260905/shadow60-generation-v1/c1-run1.answers.jsonl"
LIVE_ROOT = ROOT / "processed/eval/preflight-20260913/scope-bound-c3-v1/live-v1"
MODEL = "gemini-3.5-flash-lite"
JUDGE_MODEL = "gemini-3.1-flash-lite"
MAX_TOKENS = 1600
CAP = 168
INTERVAL = 15.0
EXPERIMENT = "shadow-core42-security-c1-c3-20260913-v1"
CONDITIONS = ("c1-sec-control", "c3-sec-scope")
API_URLS = {m: "https://generativelanguage.googleapis.com/v1beta/models/" + m + ":generateContent" for m in (MODEL, JUDGE_MODEL)}
AUTHORIZATION = "I_APPROVE_CORE42_168_ATTEMPTS"
CAPABILITY = {"key_read": False, "provider": None}


def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if "holdout" in str(path).lower() and path.suffix != ".py":
            raise PermissionError("holdout_data_disabled")
        if path.name.startswith(".env") and not (CAPABILITY["key_read"] and path == ROOT / ".env"):
            raise PermissionError("secret_read_disabled")
    if event == "urllib.Request" and args[0] != CAPABILITY["provider"]:
        raise PermissionError("unapproved_http_destination")
    if event in ("socket.connect", "socket.getaddrinfo") and CAPABILITY["provider"] not in API_URLS.values():
        raise PermissionError("unapproved_network")


def write_new(path, value):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink_output")
    data = value if isinstance(value, str) else exp.encoded(value)
    with path.open("x", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def source_pins():
    paths = [Path(__file__), HERE / "experiment.py", HERE / "test_experiment.py", HERE / "test_live.py",
             exp.PREVIOUS, ROOT / "scripts/rag/evidence_binding_experiment.py", CASES, ANSWERS,
             Path(judge.__file__), Path(c2_collector.__file__),
             exp.ROOT / "scripts/service_eval_artifacts.py"]
    return {str(p): exp.sha(p) for p in paths}


def inputs():
    exp.verify_snapshot()
    # These import paths are part of the baseline identity, not the dirty root.
    for module in (api, gen):
        if not Path(module.__file__).is_relative_to(SNAPSHOT):
            raise ValueError("wrong_serving_module")
    cases = {c["id"]: c for c in load_jsonl(CASES)}
    if len(cases) != 60:
        raise ValueError("expected_shadow60")
    selected = {cid for cid, c in cases.items() if c["shadow_bucket"] in ("simple", "multi")}
    if len(selected) != 42:
        raise ValueError("expected_prefixed_core42")
    records = load_jsonl(ANSWERS)
    result = []
    for r in records:
        validate_answer_record(r)
        if r["case_id"] not in selected:
            continue
        case = cases[r["case_id"]]
        judge.validate_answer_case(r, case)
        contexts = r["evaluation_trace"]["retrieval_stages"]["final_contexts"]
        gate = evaluate_contexts(contexts, mode="enforce")
        # This run does not change retrieval nor silently retain stale evidence
        # metrics after exclusions. All 42 must have unchanged safe text.
        if gate.excluded or gate.sanitized or len(gate.original_contexts) != len(contexts):
            raise ValueError("unexpected_security_context_change")
        result.append((case, r, gate))
    if len(result) != 42 or len({c["id"] for c, _, _ in result}) != 42:
        raise ValueError("missing_or_duplicate_core_answers")
    return result


def prompt_and_request(condition, case, gate):
    contexts = api.number_sources(list(gate.generation_contexts))
    if condition == CONDITIONS[0]:
        prompt = gen.build_prompt(case["query"], contexts)
        system = gen.SYSTEM_INSTRUCTION
        config = {"maxOutputTokens": MAX_TOKENS}
    elif condition == CONDITIONS[1]:
        prompt = exp.base.build_prompt(case["query"], contexts)
        # Preserve baseline safety instructions. Only the final formatting
        # contract changes: source numbers come from the numbered inputs.
        sentence = "출처 번호나 인용 표시는 만들지 마세요. 서버가 검증 후 붙입니다."
        if gen.SYSTEM_INSTRUCTION.count(sentence) != 1:
            raise ValueError("system_format_contract_changed")
        system = gen.SYSTEM_INSTRUCTION.replace(sentence,
            "출력은 지정 JSON 계약만 따르세요. source_number는 제공된 Source 번호만 사용하고, "
            "quote는 해당 Text의 연속 구간을 그대로 복사하세요. 서버가 검증 후 출처 표시를 붙입니다.")
        config = {"maxOutputTokens": MAX_TOKENS, "responseMimeType": "application/json",
                  "responseJsonSchema": c2_collector.RESPONSE_JSON_SCHEMA}
    else:
        raise ValueError("unknown_condition")
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
    return prompt, system, body


def prepare(out):
    out = Path(out)
    if out.exists():
        raise ValueError("preparation_must_be_new")
    for k in list(os.environ):
        if k.startswith(("RAG_", "GEMINI_", "GOOGLE_")):
            os.environ.pop(k)
    records = inputs()
    _, old = exp.run_probes(json.loads(exp.PROBES.read_text()))
    _, new = exp.run_probes(exp.synthetic_probes())
    if not old["passed"] or not new["passed"]:
        raise ValueError("component_gate_failed")
    before = source_pins()
    slots = []
    for i, (case, record, gate) in enumerate(records):
        order = CONDITIONS if i % 2 == 0 else CONDITIONS[::-1]
        for condition in order:
            prompt, system, body = prompt_and_request(condition, case, gate)
            slots.append({"case_id": case["id"], "condition_id": condition,
                          "request_sha256": sha256_json(body), "context_sha256": sha256_json(list(gate.generation_contexts))})
    if before != source_pins():
        raise ValueError("inputs_changed")
    manifest = {"version": exp.VERSION, "experiment_id": EXPERIMENT,
                "source_pins": before, "snapshot": exp.verify_snapshot(), "slots": slots,
                "generation_model": MODEL, "judge_model": JUDGE_MODEL, "max_output_tokens_both": MAX_TOKENS,
                "total_attempt_cap": CAP, "retries": 0, "inter_call_seconds": INTERVAL,
                "live_root": str(LIVE_ROOT), "selected_cases": [c["id"] for c, _, _ in records],
                "generation_n": 1, "judge_n": 1, "holdout_access": False,
                "design": "Fixed retrieval-trace replay; same security gates; generation/verification boundary comparison. Not full HTTP-service E2E.",
                "limitations": ["Known development set, not independent holdout", "C3 changes prompt/output contract/verifier as a bundle, not a single-component causal ablation",
                                "Both conditions use 1600 output tokens, unlike historical 900-token C1; historical scores are not the comparator",
                                "Same-family generation and Judge; no human calibration", "Other projects' daily usage is unknown; stop on 429"],
                "approval_record": {"user_message": "응 진해앻줘", "approved_scope": "Preserve freeze; isolated candidate, offline gate then core42 two-condition n=1 generation/Judge up to 168 attempts",
                                    "data_destination": "Google Gemini generateContent", "data": "Development questions, retrieved public university text, generated answers, and frozen gold/rubric for Judge only"}}
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / "manifest.json", manifest)
    print(json.dumps({"status": "PREPARED_OFFLINE", "manifest_sha256": exp.sha(out / "manifest.json"), "cases": 42, "pairs": 84, "attempt_cap": CAP, "external_calls": 0}))


def answer_record(base_record, case, gate, condition, raw, body, prompt, system, latency, mode):
    contexts = api.number_sources(list(gate.original_contexts))
    if condition == CONDITIONS[0]:
        sanitized = api.strip_untrusted_citation_markers(raw)
        rag_response = api.build_rag_response(case["query"], contexts, sanitized, "gemini:" + MODEL)
    else:
        verified = exp.verify_response(raw, api.number_sources(list(gate.generation_contexts)))
        sanitized = verified.answer
        rag_response = verified.to_dict()
        rag_response["postprocessing"] = {"mode": exp.VERSION, "accepted_claim_count": verified.accepted_count,
                                           "rejected_claim_count": verified.rejected_count}
    secured = enforce_output(rag_response, contexts)
    r = copy.deepcopy(base_record)
    for key in ("answer_id", "answer_sha256", "record_sha256", "postprocessor_diagnostic", "slot_outcome", "answer_eligible_for_judge"):
        r.pop(key, None)
    config = {"experiment_id": EXPERIMENT, "condition_id": condition, "execution_mode": mode,
              "generation_n": 1, "source_artifact_sha256": exp.sha(ANSWERS),
              "snapshot_sha256": exp.verify_snapshot()["snapshot_sha256"], "candidate_version": exp.VERSION if condition == CONDITIONS[1] else None,
              "frozen_retrieval_replay": True, "max_output_tokens": MAX_TOKENS}
    request_config = {"provider": "gemini", "model_requested": MODEL, "api_style": "generateContent",
                      "generation_config": body["generationConfig"], "system_instruction_sha256": exp.base.sha256_text(system), "prompt_used": True}
    r.update(experiment_id=("SYNTHETIC-" if mode == "mock" else "") + EXPERIMENT,
             condition_id=condition, generation_run_id="run1", collected_at=datetime.now(timezone.utc).isoformat(),
             answer=secured.response["answer"], cited_answer=secured.response["cited_answer"],
             claims=secured.response["claims"], citations=secured.response["citations"],
             postprocessing=rag_response["postprocessing"], generator="gemini:" + MODEL,
             collector_config=config, collector_config_sha256=sha256_json(config), latency_ms=latency,
             generation={"requested": "gemini", "used": "gemini", "model": MODEL, "fallback_reason": None,
                         "attempts": [{"provider": "gemini", "status": "success", "model": MODEL, "elapsed_ms": latency}],
                         "prompt_sha256": exp.base.sha256_text(prompt), "system_instruction_sha256": exp.base.sha256_text(system),
                         "request_config": request_config, "request_config_sha256": sha256_json(request_config)},
             security={"context_gate": gate.summary(), "output_gate": secured.summary()})
    r["request"] = {**r["request"], "provider": "gemini", "model": MODEL}
    r["response_config"] = {**r["response_config"], "generation_requested": "gemini", "generation_used": "gemini", "generation_model": MODEL}
    r["evaluation_trace"]["retrieval_stages"]["final_contexts"] = contexts
    r["evaluation_trace"].update(raw_draft=raw, sanitized_draft=sanitized,
        generation_input={"user_prompt": prompt, "system_instruction": system,
                          "prompt_sha256": exp.base.sha256_text(prompt), "system_instruction_sha256": exp.base.sha256_text(system),
                          "input_sha256": sha256_json(body)})
    r["evaluation_trace"]["timing_ms"] = {"generation": latency, "retrieval": None, "e2e": None, "fixed_retrieval_replay": True}
    record = build_answer_identity(r)
    judge.validate_answer_case(record, case)
    return record


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PermissionError("redirect_disabled")


class Budget:
    def __init__(self, root):
        self.root = Path(root)
        self.db = sqlite3.connect(self.root / "provider-attempts.sqlite")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE attempts(slot TEXT PRIMARY KEY, request_sha TEXT NOT NULL, state TEXT NOT NULL, started TEXT NOT NULL, http_status INTEGER)")
        self.db.commit()

    def reserve(self, slot, body):
        with self.db:
            if self.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] >= CAP:
                raise ValueError("attempt_cap_reached")
            self.db.execute("INSERT INTO attempts VALUES(?,?,?,?,NULL)", (slot, sha256_json(body), "reserved", datetime.now(timezone.utc).isoformat()))

    def finish(self, slot, state, status=None):
        with self.db:
            self.db.execute("UPDATE attempts SET state=?, http_status=? WHERE slot=?", (state, status, slot))


def load_key():
    CAPABILITY["key_read"] = True
    try:
        values = {}
        for line in (ROOT / ".env").read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
        key = values.get("RAG_GEMINI_API_KEY") or values.get("GEMINI_API_KEY") or values.get("GOOGLE_API_KEY")
        if not key or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", key):
            raise ValueError("configured_key_unavailable")
        return key
    finally:
        CAPABILITY["key_read"] = False


def post(budget, slot, body, model, key):
    budget.reserve(slot, body)
    url = API_URLS[model]
    CAPABILITY["provider"] = url
    status = None
    started = time.perf_counter()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(url, canonical_json(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": key})
        with opener.open(request, timeout=90) as response:
            status = response.status
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("provider_response_too_large")
        payload = json.loads(raw)
        write_new(budget.root / (slot + ".provider.json"), payload)
        budget.finish(slot, "received", status)
        candidates = payload.get("candidates") or []
        if not candidates or candidates[0].get("finishReason") != "STOP":
            raise ValueError("provider_incomplete_candidate")
        text = "".join(p.get("text", "") for p in candidates[0]["content"]["parts"] if not p.get("thought"))
        if not text:
            raise ValueError("provider_empty_text")
        return text, round((time.perf_counter() - started) * 1000, 3)
    except urllib.error.HTTPError as exc:
        budget.finish(slot, "http_error", exc.code)
        raise
    except Exception:
        budget.finish(slot, "failed", status)
        raise
    finally:
        CAPABILITY["provider"] = None


def mock_judge(case):
    return {"score": 0, "grounded_fully_correct": False,
            "claim_checks": [{"claim_id": c["claim_id"], "status": "missing", "answer_quote": None, "reason": "SYNTHETIC OFFLINE"} for c in case["required_claims"]],
            "citation_support": "none", "unsupported_facts": [], "contradictions": [],
            "abstention": "inappropriate", "uncertain": False, "reason": "SYNTHETIC OFFLINE"}


def run(prep, out, expected_sha, mode, authorize):
    prep, out = Path(prep), Path(out)
    if exp.sha(prep / "manifest.json") != expected_sha:
        raise ValueError("manifest_hash_mismatch")
    manifest = json.loads((prep / "manifest.json").read_text())
    if manifest["source_pins"] != source_pins():
        raise ValueError("prepared_sources_changed")
    if out.exists() or any(p.is_symlink() for p in (out, *out.parents)):
        raise ValueError("new_nonsymlink_run_required")
    if mode == "live" and (out != LIVE_ROOT or authorize != AUTHORIZATION):
        raise ValueError("exact_live_authorization_required")
    for key in list(os.environ):
        if key.startswith(("RAG_", "GEMINI_", "GOOGLE_")):
            os.environ.pop(key)
    by_id = {c["id"]: (c, r, g) for c, r, g in inputs()}
    slots = manifest["slots"] if mode == "live" else manifest["slots"][:4]
    # Verify every generation request before reading credentials or sending.
    for slot in slots:
        c, r, g = by_id[slot["case_id"]]
        _, _, body = prompt_and_request(slot["condition_id"], c, g)
        if sha256_json(body) != slot["request_sha256"]:
            raise ValueError("prepared_request_changed")
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / "run.json", {"mode": mode, "manifest_sha256": expected_sha, "manifest": manifest})
    budget = Budget(out)
    lock = (out / "runner.lock").open("x")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    key = load_key() if mode == "live" else None
    completed, failures = [], []
    try:
        for i, slot in enumerate(slots):
            cid, condition = slot["case_id"], slot["condition_id"]
            case, record, gate = by_id[cid]
            stem = condition + "--" + cid
            if not re.fullmatch(r"[a-z0-9_-]+", stem):
                raise ValueError("unsafe_slot_id")
            prompt, system, body = prompt_and_request(condition, case, gate)
            stage = "generation"
            try:
                if mode == "live":
                    raw, latency = post(budget, stem + "--gen", body, MODEL, key)
                else:
                    budget.reserve(stem + "--gen", body)
                    raw = "제공된 문서에서 질문의 답을 확인할 수 없습니다."
                    if condition == CONDITIONS[1]:
                        raw = canonical_json({"claims": [], "unanswered": [raw]})
                    latency = 0.0
                    budget.finish(stem + "--gen", "mock", None)
                answer = answer_record(record, case, gate, condition, raw, body, prompt, system, latency, mode)
                answer_path = out / (stem + ".answers.jsonl")
                write_new(answer_path, canonical_json(answer) + "\n")
                if mode == "live":
                    time.sleep(INTERVAL)
                stage = "judge"
                ji = judge.build_judge_input(case, answer)
                jp = judge.render_judge_prompt(ji)
                jb = {"contents": [{"parts": [{"text": jp}]}],
                      "generationConfig": {"temperature": 0.0, "maxOutputTokens": MAX_TOKENS,
                                           "responseMimeType": "application/json", "responseJsonSchema": judge.JUDGE_RESPONSE_JSON_SCHEMA}}
                if mode == "live":
                    jr, jl = post(budget, stem + "--judge", jb, JUDGE_MODEL, key)
                else:
                    budget.reserve(stem + "--judge", jb)
                    jr, jl = canonical_json(mock_judge(case)), 0.0
                    budget.finish(stem + "--judge", "mock", None)
                scored, guard = judge._parse_json_response(jr, judge_input=ji)
                judgment = judge.build_judgment_record(answer=answer, case=case, judge_run_id="judge-v11-r1",
                    judge_config=judge.build_judge_config(model=JUDGE_MODEL, max_output_tokens=MAX_TOKENS),
                    judge_input=ji, rendered_prompt=jp, judge=scored, raw_judge_response=jr,
                    attempts=[{"attempt": 1, "status": "ok", "http_status": 200 if mode == "live" else None, "latency_ms": jl}],
                    deterministic_guard=guard, answers_artifact_sha256=exp.sha(answer_path))
                write_new(out / (stem + ".judgments.jsonl"), canonical_json(judgment) + "\n")
                completed.append({"case_id": cid, "condition_id": condition, "score": scored["score"], "gfc": scored["grounded_fully_correct"],
                                  "answer_path": str(answer_path), "judgment_path": str(out / (stem + ".judgments.jsonl")),
                                  "accepted_claims": sum(c["supported"] for c in answer["claims"]), "claim_count": len(answer["claims"])})
                print(json.dumps({"pair": i + 1, "total": len(slots), "condition": condition, "case_id": cid, "score": scored["score"], "gfc": scored["grounded_fully_correct"]}), flush=True)
            except Exception as exc:
                failure = {"case_id": cid, "condition_id": condition, "stage": stage,
                           "error_type": type(exc).__name__, "http_status": exc.code if isinstance(exc, urllib.error.HTTPError) else None}
                write_new(out / (stem + ".error.json"), failure)
                failures.append(failure)
                print(json.dumps({"pair": i + 1, "failure": failure}), flush=True)
                if isinstance(exc, (urllib.error.URLError, PermissionError)):
                    break  # quota/auth/model/transport failures: no model/key switch
            if mode == "live":
                time.sleep(INTERVAL)
    finally:
        attempts = budget.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        summary = {"mode": mode, "status": "COMPLETE" if len(completed) == len(slots) else "INCOMPLETE",
                   "completed_pairs": len(completed), "planned_pairs": len(slots), "provider_attempts": attempts if mode == "live" else 0,
                   "mock_attempts": attempts if mode == "mock" else 0, "cases": completed, "failures": failures,
                   "interpretation": "n=1 developmental generation-boundary experiment; not holdout or historical majority-GFC comparison"}
        write_new(out / "completion.json", summary)
        budget.db.close()
        lock.close()
        print(json.dumps({k: summary[k] for k in ("status", "completed_pairs", "planned_pairs", "provider_attempts", "mock_attempts")}), flush=True)
    return 0 if summary["status"] == "COMPLETE" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "mock", "live"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--authorize")
    args = parser.parse_args()
    sys.addaudithook(audit)
    if args.mode == "prepare":
        prepare(args.out)
        return 0
    return run(args.prepared, args.out, args.manifest_sha256, args.mode, args.authorize)


if __name__ == "__main__":
    raise SystemExit(main())
