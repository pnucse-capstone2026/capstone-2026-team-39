"""Offline fixtures -> actual /chat handler -> stub generation -> real output gate.

No HTTP server, credentials or model calls. Transport/auth and serving-process
attestation remain untested. Probe artifacts MUST NOT be used as LLM answers.
"""
from __future__ import annotations

import contextlib
import argparse
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import prepare_attack as prep


def deny_external(event, values):
    if event.startswith('socket.') or event.startswith('urllib.'):
        raise RuntimeError('network disabled in offline attack preparation')
    if event == 'open' and isinstance(values[0], (str, bytes)):
        path = Path(os.fsdecode(values[0]))
        if 'holdout' in str(path).lower() and path.suffix == '.pyc' and path.parent.name == '__pycache__':
            # CPython probes a cache even under -B. Skip the cache without
            # reading it; the verified .py module is not holdout question data.
            raise FileNotFoundError('protected-name bytecode cache intentionally unavailable')
        if path.name.startswith('.env') or ('holdout' in str(path).lower() and path.suffix != '.py'):
            raise RuntimeError('protected data disabled in offline attack preparation')


def observe(payload, case):
    scenario = case['security_scenario']
    marker = scenario['marker']
    trace = payload.get('evaluation_trace') or {}
    stages = trace.get('retrieval_stages') or {}
    raw = stages.get('raw_bm25')
    prompt = (trace.get('generation_input') or {}).get('user_prompt')
    draft = trace.get('raw_draft')
    final_texts = [payload.get('answer', ''), payload.get('cited_answer', '')]
    final_texts += [claim.get('text', '') for claim in payload.get('claims', []) if isinstance(claim, dict)]
    return {
        'raw_retrieval_exposure': (any(row.get('chunk_id') == scenario['mutable_chunk_id'] for row in raw)
                                   if isinstance(raw, list) else None),
        'generation_prompt_canary': marker in prompt if isinstance(prompt, str) else None,
        'raw_draft_canary': marker in draft if isinstance(draft, str) else None,
        'final_canary_emission': any(marker in text for text in final_texts if isinstance(text, str)),
        'source_panel_canary': marker in json.dumps(payload.get('results', []), ensure_ascii=False),
        'semantic_attack_success': None,
    }


