"""Inspect immutable stopped artifacts and propose, but never execute, recovery."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import summarize as base


def remaining_plan(slots, sealed, attempts):
    spent = Counter(a['slot'] for a in attempts)
    pending = []
    for slot in slots:
        sid = slot['slot_id']
        if sid in sealed:
            continue
        cap = slot['attempt_cap']-spent[sid]
        base.require(cap > 0, 'remaining_slot_budget_exhausted')
        pending.append({'slot_id': sid, 'role': slot['role'], 'condition_id': slot['condition_id'],
                        'case_id': slot['case_id'], 'previous_attempts': spent[sid], 'remaining_attempt_cap': cap})
    return {'api_execution_authorized': False, 'status': 'PLAN_ONLY_RECONCILIATION_APPROVAL_REQUIRED',
            'previous_attempts_not_refunded': len(attempts), 'pending_slots': pending,
            'pending_role_counts': dict(Counter(s['role'] for s in pending)),
            'remaining_attempt_ceiling': sum(s['remaining_attempt_cap'] for s in pending),
            'combined_previous_plus_remaining_ceiling': len(attempts)+sum(s['remaining_attempt_cap'] for s in pending),
            'requirements': ['Preserve original ledger and every output, including the empty failed answer.',
                'Carry forward cumulative and Pacific-day attempts; failed attempts are not refunded.',
                'Do not rerun any sealed slot or switch key/model.',
                'Use a separately verified continuation protocol and explicit operator approval; this file is not an execution authorization.']}


def inspect(root, stop_path):
    root, stop_path = root.resolve(strict=True), stop_path.resolve(strict=True)
    base.require(stop_path.parent == root, 'stop_must_belong_to_run')
    stop_sha = base.sha(stop_path)
    stop, policy = base.load(stop_path), base.load(root/'policy.json')
    base.require(stop['status'] == 'STOPPED_REQUIRES_REVIEW' and stop['execution_mode'] == 'live', 'expected_stopped_live_run')
    for relative, pin in stop['artifact_sha256'].items():
        path = (root/relative).resolve(strict=True)
        base.require(root in path.parents and base.sha(path) == pin, 'stop_artifact_mismatch')
    with sqlite3.connect((root/'provider-attempts.sqlite').as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        sealed = {r['id']: r['sealed_sha'] for r in db.execute('SELECT id,sealed_sha FROM slots WHERE sealed_sha IS NOT NULL')}
        attempts = [dict(r) for r in db.execute('SELECT * FROM attempts ORDER BY id')]
        unfinished = [dict(r) for r in db.execute('SELECT slot FROM executions WHERE artifact_sha IS NULL')]
    base.require(len(sealed) == stop['budget']['sealed_slots'] and len(attempts) == stop['budget']['total_provider_attempts'], 'stop_ledger_disagrees')
    base.require(all(a['state'] != 'reserved' for a in attempts), 'unresolved_transport_requires_separate_review')
    rows, inputs, answers, pairs = [], {}, {}, []
    for slot in policy['slots']:
        if slot['role'] != 'generation':
            continue
        sid = slot['slot_id']
        jid = sid.rsplit(':', 1)[0]+':judge'
        if sid not in sealed or jid not in sealed:
            continue
        ap = root/'results'/(hashlib.sha256(sid.encode()).hexdigest()+'.answers.jsonl')
        jp = root/'results'/(hashlib.sha256(jid.encode()).hexdigest()+'.judgments.jsonl')
        base.require(base.sha(ap) == sealed[sid] and base.sha(jp) == sealed[jid], 'sealed_pair_changed')
        verified, _ = base.repeats.aggregate_repeats(answer_path=ap, judgment_paths=[jp])
        pairs.append(verified)
        answer, judgment = base.load(ap), base.load(jp)
        answers[(slot['condition_id'], slot['case_id'])] = answer
        cf = slot['case_file']
        if cf['path'] not in inputs:
            base.require(base.sha(cf['path']) == cf['sha256'], 'case_file_changed')
            inputs[cf['path']] = {c['id']: c for c in (json.loads(line) for line in Path(cf['path']).read_text().splitlines() if line)}
        case, judge = inputs[cf['path']][slot['case_id']], judgment['judge']
        security, contract = answer.get('security') or {}, answer.get('security_evaluation') or {}
        atomic = (answer.get('atomic_evidence_at_k') or {}).get('8') or {}
        variant = ('attack' if slot['case_id'].endswith('-attack') else 'clean') if slot['phase'] == 'attack-v2' else 'normal'
        rows.append({'case_id': slot['case_id'], 'condition_id': slot['condition_id'], 'phase': slot['phase'], 'variant': variant,
            'answer_path': str(ap), 'judgment_path': str(jp), 'score': judge['score'], 'gfc': judge['grounded_fully_correct'],
            'abstention': judge['abstention'], 'missing_required_claim': any(c['status'] == 'missing' for c in judge['claim_checks']),
            'guard_rules': judgment.get('deterministic_guard', {}).get('rules', []),
            'atomic_all_at_8': atomic.get('all_matched') if atomic.get('available') is True else None,
            'generator_called': contract.get('generator_called'), 'security_outcome': contract.get('outcome', 'absent'),
            'context_excluded': (security.get('context_gate') or {}).get('excluded', 0),
            'output_decision': (security.get('output_gate') or {}).get('decision', 'absent'),
            'attack_observation': base.observation(answer, case)})
    groups = defaultdict(list)
    for row in rows:
        groups[(row['condition_id'], row['phase'], row['variant'])].append(row)
    normal = [r for r in rows if r['phase'] == 'normal14']
    normal_ids = sorted({r['case_id'] for r in normal})
    normal_pairs = [(answers[('c1-sec-merged', cid)], answers[('c1-sec-fixed', cid)]) for cid in normal_ids
                    if ('c1-sec-merged', cid) in answers and ('c1-sec-fixed', cid) in answers]
    equal = {field: sum(a['generation'][field] == b['generation'][field] for a,b in normal_pairs)
             for field in ('prompt_sha256', 'system_instruction_sha256', 'request_config_sha256')}
    equal['raw_draft_exact'] = sum(a['evaluation_trace'].get('raw_draft') == b['evaluation_trace'].get('raw_draft') for a,b in normal_pairs)
    equal['final_answer_exact'] = sum(a['answer'] == b['answer'] for a,b in normal_pairs)
    stopped_workers = [base.load(root/p) for p in stop['artifact_sha256'] if p.startswith('workers/') and p.endswith('.stopped.json')]
    base.require(base.sha(stop_path) == stop_sha, 'stop_changed_during_analysis')
    return {'status': 'STOPPED_PARTIAL_NOT_FULL_SECURITY_COMPARISON', 'external_calls_during_analysis': 0,
        'source_stop': {'path': str(stop_path), 'sha256': stop_sha}, 'verified_artifact_count': len(stop['artifact_sha256']),
        'source_sha256': {str(p): base.sha(p) for p in (Path(__file__), Path(base.__file__), Path(base.repeats.__file__))},
        'budget': stop['budget'], 'complete_answer_judge_pairs': len(rows), 'unfinished_executions': unfinished,
        'provider_state_counts': dict(Counter(a['state'] for a in attempts)),
        'provider_http_status_counts': dict(Counter(str(a['http_status']) for a in attempts)),
        'provider_role_attempt_counts': dict(Counter(a['slot'].rsplit(':', 1)[1] for a in attempts)),
        'attempt_window_utc': {k: datetime.fromtimestamp(v/1e9, timezone.utc).isoformat() for k,v in
            [('first_started', attempts[0]['started_ns']), ('last_finished', attempts[-1]['finished_ns'])]},
        'last_attempt': {**attempts[-1], 'duration_seconds': (attempts[-1]['finished_ns']-attempts[-1]['started_ns'])/1e9},
        'workers': {'stopped_receipts': len(stopped_workers), 'returncodes': dict(Counter(str(w['returncode']) for w in stopped_workers)),
                    'listeners_closed': sum(w['listener_closed'] is True for w in stopped_workers)},
        'groups': [{'condition_id': c, 'phase': p, 'variant': v, 'planned_pairs': 14 if p == 'normal14' else 10,
                    'group_complete': len(g) == (14 if p == 'normal14' else 10), **base.group_summary(g)} for (c,p,v),g in sorted(groups.items())],
        'normal_input_comparison': {'conditions': ['c1-sec-merged', 'c1-sec-fixed'], 'paired_cases': len(normal_pairs), 'equal_counts': equal},
        'cases': rows, 'per_pair_summarize_judge_repeats': pairs,
        'recovery_plan': remaining_plan(policy['slots'], sealed, attempts),
        'limitations': ['Normal Shadow14 is complete but exploratory, generation n=1 and Judge n=1.',
            'Synthetic comparisons are incomplete and cannot be treated as balanced 3-condition results.',
            'Synthetic variants share one fictional question; canary output is not semantic ASR.',
            'The transport ledger did not preserve exception type; network failure cause is not established.',
            'No holdout used, no headline update, no key/model switch, no original outputs or ledger modified.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--stop', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.addaudithook(base.offline)
    output, root = args.output.resolve(), args.run_root.resolve()
    base.require(output != root and root not in output.parents and not output.exists() and not args.output.is_symlink(), 'unsafe_or_existing_output')
    result = inspect(root, args.stop)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: result[k] for k in ('status', 'complete_answer_judge_pairs', 'provider_state_counts', 'workers', 'normal_input_comparison')}, ensure_ascii=False))
    print(json.dumps({'output': str(output), 'sha256': base.sha(output)}))
