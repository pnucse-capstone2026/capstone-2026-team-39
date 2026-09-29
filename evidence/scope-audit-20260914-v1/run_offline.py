"""Pinned DEV replay + synthetic ablations + old metric verification; API zero.

No arbitrary input path, holdout data, live mode, key loader, retry or resume.
Outputs are exclusive-create in one new versioned directory.
"""
from __future__ import annotations

from collections import Counter
import copy
import importlib.util
import io
import itertools
import json
from pathlib import Path
import sys
import unittest

import scope_audit as s
from test_scope_audit import fixture, run as audit_fixture, remove_fact_scope

HERE = Path(__file__).resolve().parent
OUT = s.ROOT / 'processed/eval/preflight-20260914/scope-audit-v1/offline-v1'
PARENT_OUT = s.ROOT / 'processed/eval/preflight-20260914/parent-bound-v1/offline-v1'
PARENT_INVENTORY_SHA = 'a3d25d42e526410ad87d72ae31967b6d8cded6d7ad0284ec655084e1769ae84b'
HISTORICAL = s.ROOT / 'processed/eval/preflight-20260913/scope-bound-c3-v1/analysis-v1'
FROZEN = {
    'evidence/parent-bound-20260914-v1/replay.py': '868c7ec00d84c6544664427513b12d300b0eefcc6172ddd82c925cbe28499931',
    'evidence/improvement-experiment-20260913-v1/analyze_live.py': '2932dea7296409c67118fe22089f23a644af5f0191bf5b4e1af0b7113d0aa208',
    'processed/eval/preflight-20260913/scope-bound-c3-v1/analysis-v1/summary.json': '402f7e411cca2764cbf79bd4631c11b469dd3da43c8f2be0d1364298b87ba4ce',
    'processed/eval/preflight-20260913/scope-bound-c3-v1/analysis-v1/input-sha256.json': 'b5007f347bfd4a759df8dbeb0fe60198a69c907cdc204e7b8b9869878cc38325',
}


def import_pinned(relative, name):
    path = s.ROOT / relative
    if s.p.r.prior.sha(path) != FROZEN[relative]:
        raise ValueError('frozen_module_changed')
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def input_set():
    previous = import_pinned('evidence/parent-bound-20260914-v1/replay.py', 'scope_previous_replay')
    pins, request, raw = previous.inputs()
    inventory_path = PARENT_OUT / 'output-sha256.json'
    if s.p.r.prior.sha(inventory_path) != PARENT_INVENTORY_SHA:
        raise ValueError('parent_inventory_changed')
    inventory = s.p.r.prior.read(inventory_path)
    if set(inventory) | {'output-sha256.json'} != {p.name for p in PARENT_OUT.iterdir()}:
        raise ValueError('parent_file_set_changed')
    pins.update({str(PARENT_OUT / name): value for name, value in inventory.items()})
    pins[str(inventory_path)] = PARENT_INVENTORY_SHA
    pins.update({str(s.ROOT / path): sha for path, sha in FROZEN.items()})
    for path, sha in pins.items():
        if s.p.r.prior.sha(path) != sha:
            raise ValueError('frozen_input_changed')
    pins.update(s.p.r.prior.read(HISTORICAL / 'input-sha256.json'))
    pins.update({str(path): s.p.r.prior.sha(path) for path in HERE.glob('*.py')})
    verify_pins(pins)
    return pins, request, raw


def verify_pins(pins):
    for path, expected in pins.items():
        if s.p.r.prior.sha(path) != expected:
            raise ValueError('analysis_input_changed')


def persisted_equal(current, stored):
    # JSON object keys are strings. The frozen summarize() returns integer
    # histogram keys; compare the same wire representation without changing data.
    return json.loads(json.dumps(current, allow_nan=False)) == stored


