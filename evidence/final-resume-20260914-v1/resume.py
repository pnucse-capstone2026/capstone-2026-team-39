"""Reconcile exactly one user-paused, provably undispatched slot. No API calls."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'evidence/final-single-reviewer-20260914-v1'))
import runner_v3 as r
import continuation as c

PLAN_SHA = 'e76706f29b57de426cb1598d5d9432b791361388647add7fed4b20f0184b1b00'
PAUSE_SHA = '053fd4c30ab2b9b9cb91162500321e3f8938c5d011f8175c851c58e8b9f3ca18'


def validate_state(slots, attempts, active, plan):
    r.require(len(slots) == 378 and len(attempts) == 28, 'pause_counts_changed')
    target = plan['slots'][43]['slot_id']
    r.require(active == [target], 'paused_active_changed')
    for i, row in enumerate(slots):
        r.require(row['id'] == plan['slots'][i]['slot_id'] and row['ordinal'] == i, 'schedule_changed')
        r.require(row['state'] == ('complete' if i < 43 else 'started' if i == 43 else 'pending'), 'paused_slot_state_changed')
        if i >= 43: r.require(row['artifact_sha'] is None, 'undispatched_artifact_binding')
    r.require(all(a['slot'] != target for a in attempts), 'paused_slot_was_dispatched')
    r.require([a['slot'] for a in attempts] == [s['slot_id'] for s in plan['slots'][15:43]], 'attempt_binding_changed')
    r.require(all(a['state'] == 'received' and a['http_status'] == 200 and a['model'] == r.GEN_MODEL for a in attempts), 'uncertain_or_changed_attempts')
    return target


def main():
    r.require(r.sha(r.PLAN) == PLAN_SHA, 'plan_changed')
    plan = r.read(r.PLAN)
    r.verify(plan, full=True)
    pause_path = r.RUN / 'user-pause-v1.json'
    r.require(r.sha(pause_path) == PAUSE_SHA, 'pause_receipt_changed')
    pause = r.read(pause_path)
    r.require((r.RUN / 'private-key-fingerprint.json').is_file(), 'missing_existing_key_lock')
    c.verify_same_key(r.RUN, r.key_from_config())  # verifies only; no credential output
    r.require(not (r.RUN / 'quota-stop.json').exists(), 'new_quota_stop_present')
    lock = os.open(r.RUN / 'run.lock', os.O_RDWR | os.O_NOFOLLOW)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    db_path = r.RUN / 'provider-attempts.sqlite'
    out = r.RUN / 'resume-after-user-pause-v1'
    db = sqlite3.connect(db_path.resolve().as_uri() + '?mode=rw', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        r.require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'ledger_corrupt')
        r.require(db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete', 'raw_backup_requires_delete_journal_mode')
        r.require(db.execute('SELECT plan_sha FROM meta').fetchone()[0] == r.p.digest(r.canonical(plan)), 'ledger_plan_changed')
        slots = [dict(x) for x in db.execute('SELECT * FROM slots ORDER BY ordinal')]
        attempts = [dict(x) for x in db.execute('SELECT * FROM attempts ORDER BY id')]
        active = [x[0] for x in db.execute('SELECT id FROM active')]
        target = validate_state(slots, attempts, active, plan)
        r.require(target == pause['undispatched_active_slot'], 'pause_target_changed')
        for directory in ('slots', 'responses'):
            r.require(not (r.RUN / directory / '0043.json').exists(), 'paused_slot_has_artifact')
        for slot in slots[:43]:
            r.require(r.sha(r.RUN / 'slots' / f"{slot['ordinal']:04d}.json") == slot['artifact_sha'], 'completed_slot_changed')
        for group, field in [('c0-run1', 'c0_answer_artifact_sha256'), ('c1-run1', 'c1_answer_artifact_sha256')]:
            path = r.RUN / 'answers' / (group + '.answers.jsonl')
            r.require(r.sha(path) == pause[field], 'saved_answers_changed')
            expected = b''.join((r.RUN / 'slots' / f"{s['ordinal']:04d}.json").read_bytes() for s in plan['slots'][:43]
                                if s['group'] == group and s['ordinal'] != 14)
            r.require(path.read_bytes() == expected, 'saved_answer_stream_changed')
        out.mkdir(mode=0o700, exist_ok=False)
        before = db_path.read_bytes()
        r.publish(out / 'ledger-before.sqlite', before)
        with sqlite3.connect((out / 'ledger-before.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as backup:
            r.require(backup.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'backup_corrupt')
            r.require(backup.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 28, 'backup_count_mismatch')
        intent = {'schema_version': 'pnu.undispatched-user-pause-reconciliation.v1',
            'at': r.now(), 'user_resume_request_verbatim': '이어서 ㄱㄱ해줘',
            'plan_sha256': PLAN_SHA, 'pause_receipt_sha256': PAUSE_SHA,
            'ledger_before_sha256': r.p.digest(before), 'target': target,
            'target_ordinal': 43, 'target_provider_attempts': 0, 'provider_attempts_preserved': 28,
            'completed_slots_preserved': 43, 'normal_answers_preserved': 42,
            'operation': 'Cancel only the untransmitted started marker and return that same slot to pending; do not alter or refund any provider attempt.'}
        r.publish(out / 'intent.json', r.canonical(intent) + b'\n')
        changed = db.execute("UPDATE slots SET state='pending' WHERE id=? AND state='started' AND artifact_sha IS NULL", (target,)).rowcount
        removed = db.execute('DELETE FROM active WHERE id=?', (target,)).rowcount
        r.require(changed == 1 and removed == 1, 'reconciliation_scope_changed')
        r.require([dict(x) for x in db.execute('SELECT * FROM attempts ORDER BY id')] == attempts, 'attempts_changed')
        db.commit()
        result = {**intent, 'status': 'RESUME_READY', 'ledger_after_sha256': r.sha(db_path),
                  'active_slots': db.execute('SELECT COUNT(*) FROM active').fetchone()[0],
                  'slot_states': dict(db.execute('SELECT state,COUNT(*) FROM slots GROUP BY state')),
                  'new_provider_calls': 0}
        r.require(result['active_slots'] == 0 and result['slot_states'] == {'complete': 43, 'pending': 335}, 'post_reconciliation_mismatch')
        r.publish(out / 'receipt.json', r.canonical(result) + b'\n')
        print(json.dumps({'status': result['status'], 'normal_answers_preserved': 42,
            'provider_calls': 0, 'next_ordinal': 44, 'remaining_provider_cap': 334,
            'receipt_sha256': r.sha(out / 'receipt.json')}, ensure_ascii=False), flush=True)
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
        os.close(lock)


if __name__ == '__main__': main()
