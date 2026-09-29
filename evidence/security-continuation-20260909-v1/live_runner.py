"""Approved continuation of 28 remaining pairs, with immutable parent and carry-over budgets."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ORIGINAL_RUNNER = ROOT/'evidence/security-live-runner-20260909-v1/live_runner.py'
ORIGINAL_WORKER = ROOT/'evidence/security-live-runner-20260909-v1/live_worker.py'
RUNNER_SHA = 'c8d2ee129fa9e057a49b72e277e72d0de06638e1b1f3bbe9225fd83f8292969e'
WORKER_SHA = '7ff28b60504bb877867cce69200ced86c323c16400100d2e7694b6e1e5e6150e'
PARENT = ROOT/'processed/eval/preflight-20260909/security-pilot-live-v1/run'
PARENT_STOP = PARENT/'stop-1788933485184773000.json'
STOP_SHA = 'd328bb2b8df4e54ea9e4ed52f164ab4fe24ca007726271af29af2db782369e19'
LEDGER_SHA = 'dd03fb870ddfbaf8ee1de50941adf73e48af9e60b384103a0fa485fb22cb7db3'
LIVE_ROOT = ROOT/'processed/eval/preflight-20260909/security-pilot-continuation-v1/run'

if ORIGINAL_RUNNER.is_symlink() or hashlib.sha256(ORIGINAL_RUNNER.read_bytes()).hexdigest() != RUNNER_SHA:
    raise RuntimeError('original_runner_changed')
spec = importlib.util.spec_from_file_location('_pnu_pinned_live_runner', ORIGINAL_RUNNER)
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)
bridge = base.bridge
base.HERE, base.LIVE_ROOT = HERE, LIVE_ROOT
base.PINS = {**base.PINS, ORIGINAL_RUNNER: RUNNER_SHA, ORIGINAL_WORKER: WORKER_SHA}
_original_verify = base.verify_runtime
_original_provider = base.LiveProvider
_original_paced = base.PacedTransport


def __getattr__(name):
    # The byte-pinned original worker uses exactly the original ownership,
    # private-key pipe, clean environment and frozen service attestation code.
    return getattr(base, name)


def parent_snapshot(*, full=False):
    bridge.guard.verify_file(PARENT_STOP, STOP_SHA)
    bridge.guard.verify_file(PARENT/'provider-attempts.sqlite', LEDGER_SHA)
    stop = bridge.strict_json(PARENT_STOP.read_bytes())
    bridge.require(stop['status'] == 'STOPPED_REQUIRES_REVIEW' and stop['execution_mode'] == 'live', 'parent_not_expected_stop')
    if full:
        expected = set(stop['artifact_sha256']) | {PARENT_STOP.name, 'run.lock', 'daily-budget.lock'}
        actual = {p.relative_to(PARENT).as_posix() for p in PARENT.rglob('*') if p.is_file()}
        bridge.require(actual == expected and not any(p.is_symlink() for p in PARENT.rglob('*')), 'parent_file_set_changed')
        for relative, pin in stop['artifact_sha256'].items():
            path = (PARENT/relative).resolve(strict=True)
            bridge.require(PARENT in path.parents, 'parent_path_escape')
            bridge.guard.verify_file(path, pin)
    bridge.guard.verify_file(PARENT/'policy.json', stop['artifact_sha256']['policy.json'])
    policy = bridge.strict_json((PARENT/'policy.json').read_bytes())
    with sqlite3.connect((PARENT/'provider-attempts.sqlite').as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        attempts = [dict(r) for r in db.execute('SELECT * FROM attempts ORDER BY id')]
        sealed = {r['id']: r['sealed_sha'] for r in db.execute('SELECT id,sealed_sha FROM slots WHERE sealed_sha IS NOT NULL')}
        unfinished = [r['slot'] for r in db.execute('SELECT slot FROM executions WHERE artifact_sha IS NULL')]
    bridge.require(len(attempts) == 148 and len(sealed) == 148 and len(unfinished) == 1, 'parent_counts_changed')
    bridge.require(attempts[-1]['slot'] == unfinished[0] and attempts[-1]['state'] == 'transport_error'
                   and attempts[-1]['http_status'] is None and all(a['state'] != 'reserved' for a in attempts), 'parent_failure_changed')
    return policy, attempts, sealed


def pending_slots(policy, attempts, sealed):
    spent = Counter(a['slot'] for a in attempts)
    pending = []
    for original in policy['slots']:
        if original['slot_id'] in sealed:
            continue
        slot = dict(original)
        slot['attempt_cap'] -= spent[slot['slot_id']]
        bridge.require(slot['attempt_cap'] > 0, 'slot_attempts_exhausted')
        pending.append(slot)
    return pending


def verify_runtime(policy, *, all_inputs=False):
    _original_verify(policy, all_inputs=all_inputs)
    parent_policy, attempts, sealed = parent_snapshot(full=all_inputs)
    bridge.require(policy['carried_attempts'] == attempts and policy['carried_sealed_slots'] == sealed, 'carry_over_changed')
    expected = pending_slots(parent_policy, attempts, sealed)
    if policy['execution_mode'] == 'mock':
        selected = {('c1-sec-merged', 'secpilot-a07-attack'), ('c1-sec-merged', 'secpilot-a10-attack'),
                    ('c1-pre-security', 'secpilot-a07-attack')}
        expected = [s for s in expected if (s['condition_id'], s['case_id']) in selected]
    bridge.require(policy['slots'] == expected and policy['total_cap'] == sum(s['attempt_cap'] for s in expected), 'continuation_scope_changed')
    bridge.require(policy['controls'] == parent_policy['controls'] and policy['conditions'] == parent_policy['conditions'], 'frozen_controls_changed')
    bridge.require(policy['daily_soft_cap'] == 450 and not policy['auto_key_switch'] and not policy['auto_model_switch'], 'quota_controls_changed')


def make_policy(*, mock, approval_path=None, approval_sha=None):
    parent_policy, attempts, sealed = parent_snapshot(full=True)
    slots = pending_slots(parent_policy, attempts, sealed)
    bridge.require(len(slots) == 56 and sum(s['attempt_cap'] for s in slots) == 251, 'unexpected_remaining_scope')
    approval = None
    if not mock:
        bridge.require(approval_path is not None and approval_sha is not None, 'approval_required')
        bridge.guard.verify_file(approval_path, approval_sha)
        approval = bridge.strict_json(Path(approval_path).read_bytes())
        expected = {'approved': True, 'external_llm_calls': True, 'external_content_export': True,
                    'run_root': str(LIVE_ROOT), 'parent_stop_sha256': STOP_SHA, 'parent_ledger_sha256': LEDGER_SHA,
                    'pending_slots_sha256': bridge.digest(bridge.canonical(slots)), 'previous_attempts': 148,
                    'generation_slots': 28, 'judge_slots': 28, 'additional_attempt_cap': 251,
                    'combined_attempt_cap': 399, 'single_key': True, 'daily_soft_cap': 450,
                    'auto_key_switch': False, 'auto_model_switch': False, 'preserve_parent': True}
        bridge.require(all(approval.get(k) == v for k,v in expected.items()), 'continuation_approval_mismatch')
    policy = json.loads(bridge.canonical(parent_policy))
    policy.update({'slots': slots, 'total_cap': 251, 'intended_run_root': str(LIVE_ROOT),
        'parent_stop': str(PARENT_STOP), 'parent_stop_sha256': STOP_SHA, 'parent_ledger_sha256': LEDGER_SHA,
        'carried_attempts': attempts, 'carried_sealed_slots': sealed, 'execution_mode': 'mock' if mock else 'live',
        'api_execution_authorized': not mock, 'live_runner_ready': not mock,
        'status': 'MOCK_CONTINUATION_VERIFICATION' if mock else 'APPROVED_CONTINUATION',
        'launcher_sha256': base.tool_pins(), 'transport_min_interval_seconds': 0 if mock else 15})
    if mock:
        policy['experiment_id'] = 'SYNTHETIC-security-continuation-20260909-v1'
        selected = {('c1-sec-merged', 'secpilot-a07-attack'), ('c1-sec-merged', 'secpilot-a10-attack'),
                    ('c1-pre-security', 'secpilot-a07-attack')}
        policy['slots'] = [s for s in slots if (s['condition_id'], s['case_id']) in selected]
        bridge.require(len(policy['slots']) == 6, 'mock_selection_changed')
        policy['total_cap'] = sum(s['attempt_cap'] for s in policy['slots'])
        policy.pop('approval_file', None)
        policy.pop('approval_sha256', None)
    else:
        policy.update({'approval_file': str(Path(approval_path).resolve()), 'approval_sha256': approval_sha})
    verify_runtime(policy, all_inputs=True)
    return policy


def carried_today(policy, now_ns):
    day = base.pacific_day(now_ns)
    return sum(base.pacific_day(a['started_ns']) == day for a in policy['carried_attempts'])


class CarryLedger(bridge.guard.Ledger):
    def reserve(self, slot_id, request_sha, identity_sha):
        fd = os.open(self.path.parent/'daily-budget.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            bridge.require(not (self.path.parent/'quota-stop.json').exists(), 'provider_quota_stop_active')
            if self.policy['execution_mode'] == 'live':
                prior = [a for a in self.policy['carried_attempts'] if a['slot'] == slot_id]
                bridge.require(all(a['request_sha'] == request_sha for a in prior), 'parent_retry_request_changed')
            now = time.time_ns()
            used = carried_today(self.policy, now)+sum(base.pacific_day(a['started_ns']) == base.pacific_day(now) for a in self.summary()['attempts'])
            bridge.require(used < self.policy['daily_soft_cap'], 'daily_soft_cap_reached_with_carry')
            return super().reserve(slot_id, request_sha, identity_sha)
        finally:
            os.close(fd)


def budget_status(run):
    summary = run.ledger.summary()
    now = time.time_ns()
    used = sum(base.pacific_day(a['started_ns']) == base.pacific_day(now) for a in summary['attempts'])
    prior_today = carried_today(run.policy, now)
    prior_total = len(run.policy['carried_attempts'])
    completed = len(summary['sealed_slots'])
    remaining = len(run.policy['slots'])-completed
    projected = prior_today+used+(math.ceil(summary['reserved_total']/completed*remaining) if completed else remaining)
    return {'pacific_day': base.pacific_day(now), 'this_run_today': used, 'carried_today': prior_today,
            'pilot_today_total': used+prior_today, 'total_provider_attempts': summary['reserved_total'],
            'prior_provider_attempts': prior_total, 'combined_provider_attempts': prior_total+summary['reserved_total'],
            'sealed_slots': completed, 'combined_sealed_slots': len(run.policy['carried_sealed_slots'])+completed,
            'remaining_slots': remaining, 'projected_today_total': projected, 'soft_cap': run.policy['daily_soft_cap'],
            'other_project_usage': 'unknown beyond parent and this continuation', 'unresolved': summary['unresolved']}


def check_next_slot_budget(run, slot):
    status = budget_status(run)
    bridge.require(not (run.root/'quota-stop.json').exists(), 'provider_quota_stop_active')
    bridge.require(status['unresolved'] == 0, 'unresolved_provider_attempt')
    bridge.require(status['pilot_today_total']+slot['attempt_cap'] <= status['soft_cap'], 'pause_before_daily_cap_with_carry')
    if status['sealed_slots'] >= 12:
        bridge.require(status['projected_today_total'] <= status['soft_cap'], 'pause_projected_daily_quota')
    return status


def exception_metadata(exc):
    # No str/repr(exception), URL, request headers, response body or key.
    def typename(value):
        name = type(value).__name__
        return name if re.fullmatch(r'[A-Za-z0-9_]{1,80}', name) else 'UnknownExceptionType'
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else None
    return {'exception_type': typename(exc), 'reason_type': typename(reason) if reason is not None else None,
            'errno': exc.errno if isinstance(exc, OSError) and type(exc.errno) is int else None,
            'http_status': exc.code if isinstance(exc, urllib.error.HTTPError) else None}


class DiagnosticProvider(_original_provider):
    def open(self, request, timeout):
        try:
            return super().open(request, timeout)
        except BaseException as exc:
            directory = self.run.root/'provider-diagnostics'
            directory.mkdir(exist_ok=True)
            record = {'slot_id': self.slot['slot_id'], 'time_ns': time.time_ns(), **exception_metadata(exc)}
            bridge.write_new(directory/(str(record['time_ns'])+'.json'), bridge.canonical(record)+b'\n')
            raise


class CarryPacedTransport(_original_paced):
    def urlopen(self, *args, **kwargs):
        if not self.ledger.summary()['attempts'] and self.ledger.policy['carried_attempts']:
            interval = self.ledger.policy['transport_min_interval_seconds']
            remaining = interval-(time.time_ns()-self.ledger.policy['carried_attempts'][-1]['started_ns'])/1e9
            if remaining > 0:
                time.sleep(min(remaining, interval))
        return super().urlopen(*args, **kwargs)


base.verify_runtime = verify_runtime
base.DailyLedger = CarryLedger
base.budget_status = budget_status
base.check_next_slot_budget = check_next_slot_budget
base.LiveProvider = DiagnosticProvider
base.PacedTransport = CarryPacedTransport


@contextmanager
def parent_lock():
    # Read-only fd: prevent another original launcher from acquiring its lock,
    # without editing the parent, its ledger, or any output.
    fd = os.open(PARENT/'run.lock', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise bridge.GuardError('parent_run_already_owned') from None
        yield
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--verify-local', type=Path)
    mode.add_argument('--execute-approved', action='store_true')
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--approval-sha256')
    parser.add_argument('--verified-run', type=Path)
    args = parser.parse_args()
    sys.addaudithook(base.audit)
    mock = args.verify_local is not None
    with parent_lock():
        policy = make_policy(mock=mock, approval_path=args.approval, approval_sha=args.approval_sha256)
        if mock:
            root, key = args.verify_local.resolve()/'run', 'SYNTHETIC_OFFLINE_KEY'
            bridge.require(not root.exists(), 'mock_output_exists')
        else:
            bridge.require(args.verified_run is not None, 'local_verification_required')
            result = bridge.strict_json((args.verified_run/'completion.json').read_bytes())
            bridge.require(result['status'] == 'MOCK_CONTINUATION_COMPLETE' and result['launcher_sha256'] == base.tool_pins()
                           and result['budget']['sealed_slots'] == 6 and result['external_llm_calls'] == 0, 'stale_local_verification')
            for relative, pin in result['artifact_sha256'].items():
                bridge.guard.verify_file(args.verified_run/relative, pin)
            root = LIVE_ROOT
            bridge.require(not (root/'completion.json').exists(), 'continuation_already_complete')
            key = base.load_key(policy)
        run = base.open_run(root, policy, create=not root.exists())
        try:
            base.execute(run, key)
            status = 'MOCK_CONTINUATION_COMPLETE' if mock else 'LIVE_CONTINUATION_COMPLETE'
            base.save_result(run, status)
            print(json.dumps({'status': status, **budget_status(run)}), flush=True)
        except BaseException as exc:
            base.save_result(run, 'STOPPED_REQUIRES_REVIEW', exc)
            print(json.dumps({'status': 'STOPPED_REQUIRES_REVIEW', 'error_type': type(exc).__name__,
                              'error_code': str(exc) if isinstance(exc, bridge.GuardError) else None,
                              **budget_status(run)}), flush=True)
            raise SystemExit(1)


if __name__ == '__main__':
    main()