def ablations():
    presence = []
    for query, claim, evidence in itertools.product((False, True), repeat=3):
        req, ex, dec = fixture()
        if not query: ex['query_scope'] = []
        if not claim: ex['units'][0]['atoms'][0]['claim']['scope'] = {}
        if not evidence: remove_fact_scope(ex)
        result = audit_fixture(req, ex, dec)
        row = result['atom_audit'][0]
        presence.append({'query_scope_present': query, 'explicit_claim_scope_present': claim,
                         'evidence_scope_present': evidence, 'typed_status': row['typed_status'],
                         'typed_reason': row['typed_reason'],
                         'local_preconditions_satisfied': result['local_preconditions_satisfied']})
    subsets = []
    for mask in itertools.product((False, True), repeat=len(s.DIMENSIONS)):
        req, ex, dec = fixture()
        missing = {name for name, present in zip(s.DIMENSIONS, mask) if not present}
        ex['query_scope'] = [x for x in ex['query_scope'] if x['dimension'] not in missing]
        result = audit_fixture(req, ex, dec)
        subsets.append({'missing_query_dimensions': sorted(missing),
                        'typed_status': result['atom_audit'][0]['typed_status'],
                        'issues': result['issues'],
                        'local_preconditions_satisfied': result['local_preconditions_satisfied']})
    # Separate known false-accept demonstration. Never count it as model accuracy.
    req, ex, dec = fixture()
    ex['query_scope'] = [x for x in ex['query_scope'] if x['dimension'] != 'year']
    ex['units'][0]['atoms'][0]['claim']['scope'].pop('year')
    remove_fact_scope(ex, 'year')
    dec['dimensions']['year'].update(status='not_specified', quote=None)
    shared_omission = audit_fixture(req, ex, dec)
    return {'data_kind': 'synthetic_contract_inputs_not_model_outputs',
            'scope_presence_factorial': presence, 'query_dimension_subsets': subsets,
            'subset_combinations': len(subsets),
            'subset_local_ready': sum(row['local_preconditions_satisfied'] for row in subsets),
            'subset_held': sum(not row['local_preconditions_satisfied'] for row in subsets),
            'known_shared_false_absence': {
                'local_preconditions_satisfied': shared_omission['local_preconditions_satisfied'],
                'semantic_verified': False, 'description': 'Year appears in synthetic query and source '
                    'but is jointly omitted from declaration/query/claim/fact. Exact matching alone misses this.'},
            'external_calls': 0, 'benchmark_accuracy': None}


def old_metrics():
    module = import_pinned('evidence/improvement-experiment-20260913-v1/analyze_live.py', 'scope_old_metrics')
    old = s.p.r.prior.read(HISTORICAL / 'summary.json')
    rows = copy.deepcopy(old['rows'])
    verified = 0
    for row in rows:
        if row['status'] != 'valid':
            continue
        _, joined = module.aggregate_repeats(answer_path=Path(row['answer_path']),
                                             judgment_paths=[Path(row['judgment_path'])])
        if (len(joined) != 1 or joined[0]['case_id'] != row['case_id']
                or joined[0]['score_mean'] != row['score'] or joined[0]['gfc_majority'] != row['gfc']):
            raise ValueError('historical_answer_judgment_mismatch')
        verified += 1
    conditions = {name: module.summarize([row for row in rows if row['condition_id'] == name])
                  for name in module.CONDITIONS}
    paired = module.paired(rows)
    diagnostics = {name: module.diagnose([row for row in rows if row['condition_id'] == name])
                   for name in module.CONDITIONS}
    if (not persisted_equal(conditions, old['conditions'])
            or not persisted_equal(paired, old['paired_common_valid'])
            or not persisted_equal(diagnostics, old['diagnostics']) or verified != 80):
        raise ValueError('historical_aggregate_mismatch')
    # Only summary-level data is emitted; the old answers/Judge stay untouched.
    return {'source_summary': str(HISTORICAL / 'summary.json'),
            'source_sha256': s.p.r.prior.sha(HISTORICAL / 'summary.json'),
            'validated_original_answer_judgment_pairs': verified, 'conditions': conditions,
            'paired_common_valid': paired, 'diagnostics': diagnostics,
            'historical_numbers_changed': False, 'new_generation_or_judge_calls': 0,
            'design': 'Known development data, n=1 generation and n=1 Judge; not final holdout.'}


