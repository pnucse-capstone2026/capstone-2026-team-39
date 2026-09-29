"""Separate one-reviewer final protocol. Frozen HTTP service and Judge are unmodified.

prepare/check/mock do not contact a provider. Live is explicit, single-key,
single-stream, append-only and metered before every provider request.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

import prepare as p

ROOT, BASE, HERE = p.ROOT, p.BASE, Path(__file__).resolve().parent
PREP = BASE / 'preparation-v1'
PLAN = BASE / 'execution-plan-v1.json'
RUN = BASE / 'live-v1'
SNAPSHOT_BASE = ROOT / 'processed/eval/preflight-20260909/security-live-preparation-v1/preparation-v1'
SNAPSHOT = SNAPSHOT_BASE / 'snapshots/c1-sec-merged'
GEN_MODEL, JUDGE_MODEL = 'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite'
EXPERIMENT = 'pnu-final-single-reviewer-security-20260914-v1'
INDEX = ROOT / 'processed/index/pnu-20260725-curated-cascade-v5-allow-suspect.sqlite'
MANIFEST = ROOT / 'processed/curation/20260725-pnu-curated-v5/curated-manifest.jsonl'
INDEX_SHA = 'a4c1ca6318cb14721866a6512f3dfeacdca58c62ecff98ea7b81df2e75c74c31'
MANIFEST_SHA = '1fa7e0f2f5d03a2a1249584d91f7232c9b2cb4642b39115bf3c9e6a871845a2f'
PACIFIC = ZoneInfo('America/Los_Angeles')
AUTH = 'I_APPROVE_SINGLE_REVIEWER_FINAL_EVALUATION'
require, canonical, sha, publish = p.require, p.canonical, p.file_sha, p.publish


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_bytes())


def load_cases():
    return [json.loads(line) for line in (PREP / 'reviewed-cases-v1.jsonl').read_bytes().splitlines() if line.strip()]


def modules():
    # prepare imports only the root's identical holdout validator; runtime modules
    # below are all loaded from the previously measured, hash-verified snapshot.
    sys.path.insert(0, str(SNAPSHOT / 'scripts'))
    import evaluate_service_answers as collect
    import evaluate_security_service_answers as security
    import judge_service_answers as judge
    import service_eval_artifacts as artifacts
    for module in (collect, security, judge, artifacts):
        require(Path(module.__file__).resolve().is_relative_to(SNAPSHOT), 'module_outside_snapshot')
    return collect, security, judge, artifacts


def schedule(cases, runs=3):
    core = [c for c in cases if c['split'] == 'holdout-core']
    challenge = [c for c in cases if c['split'] == 'holdout-challenge']
    require(len(core) == 27 and len(challenge) == 9, 'case_composition')
    slots = []
    require(runs in (1, 3), 'unsupported_run_count')
    for run in range(1, runs + 1):
        # Run-major checkpoints are declared before observing any final answer.
        # Case-paired ordering balances AB/BA and never selects on output quality.
        ordered = sorted(core, key=lambda c: p.digest(('20260914:' + c['id']).encode()))
        for index, case in enumerate(ordered):
            for condition in (('c0', 'c1') if (index + run) % 2 else ('c1', 'c0')):
                slots.append({'case_id': case['id'], 'condition_id': condition, 'generation_run_id': f'run{run}', 'group': f'{condition}-run{run}', 'phase': 'generation'})
        for case in challenge:
            slots.append({'case_id': case['id'], 'condition_id': 'c1', 'generation_run_id': f'run{run}', 'group': f'challenge-c1-run{run}', 'phase': 'generation'})
        generation = list(slots[-63:])
        slots.extend({**s, 'phase': 'judge'} for s in generation)
    for i, slot in enumerate(slots):
        slot['ordinal'] = i
        slot['slot_id'] = f"{slot['phase']}:{slot['group']}:{slot['case_id']}"
    require(len(slots) == 126 * runs and len({s['slot_id'] for s in slots}) == 126 * runs, 'schedule_shape')
    return slots


def known_usage():
    today = datetime.now(PACIFIC).date().isoformat()
    found, unknown, total = [], [], 0
    for path in sorted((ROOT / 'processed/eval').rglob('provider-attempts.sqlite')):
        if BASE in path.parents:
            continue
        try:
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
                columns = {r[1] for r in db.execute('PRAGMA table_info(attempts)')}
                column = 'started_ns' if 'started_ns' in columns else 'started' if 'started' in columns else None
                if column is None:
                    unknown.append(str(path)); continue
                values = [r[0] for r in db.execute('SELECT ' + column + ' FROM attempts')]
            days = []
            for value in values:
                if column == 'started_ns':
                    dt = datetime.fromtimestamp(value / 1e9, timezone.utc)
                else:
                    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                    require(dt.tzinfo is not None, 'naive_prior_timestamp')
                days.append(dt.astimezone(PACIFIC).date().isoformat())
            count = days.count(today)
            total += count
            found.append({'path': str(path), 'all_attempts': len(values), 'today_attempts': count})
        except (ValueError, sqlite3.Error, OSError, TypeError):
            unknown.append(str(path))
    return {'pacific_day': today, 'known_project_today': total, 'ledgers': found,
            'unreadable_or_unknown_ledgers': unknown, 'account_total_verified': False,
            'other_projects_usage': 'unknown; stop at provider 429, no key rotation'}


def prepare_plan():
    condition = read(SNAPSHOT_BASE / 'execution-proposal.json')['conditions']['c1-sec-merged']
    actual = {name: sha(SNAPSHOT / name) for name in condition['files_sha256']}
    require(actual == condition['files_sha256'], 'snapshot_changed')
    require(p.digest(canonical(actual)) == condition['snapshot_sha256'], 'snapshot_identity')
    require(sha(INDEX) == INDEX_SHA and sha(MANIFEST) == MANIFEST_SHA, 'corpus_pin')
    for name, expected in read(PREP / 'inventory.json').items():
        require(sha(PREP / name) == expected, 'preparation_pin')
    signoff = read(PREP / 'single-reviewer-signoff.json')
    require(signoff['status'] == 'APPROVED_AFTER_METADATA_CORRECTION' and signoff['reviewer_count'] == 1, 'single_review_not_approved')
    cases = load_cases()
    require(sha(PREP / 'reviewed-cases-v1.jsonl') == signoff['corrected_cases_sha256'], 'signoff_cases_pin')
    _, _, judge, _ = modules()
    pins = {str(SNAPSHOT / name): value for name, value in actual.items()}
    pins.update({str(path): sha(path) for path in PREP.iterdir() if path.is_file()})
    pins.update({str(path): sha(path) for path in HERE.glob('*.py')})
    env = read(SNAPSHOT_BASE / 'execution-proposal.json')['environment']
    env.update(RAG_CONTEXT_SECURITY_MODE='enforce', RAG_RETRIEVAL_MODE='bm25')
    plan = {'schema_version': 'pnu.single-reviewer-final-plan.v1', 'experiment_id': EXPERIMENT,
        'protocol_id': 'pnu.final-eval.single-reviewer.v1', 'created_at': now(), 'source_pins': pins,
        'snapshot': {'root': str(SNAPSHOT), 'sha256': condition['snapshot_sha256'], 'file_count': len(actual), 'origin_commit': condition['base_commit']},
        'freeze_method': 'Previously recorded immutable file-content snapshot, reverified before dispatch; NOT a clean commit of the active dirty workspace.',
        'original_two_reviewer_clean_git_gate': 'NOT_APPLICABLE_SEPARATE_AMENDED_PROTOCOL; not bypassed or reported as passed',
        'cases_sha256': signoff['corrected_cases_sha256'], 'signoff_sha256': sha(PREP / 'single-reviewer-signoff.json'),
        'index': {'path': str(INDEX), 'sha256': INDEX_SHA, 'source_manifest_path': str(MANIFEST), 'source_manifest_sha256': MANIFEST_SHA,
                  'corpus_revision': '20260725-pnu-curated-cascade-v5:cascade:5d1b5fee3d2eafa1a7c77d33'},
        'environment': env, 'conditions': {'c0': {'retrieval_tuning': False, 'security': 'enforce'}, 'c1': {'retrieval_tuning': True, 'security': 'enforce'}},
        'top_k': 8, 'parser_profile': 'cascade', 'retrieval_mode': 'bm25', 'context_chunks_per_document': 2,
        'generator_model': GEN_MODEL, 'judge_model': JUDGE_MODEL, 'judge_config': judge.build_judge_config(model=JUDGE_MODEL, max_output_tokens=1600),
        'slots': schedule(cases), 'generation_n': 3, 'judge_n': 1,
        'planned_generation_slots': 189, 'planned_judge_slots': 189, 'provider_attempt_cap': 378,
        'per_slot_attempt_cap': 1, 'retries': 0, 'minimum_inter_call_seconds': 3, 'daily_soft_cap': 450,
        'declared_daily_limit': 500, 'single_key': True, 'auto_key_switch': False, 'auto_model_switch': False,
        'prior_usage': known_usage(), 'human_answer_review': {'planned': 63, 'completed': 0, 'judge_disclosed': False},
        'design_amendments_before_execution': ['Both C0/C1 include the identical merged security package; only retrieval tuning differs.',
            'Run-major checkpoints; within each run, case-paired alternating AB/BA in fixed SHA order.',
            'User reconfirmed generation n=3 before dispatch; each answer receives Judge r1. Run1 is an intermediate checkpoint, not the final n=3 result.',
            'Oracle and repeat-Judge stability diagnostics are omitted under the deadline; main Core and Challenge remain complete.',
            'No automatic retries or model fallback; terminal generation errors and invalid Judge results reported separately.',
            'Human gold review is not human answer grading. Judge results remain automatic/un-calibrated until the 63 human answer labels exist.'],
        'authorization': read(PREP / 'user-approval.json'),
        'repeat_authorization': {'source': 'user_message_in_current_conversation', 'verbatim': '오늘 자정까지긴 한데, 교수님한테 제출해야해. 3회 그대로 진행해줘. 일단 지금까지 작성된거 교수님한테 제출할게', 'generation_n': 3}}
    publish(PLAN, canonical(plan) + b'\n')
    print(json.dumps({'status': 'PLAN_FROZEN', 'plan_sha256': sha(PLAN), 'planned_external_calls': 378,
        'first_checkpoint_calls': 126, 'prior_known_today': plan['prior_usage']['known_project_today'],
        'unknown_ledgers': len(plan['prior_usage']['unreadable_or_unknown_ledgers']), 'snapshot_verified_files': len(actual)}, ensure_ascii=False), flush=True)


def verify(plan, *, full=False):
    for name, expected in plan['source_pins'].items():
        require(sha(name) == expected, 'pinned_input_changed:' + Path(name).name)
    if full:
        require(sha(INDEX) == INDEX_SHA and sha(MANIFEST) == MANIFEST_SHA, 'corpus_changed')


class Ledger:
    def __init__(self, root, plan, create=False):
        self.root, self.plan = Path(root), plan
        self.path = self.root / 'provider-attempts.sqlite'
        if create:
            self.root.mkdir(mode=0o700)
            publish(self.root / 'plan.json', canonical(plan) + b'\n')
            with sqlite3.connect(self.path) as db:
                db.executescript('PRAGMA synchronous=FULL; CREATE TABLE meta(plan_sha TEXT NOT NULL); CREATE TABLE slots(id TEXT PRIMARY KEY, ordinal INTEGER UNIQUE, state TEXT NOT NULL, artifact_sha TEXT); CREATE TABLE active(id TEXT NOT NULL); CREATE TABLE attempts(id INTEGER PRIMARY KEY, slot TEXT NOT NULL, started TEXT NOT NULL, state TEXT NOT NULL, request_sha TEXT NOT NULL, model TEXT NOT NULL, http_status INTEGER);')
                db.execute('INSERT INTO meta VALUES(?)', (p.digest(canonical(plan)),))
                db.executemany('INSERT INTO slots VALUES(?,?,?,NULL)', [(s['slot_id'], s['ordinal'], 'pending') for s in plan['slots']])
            os.chmod(self.path, 0o600)
            for name in ('slots', 'responses', 'workers', 'answers', 'judge'):
                (self.root / name).mkdir(mode=0o700)
        require(self.path.is_file() and not self.path.is_symlink(), 'missing_or_symlink_ledger')
        with self.db() as db:
            require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'ledger_corrupt')
            require(db.execute('SELECT plan_sha FROM meta').fetchone()[0] == p.digest(canonical(plan)), 'ledger_plan_mismatch')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=rw', uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL'); db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            db.close()

    def begin(self, slot):
        with self.db() as db:
            require(db.execute('SELECT COUNT(*) FROM active').fetchone()[0] == 0, 'unresolved_active_slot')
            first = db.execute("SELECT id,state FROM slots WHERE state != 'complete' ORDER BY ordinal LIMIT 1").fetchone()
            require(first is not None and first['id'] == slot['slot_id'] and first['state'] == 'pending', 'schedule_not_next_or_uncertain')
            db.execute("UPDATE slots SET state='started' WHERE id=?", (slot['slot_id'],))
            db.execute('INSERT INTO active VALUES(?)', (slot['slot_id'],))

    def reserve(self, condition, model, body):
        with self.db() as db:
            active = db.execute('SELECT id FROM active').fetchone()
            require(active is not None, 'no_active_dispatch')
            slot = next(s for s in self.plan['slots'] if s['slot_id'] == active[0])
            require((condition == 'judge' and slot['phase'] == 'judge' and model == JUDGE_MODEL) or
                    (condition == slot['condition_id'] and slot['phase'] == 'generation' and model == GEN_MODEL), 'wrong_phase_or_model')
            rows = list(db.execute('SELECT started,state FROM attempts'))
            require(not any(r['state'] == 'reserved' for r in rows), 'uncertain_provider_attempt')
            require(len(rows) < self.plan['provider_attempt_cap'], 'attempt_cap_reached')
            require(db.execute('SELECT COUNT(*) FROM attempts WHERE slot=?', (slot['slot_id'],)).fetchone()[0] == 0, 'single_attempt_only')
            today = datetime.now(PACIFIC).date().isoformat()
            used = sum(datetime.fromisoformat(r['started']).astimezone(PACIFIC).date().isoformat() == today for r in rows)
            prior = self.plan['prior_usage']['known_project_today'] if today == self.plan['prior_usage']['pacific_day'] else 0
            require(prior + used < self.plan['daily_soft_cap'], 'daily_soft_cap_reached')
            if rows:
                elapsed = time.time() - datetime.fromisoformat(rows[-1]['started']).timestamp()
                time.sleep(max(0, self.plan['minimum_inter_call_seconds'] - elapsed))
            cursor = db.execute('INSERT INTO attempts(slot,started,state,request_sha,model) VALUES(?,?,?,?,?)',
                (slot['slot_id'], now(), 'reserved', p.digest(body), model))
            return cursor.lastrowid

    def finish_attempt(self, attempt, status, state):
        with self.db() as db:
            db.execute('UPDATE attempts SET http_status=?,state=? WHERE id=? AND state=?', (status, state, attempt, 'reserved'))

    def seal(self, slot, artifact_sha):
        with self.db() as db:
            require(db.execute('SELECT id FROM active').fetchone()[0] == slot['slot_id'], 'active_slot_mismatch')
            require(db.execute("SELECT COUNT(*) FROM attempts WHERE state='reserved'").fetchone()[0] == 0, 'unresolved_provider')
            db.execute("UPDATE slots SET state='complete',artifact_sha=? WHERE id=? AND state='started'", (artifact_sha, slot['slot_id']))
            db.execute('DELETE FROM active')


class Metered:
    def __init__(self, ledger, condition):
        self.ledger, self.condition = ledger, condition
        self.original = urllib.request.urlopen

    def open(self, request, *args, **kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        expected = JUDGE_MODEL if self.condition == 'judge' else GEN_MODEL
        require(url == f'https://generativelanguage.googleapis.com/v1beta/models/{expected}:generateContent', 'unapproved_provider_destination')
        require(isinstance(request.data, bytes), 'provider_body_missing')
        verify(self.ledger.plan)
        attempt = self.ledger.reserve(self.condition, expected, request.data)
        try:
            response = self.original(request, *args, **kwargs)
            # Reservation remains uncertain until the full response has been read.
            raw, status = response.read(), response.status
            response.close()
            import io
            buffered = io.BytesIO(raw); buffered.status = status
            self.ledger.finish_attempt(attempt, status, 'received')
            return buffered
        except urllib.error.HTTPError as error:
            self.ledger.finish_attempt(attempt, error.code, 'http_error')
            if error.code == 429 and not (self.ledger.root / 'quota-stop.json').exists():
                publish(self.ledger.root / 'quota-stop.json', canonical({'at': now(), 'attempt': attempt, 'http_status': 429}) + b'\n')
            raise
        # Unknown transport outcome stays reserved and stops the run; never refunded.


def key_from_config():
    values = {k: os.environ[k] for k in ('RAG_GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GEMINI_API_KEY') if os.environ.get(k)}
    if not values:
        path = ROOT / '.env'
        require(path.is_file() and not path.is_symlink(), 'configured_key_missing')
        for line in path.read_text().splitlines():
            match = re.fullmatch(r'\s*(?:export\s+)?(RAG_GEMINI_API_KEY|GOOGLE_API_KEY|GEMINI_API_KEY)\s*=\s*(.*?)\s*', line)
            if match:
                values[match[1]] = match[2].strip('"\'')
    for name in ('RAG_GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GEMINI_API_KEY'):
        if values.get(name):
            require(re.fullmatch(r'[A-Za-z0-9_-]{20,256}', values[name]), 'invalid_key_format')
            return values[name]
    raise ValueError('configured_key_missing')


def worker(condition, port):
    plan = read(RUN / 'plan.json'); verify(plan, full=True)
    raw = sys.stdin.buffer.readline(258)
    require(raw.endswith(b'\n') and len(raw) < 258, 'key_pipe_frame')
    key = raw[:-1].decode('ascii'); require(re.fullmatch(r'[A-Za-z0-9_-]{20,256}', key), 'key_pipe_format')
    os.environ['RAG_GEMINI_API_KEY'] = key
    ledger = Ledger(RUN, plan)
    urllib.request.urlopen = Metered(ledger, condition).open
    sys.path.insert(0, str(SNAPSHOT / 'scripts'))
    import search_api as api
    require(Path(api.__file__).resolve() == SNAPSHOT / 'scripts/search_api.py', 'wrong_service')
    args = [str(SNAPSHOT / 'scripts/search_api.py'), '--host', '127.0.0.1', '--port', str(port),
        '--env-file', str(RUN / 'absent.env'), '--index', str(INDEX), '--default-parser-profile', 'cascade',
        '--context-chunks-per-document', '2', '--dense-index', str(RUN / 'absent-dense'), '--learned-dense-root', str(RUN / 'absent-dense')]
    if condition == 'c0': args.append('--no-retrieval-tuning')
    sys.argv = args
    api.main()


def start_workers(plan, key):
    collect, _, _, _ = modules()
    workers, healths = {}, {}
    try:
        for condition, port in (('c0', 18770), ('c1', 18771)):
            env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8', **plan['environment']}
            log = (RUN / 'workers' / f'{condition}-{time.time_ns()}.log').open('xb')
            child = subprocess.Popen([sys.executable, '-B', str(HERE / 'runner.py'), 'worker', '--condition', condition, '--port', str(port)],
                env=env, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
            workers[condition] = (child, log, f'http://127.0.0.1:{port}')
            child.stdin.write((key + '\n').encode()); child.stdin.close()
            for attempt in range(80):
                require(child.poll() is None, 'service_worker_exited:' + condition)
                try:
                    health = collect.call_health(workers[condition][2], 3)
                    break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(.25)
            else: raise ValueError('service_not_ready:' + condition)
            profile = next(r for r in health['parser_profiles'] if r['id'] == 'cascade')
            freeze = health['service_config']['freeze']
            require(freeze['startup_code_sha256'] == sha(SNAPSHOT / 'scripts/search_api.py'), 'startup_code_pin')
            require(profile['index_sha256'] == INDEX_SHA and profile['source_manifest_sha256'] == MANIFEST_SHA, 'health_index_pin')
            collect.validate_health_controls(health, parser_profile='cascade', retrieval_mode='bm25',
                expected_corpus_revision=plan['index']['corpus_revision'], expected_retrieval_tuning=condition == 'c1',
                expected_context_chunks_per_document=2, expected_generation_provider='gemini', expected_generation_model=GEN_MODEL,
                expected_generation_max_context_chars=24000, expected_generation_max_output_tokens=900, require_eval_trace=True)
            healths[condition] = health
        publish(RUN / f'health-{time.time_ns()}.json', canonical(healths) + b'\n')
        return workers
    except BaseException:
        stop_workers(workers); raise


def stop_workers(workers):
    for child, log, _ in workers.values():
        if child.poll() is None:
            child.terminate()
            try: child.wait(timeout=8)
            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
        log.close()


def answer_record(plan, slot, case, response, latency):
    collect, security, _, artifacts = modules()
    observation = security.inspect_security_response(response, expected_mode='enforce')
    if observation['generator_called']:
        collect.validate_response_controls(response, provider='frontier', model=GEN_MODEL,
            parser_profile='cascade', retrieval_mode='bm25', expected_corpus_revision=plan['index']['corpus_revision'],
            require_eval_trace=True)
    else:
        require(response.get('parser_profile') == 'cascade' and response.get('retrieval_mode') == 'bm25'
            and response.get('institution') is None and response['generation']['requested'] == 'frontier', 'blocked_response_controls')
    contexts = collect.final_evaluation_contexts(response)
    require(contexts is not None, 'missing_final_context_trace')
    answer = collect.validate_answer_payload(response)
    config = {'single_reviewer_final_plan_sha256': p.digest(canonical(plan)), 'protocol_id': plan['protocol_id'],
        'cases_sha256': plan['cases_sha256'], 'snapshot_sha256': plan['snapshot']['sha256'],
        'index_sha256': INDEX_SHA, 'max_attempts': 1, 'eval_trace': True, 'security_mode': 'enforce',
        'provider': 'frontier', 'model': GEN_MODEL, 'parser_profile': 'cascade', 'retrieval_mode': 'bm25',
        'context_k': 8, 'retrieval_tuning': slot['condition_id'] == 'c1'}
    rec = collect.build_record_base(case, experiment_id=EXPERIMENT, condition_id=slot['condition_id'],
        generation_run_id=slot['generation_run_id'], collector_config=config)
    rec.update(answer=answer, latency_ms=latency, slot_outcome='answer', answer_eligible_for_judge=True,
        request_attempts=[{'attempt_number': 1, 'status': 'ok', 'elapsed_ms': latency}], collection_attempt_number=1,
        request=collect.build_chat_body(case, provider='frontier', model=GEN_MODEL, top_k=8, parser_profile='cascade', retrieval_mode='bm25', eval_trace=True),
        sources=response.get('results', []), security_observation=observation, single_reviewer_slot=dict(slot))
    for key in ('cited_answer', 'claims', 'citations', 'postprocessing', 'generator', 'generation', 'evaluation_trace', 'security', 'retrieval'):
        rec[key] = response.get(key)
    rec['role_mapping'] = response.get('role')
    require(isinstance(rec['role_mapping'], dict) and rec['role_mapping'].get('id') == case['role'], 'response_role_mismatch')
    rec['atomic_evidence_at_k'] = {str(k): collect.atomic_evidence_hit(contexts, case, k=k) for k in (5, 8)}
    rec = artifacts.build_answer_identity(rec)
    artifacts.validate_answer_record(rec)
    return rec


def append(path, value):
    require(not path.is_symlink(), 'symlink_artifact')
    with path.open('ab') as f:
        f.write(canonical(value) + b'\n'); f.flush(); os.fsync(f.fileno())


def verify_answer_group(plan, group):
    expected = []
    for slot in plan['slots']:
        if slot['phase'] == 'generation' and slot['group'] == group:
            expected.append((RUN / 'slots' / f"{slot['ordinal']:04d}.json").read_bytes())
    require(bool(expected), 'empty_answer_group')
    path = RUN / 'answers' / (group + '.answers.jsonl')
    require(not path.is_symlink() and path.read_bytes() == b''.join(expected), 'answer_group_changed')
    return path


def live(authorization, plan_sha):
    require(authorization == AUTH and sha(PLAN) == plan_sha, 'explicit_plan_approval_required')
    plan = read(PLAN); verify(plan, full=True)
    ledger = Ledger(RUN, plan, create=not RUN.exists())
    lock = os.open(RUN / 'run.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    workers = {}
    try:
        require(not (RUN / 'quota-stop.json').exists(), 'quota_stop_active')
        with ledger.db() as db:
            require(db.execute('SELECT COUNT(*) FROM active').fetchone()[0] == 0, 'uncertain_slot_requires_reconciliation')
            completed = {r['id']: r['artifact_sha'] for r in db.execute("SELECT id,artifact_sha FROM slots WHERE state='complete'")}
        cases = {c['id']: c for c in load_cases()}
        for slot in plan['slots']:
            if slot['slot_id'] in completed:
                require(sha(RUN / 'slots' / f"{slot['ordinal']:04d}.json") == completed[slot['slot_id']], 'completed_slot_changed')
        key = key_from_config()
        workers = start_workers(plan, key)
        collect, _, judge, _ = modules()
        for slot in plan['slots']:
            if slot['slot_id'] in completed: continue
            require(not (RUN / 'quota-stop.json').exists(), 'quota_stop_active')
            verify(plan)
            ledger.begin(slot)
            case = cases[slot['case_id']]
            if slot['phase'] == 'generation':
                started = time.perf_counter()
                response = collect.call_chat(workers[slot['condition_id']][2], case, 'frontier', GEN_MODEL, 180,
                    top_k=8, parser_profile='cascade', retrieval_mode='bm25', institution=None, eval_trace=True)
                publish(RUN / 'responses' / f"{slot['ordinal']:04d}.json", canonical(response) + b'\n')
                record = answer_record(plan, slot, case, response, round((time.perf_counter() - started) * 1000, 3))
                target = RUN / 'answers' / (slot['group'] + '.answers.jsonl')
            else:
                answers_path = verify_answer_group(plan, slot['group'])
                answers = [json.loads(line) for line in answers_path.read_bytes().splitlines() if line.strip()]
                answer = next(a for a in answers if a['case_id'] == slot['case_id'])
                judge.validate_answer_case(answer, case)
                judge_input = judge.build_judge_input(case, answer)
                prompt = judge.render_judge_prompt(judge_input)
                original = urllib.request.urlopen
                urllib.request.urlopen = Metered(ledger, 'judge').open
                try:
                    result = judge.call_gemini_judge(prompt=prompt, api_key=key, model=JUDGE_MODEL, max_output_tokens=1600, timeout=120, retries=1, judge_input=judge_input)
                finally:
                    urllib.request.urlopen = original
                record = judge.build_judgment_record(answer=answer, case=case, judge_run_id='judge-v11-r1',
                    answers_artifact_sha256=sha(answers_path), judge_config=plan['judge_config'], judge_input=judge_input,
                    rendered_prompt=prompt, judge=result['judge'], raw_judge_response=result['raw_judge_response'],
                    attempts=result['attempts'], deterministic_guard=result['deterministic_guard'], error=result['error'])
                target = RUN / 'judge' / (slot['group'] + '-judge-v11-r1.jsonl')
            artifact = RUN / 'slots' / f"{slot['ordinal']:04d}.json"
            publish(artifact, canonical(record) + b'\n')
            append(target, record)
            ledger.seal(slot, sha(artifact))
            print(json.dumps({'status': 'SLOT_COMPLETE', 'ordinal': slot['ordinal'] + 1, 'total': len(plan['slots']),
                'phase': slot['phase'], 'group': slot['group'], 'case_id': slot['case_id'],
                'judge_error': bool(record.get('error'))}, ensure_ascii=False), flush=True)
        publish(RUN / 'completion.json', canonical({'status': 'AUTOMATIC_EVALUATION_COMPLETE', 'slots': len(plan['slots']),
            'human_answer_labels': 0, 'plan_sha256': plan_sha, 'at': now()}) + b'\n')
    finally:
        stop_workers(workers); os.close(lock)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('mode', choices=('prepare', 'check', 'preflight', 'live', 'worker'))
    ap.add_argument('--authorization'); ap.add_argument('--plan-sha256')
    ap.add_argument('--condition', choices=('c0', 'c1')); ap.add_argument('--port', type=int)
    args = ap.parse_args()
    if args.mode == 'prepare': prepare_plan()
    elif args.mode == 'check':
        plan = read(PLAN); verify(plan, full=True)
        print(json.dumps({'status': 'PINS_OK', 'plan_sha256': sha(PLAN), 'slots': len(plan['slots'])}))
    elif args.mode == 'preflight':
        plan = read(PLAN); verify(plan, full=True)
        ledger = Ledger(RUN, plan, create=not RUN.exists())
        workers = start_workers(plan, 'SYNTHETIC_OFFLINE_KEY')
        stop_workers(workers)
        with ledger.db() as db:
            require(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 0, 'preflight_contacted_provider')
        print(json.dumps({'status': 'HTTP_HEALTH_PREFLIGHT_OK', 'conditions': 2, 'synthetic_credentials': True, 'provider_requests': 0}))
    elif args.mode == 'worker': worker(args.condition, args.port)
    else: live(args.authorization, args.plan_sha256)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        # Do not leak a provider URL containing credentials or answer text.
        print(json.dumps({'status': 'STOPPED', 'error_type': type(error).__name__,
            'safe_reason': str(error) if isinstance(error, ValueError) else type(error).__name__}, ensure_ascii=False), flush=True)
        raise SystemExit(1)
