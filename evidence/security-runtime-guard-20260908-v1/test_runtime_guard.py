"""Offline unit/probe suite. Synthetic data and fake HTTP only, no service start."""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from runtime_guard import (GuardError, GuardedTransport, Ledger, NoRedirect,
                           attest_bound_server, canonical, digest, file_sha,
                           verify_owned_handshake, verify_snapshot)


def deny_external(event, args):
    if event.startswith(('socket.', 'urllib.')):
        raise RuntimeError('offline_suite_network_disabled')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if 'holdout' in str(path).lower() and path.suffix == '.pyc' and path.parent.name == '__pycache__':
            raise FileNotFoundError('protected_name_bytecode_unavailable')
        if path.name.startswith('.env') or ('holdout' in str(path).lower() and path.suffix != '.py'):
            raise RuntimeError('offline_suite_protected_data_disabled')


def policy():
    return {'experiment_id': 'SYNTHETIC_ONLY', 'total_cap': 5,
            'controls': {'sleep': 3}, 'slots': [
                {'slot_id': 'g', 'role': 'generation', 'model': 'gemini-3.5-flash-lite',
                 'attempt_cap': 3, 'timeout_ceiling': 120},
                {'slot_id': 'j', 'role': 'judge', 'model': 'gemini-3.1-flash-lite',
                 'attempt_cap': 6, 'timeout_ceiling': 180}]}


class Response(io.BytesIO):
    status = 200


class FakeOpener:
    def __init__(self):
        self.calls, self.error, self.body = 0, None, b'{"synthetic": true}'

    def open(self, request, timeout):
        self.calls += 1
        if self.error:
            raise self.error
        return Response(self.body)


