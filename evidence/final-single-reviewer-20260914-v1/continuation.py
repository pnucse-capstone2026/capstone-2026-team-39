"""Approved quota-stop continuation; never mutates the stopped evaluation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from datetime import datetime

import prepare as p

BASE = p.BASE
PARENT = BASE / 'live-v2'
PARENT_PLAN = BASE / 'execution-plan-v2.json'
PARENT_SHA = 'dc39e90a68b391b74968baea48aefe4eadf7120bf3ca28a8a516b265ef8ba98a'
STOP_SHA = 'f6c99e338c0adb40a921b0c1eee18a5dba04ba19595c583095b9255a345597b8'
TRANSFER_SHA = 'bef9bd730aa9e66afaf1093bb0dec05ba7cfbfcca07e86b3cc237855fbbbd0db'


def validate_parent(r):
    p.require(p.file_sha(PARENT_PLAN) == PARENT_SHA, 'parent_plan_changed')
    plan = r.read(PARENT_PLAN)
    r.verify(plan, full=True)
    p.require(p.file_sha(PARENT / 'stop-summary-v1.json') == STOP_SHA, 'stop_summary_changed')
    p.require(p.file_sha(BASE / 'external-transfer-approval-v1.json') == TRANSFER_SHA, 'external_approval_changed')
    with sqlite3.connect((PARENT / 'provider-attempts.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        p.require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'parent_ledger_corrupt')
        attempts = [dict(x) for x in db.execute('SELECT * FROM attempts ORDER BY id')]
        slots = [dict(x) for x in db.execute('SELECT * FROM slots ORDER BY ordinal')]
        active = [x[0] for x in db.execute('SELECT id FROM active')]
    p.require(len(attempts) == 15 and len(slots) == 378, 'parent_count_changed')
    p.require([a['http_status'] for a in attempts] == [200] * 14 + [429], 'parent_attempts_changed')
    p.require([a['state'] for a in attempts] == ['received'] * 14 + ['http_error'], 'parent_transport_uncertain')
    p.require(active == [plan['slots'][14]['slot_id']], 'parent_active_changed')
    for i, slot in enumerate(slots):
        p.require(slot['id'] == plan['slots'][i]['slot_id'], 'parent_schedule_changed')
        p.require(slot['state'] == ('complete' if i < 14 else 'started' if i == 14 else 'pending'), 'parent_disposition_changed')
        if i < 14:
            p.require(p.file_sha(PARENT / 'slots' / f'{i:04d}.json') == slot['artifact_sha'], 'parent_answer_changed')
    p.require([a['slot'] for a in attempts] == [s['slot_id'] for s in plan['slots'][:15]], 'parent_attempt_binding')
    p.require(all(a['model'] == r.GEN_MODEL for a in attempts), 'parent_model_changed')
    for group in ('c0-run1', 'c1-run1'):
        expected = b''.join((PARENT / 'slots' / f"{s['ordinal']:04d}.json").read_bytes()
                            for s in plan['slots'][:14] if s['group'] == group)
        p.require((PARENT / 'answers' / f'{group}.answers.jsonl').read_bytes() == expected, 'parent_stream_changed')
    response = r.read(PARENT / 'responses/0014.json')
    p.require(response['generation']['requested'] == 'frontier' and response['generation']['used'] == 'extractive', 'parent_error_not_fallback')
    p.require(r.read(PARENT / 'quota-stop.json')['http_status'] == 429, 'parent_not_quota_stopped')
    return plan, attempts


def prepare_plan(r):
    parent, attempts = validate_parent(r)
    plan = json.loads(p.canonical(parent))
    plan['schema_version'] = 'pnu.single-reviewer-final-continuation-plan.v1'
    plan['created_at'] = r.now()
    plan['minimum_inter_call_seconds'] = 15
    # 174 outstanding generations and 188 real Judge calls. The failed output
    # has no model judgment. All 189 planned generation outcomes stay in n=3.
    plan['provider_attempt_cap'] = 362
    plan['planned_generation_slots'] = 189
    plan['planned_judge_slots'] = 188
    plan['remaining_generation_calls'] = 174
    plan['remaining_judge_calls'] = 188
    plan['total_provider_cap_including_parent'] = 377
    plan['terminal_generation_ordinals'] = [14]
    terminal_id = parent['slots'][14]['case_id']
    terminal_group = parent['slots'][14]['group']
    skip = [s['ordinal'] for s in plan['slots'] if s['phase'] == 'judge' and s['case_id'] == terminal_id and s['group'] == terminal_group]
    p.require(skip == [77], 'unexpected_matching_judge_slot')
    plan['skip_judge_ordinals'] = skip
    plan['imported_answer_ordinals'] = list(range(14))
    plan['prior_usage'] = r.known_usage()
    today = plan['prior_usage']['pacific_day']
    parent_today = sum(datetime.fromisoformat(a['started']).astimezone(r.PACIFIC).date().isoformat() == today for a in attempts)
    plan['prior_usage']['known_project_today'] += parent_today
    plan['prior_usage']['ledgers'].append({'path': str(PARENT / 'provider-attempts.sqlite'), 'all_attempts': 15, 'today_attempts': parent_today})
    plan['continuation'] = {
        'parent_plan_sha256': PARENT_SHA, 'parent_run': str(PARENT),
        'reason': 'User-approved resumption at 15-second global spacing after provider429',
        'user_reply_verbatim': '응',
        'answered_question': '같은 키로 호출 간격을 15초로 늘려 재개할까? 성공한 14개는 재생성하지 않고, 실패한 1건은 오류로 남길게.',
        'external_transfer_approval_sha256': TRANSFER_SHA,
        'reused_answers': 14, 'preserved_terminal_generations': 1,
        'parent_ledger_rewritten': False, 'parent_attempts_refunded': False,
        'retried_successes': 0, 'retried_failure': False,
        'terminal_gfc_policy': 'GFC=0 as operational failure on all planned slots; never invent a Judge score; also report valid-output-only sensitivity.',
        'mean_score_policy': 'Only valid Judge scores, accompanied by valid/planned counts; missing Judge scores are not silently zeroed.',
        'human_answer_review_eligible': 62, 'human_answer_review_planned_slots': 63,
        'same_credential_policy': 'Same configured key lookup as parent; lock a private fingerprint for this continuation. Parent did not store a key fingerprint.'}
    plan['design_amendments_before_execution'] += [
        'After the logged quota stop, user approved 15-second spacing; content/generation/Judge controls unchanged.',
        '14 exact answer records are imported without rehashing their collector identities. New records retain the continuation collector identity.',
        'Provider429/fallback slot stays an operational failure, no synthetic answer or Judge. The old three-retry terminal schema is not falsified.']
    parent_files = [PARENT_PLAN, BASE / 'external-transfer-approval-v1.json']
    parent_files += [x for x in PARENT.rglob('*') if x.is_file() and x.name != 'run.lock']
    plan['source_pins'].update({str(x): p.file_sha(x) for x in parent_files})
    plan['source_pins'].update({str(x): p.file_sha(x) for x in r.HERE.glob('*.py')})
    p.require(plan['prior_usage']['known_project_today'] + 362 <= 450, 'known_daily_budget_insufficient')
    p.publish(r.PLAN, p.canonical(plan) + b'\n')
    print(json.dumps({'status': 'CONTINUATION_PLAN_FROZEN', 'sha256': p.file_sha(r.PLAN),
        'new_calls_max': 362, 'total_calls_max': 377, 'imported_answers': 14,
        'preserved_error': 1, 'spacing_seconds': 15, 'known_prior_today': plan['prior_usage']['known_project_today']}), flush=True)


def terminal_record(plan):
    slot = plan['slots'][14]
    return {'schema_version': 'pnu.single-reviewer-terminal.v1', 'record_type': 'operational_generation_failure',
        'slot': slot, 'case_id': slot['case_id'], 'condition_id': slot['condition_id'],
        'generation_run_id': slot['generation_run_id'], 'answer_eligible_for_judge': False,
        'judge_score': None, 'operational_gfc': False, 'provider_http_status': 429,
        'actual_generation_used': 'extractive', 'required_generation_used': 'frontier',
        'parent_provider_attempt_id': 15, 'parent_plan_sha256': PARENT_SHA,
        'raw_response_path': str(PARENT / 'responses/0014.json'),
        'raw_response_sha256': p.file_sha(PARENT / 'responses/0014.json'),
        'policy': plan['continuation']['terminal_gfc_policy'],
        'continuation_plan_sha256': p.digest(p.canonical(plan))}


def initialized_ledger(r, plan):
    if r.RUN.exists():
        p.require((r.RUN / 'import-complete.json').is_file(), 'incomplete_import_requires_offline_reconciliation')
        return r.Ledger(r.RUN, plan)
    validate_parent(r)
    ledger = r.Ledger(r.RUN, plan, create=True)
    for slot in plan['slots'][:14]:
        raw = (PARENT / 'slots' / f"{slot['ordinal']:04d}.json").read_bytes()
        path = r.RUN / 'slots' / f"{slot['ordinal']:04d}.json"
        ledger.begin(slot)
        p.publish(path, raw)
        r.append(r.RUN / 'answers' / (slot['group'] + '.answers.jsonl'), json.loads(raw))
        ledger.seal(slot, p.file_sha(path))
    slot = plan['slots'][14]
    ledger.begin(slot)
    path = r.RUN / 'slots/0014.json'
    p.publish(path, p.canonical(terminal_record(plan)) + b'\n')
    ledger.seal(slot, p.file_sha(path))
    p.publish(r.RUN / 'import-complete.json', p.canonical({'imported_answers': 14,
        'operational_failures': 1, 'provider_calls': 0, 'parent_plan_sha256': PARENT_SHA}) + b'\n')
    return ledger


def skipped_judge_record(plan, slot):
    p.require(slot['ordinal'] in plan['skip_judge_ordinals'], 'unapproved_judge_skip')
    return {'schema_version': 'pnu.single-reviewer-judge-skip.v1', 'record_type': 'judge_not_called',
        'slot': slot, 'reason': 'original generation failed requested-provider control after HTTP429',
        'judge_score': None, 'operational_gfc': False, 'linked_generation_ordinal': 14,
        'continuation_plan_sha256': p.digest(p.canonical(plan))}


def verify_same_key(root, key):
    path = root / 'private-key-fingerprint.json'
    expected = {'sha256': hashlib.sha256(('pnu-final-key:' + key).encode()).hexdigest()}
    if path.exists():
        p.require(not path.is_symlink() and json.loads(path.read_bytes()) == expected, 'configured_key_changed')
    else:
        p.publish(path, p.canonical(expected) + b'\n')
        os.chmod(path, 0o600)


def preserve_http_diagnostic(root, attempt, error):
    """No raw provider error message, echoed query, or credentials in diagnostics."""
    details = {'attempt': attempt, 'http_status': error.code, 'at_ns': time.time_ns()}
    retry = error.headers.get('Retry-After') if error.headers is not None else None
    if retry and re.fullmatch(r'[0-9.]{1,20}', retry):
        details['retry_after_seconds'] = retry
    try:
        raw = error.read(65536)
        details['provider_error_body_sha256'] = hashlib.sha256(raw).hexdigest()
        body = json.loads(raw).get('error', {})
        if body.get('status') in ('RESOURCE_EXHAUSTED', 'UNAVAILABLE', 'PERMISSION_DENIED', 'INVALID_ARGUMENT'):
            details['provider_status'] = body['status']
        details['quota_indicators'] = []
        for detail in body.get('details', []):
            if not isinstance(detail, dict): continue
            delay = detail.get('retryDelay')
            if isinstance(delay, str) and re.fullmatch(r'[0-9.]{1,20}s', delay): details['retry_delay'] = delay
            for violation in detail.get('violations', []):
                if not isinstance(violation, dict): continue
                names = ' '.join(str(violation.get(k, '')) for k in ('quotaMetric', 'quotaId'))
                for token in ('PerMinute', 'PerDay', 'requests_per_minute', 'tokens_per_minute', 'requests_per_day'):
                    if token.lower() in names.lower() and token not in details['quota_indicators']:
                        details['quota_indicators'].append(token)
    except (ValueError, OSError, TypeError, AttributeError):
        details['provider_error_body_parseable'] = False
    p.publish(root / f'provider-error-{attempt:04d}.json', p.canonical(details) + b'\n')