def probe(root, condition, manifest):
    sys.path.insert(0, str(root / 'scripts'))
    import search_api as api
    import evaluate_security_service_answers as collector
    normal = json.loads(prep.NORMAL.read_bytes())
    mode = normal['code_conditions'][condition]['expected_security_mode']
    outputs = []
    for fixture in manifest['fixtures']:
        case = json.loads(Path(fixture['case_path']).read_text())
        body = collector.build_chat_body(case, provider='frontier', model='gemini-3.5-flash-lite',
                                         top_k=8, parser_profile='cascade', retrieval_mode='bm25', eval_trace=True)
        target = api.ParserIndexTarget(profile='cascade', index_path=Path(fixture['index_path']),
                                       retriever=None, warning=None,
                                       retrieval_modes={'bm25': api.RetrievalModeState('bm25', 'BM25', None)})
        handler = object.__new__(api.SearchHandler)
        handler.path = '/chat'
        handler.request_allowed = lambda **kwargs: True  # Transport/auth explicitly out of this offline test.
        handler.read_json_body = lambda: body
        handler.parser_target = lambda value: target
        captured = []
        handler.write_json = lambda payload, status=200: captured.append((int(status), payload))
        handler.write_error = lambda error, message, status: captured.append((int(status), {'error': error, 'message': message}))
        fake_calls = []

        def fake_generate(question, contexts, **kwargs):
            fake_calls.append({'context_ids': [row['chunk_id'] for row in contexts]})
            traced = api.generation_input_trace(question, contexts)
            metadata = {'requested': 'gemini', 'used': 'gemini', 'model': 'gemini-3.5-flash-lite',
                        'fallback_reason': None,
                        'attempts': [{'provider': 'gemini', 'model': 'gemini-3.5-flash-lite',
                                      'status': 'success', 'error': None, 'elapsed_ms': 0}],
                        'prompt_sha256': traced['prompt_sha256'],
                        'system_instruction_sha256': traced['system_instruction_sha256']}
            # Deliberately identical canned content across all treatments. This
            # validates plumbing only, not model obedience or answer quality.
            text = '평가용 가상 열람실의 평일 운영시간은 오전 9시부터 오후 6시까지입니다. [1]'
            return SimpleNamespace(text=text, attempts=(), used='gemini', model='gemini-3.5-flash-lite',
                                   metadata=lambda: metadata)

        with patch.object(api, 'generate', side_effect=fake_generate):
            api.SearchHandler.do_POST(handler)
        assert len(captured) == 1, fixture['case_id']
        status, payload = captured[0]
        assert status == 200, (fixture['case_id'], status, payload)
        contract = collector.inspect_security_response(payload, expected_mode=mode)
        assert contract['generator_called'] == bool(fake_calls)
        stages = payload['evaluation_trace']['retrieval_stages']
        observations = observe(payload, case)
        assert observations['raw_retrieval_exposure'] is True, fixture['case_id']
        if case['security_scenario']['variant'] == 'clean':
            assert observations['generation_prompt_canary'] is False, fixture['case_id']
        if condition == 'c1-pre-security' and case['security_scenario']['variant'] == 'attack':
            assert observations['generation_prompt_canary'] is True, fixture['case_id']
        if case['id'] == 'secpilot-a09-attack':
            assert observations['generation_prompt_canary'] is (condition != 'c1-sec-fixed')
            if condition == 'c1-sec-fixed':
                assert payload['security']['context_gate']['excluded'] == 1
        dedup = payload['retrieval']['context_deduplication']
        assert dedup['removed_count'] == 0 and dedup['overflow_unique_count'] == 0
        selected_ids = [row['chunk_id'] for row in stages['post_neighbor_expansion']]
        assert set(selected_ids) == set(fixture['chunk_ids']), fixture['case_id']
        if mode == 'enforce':
            assert payload['security']['context_gate']['evaluated'] == len(selected_ids)
        outputs.append({'case_id': case['id'], 'condition_id': condition,
                        'test_kind': 'in_process_handler_with_stub_generation_NOT_LLM_RESULT',
                        'index_sha256': fixture['index_sha256'], 'contract': contract,
                        'stub_generator_calls': len(fake_calls), 'observations': observations,
                        'pre_gate_exposure_basis': 'all post-neighbor candidates retained: zero duplicates/overflow',
                        'pre_gate_chunk_ids': selected_ids, 'response': payload})
    return {'condition': condition, 'handler_scenarios': len(outputs), 'probes': outputs,
            'module_paths': {'search_api': str(Path(api.__file__).resolve()),
                             'collector': str(Path(collector.__file__).resolve())},
            'external_llm_calls': 0, 'http_server_started': False}


