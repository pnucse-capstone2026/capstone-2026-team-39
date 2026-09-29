"""Single-key, zero-retry semantic adapter run. First error stops all calls.

Never overwrites or automatically resumes an existing run. A durable reservation
is consumed even if receipt is uncertain. No service/gold/Judge integration.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
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
ADAPTER = ROOT / "evidence/semantic-adapter-20260913-v1"
BASE = ROOT / "processed/eval/preflight-20260913/semantic-adapter-v1"
PREP = BASE / "preparation-v1"
RUNTIME = BASE / "runtime-v1"
LIVE = BASE / "live-v1"
PREP_SHA = "cffa239c19bf9511fa006d4982eae66380859e1dca41dc8dd905a43238524baa"
# Full hash, separately pinned to reject any change to the prepared request set.
OUTPUT_SHA = "9a5edef1e319c06efcd2fc4a3da4678d27ccf2cb33b76feb106fba455829e0d6"
ADAPTER_SHA = "2af0000b4b69889855d50976c5d8e6fa1e7fb3762dd88d936b88500701031ee2"
AUTH = "I_APPROVE_SEMANTIC_CORE42_84_ATTEMPTS"
MODELS = {"extract": "gemini-3.5-flash-lite", "semantic_review": "gemini-3.1-flash-lite"}
URLS = {stage: "https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent"
        for stage, model in MODELS.items()}
CAP = 84
INTERVAL = 15
CAPABILITY = {"key_read": False, "url": None}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if sha(ADAPTER / "adapter.py") != ADAPTER_SHA:
    raise ValueError("frozen_adapter_changed")
sys.path.insert(0, str(ADAPTER))
import adapter as a


def now():
    return datetime.now(timezone.utc).isoformat()


def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0])).resolve()
        if "holdout" in str(path).lower() and path.suffix != ".py":
            raise PermissionError("holdout_data_disabled")
        if path.name.startswith(".env") and not (CAPABILITY["key_read"] and path == ROOT / ".env"):
            raise PermissionError("secret_read_disabled")
    if event == "urllib.Request" and args[0] != CAPABILITY["url"]:
        raise PermissionError("unapproved_http_destination")
    if event in ("socket.connect", "socket.getaddrinfo") and CAPABILITY["url"] not in URLS.values():
        raise PermissionError("unapproved_network")
    if event == "socket.getaddrinfo" and args[0] != "generativelanguage.googleapis.com":
        raise PermissionError("unapproved_dns_destination")


def write_new(path, value):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("symlink_output")
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def read(path):
    return a.strict_json(Path(path).read_text())


def prepared_inputs():
    if sha(PREP / "manifest.json") != PREP_SHA or sha(PREP / "output-sha256.json") != OUTPUT_SHA:
        raise ValueError("prepared_manifest_changed")
    inventory = read(PREP / "output-sha256.json")
    if set(inventory) | {"output-sha256.json"} != {p.name for p in PREP.iterdir()}:
        raise ValueError("prepared_file_set_changed")
    pins = {str(PREP / name): expected for name, expected in inventory.items()}
    pins[str(PREP / "output-sha256.json")] = OUTPUT_SHA
    pins.update(read(PREP / "input-sha256.json"))
    pins.update({str(p): sha(p) for p in HERE.glob("*.py")})
    for path, expected in pins.items():
        if sha(path) != expected:
            raise ValueError("pinned_input_changed")
    slots = read(PREP / "manifest.json")["slots"]
    if len(slots) != 42 or len({s["case_id"] for s in slots}) != 42:
        raise ValueError("expected42_unique_cases")
    requests = []
    for slot in slots:
        cid = slot["case_id"]
        if not re.fullmatch(r"[a-z0-9_-]+", cid) or slot["request_file"] != "extract--" + cid + ".request.json":
            raise ValueError("unsafe_case_path")
        item = read(PREP / slot["request_file"])
        req = item["request"]
        if (item["record_type"] != "prepared_request_not_executed" or slot["request_id"] != req["request_id"]
                or item["messages"] != a.model_messages(req)):
            raise ValueError("prepared_request_binding_mismatch")
        requests.append((cid, req))
    return pins, requests


def prepare_runtime():
    if RUNTIME.exists() or any(p.is_symlink() for p in (RUNTIME, *RUNTIME.parents)):
        raise ValueError("new_runtime_required")
    pins, requests = prepared_inputs()
    manifest = {"experiment": "semantic-core42-20260913-v1", "created_at": now(),
                "approval": {"user_message": "ㄱ", "approved": True,
                             "scope": "One key, at most 42 extraction + 42 semantic review attempts; zero retries; first error stops.",
                             "destination": "Google Gemini generateContent",
                             "data": "Development questions, existing raw drafts and retrieved university sources; no gold or prior Judge verdicts."},
                "source_pins": pins, "selected_cases": [cid for cid, _ in requests],
                "models": MODELS, "max_output_tokens": 8192, "temperature": 0.0,
                "response_mime_type": "application/json", "total_attempt_cap": CAP,
                "retries": 0, "inter_call_seconds": INTERVAL, "live_root": str(LIVE),
                "automatic_resume": False, "known_prior_project_calls": 164,
                "account_daily_usage_verified": False, "availability_check": "First actual call; no separate metadata or token-count calls.",
                "new_generation_calls": 0, "final_judge_v11_calls": 0,
                "candidate_gfc": None, "eligible_for_service": False}
    RUNTIME.mkdir(parents=True, exist_ok=False)
    write_new(RUNTIME / "manifest.json", manifest)
    print(json.dumps({"status": "RUNTIME_PREPARED", "manifest_sha256": sha(RUNTIME / "manifest.json"),
                      "cases": len(requests), "external_calls": 0}), flush=True)


def provider_body(request):
    messages = a.model_messages(request)
    return {"systemInstruction": {"parts": [{"text": messages[0]["content"]}]},
            "contents": [{"role": "user", "parts": [{"text": messages[1]["content"]}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 8192, "responseMimeType": "application/json"}}


class Budget:
    def __init__(self, root):
        self.root = Path(root)
        path = self.root / "provider-attempts.sqlite"
        if path.exists():
            raise ValueError("existing_ledger_no_automatic_resume")
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE attempts(slot TEXT PRIMARY KEY, stage TEXT NOT NULL, request_sha TEXT NOT NULL, state TEXT NOT NULL, started TEXT NOT NULL, http_status INTEGER)")
        self.db.commit()

    def reserve(self, slot, stage, body):
        if stage not in MODELS or not re.fullmatch(r"[a-z0-9_-]+", slot):
            raise ValueError("invalid_slot")
        with self.db:
            if self.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] >= CAP:
                raise ValueError("attempt_cap_reached")
            if self.db.execute("SELECT COUNT(*) FROM attempts WHERE stage=?", (stage,)).fetchone()[0] >= 42:
                raise ValueError("stage_cap_reached")
            self.db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,NULL)", (slot, stage, a.digest(body), "reserved", now()))

    def finish(self, slot, state, status=None):
        with self.db:
            changed = self.db.execute("UPDATE attempts SET state=?, http_status=? WHERE slot=?", (state, status, slot)).rowcount
            if changed != 1:
                raise ValueError("unreserved_slot")

    def rows(self):
        return [dict(zip(("slot", "stage", "request_sha256", "state", "started", "http_status"), row))
                for row in self.db.execute("SELECT * FROM attempts ORDER BY rowid")]


def load_key():
    # Reuse the existing project's primary-key precedence; never display values,
    # accept key rotations, look for other credentials, or place a key in a URL.
    CAPABILITY["key_read"] = True
    try:
        values = {}
        for line in (ROOT / ".env").read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() in {"RAG_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"}:
                values[name.strip()] = value.strip().strip("\"'")
        key = values.get("RAG_GEMINI_API_KEY") or values.get("GEMINI_API_KEY") or values.get("GOOGLE_API_KEY")
        if not key or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", key):
            raise ValueError("configured_primary_key_unavailable")
        return key
    finally:
        CAPABILITY["key_read"] = False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PermissionError("redirect_disabled")


def response_text(payload):
    candidates = payload.get("candidates") or []
    if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
        raise ValueError("provider_incomplete_candidate")
    text = "".join(p.get("text", "") for p in candidates[0]["content"]["parts"] if not p.get("thought"))
    if not text.strip():
        raise ValueError("provider_empty_text")
    return text


def post(budget, slot, stage, request, key):
    body = provider_body(request)
    write_new(budget.root / (slot + ".request.json"), {"request": request, "model": MODELS[stage], "body": body})
    budget.reserve(slot, stage, body)  # durable BEFORE any network request
    CAPABILITY["url"] = URLS[stage]
    status = None
    started = time.perf_counter()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        req = urllib.request.Request(URLS[stage], a.encode(body).encode(),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        with opener.open(req, timeout=90) as response:
            status = response.status
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024 or key.encode() in raw:
            raise ValueError("unsafe_or_oversized_provider_response")
        write_new(budget.root / (slot + ".provider.txt"), raw.decode("utf-8"))
        payload = json.loads(raw)
        budget.finish(slot, "received", status)
        text = response_text(payload)
        write_new(budget.root / (slot + ".response.txt"), text)
        write_new(budget.root / (slot + ".receipt.json"), {"http_status": status, "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "usage_metadata": payload.get("usageMetadata", {}), "model_version": payload.get("modelVersion"),
                    "response_sha256": hashlib.sha256(text.encode()).hexdigest()})
        return text
    except urllib.error.HTTPError as error:
        budget.finish(slot, "http_error", error.code)
        raise
    except Exception:
        budget.finish(slot, "failed", status)
        raise
    finally:
        CAPABILITY["url"] = None


def run_cases(requests, out, call, pause):
    results, failures = [], []
    calls = 0
    for cid, request in requests:
        stage = "extract"
        try:
            if calls:
                pause(INTERVAL)
            calls += 1
            extracted = call(cid + "--extract", stage, request)
            parsed = a.parse_extraction(request, extracted)
            write_new(out / (cid + ".extraction-validation.json"), parsed)
            stage = "semantic_review"
            semantic = a.build_semantic_request(request, extracted)
            pause(INTERVAL)
            calls += 1
            reviewed = call(cid + "--semantic_review", stage, semantic)
            parsed_review = a.parse_semantic(semantic, reviewed)
            write_new(out / (cid + ".semantic-validation.json"), parsed_review)
            result = a.combine(request, extracted, reviewed)
            if result["status"] != "offline_review_complete":
                raise ValueError("combine_blocked")
            write_new(out / (cid + ".decision.json"), result)
            results.append({"case_id": cid, "units": result["units"], "question_coverage": result["question_coverage"]})
            print(json.dumps({"completed_cases": len(results), "case_id": cid,
                              "offline_candidate_units": sum(u["offline_candidate_only"] for u in result["units"])}), flush=True)
        except Exception as error:
            failure = {"case_id": cid, "stage": stage, "error_type": type(error).__name__,
                       "reason": str(error)[:300] if isinstance(error, ValueError) else "transport_or_runtime_error_details_suppressed",
                       "http_status": error.code if isinstance(error, urllib.error.HTTPError) else None}
            write_new(out / (cid + ".error.json"), failure)
            failures.append(failure)
            print(json.dumps({"status": "STOPPED_ON_FIRST_ERROR", "failure": failure}), flush=True)
            break
    return results, failures


def live(expected_sha, authorization):
    if authorization != AUTH or sha(RUNTIME / "manifest.json") != expected_sha:
        raise ValueError("exact_authorization_and_manifest_required")
    manifest = read(RUNTIME / "manifest.json")
    pins, requests = prepared_inputs()
    if manifest["source_pins"] != pins or manifest["models"] != MODELS or manifest["live_root"] != str(LIVE):
        raise ValueError("runtime_input_changed")
    if LIVE.exists() or any(p.is_symlink() for p in (LIVE, *LIVE.parents)):
        raise ValueError("existing_run_no_automatic_resume")
    LIVE.mkdir(parents=True, exist_ok=False)
    write_new(LIVE / "run.json", {"manifest_sha256": expected_sha, "manifest": manifest, "started_at": now()})
    budget = Budget(LIVE)
    results, failures, fatal = [], [], None
    try:
        key = load_key()
        results, failures = run_cases(requests, LIVE, lambda slot, stage, req: post(budget, slot, stage, req, key), time.sleep)
    except Exception as error:
        fatal = {"error_type": type(error).__name__, "reason": "execution_stopped_without_retry"}
    finally:
        rows = budget.rows()
        budget.db.close()
        summary = {"status": "COMPLETE" if len(results) == len(requests) else "STOPPED_INCOMPLETE",
                   "planned_cases": len(requests), "completed_cases": len(results),
                   "reserved_attempts": len(rows), "received_http200": sum(r["http_status"] == 200 for r in rows),
                   "attempts_by_stage": dict(Counter(r["stage"] for r in rows)), "attempts": rows,
                   "results": results, "failures": failures, "fatal": fatal,
                   "extraction_validations": len(list(LIVE.glob("*.extraction-validation.json"))),
                   "semantic_validations": len(list(LIVE.glob("*.semantic-validation.json"))),
                   "candidate_gfc": None, "eligible_for_service": False, "human_review": False,
                   "retries": 0, "single_key": True, "account_daily_usage_verified": False,
                   "finished_at": now(), "limitation": "Known development inputs, no gold accuracy measurement; structured/semantic acceptance is not correctness or GFC."}
        write_new(LIVE / "completion.json", summary)
        write_new(LIVE / "output-sha256.json", {p.name: sha(p) for p in LIVE.iterdir() if p.is_file()})
        print(json.dumps({k: summary[k] for k in ("status", "completed_cases", "reserved_attempts", "received_http200", "extraction_validations", "semantic_validations")}), flush=True)
    return 0 if summary["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "live"))
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--authorize")
    args = parser.parse_args()
    sys.addaudithook(audit)
    if args.mode == "prepare":
        prepare_runtime()
    else:
        raise SystemExit(live(args.manifest_sha256, args.authorize))
