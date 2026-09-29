"""Read-only automatic-evaluation checkpoints for the amended 1-reviewer protocol.

No provider requests, no service edits, no fabricated human or Judge labels.
Reports are new immutable files. Run1 is explicitly intermediate, not final n=3.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import json
from pathlib import Path
import sqlite3
from statistics import mean
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'evidence/final-single-reviewer-20260914-v1'))
import runner_v3 as r

collect, _, judge_module, artifacts = r.modules()
sys.path.insert(0, str(ROOT / 'scripts'))
import analyze_final_generation_gfc as stats
import summarize_judge_repeats as repeats
import analyze_generation_failures as failures

SEED = 20260914


def ledger_snapshot():
    with sqlite3.connect((r.RUN / 'provider-attempts.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as db:
        return {x[0]: {'state': x[1], 'sha256': x[2]} for x in db.execute('SELECT id,state,artifact_sha FROM slots')}


def checkpoint_ready(plan, states, n):
    r.require(n in (1, 3), 'unsupported_checkpoint')
    return all(states[s['slot_id']]['state'] == 'complete' for s in plan['slots'][:126 * n])


def group_metrics(rows):
    valid = [x for x in rows if x['score'] is not None]
    known = [x for x in rows if x['gfc'] is not None]
    success = sum(x['gfc'] is True for x in rows)
    errors = sum(x['service_error'] for x in rows)
    judge_errors = sum(x['judge_error'] for x in rows)
    available_evidence = [x for x in rows if x.get('all_evidence_at8') is not None]
    source = [x for x in rows if x.get('source_hit5') is not None]
    return {'planned_slots': len(rows), 'generated_answers': len(rows) - errors,
        'service_errors': errors, 'valid_judgments': len(valid), 'judge_errors': judge_errors,
        'gfc_successes': success, 'known_gfc_outcomes': len(known),
        'gfc_rate_all_planned': success / len(rows) if len(known) == len(rows) else None,
        'gfc_rate_bounds_all_planned': [success / len(rows), (success + len(rows) - len(known)) / len(rows)],
        'valid_output_gfc_rate': success / len(valid) if valid else None,
        'valid_judgment_score_mean': mean(x['score'] for x in valid) if valid else None,
        'source_hit5': {'hits': sum(x['source_hit5'] for x in source), 'available': len(source)},
        'all_atomic_evidence_at8': {'hits': sum(x['all_evidence_at8'] for x in available_evidence), 'available': len(available_evidence)},
        'generation_latency_ms_mean': mean(x['latency_ms'] for x in rows if x.get('latency_ms') is not None) if any(x.get('latency_ms') is not None for x in rows) else None,
        'failure_causes_multilabel': dict(Counter(c for x in rows for c in x.get('failure_causes', []))),
        'guard_counts': dict(Counter(c for x in rows for c in x.get('guard_rules', [])))}


def paired_metrics(rows, families, n, *, iterations=10000):
    core = [x for x in rows if x['split'] == 'holdout-core']
    ids = sorted({x['case_id'] for x in core})
    cells = {(q, c): [x for x in core if x['case_id'] == q and x['condition'] == c] for q in ids for c in ('c0', 'c1')}
    r.require(all(len(v) == n for v in cells.values()), 'unbalanced_case_repeats')
    if any(x['gfc'] is None for x in core):
        return {'available': False, 'reason': 'Invalid/missing Judge outcome; no imputation or silent case removal',
            'question_count': len(ids), 'generation_repeats': n, 'unknown_outcomes': sum(x['gfc'] is None for x in core)}
    values = {c: {q: mean(int(x['gfc']) for x in cells[q, c]) for q in ids} for c in ('c0', 'c1')}
    deltas = {q: values['c1'][q] - values['c0'][q] for q in ids}
    family_map = {q: families[q] for q in ids}
    grouped = {}
    for q in ids: grouped.setdefault(family_map[q], []).append(deltas[q])
    # One sign per family, weighting the observed effect by question counts.
    family_sums = [sum(v) for v in grouped.values()]
    majority = {c: [values[c][q] > .5 for q in ids] for c in ('c0', 'c1')}
    return {'available': True, 'question_count': len(ids), 'generation_repeats': n,
        'family_count': len(grouped), 'c0_gfc_rate': mean(values['c0'].values()),
        'c1_gfc_rate': mean(values['c1'].values()), 'delta_c1_minus_c0': mean(deltas.values()),
        'family_bootstrap': stats.family_cluster_bootstrap(deltas, family_map, iterations=iterations, seed=SEED),
        'family_sign_flip': stats.paired_sign_flip(family_sums, iterations=100000, seed=SEED),
        'majority_sensitivity': {'threshold': 'at least 2 of 3' if n == 3 else 'single run; not a 3-run majority',
            'c0_successes': sum(majority['c0']), 'c1_successes': sum(majority['c1']),
            'mcnemar': stats.exact_mcnemar(majority['c0'], majority['c1'])}}


def load_observations(plan, n):
    r.verify(plan, full=True)
    states = ledger_snapshot()
    r.require(checkpoint_ready(plan, states, n), 'checkpoint_not_complete')
    cases = {c['id']: c for c in r.load_cases()}
    selected = plan['slots'][:126 * n]
    by_key = {(s['case_id'], s['group'], s['phase']): s for s in selected}
    pins, records, rows, checks = {}, {}, [], {}
    for slot in selected:
        path = r.RUN / 'slots' / f"{slot['ordinal']:04d}.json"
        actual = r.sha(path)
        r.require(actual == states[slot['slot_id']]['sha256'], 'slot_hash_mismatch')
        pins[str(path)] = actual
        records[slot['ordinal']] = r.read(path)
    for group in sorted({s['group'] for s in selected}):
        path = r.verify_answer_group(plan, group)
        pins[str(path)] = r.sha(path)
        jslots = [s for s in selected if s['group'] == group and s['phase'] == 'judge' and s['ordinal'] not in plan['skip_judge_ordinals']]
        jpath = r.RUN / 'judge' / f'{group}-judge-v11-r1.jsonl'
        expected = b''.join((r.RUN / 'slots' / f"{s['ordinal']:04d}.json").read_bytes() for s in jslots)
        r.require(jpath.read_bytes() == expected, 'judgment_stream_mismatch')
        pins[str(jpath)] = r.sha(jpath)
        if all(not records[s['ordinal']].get('error') for s in jslots):
            result, _ = repeats.aggregate_repeats(answer_path=path, judgment_paths=[jpath])
            checks[group] = result['summary']
        else:
            checks[group] = {'status': 'not_comparable_due_to_judge_errors'}
    for slot in selected:
        if slot['phase'] != 'generation': continue
        case, answer = cases[slot['case_id']], records[slot['ordinal']]
        row = {'case_id': case['id'], 'family_id': case.get('family_id') or case['id'], 'split': case['split'],
            'condition': slot['condition_id'], 'run': slot['generation_run_id'], 'group': slot['group'],
            'service_error': slot['ordinal'] in plan['terminal_generation_ordinals'],
            'judge_error': False, 'score': None, 'gfc': None, 'source_hit5': None,
            'all_evidence_at8': None, 'failure_causes': [], 'guard_rules': []}
        jslot = by_key[case['id'], slot['group'], 'judge']
        judgment = records[jslot['ordinal']]
        if row['service_error']:
            r.require(answer['record_type'] == 'operational_generation_failure' and answer['provider_http_status'] == 429, 'invalid_terminal_provenance')
            r.require(judgment['record_type'] == 'judge_not_called' and judgment['linked_generation_ordinal'] == slot['ordinal'], 'invalid_terminal_judge_skip')
            row['gfc'] = False
            row['failure_causes'] = ['operational_provider429']
        else:
            artifacts.validate_answer_record(answer)
            artifacts.validate_judgment_record(judgment)
            judge_module.validate_answer_case(answer, case)
            for field in ('answer_id', 'answer_sha256', 'case_id', 'experiment_id', 'condition_id', 'generation_run_id'):
                r.require(answer[field] == judgment[field], 'answer_judge_binding_mismatch:' + field)
            r.require(judgment['answer_record_sha256'] == artifacts.sha256_json(answer), 'answer_record_binding_mismatch')
            r.require(judgment['answers_artifact_sha256'] == pins[str(r.RUN / 'answers' / (slot['group'] + '.answers.jsonl'))], 'answers_artifact_binding_mismatch')
            r.require(judgment['judge_config_sha256'] == plan['judge_config']['judge_config_sha256'], 'judge_config_changed')
            ji = judge_module.build_judge_input(case, answer)
            r.require(judgment['judge_input_sha256'] == artifacts.sha256_json(ji), 'judge_input_changed')
            payload = judgment['judge']
            row['judge_error'] = bool(judgment.get('error')) or type(payload.get('score')) is not int or payload.get('score') not in (0, 1, 2) or type(payload.get('grounded_fully_correct')) is not bool
            if not row['judge_error']:
                r.require(not payload['grounded_fully_correct'] or payload['score'] == 2, 'invalid_gfc_score')
                row.update(score=payload['score'], gfc=payload['grounded_fully_correct'], failure_causes=failures.failure_causes(judgment), guard_rules=failures._guard_rules(judgment))
            row['latency_ms'] = answer.get('latency_ms')
            row['source_hit5'] = collect.retrieval_hit(answer['sources'], case.get('expected', {}), 5)['matched']
            atomic = answer.get('atomic_evidence_at_k', {}).get('8', {})
            if atomic.get('available'): row['all_evidence_at8'] = atomic['all_matched']
        rows.append(row)
    for group, expected in checks.items():
        if expected.get('status'): continue
        actual = group_metrics([x for x in rows if x['group'] == group and not x['service_error']])
        r.require(actual['gfc_successes'] == expected['question_level_majority_gfc_true_count'], 'gfc_summary_mismatch')
        r.require(abs(actual['valid_judgment_score_mean'] - expected['mean_score']) < 1e-12, 'mean_summary_mismatch')
    return rows, pins, checks


def render(payload):
    n = payload['generation_repeats']
    lines = [f'# 최종평가 자동 집계: 생성 {n}회', '',
        '**자동 Judge 결과이며 사람 답변 채점·일치도 검증은 미완료다.**', '',
        '1회차 중간 결과다. 최종 3회 결과와 구분한다.' if n == 1 else '사전에 계획한 3회 생성 슬롯을 모두 포함한다. 오류·유효 판정 수를 별도 표시한다.', '',
        '| 집합·조건 | 예정 슬롯 | 정상 출력 | 운영 오류 | 유효 Judge | GFC 성공 | GFC/예정 | 평균점수(유효 Judge, 0~2) |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name, result in payload['conditions'].items():
        rate = result['gfc_rate_all_planned']
        score = result['valid_judgment_score_mean']
        lines.append(f"| {name} | {result['planned_slots']} | {result['generated_answers']} | {result['service_errors']} | {result['valid_judgments']} | {result['gfc_successes']} | {rate:.1%} | {score:.4f} |" if rate is not None and score is not None else f"| {name} | {result['planned_slots']} | {result['generated_answers']} | {result['service_errors']} | {result['valid_judgments']} | {result['gfc_successes']} | 판정 불완전 | {score} |")
    paired = payload['core_paired']
    lines += ['', '주지표는 GFC이며 0~2점 평균은 보조 지표다. Core와 Challenge는 합산하지 않는다.', '']
    if paired['available']:
        low, high = paired['family_bootstrap']['ci95']
        delta = paired['delta_c1_minus_c0']
        lines += [f"Core C1−C0 GFC 차이: {delta * 100:+.2f}%p, family bootstrap 95% CI [{low * 100:+.2f}, {high * 100:+.2f}]%p.",
            f"독립 문항 {paired['question_count']}개, family {paired['family_count']}개. 3회 출력은 독립 표본 수를 3배로 늘리지 않는다.",
            '신뢰구간이 0을 포함하므로 성능 향상이 확정됐다고 말할 수 없다.' if low <= 0 <= high else '이 차이는 아직 사람과 보정하지 않은 자동평가 안의 결과다. 일반적 성능 향상이나 기존 2인 gate 통과를 뜻하지 않는다.']
    else:
        lines += ['유효하지 않은 Judge 판정이 있어 전체 Core 쌍 비교를 확정하지 않았다. 상세 JSON의 분모·범위를 확인한다.']
    lines += ['', 'HTTP429 뒤 발생한 추출형 fallback 1건은 요청 모델의 정상 답변에 섞지 않았다. 운영 실패 GFC=0으로 포함하며 가짜 Judge 점수는 만들지 않았다.',
        '사람 답변 검수는 예정63슬롯 중 실제 출력62개 대상이며, 정답지36개 검수와 별개다. 현재 사람 라벨·Judge 일치도 결과 없음.',
        '', f"계획 SHA-256: `{payload['plan_sha256']}`", '정본 수치와 입력 해시는 같은 폴더의 `summary.json` 및 `inventory.json`에 있다.', '']
    return '\n'.join(lines)


def write_checkpoint(n):
    plan = r.read(r.PLAN)
    rows, pins, checks = load_observations(plan, n)
    families = {x['case_id']: x['family_id'] for x in rows}
    metrics = {f'{split}/{condition}': group_metrics([x for x in rows if x['split'] == split and x['condition'] == condition])
        for split, condition in [('holdout-core', 'c0'), ('holdout-core', 'c1'), ('holdout-challenge', 'c1')]}
    payload = {'schema_version': 'pnu.single-reviewer-auto-summary.v1',
        'status': 'INTERMEDIATE_RUN1' if n == 1 else 'ALL_THREE_GENERATION_RUNS_ACCOUNTED',
        'created_at': r.now(), 'generation_repeats': n, 'judge_repeats_per_answer': 1,
        'human_calibrated': False, 'original_two_reviewer_gate_passed': False,
        'plan_sha256': r.sha(r.PLAN), 'conditions': metrics,
        'core_paired': paired_metrics(rows, families, n),
        'per_run_groups': {group: group_metrics([x for x in rows if x['group'] == group]) for group in sorted({x['group'] for x in rows})},
        'summarize_judge_repeats_cross_checks': checks, 'observations': rows,
        'input_hashes': pins, 'analysis_code_hashes': {str(x): r.sha(x) for x in HERE.glob('*.py')},
        'statistical_helpers_sha256': r.sha(ROOT / 'scripts/analyze_final_generation_gfc.py'),
        'latency_note': 'Captured collector wall time includes rate-limit waiting; not an unthrottled service latency benchmark.'}
    output = r.BASE / 'analysis' / f'run{n}-v1'
    output.mkdir(parents=True, exist_ok=False)
    r.publish(output / 'summary.json', r.canonical(payload) + b'\n')
    r.publish(output / 'report.md', render(payload).encode())
    r.publish(output / 'inventory.json', r.canonical({x.name: r.sha(x) for x in output.iterdir() if x.is_file()}) + b'\n')
    print(json.dumps({'status': payload['status'], 'path': str(output), 'sha256': r.sha(output / 'summary.json')}), flush=True)


def watch():
    plan = r.read(r.PLAN)
    for _ in range(1440):  # up to six hours, no provider activity
        states = ledger_snapshot()
        for n in (1, 3):
            output = r.BASE / 'analysis' / f'run{n}-v1'
            if checkpoint_ready(plan, states, n) and not output.exists(): write_checkpoint(n)
        if (r.BASE / 'analysis/run3-v1/inventory.json').exists(): return
        if (r.RUN / 'quota-stop.json').exists():
            print('WATCH_STOPPED_PROVIDER429', flush=True); return
        with (r.RUN / 'run.lock').open('rb') as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: pass
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                print('WATCH_STOPPED_RUNNER_NOT_ACTIVE', flush=True); return
        time.sleep(15)
    raise RuntimeError('watch_deadline_reached')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('watch', 'run1', 'run3'))
    args = parser.parse_args()
    if args.mode == 'watch': watch()
    else: write_checkpoint(int(args.mode[-1]))