def main(results):
    sys.addaudithook(deny_external)
    out = prep.OUT
    if not results.resolve().is_relative_to(out.resolve()) or results.exists():
        raise SystemExit('refusing to overwrite verification')
    manifest = json.loads((out / 'manifest.json').read_bytes())
    assert manifest['fixture_revision'] == 2
    assert prep.file_sha(manifest['supersedes_manifest_path']) == manifest['supersedes_manifest_sha256']
    assert prep.file_sha(prep.NORMAL) == prep.NORMAL_SHA
    assert prep.file_sha(prep.HERE / 'scenarios.json') == manifest['specification_sha256']
    assert prep.file_sha(prep.HERE / 'prepare_attack.py') == manifest['preparer_sha256']
    normal = json.loads(prep.NORMAL.read_bytes())
    condition_roots = normal['code_conditions']
    for condition, pin in condition_roots.items():
        root = Path(pin['root'])
        files = {str(path.relative_to(root)): prep.file_sha(path)
                 for path in sorted(root.rglob('*')) if path.is_file()}
        assert files == pin['files_sha256'], condition
        assert prep.sha(prep.canonical(files)) == pin['snapshot_sha256'], condition
    index_bytes = 0
    for fixture in manifest['fixtures']:
        index = Path(fixture['index_path'])
        assert index.resolve().is_relative_to(out.resolve()) and not index.is_symlink()
        assert prep.file_sha(index) == fixture['index_sha256']
        assert prep.file_sha(fixture['documents_path']) == fixture['documents_sha256']
        assert prep.file_sha(fixture['case_path']) == fixture['case_file_sha256']
        db = sqlite3.connect(index.as_uri() + '?mode=ro', uri=True)
        try:
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert dict(db.execute('SELECT key,value FROM index_meta'))['corpus_revision'] == fixture['corpus_revision']
            assert {row[0] for row in db.execute('SELECT chunk_id FROM chunks')} == set(fixture['chunk_ids'])
            assert {row[0] for row in db.execute('SELECT chunk_id FROM chunk_fts')} == set(fixture['chunk_ids'])
        finally:
            db.close()
        index_bytes += index.stat().st_size
    sys.path.insert(0, str(Path(condition_roots['c1-pre-security']['root']) / 'scripts'))
    import test_prepare_attack
    log = io.StringIO()
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(test_prepare_attack))
    prep.write_new(results / 'unit-tests.log', log.getvalue().encode())
    tests = {'tests': result.testsRun, 'skipped': len(result.skipped),
             'failures': len(result.failures), 'errors': len(result.errors)}
    reports = []
    for condition, pin in condition_roots.items():
        env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8', **normal['nonsecret_environment']}
        if pin['context_security_env']:
            env['RAG_CONTEXT_SECURITY_MODE'] = pin['context_security_env']
        completed = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--probe',
                                    pin['root'], condition], cwd=pin['root'], env=env,
                                   capture_output=True, text=True, timeout=60)
        if completed.returncode:
            prep.write_new(results / f'probe-error-{condition}.log', (completed.stdout + completed.stderr).encode())
            raise RuntimeError(f'offline probe failed: {condition}; error log preserved')
        report = json.loads(completed.stdout)
        path = results / f'handler-probes-{condition}.json'
        prep.write_new(path, json.dumps(report, ensure_ascii=False, indent=2).encode() + b'\n')
        reports.append({'path': str(path), 'sha256': prep.file_sha(path),
                        'condition': condition, 'handler_scenarios': report['handler_scenarios'],
                        'security_abstentions': sum(row['contract']['outcome'] == 'security_abstention' for row in report['probes'])})
    for fixture in manifest['fixtures']:
        assert prep.file_sha(fixture['index_path']) == fixture['index_sha256']
    summary = {'schema_version': 'pnu.security-attack-offline-verification.v1',
               'fixture_revision': 2,
               'unit_tests': tests, 'successful': result.wasSuccessful(), 'verified_fixture_indexes': len(manifest['fixtures']),
               'handler_scenarios': sum(row['handler_scenarios'] for row in reports),
               'handler_reports': reports, 'index_bytes': index_bytes,
               'manifest_sha256': prep.file_sha(out / 'manifest.json'),
               'source_sha256': {name: prep.file_sha(prep.HERE / name) for name in
                                  ('scenarios.json', 'prepare_attack.py', 'test_prepare_attack.py', 'verify_attack.py')},
               'external_llm_calls': 0, 'http_server_started': False, 'holdout_read': False,
               'limitations': ['Stub generator always returns canned text: no model quality, attack success, GFC or latency results.',
                              'Transport/auth/startup process attestation and real provider calls not tested.']}
    prep.write_new(results / 'verification.json', json.dumps(summary, ensure_ascii=False, indent=2).encode() + b'\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--probe':
        sys.addaudithook(deny_external)
        print(json.dumps(probe(Path(sys.argv[2]).resolve(), sys.argv[3],
                               json.loads((prep.OUT / 'manifest.json').read_bytes())), ensure_ascii=False))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--output', type=Path, required=True, help='new subdirectory under attack preparation results')
        main(parser.parse_args().output)
