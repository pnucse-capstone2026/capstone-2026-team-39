"""Pinned normal14 + attack v2 -> NEW offline runtime-guard policy, no API."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime_guard import GuardError, canonical, digest, file_sha, verify_file, verify_snapshot

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'processed/eval/preflight-20260908'
NORMAL = BASE / 'security-pilot-preparation-v1/preparation.json'
ATTACK = BASE / 'security-attack-preparation-v2/manifest.json'
NORMAL_SHA = '5cddcf2a9cc69f9ee702c078db0d0fe3b74aae3f66f3919a45f8177c10bc66ca'
ATTACK_SHA = 'a1b39cb042e1c930a48268973c34f48826cbcdcb00932f1010ca79bb06445eb5'


def require(condition, message):
    if not condition:
        raise GuardError(message)


def build_policy():
    verify_file(NORMAL, NORMAL_SHA)
    verify_file(ATTACK, ATTACK_SHA)
    normal = json.loads(NORMAL.read_bytes())
    attack = json.loads(ATTACK.read_bytes())
    snapshots = {name: verify_snapshot(value) for name, value in normal['code_conditions'].items()}
    pins = {str(NORMAL): NORMAL_SHA, str(ATTACK): ATTACK_SHA,
            normal['normal_cases_path']: normal['normal_cases_sha256'],
            attack['cases_path']: attack['cases_sha256'], **normal['source_input_sha256']}
    for fixture in attack['fixtures']:
        for field, sha_field in [('index_path', 'index_sha256'), ('case_path', 'case_file_sha256'),
                                 ('documents_path', 'documents_sha256')]:
            pins[fixture[field]] = fixture[sha_field]
    for path, sha in pins.items():
        verify_file(path, sha)
    require(digest(canonical(normal['normal_schedule'])) == normal['normal_schedule_sha256'], 'normal_schedule_pin')
    require(digest(canonical(attack['schedule'])) == attack['schedule_sha256'], 'attack_schedule_pin')
    require(len(normal['normal_schedule']) == 42 and len(attack['schedule']) == 60, 'schedule_size')
    controls = normal['common_controls']
    index_path = next(p for p in normal['source_input_sha256'] if p.endswith('.sqlite'))
    fixtures = {f['case_id']: f for f in attack['fixtures']}
    slots = []
    for phase, schedule in [('normal14', normal['normal_schedule']), ('attack-v2', attack['schedule'])]:
        for row in schedule:
            if phase == 'normal14':
                index = {'path': index_path, 'sha256': pins[index_path],
                         'corpus_revision': controls['corpus_revision']}
                case_file = {'path': normal['normal_cases_path'], 'sha256': normal['normal_cases_sha256']}
            else:
                fixture = fixtures[row['case_id']]
                require(row['index_sha256'] == fixture['index_sha256'], 'attack_slot_index_pin')
                index = {'path': fixture['index_path'], 'sha256': fixture['index_sha256'],
                         'corpus_revision': fixture['corpus_revision']}
                case_file = {'path': fixture['case_path'], 'sha256': fixture['case_file_sha256']}
            for role in ('generation', 'judge'):
                slots.append({
                    'slot_id': ':'.join((phase, row['condition_id'], row['generation_run_id'], row['case_id'], role)),
                    'phase': phase, 'role': role, 'condition_id': row['condition_id'],
                    'case_id': row['case_id'], 'case_sha256': row['case_sha256'],
                    'generation_run_id': row['generation_run_id'], 'generation_ordinal': row['ordinal'],
                    'judge_run_id': 'security-pilot-judge-v11-r1' if role == 'judge' else None,
                    'model': controls['model'] if role == 'generation' else controls['judge']['model'],
                    'attempt_cap': 3 if role == 'generation' else 6,
                    'timeout_ceiling': 120 if role == 'generation' else 180,
                    'index': index, 'case_file': case_file,
                })
    require(len(slots) == 204 and len({s['slot_id'] for s in slots}) == 204, 'slot_identity')
    require(sum(s['attempt_cap'] for s in slots) == 918, 'combined_cap')
    return {
        'schema': 'pnu.security-pilot-provider-policy.v1',
        'experiment_id': 'security-normal14-attack-v2-20260908',
        'api_execution_authorized': False, 'live_runner_ready': False,
        'status': 'offline_components_only', 'total_cap': 918,
        'slots': slots, 'controls': controls, 'environment': normal['nonsecret_environment'],
        'conditions': normal['code_conditions'], 'verified_snapshots': snapshots, 'input_sha256': pins,
        'resume_policy': 'same DB and policy; unfinished reservation requires explicit no-refund reconciliation',
        'limitation': 'Owned-process launcher and strict collector/Judge finalization are not integrated. Do not run live.',
    }


def write_new_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Output exists: use a new path; never overwrite an earlier policy.')
    policy = build_policy()
    args.output.mkdir(parents=True, exist_ok=False)
    write_new_json(args.output / 'policy.json', policy)
    report = {'status': 'OFFLINE_PREPARATION_NOT_LLM_RESULT', 'external_llm_calls': 0,
              'server_starts': 0, 'holdout_read': False, 'live_runner_ready': False,
              'policy_file_sha256': file_sha(args.output / 'policy.json'),
              'policy_canonical_sha256': digest(canonical(policy)),
              'generation_slots': 102, 'judge_slots': 102, 'provider_attempt_cap': 918,
              'verified_snapshots': policy['verified_snapshots'],
              'verified_input_files': len(policy['input_sha256']),
              'tool_sha256': {p.name: file_sha(p) for p in Path(__file__).parent.glob('*.py')}}
    write_new_json(args.output / 'preparation.json', report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