def tests():
    directories = [HERE, s.PARENT.parent,
                   s.ROOT / 'evidence/quote-adapter-20260913-v2',
                   s.ROOT / 'evidence/semantic-adapter-20260913-v1',
                   s.ROOT / 'evidence/evidence-contract-20260913-v1',
                   s.ROOT / 'evidence/array-schema-20260914-v1']
    results = []
    for directory in directories:
        suite = unittest.TestLoader().discover(str(directory), pattern='test_*.py')
        output = io.StringIO()
        result = unittest.TextTestRunner(stream=output, verbosity=1).run(suite)
        results.append({'directory': str(directory.relative_to(s.ROOT)), 'tests': result.testsRun,
                        'failures': len(result.failures), 'errors': len(result.errors),
                        'skips': len(result.skipped), 'runner_output': output.getvalue()})
        if not result.wasSuccessful():
            raise ValueError('test_failure:' + output.getvalue())
    return {'suites': results, 'distinct_tests': sum(row['tests'] for row in results),
            'failures': 0, 'errors': 0, 'skips': sum(row['skips'] for row in results)}


def main():
    pins, request, raw = input_set()
    dev = s.analyze(request, raw)
    synthetic = ablations()
    historical = old_metrics()
    test_results = tests()
    verify_pins(pins)
    summary = {'analysis_contract': s.VERSION, 'status': 'DONE_WITH_CONCERNS',
               'dev_replayed_cases': 1, 'dev_atoms': dev['atoms'],
               'dev_typed_status_counts': dev['typed_status_counts'],
               'dev_local_preconditions_satisfied': dev['local_preconditions_satisfied'],
               'dev_scope_declaration_fabricated': False,
               'synthetic_subset_combinations': synthetic['subset_combinations'],
               'synthetic_subset_held': synthetic['subset_held'],
               'synthetic_subset_ready': synthetic['subset_local_ready'],
               'verified_old_judgment_pairs': historical['validated_original_answer_judgment_pairs'],
               'related_tests': test_results['distinct_tests'], 'test_failures': 0,
               'test_errors': 0, 'test_skips': test_results['skips'],
               'external_calls': 0, 'new_model_outputs': 0, 'new_gfc_evaluation': False,
               'original_run_reclassified': False, 'eligible_for_service': False,
               'candidate_gfc': None, 'known_previous_project_attempts': 172,
               'account_daily_usage_verified': False, 'live_request_prepared': False}
    manifest = {'created_at': s.p.r.prior.now(), 'analysis_contract': s.VERSION,
                'user_scope': 'Autonomous offline continuation during sleep; service/holdout frozen.',
                'source_pins': pins, 'input_request_id': request['request_id'],
                'input_response_sha256': dev['input_response_sha256'], 'external_calls': 0,
                'scope_declaration': 'Offline schema proposal only; no model prompt/body or API run.'}
    s.p.r.s.previous.new_directory(OUT)
    outputs = {'manifest.json': manifest, 'input-sha256.json': pins, 'dev-scope-audit.json': dev,
               'synthetic-ablations.json': synthetic, 'historical-metrics-verified.json': historical,
               'test-results.json': test_results, 'summary.json': summary,
               'scope-declaration-contract.proposal.json': s.declaration_contract(request)}
    for name, value in outputs.items():
        s.p.r.prior.write_new(OUT / name, value)
    s.p.r.prior.write_new(OUT / 'output-sha256.json',
                         {name: s.p.r.prior.sha(OUT / name) for name in outputs})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('PINNED_INPUT_COUNT', len(pins))
    print('MANIFEST_SHA256', s.p.r.prior.sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    # Stronger than the legacy opt-in transport hook: no network even if an
    # imported module happens to mutate its own CAPABILITY dictionary.
    def offline_audit(event, args):
        if event.startswith(('socket.', 'urllib.')) or event in ('subprocess.Popen', 'os.system'):
            raise PermissionError('offline_scope_analysis_only')
        s.p.r.prior.audit(event, args)
    sys.addaudithook(offline_audit)
    main()
