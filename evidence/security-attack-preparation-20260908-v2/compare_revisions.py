"""Seal the v1/v2 fixture delta and offline a09 exposure comparison."""
from __future__ import annotations

import json
from pathlib import Path

import prepare_attack as prep


def read_json(path, expected=None):
    if expected is not None and prep.file_sha(path) != expected:
        raise ValueError(f'fingerprint mismatch: {path}')
    return json.loads(Path(path).read_bytes())


def check_fixtures(manifest):
    for fixture in manifest['fixtures']:
        for path_key, sha_key in (('index_path', 'index_sha256'),
                                   ('documents_path', 'documents_sha256'),
                                   ('case_path', 'case_file_sha256')):
            if prep.file_sha(fixture[path_key]) != fixture[sha_key]:
                raise ValueError(f'fixture changed: {fixture[path_key]}')


def main():
    output = prep.OUT / 'revision-comparison.json'
    if output.exists():
        raise SystemExit('refusing to overwrite revision comparison')
    v1_root = prep.OUT.parent / 'security-attack-preparation-v1'
    v1 = read_json(v1_root / 'manifest.json', 'a843147aaa546ea3fbd6fa3d5befb47377de331d990f0c630e977189a88e0d2a')
    v2 = read_json(prep.OUT / 'manifest.json', 'a1b39cb042e1c930a48268973c34f48826cbcdcb00932f1010ca79bb06445eb5')
    v1_verification = read_json(v1_root / 'verification-v2/verification.json',
                                'b208b59a6715ffb2579e8ca04fbbdf9da0aa644d9ca3112ef3e6b3e2ae0d3e76')
    v2_verification = read_json(prep.OUT / 'verification-v1/verification.json')
    assert v2_verification['successful']
    assert v2_verification['manifest_sha256'] == prep.file_sha(prep.OUT / 'manifest.json')
    check_fixtures(v1)
    check_fixtures(v2)
    for filename, expected in v1_verification['source_sha256'].items():
        assert prep.file_sha(prep.HERE.parent / 'security-attack-preparation-20260908-v1' / filename) == expected
    for filename, expected in v2_verification['source_sha256'].items():
        assert prep.file_sha(prep.HERE / filename) == expected
    assert prep.file_sha(prep.WORK / 'docs/security-attack-preparation-20260908.md') == 'e01b8ea7cfb3e5d06e7530d4ab91cee67ba35f34336388b9c5ebb630268ce5ba'
    assert prep.file_sha(v1_root / 'cases.jsonl') == v1['cases_sha256']
    assert prep.file_sha(prep.OUT / 'cases.jsonl') == v2['cases_sha256']
    by_v1 = {row['case_id']: row for row in v1['fixtures']}
    by_v2 = {row['case_id']: row for row in v2['fixtures']}
    assert by_v1.keys() == by_v2.keys()
    changed_documents = []
    changed_cases = []
    for case_id in by_v1:
        old, new = by_v1[case_id], by_v2[case_id]
        if old['documents_sha256'] != new['documents_sha256']:
            changed_documents.append(case_id)
        if old['case_sha256'] != new['case_sha256']:
            changed_cases.append(case_id)
        old_case, new_case = read_json(old['case_path']), read_json(new['case_path'])
        assert old_case['query'] == new_case['query']
    assert changed_documents == ['secpilot-a09-attack']
    assert set(changed_cases) == {'secpilot-a09-clean', 'secpilot-a09-attack'}
    for key in ('attack_payloads', 'paired_clean_scenarios', 'unique_user_questions',
                'seed', 'condition_order', 'attack_budget', 'combined_normal_and_attack_budget'):
        assert v1[key] == v2[key], key
    def schedule_order(manifest):
        return [(row['ordinal'], row['condition_id'], row['case_id']) for row in manifest['schedule']]
    assert schedule_order(v1) == schedule_order(v2)
    cells = []
    unaffected_comparisons = 0
    for before_report, after_report in zip(v1_verification['handler_reports'], v2_verification['handler_reports']):
        assert before_report['condition'] == after_report['condition']
        before = read_json(prep.WORK / before_report['path'], before_report['sha256'])
        after = read_json(prep.WORK / after_report['path'], after_report['sha256'])
        before_rows = {row['case_id']: row for row in before['probes']}
        after_rows = {row['case_id']: row for row in after['probes']}
        for case_id in before_rows:
            if case_id.startswith('secpilot-a09-'):
                continue
            for key in ('contract', 'observations', 'stub_generator_calls'):
                assert before_rows[case_id][key] == after_rows[case_id][key], (case_id, key)
            unaffected_comparisons += 1
        old, new = before_rows['secpilot-a09-attack'], after_rows['secpilot-a09-attack']
        gate = (new['response'].get('security') or {}).get('context_gate') or {}
        cells.append({'condition_id': after_report['condition'],
                      'v1_title_prompt_canary': old['observations']['generation_prompt_canary'],
                      'v2_filename_prompt_canary': new['observations']['generation_prompt_canary'],
                      'v2_contexts_excluded': gate.get('excluded'),
                      'v2_source_panel_canary': new['observations']['source_panel_canary'],
                      'v2_stub_generator_calls': new['stub_generator_calls']})
    normal = read_json(prep.NORMAL, prep.NORMAL_SHA)
    for name, pin in normal['code_conditions'].items():
        root = Path(pin['root'])
        files = {str(path.relative_to(root)): prep.file_sha(path) for path in root.rglob('*') if path.is_file()}
        assert files == pin['files_sha256'], name
    for raw_path, expected in normal['source_input_sha256'].items():
        assert prep.file_sha(raw_path) == expected
    original_sha = {}
    for relative in ('scripts/search_api.py', 'scripts/bm25_search.py', 'scripts/rag/generators.py',
                     'scripts/judge_service_answers.py', 'scripts/evaluate_service_answers.py',
                     'scripts/service_eval_artifacts.py'):
        expected = normal['code_conditions']['c1-pre-security']['files_sha256'][relative]
        assert prep.file_sha(prep.WORK / relative) == expected
        original_sha[relative] = expected
    result = {
        'schema_version': 'pnu.security-attack-revision-comparison.v1',
        'resolved_finding': 'T1: a09 now exercises metadata actually rendered to generation input',
        'changed_document_sets': changed_documents, 'changed_case_objects': changed_cases,
        'unchanged_other_scenario_observation_pairs': unaffected_comparisons,
        'same_case_condition_order_and_budget': True, 'a09_comparison': cells,
        'v1_preserved': True, 'source_snapshots_preserved': True,
        'original_source_sha256': original_sha, 'original_inputs_preserved': True,
        'unit_tests': v2_verification['unit_tests'], 'handler_scenarios': v2_verification['handler_scenarios'],
        'external_llm_calls': 0, 'server_started': False, 'holdout_read': False,
        'limitations': ['In-process handler with canned generation, no actual LLM quality or attack-success result.',
                       'Runtime attestation and global retry-budget/resume enforcement remain pending.'],
        'input_sha256': {str(path): prep.file_sha(path) for path in (
            v1_root / 'manifest.json', v1_root / 'verification-v2/verification.json',
            prep.OUT / 'manifest.json', prep.OUT / 'verification-v1/verification.json')},
        'comparator_sha256': prep.file_sha(Path(__file__)),
    }
    prep.write_new(output, json.dumps(result, ensure_ascii=False, indent=2).encode() + b'\n')
    print(json.dumps({'a09_comparison': cells, 'v1_preserved': True,
                      'unchanged_other_scenario_observation_pairs': unaffected_comparisons,
                      'comparison_sha256': prep.file_sha(output)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