class HashTests(unittest.TestCase):
    def test_python39_hash_without_file_digest(self):
        # Regression: original draft used the Python 3.11-only file_digest API.
        with patch.object(hashlib, 'file_digest', None, create=True):
            self.assertEqual(file_sha(__file__), digest(Path(__file__).read_bytes()))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pnu-runtime-guard-test-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'synthetic.sqlite'
        self.policy = policy()
        self.ledger = Ledger.create(self.path, self.policy)
        self.request_sha, self.identity_sha = digest(b'body'), digest(b'identity')

    def reserve(self, slot='g', **kwargs):
        return self.ledger.reserve(slot, kwargs.get('request_sha', self.request_sha), self.identity_sha)

    def consume(self, slot='g'):
        attempt = self.reserve(slot)
        self.ledger.finish(attempt, 'response_closed', 200)
        return attempt

    def test_create_no_overwrite_and_missing_resume_no_create(self):
        with self.assertRaises(FileExistsError):
            Ledger.create(self.path, self.policy)
        missing = self.path.parent / 'missing.sqlite'
        with self.assertRaises(FileNotFoundError):
            Ledger(missing, self.policy)
        self.assertFalse(missing.exists())

    def test_resume_rejects_changed_sleep_or_budget(self):
        for key, value in [('controls', {'sleep': 1}), ('total_cap', 1000)]:
            changed = copy.deepcopy(self.policy)
            changed[key] = value
            with self.assertRaisesRegex(GuardError, 'ledger_policy_changed'):
                Ledger(self.path, changed)

    def test_restart_keeps_slot_budget(self):
        for _ in range(3):
            self.consume()
            self.ledger = Ledger(self.path, self.policy)
        with self.assertRaisesRegex(GuardError, 'slot_budget_exhausted'):
            self.reserve()
        self.assertEqual(self.ledger.summary()['reserved_total'], 3)

    def test_global_cap_crosses_roles(self):
        for role in ('g', 'g', 'g', 'j', 'j'):
            self.consume(role)
        with self.assertRaisesRegex(GuardError, 'total_budget_exhausted'):
            self.reserve('j')

    def test_concurrent_reservations_only_one_inflight(self):
        def attempt(_):
            try:
                return Ledger(self.path, self.policy).reserve('g', self.request_sha, self.identity_sha)
            except GuardError:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(self.ledger.summary()['reserved_total'], 1)

    def test_crash_blocks_other_role_and_resume_without_refund(self):
        attempt = self.reserve()
        self.ledger = Ledger(self.path, self.policy)
        with self.assertRaisesRegex(GuardError, 'unresolved_attempt'):
            self.reserve('j')
        self.ledger.reconcile_uncertain(attempt, 'Synthetic operator verified child termination; count retained')
        self.consume()
        summary = self.ledger.summary()
        self.assertEqual(summary['reserved_total'], 2)
        self.assertEqual(summary['attempts'][0]['state'], 'uncertain_acknowledged')

    def test_retry_body_is_pinned(self):
        self.consume()
        with self.assertRaisesRegex(GuardError, 'retry_request_changed'):
            self.reserve(request_sha=digest(b'different prompt'))

    def test_sealed_slot_cannot_regenerate(self):
        self.consume()
        self.ledger.seal_slot('g', digest(b'validated artifact'))
        self.ledger = Ledger(self.path, self.policy)
        with self.assertRaisesRegex(GuardError, 'slot_sealed'):
            self.reserve()

    def test_validated_zero_call_abstention_and_idempotent_seal(self):
        self.ledger.seal_slot('g', self.request_sha)
        self.ledger.seal_slot('g', self.request_sha)
        self.assertEqual(self.ledger.summary()['reserved_total'], 0)
        with self.assertRaisesRegex(GuardError, 'sealed_artifact_changed'):
            self.ledger.seal_slot('g', self.identity_sha)

    def test_unresolved_cannot_seal(self):
        self.reserve()
        with self.assertRaisesRegex(GuardError, 'unresolved_attempt'):
            self.ledger.seal_slot('g', self.identity_sha)

    def test_unlisted_slot_and_duplicate_finish(self):
        with self.assertRaisesRegex(GuardError, 'unlisted_slot'):
            self.reserve('new-run-id')
        attempt = self.consume()
        with self.assertRaisesRegex(GuardError, 'attempt_not_reserved'):
            self.ledger.finish(attempt, 'response_closed')

    def test_tampered_slot_table_rejected(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE slots SET config='{}' WHERE id='g'")
        with self.assertRaisesRegex(GuardError, 'ledger_slots_changed'):
            self.reserve()

    def test_real_child_exit_keeps_uncertain_reservation(self):
        result = subprocess.run([sys.executable, '-B', __file__, '--reserve-and-exit', str(self.path)],
                                env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                                     'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 23, result.stderr.decode())
        resumed = Ledger(self.path, self.policy)
        self.assertEqual(resumed.summary()['reserved_total'], 1)
        self.assertEqual(resumed.summary()['unresolved'], 1)
        with self.assertRaisesRegex(GuardError, 'unresolved_attempt'):
            resumed.reserve('j', self.request_sha, self.identity_sha)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pnu-transport-test-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'synthetic.sqlite'
        self.ledger = Ledger.create(self.path, policy())
        self.opener = FakeOpener()
        self.transport = GuardedTransport(self.ledger, 'g', opener=self.opener,
                                          identity_check=lambda: {'verified_synthetic': True})

    def request(self, **kwargs):
        return urllib.request.Request(kwargs.get('url', self.transport.endpoint),
                                      data=kwargs.get('body', b'{"prompt":"SYNTHETIC_SENSITIVE_TEXT"}'),
                                      headers={'x-goog-api-key': 'SYNTHETIC_SECRET_KEY'}, method='POST')

    def send(self, **kwargs):
        with self.transport.urlopen(self.request(**kwargs), timeout=30) as response:
            return response.read()

    def test_reservation_precedes_send_and_response_is_unchanged(self):
        original = self.opener.open
        def inspect(request, timeout):
            self.assertEqual(self.ledger.summary()['unresolved'], 1)
            return original(request, timeout)
        self.opener.open = inspect
        self.assertEqual(self.send(), self.opener.body)
        self.assertEqual(self.ledger.summary()['unresolved'], 0)

    def test_model_endpoint_and_query_changes_blocked_before_count(self):
        for url in [self.transport.endpoint.replace('3.5', '3.1'), self.transport.endpoint + '?key=secret',
                    self.transport.endpoint.replace('https:', 'http:'), 'https://attacker.invalid']:
            with self.assertRaisesRegex(GuardError, 'unapproved_provider_request'):
                self.send(url=url)
        self.assertEqual(self.opener.calls, 0)
        self.assertEqual(self.ledger.summary()['reserved_total'], 0)

    def test_bad_timeout_blocks(self):
        for timeout in (None, 0, -1, 121, float('inf'), float('nan'), True):
            with self.assertRaisesRegex(GuardError, 'unapproved_timeout'):
                self.transport.urlopen(self.request(), timeout=timeout)
        self.assertEqual(self.opener.calls, 0)

    def test_identity_mismatch_blocks_before_send(self):
        def reject():
            raise GuardError('synthetic_wrong_index')
        self.transport.identity_check = reject
        with self.assertRaisesRegex(GuardError, 'synthetic_wrong_index'):
            self.send()
        self.assertEqual(self.ledger.summary()['reserved_total'], 0)

    def test_timeout_counts_without_persisting_key_prompt_or_error(self):
        self.opener.error = TimeoutError('SYNTHETIC_SECRET_KEY')
        with self.assertRaises(TimeoutError):
            self.send()
        summary = self.ledger.summary()
        self.assertEqual(summary['reserved_total'], 1)
        self.assertEqual(summary['attempts'][0]['state'], 'transport_error')
        data = self.path.read_bytes().decode(errors='ignore')
        self.assertNotIn('SYNTHETIC_SECRET_KEY', data)
        self.assertNotIn('SYNTHETIC_SENSITIVE_TEXT', data)

    def test_http_429_counts(self):
        self.opener.error = urllib.error.HTTPError(self.transport.endpoint, 429, 'synthetic', {}, io.BytesIO(b'{}'))
        with self.assertRaises(urllib.error.HTTPError):
            self.send()
        self.assertEqual(self.ledger.summary()['attempts'][0]['http_status'], 429)

    def test_read_failure_counts(self):
        class BrokenResponse(Response):
            def read(self, *args, **kwargs):
                raise TimeoutError('synthetic read timeout')
        self.opener.open = lambda request, timeout: BrokenResponse()
        with self.assertRaises(TimeoutError):
            self.send()
        self.assertEqual(self.ledger.summary()['attempts'][0]['state'], 'response_read_error')

    def test_unclosed_response_stops_next_request(self):
        response = self.transport.urlopen(self.request(), timeout=30)
        with self.assertRaisesRegex(GuardError, 'unresolved_attempt'):
            self.send()
        response.close()
        response.close()
        self.assertEqual(self.ledger.summary()['reserved_total'], 1)

    def test_install_wraps_urlopen_and_restores(self):
        original = urllib.request.urlopen
        with self.transport.installed():
            with urllib.request.urlopen(self.request(), timeout=30) as response:
                self.assertEqual(response.read(), self.opener.body)
        self.assertIs(urllib.request.urlopen, original)

    def test_redirect_never_creates_unmetered_followup(self):
        with self.assertRaisesRegex(GuardError, 'provider_redirect_blocked'):
            NoRedirect().redirect_request(self.request(), None, 307, 'redirect', {}, 'https://example.invalid')
        self.assertEqual(self.opener.calls, 0)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pnu-identity-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/search_api.py').write_bytes(b'# SYNTHETIC_API\n')
        (self.root / 'scripts/generator.py').write_bytes(b'# SYNTHETIC_GENERATOR\n')
        files = {p.relative_to(self.root).as_posix(): file_sha(p) for p in (self.root / 'scripts').iterdir()}
        self.condition = {'root': str(self.root), 'files_sha256': files, 'snapshot_sha256': digest(canonical(files))}
        self.index_path = self.root.parent / (self.root.name + '-synthetic-index')
        self.index_path.write_bytes(b'SYNTHETIC_INDEX')
        self.addCleanup(self.index_path.unlink)
        self.index = {'path': str(self.index_path), 'sha256': file_sha(self.index_path), 'corpus_revision': 'synthetic'}
        self.freeze = {'startup_code_sha256': files['scripts/search_api.py'],
                       'index_sha256': self.index['sha256'], 'process_started_at': 'synthetic-start',
                       'startup_git_commit': None, 'startup_worktree_clean': True}
        handler = type('SyntheticHandler', (), {'parser_targets': {'cascade': SimpleNamespace(index_path=self.index_path)},
                                               'default_parser_profile': 'cascade', 'context_chunks_per_document': 2})
        self.api = SimpleNamespace(__file__=str(self.root / 'scripts/search_api.py'), SearchHandler=handler,
                                   freeze_runtime_metadata=lambda path: self.freeze)
        self.server = SimpleNamespace(RequestHandlerClass=handler,
                                      socket=SimpleNamespace(getsockname=lambda: ('127.0.0.1', 18933), fileno=lambda: 123))
        self.modules = {'search_api': self.api, 'generator': SimpleNamespace(__file__=str(self.root / 'scripts/generator.py'))}
        self.nonce = 'a' * 64

    def attest(self):
        return attest_bound_server(self.api, self.server, self.condition, self.index, self.nonce, self.modules)

    def test_simulated_bound_identity_passes(self):
        attestation = self.attest()
        child = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
        self.assertTrue(verify_owned_handshake(attestation, child, self.nonce, self.condition,
                                               self.index, {'freeze': self.freeze}, 18933))
        self.assertEqual(len(attestation['imported_modules']), 2)

    def test_wrong_import_root_rejected(self):
        self.modules['generator'].__file__ = '/private/tmp/wrong/generator.py'
        with self.assertRaisesRegex(GuardError, 'module_from_wrong_root'):
            self.attest()

    def test_non_api_code_change_detected_even_if_health_code_same(self):
        (self.root / 'scripts/generator.py').write_bytes(b'# SYNTHETIC_CHANGED\n')
        with self.assertRaisesRegex(GuardError, 'file_pin_mismatch'):
            self.attest()

    def test_extra_code_detected(self):
        (self.root / 'scripts/extra.py').write_bytes(b'# SYNTHETIC_EXTRA\n')
        with self.assertRaisesRegex(GuardError, 'snapshot_file_set_changed'):
            verify_snapshot(self.condition)

    def test_wrong_index_and_changed_index_detected(self):
        self.api.SearchHandler.parser_targets['cascade'].index_path = '/private/tmp/wrong.sqlite'
        with self.assertRaisesRegex(GuardError, 'wrong_handler_index'):
            self.attest()
        self.api.SearchHandler.parser_targets['cascade'].index_path = self.index_path
        self.index_path.write_bytes(b'SYNTHETIC_CHANGED_INDEX')
        with self.assertRaisesRegex(GuardError, 'file_pin_mismatch'):
            self.attest()

    def test_wrong_pid_nonce_port_starttime_and_dead_child_rejected(self):
        attestation = self.attest()
        child = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
        for key, value in [('pid', child.pid + 1), ('nonce', 'b' * 64), ('listener', ['127.0.0.1', 18932])]:
            changed = copy.deepcopy(attestation)
            changed[key] = value
            with self.assertRaises(GuardError):
                verify_owned_handshake(changed, child, self.nonce, self.condition,
                                       self.index, {'freeze': self.freeze}, 18933)
        with self.assertRaises(GuardError):
            verify_owned_handshake(attestation, child, self.nonce, self.condition,
                                   self.index, {'freeze': {**self.freeze, 'process_started_at': 'wrong'}}, 18933)
        child.poll = lambda: 0
        with self.assertRaises(GuardError):
            verify_owned_handshake(attestation, child, self.nonce, self.condition,
                                   self.index, {'freeze': self.freeze}, 18933)


def probe_condition(condition_name, output):
    """Actual frozen imports + provider retry code, but simulated bound socket/HTTP.

    This is explicitly NOT proof of an actual serving process or LLM behavior.
    """
    import prepare_guard as prep
    prep.verify_file(prep.NORMAL, prep.NORMAL_SHA)
    prep.verify_file(prep.ATTACK, prep.ATTACK_SHA)
    normal = json.loads(prep.NORMAL.read_bytes())
    condition = normal['code_conditions'][condition_name]
    verify_snapshot(condition)
    fixture = json.loads(prep.ATTACK.read_bytes())['fixtures'][0]
    index = {'path': fixture['index_path'], 'sha256': fixture['index_sha256'],
             'corpus_revision': fixture['corpus_revision']}
    synthetic = policy()
    synthetic['total_cap'] = 9
    ledger = Ledger.create(output / 'SYNTHETIC-provider-attempts.sqlite', synthetic)
    identity = {}
    opener = FakeOpener()
    opener.body = json.dumps({'candidates': [{'content': {'parts': [{'text': 'SYNTHETIC_OUTPUT'}]}}],
                              'modelVersion': 'gemini-3.5-flash-lite'}).encode()
    transport = GuardedTransport(ledger, 'g', opener=opener, identity_check=lambda: identity)
    sys.path.insert(0, str(Path(condition['root']) / 'scripts'))
    with transport.installed():
        import search_api as api
        from rag import generators
        import judge_service_answers as judge
        target = api.ParserIndexTarget(profile='cascade', index_path=Path(index['path']),
                                       retriever=None, warning=None,
                                       retrieval_modes={'bm25': api.RetrievalModeState('bm25', 'BM25', None)})
        api.SearchHandler.parser_targets = {'cascade': target}
        api.SearchHandler.default_parser_profile = 'cascade'
        api.SearchHandler.context_chunks_per_document = 2
        simulated_server = SimpleNamespace(RequestHandlerClass=api.SearchHandler,
                                            socket=SimpleNamespace(getsockname=lambda: ('127.0.0.1', 18933),
                                                                   fileno=lambda: 123))
        identity.update(attest_bound_server(api, simulated_server, condition, index, 'a' * 64, dict(sys.modules)))
        identity['listener_is_simulated'] = True
        before = []
        for _ in range(3):
            result = generators._run_gemini('SYNTHETIC_PROMPT_ONLY', time.monotonic() + 150,
                                             requested_model='gemini-3.5-flash-lite')
            assert result.text == 'SYNTHETIC_OUTPUT'
            before.append(result.request_config)
        assert before[0] == before[1] == before[2]
        try:
            generators._run_gemini('SYNTHETIC_PROMPT_ONLY', time.monotonic() + 150,
                                   requested_model='gemini-3.5-flash-lite')
        except GuardError as exc:
            assert str(exc) == 'slot_budget_exhausted'
        else:
            raise AssertionError('generation fourth attempt was not blocked')
    assert opener.calls == 3
    judge_opener = FakeOpener()
    judge_opener.error = urllib.error.HTTPError('https://example.invalid', 429, 'synthetic', {}, io.BytesIO(b'{}'))
    judge_transport = GuardedTransport(ledger, 'j', opener=judge_opener, identity_check=lambda: identity)
    with judge_transport.installed(), patch.object(judge.time, 'sleep'):
        result = judge.call_gemini_judge(prompt='SYNTHETIC_JUDGE_PROMPT', api_key='SYNTHETIC_KEY',
                                         model='gemini-3.1-flash-lite', max_output_tokens=1600,
                                         timeout=180, retries=6)
        assert len(result['attempts']) == 6 and result['error'] is not None
        assert all(a['http_status'] == 429 for a in result['attempts'])
        extra = judge.call_gemini_judge(prompt='SYNTHETIC_JUDGE_PROMPT', api_key='SYNTHETIC_KEY',
                                        model='gemini-3.1-flash-lite', max_output_tokens=1600,
                                        timeout=180, retries=6)
        assert extra['error'] == 'slot_budget_exhausted'
    assert judge_opener.calls == 6 and ledger.summary()['reserved_total'] == 9
    verify_snapshot(condition)
    prep.verify_file(index['path'], index['sha256'])
    report = {'status': 'MOCK_TRANSPORT_NOT_LLM_RESULT', 'condition': condition_name,
              'actual_frozen_imports': True, 'server_started': False, 'listener_is_simulated': True,
              'external_llm_calls': 0, 'generator_mock_sends': opener.calls,
              'judge_mock_sends': judge_opener.calls, 'blocked_excess_requests': 2,
              'identity': identity, 'ledger': ledger.summary(),
              'generation_request_config': before[0]}
    prep.write_new_json(output / 'probe.json', report)
    print(json.dumps({'condition': condition_name, 'mock_sends': 9, 'passed': True}))


def verify_all(output):
    import ast
    import prepare_guard as prep
    output.mkdir(parents=True, exist_ok=False)
    logfile = output / 'unit-tests.log'
    with logfile.open('x') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    test_counts = {'total': result.testsRun, 'skipped': len(result.skipped),
                   'failures': len(result.failures), 'errors': len(result.errors)}
    if not result.wasSuccessful():
        prep.write_new_json(output / 'FAILED-unit-tests.json', test_counts)
        raise SystemExit('Unit tests failed; see new log, no existing output overwritten.')
    prepared = prep.build_policy()
    prep.write_new_json(output / 'policy.json', prepared)
    # Exercise ALL 204 slots through the transport wrapper, with only fake HTTP.
    simulated = Ledger.create(output / 'SYNTHETIC-918-attempts.sqlite', prepared)
    fake = FakeOpener()
    for slot in prepared['slots']:
        wrapper = GuardedTransport(simulated, slot['slot_id'], opener=fake,
                                   identity_check=lambda: {'simulated': True})
        request = urllib.request.Request(wrapper.endpoint, data=b'{"SYNTHETIC":true}', method='POST')
        for _ in range(slot['attempt_cap']):
            with wrapper.urlopen(request, timeout=30) as response:
                response.read()
        # Resume a new ledger handle between logical slots; no fresh DB/reset.
        simulated = Ledger(simulated.path, prepared)
    try:
        wrapper.urlopen(request, timeout=30)
    except GuardError as exc:
        assert str(exc) == 'slot_budget_exhausted'
    else:
        raise AssertionError('attempt 919 was not blocked')
    assert fake.calls == 918 and simulated.summary()['remaining_ceiling'] == 0
    prep.write_new_json(output / 'SYNTHETIC-budget-result.json', simulated.summary())
    probes = {}
    for name, condition in prepared['conditions'].items():
        probe_output = output / name
        probe_output.mkdir()
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'en_US.UTF-8',
               **prepared['environment'], 'RAG_GEMINI_API_KEY': 'SYNTHETIC_OFFLINE_KEY'}
        if condition['context_security_env']:
            env['RAG_CONTEXT_SECURITY_MODE'] = condition['context_security_env']
        completed = subprocess.run([sys.executable, '-B', __file__, '--probe-condition', name,
                                    '--output', str(probe_output)], env=env, capture_output=True, timeout=40)
        with (probe_output / 'process.log').open('xb') as stream:
            stream.write(completed.stdout + completed.stderr)
        if completed.returncode:
            raise SystemExit('Frozen-code mock probe failed: ' + name)
        probes[name] = json.loads((probe_output / 'probe.json').read_bytes())
    # Hash all pinned inputs again after probes; compare exact original policy.
    assert prep.build_policy() == prepared
    service_pins = {
        'scripts/search_api.py': '9a8865db361d6d3a044d26fc57698f56840540ed573a5f37bb50ef684c1cf4a5',
        'scripts/bm25_search.py': '6c474dc83a4f27ab3172de5b839f731e9954206b1d3cb1748dc08f3848cb1ad7',
        'scripts/rag/generators.py': '67cccfd602b808929c226fcbb659635495e50e05103592664020c6c3a725166b',
        'scripts/judge_service_answers.py': '95b653d336058bc8eb8829c64472a3ad94162bc3a08d87400f0391e7b06e7414',
        'scripts/evaluate_service_answers.py': '24c1b03cb03d291b4562764f5523cd481db6c992885f2c071d2bf247d8c6445d',
        'scripts/service_eval_artifacts.py': '1b83fdd2004db18b29fd5a5d5bbff5abf9d877a434de0f22ad247e1b1902ff9d',
    }
    for relative, expected in service_pins.items():
        prep.verify_file(prep.ROOT / relative, expected)
    sources = list(Path(__file__).parent.glob('*.py'))
    for source in sources:
        text = source.read_text()
        ast.parse(text)
        assert text.endswith('\n') and all(line.rstrip() == line for line in text.splitlines())
    diff = subprocess.run(['git', 'diff', '--check'], cwd=prep.ROOT, capture_output=True, timeout=20)
    with (output / 'git-diff-check.log').open('xb') as stream:
        stream.write(diff.stdout + diff.stderr)
    assert diff.returncode == 0
    report = {'status': 'OFFLINE_COMPONENTS_VERIFIED_NOT_LIVE_READY', 'tests': test_counts,
              'python_version': sys.version, 'external_llm_calls': 0, 'server_starts': 0,
              'holdout_read': False, 'api_execution_authorized': False, 'live_runner_ready': False,
              'policy_canonical_sha256': digest(canonical(prepared)),
              'slots': 204, 'generation_slots': 102, 'judge_slots': 102,
              'synthetic_budget_sends': 918, 'attempt_919_blocked': True,
              'frozen_condition_probes': {k: {'generator_mock_sends': v['generator_mock_sends'],
                                            'judge_mock_sends': v['judge_mock_sends']} for k, v in probes.items()},
              'identity_listener_simulated': True, 'verified_snapshots': prepared['verified_snapshots'],
              'verified_input_files': len(prepared['input_sha256']), 'original_service_sha256': service_pins,
              'tool_sha256': {p.name: file_sha(p) for p in sources},
              'artifact_sha256': {p.relative_to(output).as_posix(): file_sha(p)
                                  for p in sorted(output.rglob('*')) if p.is_file()}}
    prep.write_new_json(output / 'verification.json', report)
    print(json.dumps({k: report[k] for k in ('status', 'tests', 'synthetic_budget_sends', 'external_llm_calls')},
                     ensure_ascii=False))


def main():
    sys.addaudithook(deny_external)
    if '--reserve-and-exit' in sys.argv:
        ledger = Ledger(sys.argv[sys.argv.index('--reserve-and-exit') + 1], policy())
        ledger.reserve('g', digest(b'body'), digest(b'identity'))
        os._exit(23)  # Deliberate synthetic crash after durable reservation.
    if '--verify-output' in sys.argv or '--probe-condition' in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument('--verify-output', type=Path)
        parser.add_argument('--probe-condition')
        parser.add_argument('--output', type=Path)
        args = parser.parse_args()
        if args.verify_output:
            verify_all(args.verify_output)
        else:
            probe_condition(args.probe_condition, args.output)
    else:
        unittest.main(verbosity=2)


if __name__ == '__main__':
    main()
