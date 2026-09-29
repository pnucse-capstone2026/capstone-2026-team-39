"""Read-only summary of a completed, sealed security pilot; never calls a model."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import summarize_judge_repeats as repeats


def offline(event, args):
    if event.startswith(('socket.', 'urllib.')):
        raise RuntimeError('analysis_network_disabled')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = Path(args[0].decode() if isinstance(args[0], bytes) else args[0])
        if path.name.startswith('.env') or ('holdout' in str(path).lower() and path.suffix != '.py'):
            raise RuntimeError('protected_data_disabled')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require(ok, code):
    if not ok:
        raise ValueError(code)


def load(path):
    return json.loads(Path(path).read_text())


def bool_rate(values):
    known = [v for v in values if type(v) is bool]
    return {'true': sum(known), 'observed': len(known), 'missing': len(values)-len(known),
            'rate': sum(known)/len(known) if known else None}


def observation(answer, case):
    scenario = case.get('security_scenario')
    if not scenario:
        return {}
    marker = scenario['marker']
    trace = answer.get('evaluation_trace') or {}
    raw = (trace.get('retrieval_stages') or {}).get('raw_bm25')
    prompt = (trace.get('generation_input') or {}).get('user_prompt')
    draft = trace.get('raw_draft')
    final = [answer.get('answer', ''), answer.get('cited_answer', '')]
    final.extend(c.get('text', '') for c in answer.get('claims', []) if isinstance(c, dict))
    return {
        'raw_retrieval_exposure': any(r.get('chunk_id') == scenario['mutable_chunk_id'] for r in raw) if isinstance(raw, list) else None,
        'generation_prompt_canary': marker in prompt if isinstance(prompt, str) else None,
        'raw_draft_canary': marker in draft if isinstance(draft, str) else None,
        'final_canary_emission': any(marker in text for text in final if isinstance(text, str)),
        'artifact_source_metadata_canary': marker in json.dumps(answer.get('sources', []), ensure_ascii=False),
        'semantic_attack_success': None,
    }


def group_summary(rows):
    n = len(rows)
    require(n > 0, 'empty_group')
    counts = Counter(rule for r in rows for rule in set(r['guard_rules']))
    result = {
        'pairs': n, 'gfc_count': sum(r['gfc'] for r in rows),
        'gfc_rate': sum(r['gfc'] for r in rows)/n,
        'mean_score_0_to_2': sum(r['score'] for r in rows)/n,
        'inappropriate_abstention': sum(r['abstention'] == 'inappropriate' for r in rows),
        'missing_required_claim': sum(r['missing_required_claim'] for r in rows),
        'atomic_all_at_8': bool_rate([r['atomic_all_at_8'] for r in rows]),
        'generator_called': bool_rate([r['generator_called'] for r in rows]),
        'context_excluded_cases': sum(r['context_excluded'] > 0 for r in rows),
        'output_decisions': dict(Counter(r['output_decision'] for r in rows)),
        'security_outcomes': dict(Counter(r['security_outcome'] for r in rows)),
        'judge_guard_case_counts': dict(sorted(counts.items())),
    }
    observations = [r['attack_observation'] for r in rows if r['attack_observation']]
    if observations:
        result['attack_observations'] = {
            field: bool_rate([o[field] for o in observations])
            for field in observations[0] if field != 'semantic_attack_success'
        }
        result['semantic_attack_success'] = None
    return result


def summarize(run_root):
    root = Path(run_root).resolve(strict=True)
    completion_path = root / 'completion.json'
    completion_sha = sha(completion_path)
    completion = load(completion_path)
    require(completion['status'] in ('LIVE_PILOT_COMPLETE', 'MOCK_COMPLETE_NOT_LLM_EVAL'), 'run_not_complete')
    # Complete-only: do not read or modify a ledger while a run is executing.
    for relative, pin in completion['artifact_sha256'].items():
        path = (root / relative).resolve(strict=True)
        require(root in path.parents and sha(path) == pin, 'completion_artifact_mismatch')
    policy = load(root / 'policy.json')
    require(completion['budget']['sealed_slots'] == len(policy['slots']), 'incomplete_slots')
    with sqlite3.connect((root / 'provider-attempts.sqlite').as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        sealed = {r['id']: r['sealed_sha'] for r in db.execute('SELECT id,sealed_sha FROM slots')}
        attempts = [dict(r) for r in db.execute('SELECT slot,state,http_status FROM attempts ORDER BY id')]
    require(all(sealed.values()) and len(sealed) == len(policy['slots']), 'unsealed_slots')
    require(all(a['state'] != 'reserved' for a in attempts), 'unresolved_attempts')
    require(len(attempts) == completion['budget']['total_provider_attempts'], 'attempt_count_mismatch')
    pair_paths, rows, verified_pairs, case_cache = {}, [], [], {}
    for slot in policy['slots']:
        sid = slot['slot_id']
        suffix = '.answers.jsonl' if slot['role'] == 'generation' else '.judgments.jsonl'
        path = root / 'results' / (hashlib.sha256(sid.encode()).hexdigest()+suffix)
        require(sha(path) == sealed[sid], 'sealed_artifact_changed')
        pair_paths.setdefault(sid.rsplit(':', 1)[0], {})[slot['role']] = (path, slot)
    for pair in pair_paths.values():
        require(set(pair) == {'generation', 'judge'}, 'missing_pair')
        ap, slot = pair['generation']
        jp, _ = pair['judge']
        payload, repeated_rows = repeats.aggregate_repeats(answer_path=ap, judgment_paths=[jp])
        require(len(repeated_rows) == 1 and payload['summary']['judge_repeat_count'] == 1, 'expected_single_pair')
        answer, judgment = load(ap), load(jp)
        case_file = slot['case_file']
        cache_key = (case_file['path'], case_file['sha256'])
        if cache_key not in case_cache:
            require(sha(case_file['path']) == case_file['sha256'], 'case_file_changed')
            case_cache[cache_key] = {c['id']: c for c in (json.loads(line) for line in Path(case_file['path']).read_text().splitlines() if line)}
        case = case_cache[cache_key][slot['case_id']]
        judge = judgment['judge']
        require(repeated_rows[0]['score_mean'] == judge['score'] and repeated_rows[0]['gfc_majority'] == judge['grounded_fully_correct'], 'repeat_summary_disagrees')
        security = answer.get('security') or {}
        contract = answer.get('security_evaluation') or {}
        atomic = (answer.get('atomic_evidence_at_k') or {}).get('8') or {}
        variant = ('attack' if slot['case_id'].endswith('-attack') else 'clean') if slot['phase'] == 'attack-v2' else 'normal'
        rows.append({
            'case_id': slot['case_id'], 'condition_id': slot['condition_id'], 'phase': slot['phase'], 'variant': variant,
            'answer_path': str(ap), 'judgment_path': str(jp), 'score': judge['score'], 'gfc': judge['grounded_fully_correct'],
            'abstention': judge['abstention'], 'missing_required_claim': any(c['status'] == 'missing' for c in judge['claim_checks']),
            'guard_rules': judgment.get('deterministic_guard', {}).get('rules', []),
            'atomic_all_at_8': atomic.get('all_matched') if atomic.get('available') is True else None,
            'generator_called': contract.get('generator_called'), 'security_outcome': contract.get('outcome', 'absent'),
            'context_excluded': (security.get('context_gate') or {}).get('excluded', 0),
            'output_decision': (security.get('output_gate') or {}).get('decision', 'absent'),
            'attack_observation': observation(answer, case),
        })
        verified_pairs.append(payload)
    groups = defaultdict(list)
    for row in rows:
        groups[(row['condition_id'], row['phase'], row['variant'])].append(row)
    require(sha(completion_path) == completion_sha, 'completion_changed')
    return {
        'status': 'LIVE_PILOT_SUMMARY' if completion['execution_mode'] == 'live' else 'MOCK_ONLY_NOT_PERFORMANCE',
        'completion': {'path': str(completion_path), 'sha256': completion_sha},
        'analysis_source_sha256': sha(__file__), 'repeat_summarizer_sha256': sha(repeats.__file__),
        'budget': completion['budget'], 'provider_http_status_counts': dict(Counter(str(a['http_status']) for a in attempts)),
        'provider_state_counts': dict(Counter(a['state'] for a in attempts)),
        'methodology': {'independent_generations': 1, 'judge_repeats': 1, 'holdout_used': False,
            'headline_updated': False, 'normal_shadow_sample_n': len({r['case_id'] for r in rows if r['phase'] == 'normal14'}),
            'synthetic_attack_question_count': 1, 'canary_emission_is_semantic_ASR': False,
            'note': 'Development pilot only; normal and synthetic denominators remain separate. No significance or latency superiority claim. Mock scores are never performance evidence.'},
        'groups': [{'condition_id': c, 'phase': p, 'variant': v, **group_summary(g)} for (c,p,v),g in sorted(groups.items())],
        'cases': rows, 'per_pair_summarize_judge_repeats': verified_pairs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.addaudithook(offline)
    output = args.output.resolve()
    root = args.run_root.resolve()
    require(root != output and root not in output.parents, 'output_must_be_outside_original_run')
    require(not args.output.is_symlink() and not output.exists(), 'output_exists_or_symlink')
    result = summarize(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x') as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'status': result['status'], 'pairs': len(result['cases']), 'output': str(output), 'sha256': sha(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
