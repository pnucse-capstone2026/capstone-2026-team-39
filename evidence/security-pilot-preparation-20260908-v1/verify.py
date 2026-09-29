"""Offline snapshot/configuration checks. Does not launch an HTTP server."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


def guard(event, values):
    if event.startswith('socket.') or event.startswith('urllib.'):
        raise RuntimeError('network disabled during preparation verification')
    if event == 'open' and isinstance(values[0], (str, bytes)):
        path = Path(os.fsdecode(values[0]))
        if path.name.startswith('.env') or ('holdout' in str(path).lower() and path.suffix != '.py'):
            raise RuntimeError('protected data disabled during preparation verification')


sys.addaudithook(guard)


def probe(root):
    sys.path.insert(0, str(root / 'scripts'))
    import search_api
    from rag import generators
    import judge_service_answers as judge
    model = os.environ['RAG_GEMINI_MODEL']
    modules = {name: str(Path(module.__file__).resolve())
               for name, module in sorted(sys.modules.items())
               if getattr(module, '__file__', None)
               and Path(module.__file__).resolve().is_relative_to(root)}
    for module in (search_api, generators, judge):
        assert Path(module.__file__).resolve().is_relative_to(root)
    return {
        'server_model_candidates': search_api.gemini_model_candidates(),
        'generator_model_candidates': list(generators._gemini_model_candidates(model)),
        'generation_config': generators._gemini_generation_config(model),
        'runtime_controls': generators.generation_runtime_controls(model),
        'judge_config': judge.build_judge_config(model='gemini-3.1-flash-lite', max_output_tokens=1600),
        'imported_snapshot_modules': modules,
        'server_started': False, 'provider_called': False,
    }


def main():
    import prepare
    import test_prepare
    out = prepare.OUT
    if (out / 'verification.json').exists() or (out / 'verification-tests.log').exists():
        raise SystemExit('refusing to overwrite verification artifacts')
    metadata = json.loads((out / 'preparation.json').read_bytes())
    assert metadata['preparer_sha256'] == prepare.digest(Path(prepare.__file__).read_bytes())
    unit_output = io.StringIO()
    with contextlib.redirect_stdout(unit_output), contextlib.redirect_stderr(unit_output):
        suite = unittest.defaultTestLoader.loadTestsFromModule(test_prepare)
        result = unittest.TextTestRunner(stream=unit_output, verbosity=2).run(suite)
    checks = {}
    probes = {}
    source_cases = [json.loads(line) for line in prepare.checked_bytes(
        prepare.WORK / 'config/pnu-service-shadow60-v1.jsonl', prepare.CASE_SHA).splitlines()]
    selected = [json.loads(line) for line in prepare.checked_bytes(
        Path(metadata['normal_cases_path']), metadata['normal_cases_sha256']).splitlines()]
    assert selected == prepare.select_normal(source_cases)
    assert len({row['id'] for row in selected}) == 14
    assert len({row['category'] for row in selected}) == 7
    checks['unchanged_score_blind_case_selection'] = True
    for raw_path, sha in metadata['source_input_sha256'].items():
        prepare.checked_bytes(Path(raw_path), sha)
    checks['source_index_manifest_and_cases_hashes'] = True
    schedule = metadata['normal_schedule']
    assert len(schedule) == 42
    assert prepare.digest(prepare.canonical(schedule)) == metadata['normal_schedule_sha256']
    for condition in prepare.CONDITIONS:
        slots = [row for row in schedule if row['condition_id'] == condition]
        assert [row['case_id'] for row in slots] == [row['id'] for row in selected]
        assert all(slot['case_sha256'] == prepare.digest(prepare.canonical(case))
                   for slot, case in zip(slots, selected))
    checks['schedule_each_case_once_per_condition'] = True
    for condition, pin in metadata['code_conditions'].items():
        root = Path(pin['root'])
        identity = prepare.code_identity(root)
        assert identity['snapshot_sha256'] == pin['snapshot_sha256']
        assert identity['files_sha256'] == pin['files_sha256']
        child_env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8',
                     **metadata['nonsecret_environment']}
        if pin['context_security_env']:
            child_env['RAG_CONTEXT_SECURITY_MODE'] = pin['context_security_env']
        completed = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--probe', str(root)],
                                   cwd=root, env=child_env, capture_output=True, text=True,
                                   timeout=60, check=True)
        data = json.loads(completed.stdout)
        assert data['server_model_candidates'] == ['gemini-3.5-flash-lite']
        assert data['generator_model_candidates'] == ['gemini-3.5-flash-lite']
        assert data['generation_config'] == {'maxOutputTokens': 900}
        assert data['runtime_controls'] == {'max_context_chars': 24000, 'max_output_tokens': 900,
                                           'sampling_parameters': []}
        assert data['judge_config']['judge_config_sha256'] == metadata['common_controls']['judge']['config_sha256']
        assert prepare.code_identity(root) == identity
        probes[condition] = data
    checks['all_three_snapshot_identities_and_offline_effective_configs'] = True
    pins = metadata['code_conditions']
    for relative in ('scripts/bm25_search.py', 'scripts/judge_service_answers.py',
                     'scripts/evaluate_service_answers.py', 'scripts/service_eval_artifacts.py',
                     'scripts/evaluate_security_service_answers.py'):
        assert len({pin['files_sha256'][relative] for pin in pins.values()}) == 1
    checks['retriever_judge_and_collectors_identical_across_conditions'] = True
    merged = pins['c1-sec-merged']['files_sha256']
    fixed = pins['c1-sec-fixed']['files_sha256']
    changed = sorted(path for path in merged.keys() | fixed.keys() if merged.get(path) != fixed.get(path))
    assert changed == ['scripts/rag/context_fields.py', 'scripts/rag/generators.py',
                       'scripts/rag/security/context_gate.py', 'scripts/rag/security/output_gate.py']
    checks['fixed_treatment_exactly_four_approved_runtime_files'] = True
    summary = {
        'schema_version': 'pnu.security-normal-pilot-verification.v1',
        'preparation_sha256': prepare.digest((out / 'preparation.json').read_bytes()),
        'unit_tests': {'tests': result.testsRun, 'skipped': len(result.skipped),
                       'failures': len(result.failures), 'errors': len(result.errors),
                       'successful': result.wasSuccessful()},
        'artifact_checks': checks, 'offline_config_probes': probes,
        'fixed_runtime_changes': changed, 'external_llm_calls': 0,
        'server_started': False, 'holdout_read': False,
        'limitations': ['No serving-process identity, request, model availability or attack evaluation checked.',
                        'Network denied by Python audit hook; no network attempted.'],
        'source_sha256': {path.name: prepare.digest(path.read_bytes())
                          for path in (Path(__file__), Path(test_prepare.__file__), Path(prepare.__file__))},
    }
    prepare.write_new(out / 'verification-tests.log', unit_output.getvalue().encode())
    prepare.write_new(out / 'verification.json', json.dumps(summary, ensure_ascii=False, indent=2).encode() + b'\n')
    print(json.dumps({'unit_tests': summary['unit_tests'], 'artifact_checks': checks,
                      'external_llm_calls': 0}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--probe':
        print(json.dumps(probe(Path(sys.argv[2]).resolve())))
    else:
        main()
