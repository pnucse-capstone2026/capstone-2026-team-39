"""Synthetic integration through frozen collector/Judge; no socket or API use."""
from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import execution_bridge as bridge

ROOT = Path(__file__).resolve().parents[2]
NORMAL = ROOT / 'processed/eval/preflight-20260908/security-pilot-preparation-v1/preparation.json'
NORMAL_SHA = '5cddcf2a9cc69f9ee702c078db0d0fe3b74aae3f66f3919a45f8177c10bc66ca'


def offline_audit(event, args):
    if event.startswith(('socket.', 'urllib.')):
        raise RuntimeError('offline_network_disabled')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        # macOS may redirect caches via PYTHONPYCACHEPREFIX, outside __pycache__.
        # Treat every protected-name cache as missing; never read its bytes.
        if 'holdout' in str(path).lower() and path.suffix == '.pyc':
            raise FileNotFoundError('skip_protected_name_cache')
        if path.name.startswith('.env') or ('holdout' in str(path).lower() and path.suffix != '.py'):
            raise RuntimeError('protected_data_disabled')


sys.addaudithook(offline_audit)
bridge.guard.verify_file(NORMAL, NORMAL_SHA)
NORMAL_DATA = json.loads(NORMAL.read_bytes())
COMMON = NORMAL_DATA['code_conditions']['c1-sec-fixed']
bridge.guard.verify_snapshot(COMMON)
sys.path.insert(0, str(Path(COMMON['root']) / 'scripts'))
import service_eval_artifacts as artifacts
import evaluate_security_service_answers as collector
import judge_service_answers as judge
for module in (artifacts, collector, judge):
    relative = Path(module.__file__).resolve().relative_to(Path(COMMON['root'])).as_posix()
    bridge.guard.verify_file(module.__file__, COMMON['files_sha256'][relative])


class Response(io.BytesIO):
    status = 200


class FakeProvider:
    def __init__(self):
        self.sends = 0
        self.failures = 0
        self.judgment = {'score': 0, 'grounded_fully_correct': False, 'claim_checks': [],
                         'unsupported_facts': [], 'contradictions': [], 'citation_support': 'none',
                         'abstention': 'not_applicable', 'uncertain': False, 'reason': 'SYNTHETIC_ONLY'}

    def open(self, request, timeout):
        self.sends += 1
        if self.failures:
            self.failures -= 1
            raise urllib.error.HTTPError(request.full_url, 429, 'SYNTHETIC', {}, io.BytesIO(b'{}'))
        return Response(json.dumps({'candidates': [{'content': {'parts': [
            {'text': json.dumps(self.judgment)}]}}]}).encode())


class Scenario:
    def __init__(self, parent, *, condition='c1-sec-fixed', blocked=False):
        self.parent = Path(parent)
        self.condition, self.blocked = condition, blocked
        self.mode = 'absent' if condition == 'c1-pre-security' else 'enforce'
        self.case = {'id': 'SYNTHETIC-case', 'query': '합성 문서의 운영시간?', 'required_claims': []}
        self.case_path = self.parent / 'SYNTHETIC-cases.jsonl'
        bridge.write_new(self.case_path, json.dumps(self.case, ensure_ascii=False).encode() + b'\n')
        self.index = self.parent / 'SYNTHETIC-index.bin'
        bridge.write_new(self.index, b'SYNTHETIC_INDEX_NOT_SQLITE')
        self.prefix = 'SYNTHETIC:' + condition + ':run1:case'
        slots = []
        for role in ('generation', 'judge'):
            slots.append({'slot_id': self.prefix + ':' + role, 'role': role, 'condition_id': condition,
                          'case_id': self.case['id'], 'case_sha256': bridge.digest(bridge.canonical(self.case)),
                          'generation_run_id': 'run1', 'judge_run_id': 'synthetic-v11-r1',
                          'model': 'gemini-3.5-flash-lite' if role == 'generation' else 'gemini-3.1-flash-lite',
                          'attempt_cap': 3 if role == 'generation' else 6,
                          'timeout_ceiling': 120 if role == 'generation' else 180,
                          'source_manifest_sha256': bridge.file_sha(self.case_path),
                          'index': {'path': str(self.index), 'sha256': bridge.file_sha(self.index),
                                    'corpus_revision': 'SYNTHETIC'},
                          'case_file': {'path': str(self.case_path), 'sha256': bridge.file_sha(self.case_path)}})
        self.policy = {'experiment_id': 'SYNTHETIC-execution-bridge', 'total_cap': 9,
                       'controls': copy.deepcopy(NORMAL_DATA['common_controls']), 'slots': slots}
        self.run = bridge.Run.create_offline(self.parent / 'run', self.policy)
        self.validator = bridge.Validator(artifacts=artifacts, collector=collector, judge=judge, policy=self.policy)
        self.fake = FakeProvider()
        self.identity = {'kind': 'SYNTHETIC_TRANSPORT', 'condition_id': condition, 'pid': os.getpid()}
        self.health = self.make_health()
        self.response = self.make_response()
        self.chat_calls = 0

    @property
    def generation_id(self):
        return self.prefix + ':generation'

    @property
    def judge_id(self):
        return self.prefix + ':judge'

    def make_health(self):
        freeze = {'startup_git_commit': None, 'startup_worktree_clean': True,
                  'startup_code_sha256': COMMON['files_sha256']['scripts/search_api.py'],
                  'process_started_at': 'SYNTHETIC-START', 'index_sha256': bridge.file_sha(self.index),
                  'index_size_bytes': self.index.stat().st_size}
        return {'ready': True, 'parser_profiles': [{'id': 'cascade', 'ready': True,
                'corpus_revision': 'SYNTHETIC', 'index_sha256': bridge.file_sha(self.index),
                'index_size_bytes': self.index.stat().st_size, 'source_manifest_sha256': bridge.file_sha(self.case_path),
                'retrieval_modes': [{'id': 'bm25', 'ready': True}]}],
                'service_config': {'freeze': freeze, 'retrieval_tuning': True,
                'context_chunks_per_document': 2, 'evaluation_trace_enabled': True,
                'generation': {'provider': 'frontier', 'configured': True,
                               'allowed_models': ['gemini-3.5-flash-lite'],
                               'models': {'gemini-3.5-flash-lite': {
                                   'max_context_chars': 24000, 'max_output_tokens': 900, 'sampling_parameters': []}}}}}

    def projected_health(self):
        return collector.validate_health_controls(self.health, parser_profile='cascade', retrieval_mode='bm25',
            expected_corpus_revision='SYNTHETIC', expected_retrieval_tuning=True,
            expected_context_chunks_per_document=2, expected_generation_provider='frontier',
            expected_generation_model='gemini-3.5-flash-lite', expected_generation_max_context_chars=24000,
            expected_generation_max_output_tokens=900, expected_generation_sampling_parameters=[], require_eval_trace=True)

    def make_response(self):
        gate = {'status': 'blocked' if self.blocked else 'passed', 'policy_version': 'context-security-v1',
                'mode': 'enforce', 'evaluated': 1, 'allowed': 0 if self.blocked else 1, 'sanitized': 0,
                'excluded': 1 if self.blocked else 0, 'reasons': {'synthetic': 1}}
        output = {'status': 'blocked' if self.blocked else 'passed', 'policy_version': 'output-security-v1',
                  'decision': 'abstain' if self.blocked else 'answer', 'claims_checked': 0 if self.blocked else 1,
                  'claims_supported': 0 if self.blocked else 1, 'invalid_citations': 0}
        prompt, system = 'SYNTHETIC_PROMPT', 'SYNTHETIC_SYSTEM'
        generation = {'requested': 'frontier', 'used': 'none' if self.blocked else 'frontier',
                      'model': None if self.blocked else 'gemini-3.5-flash-lite',
                      'fallback_reason': 'no_results' if self.blocked else None, 'attempts': []}
        self.body = {'systemInstruction': {'parts': [{'text': system}]},
                     'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
                     'generationConfig': {'maxOutputTokens': 900}}
        if not self.blocked:
            request_config = {'provider': 'gemini', 'model_requested': 'gemini-3.5-flash-lite',
                              'api_style': 'generateContent', 'generation_config': {'maxOutputTokens': 900},
                              'prompt_used': True, 'system_instruction_sha256': bridge.digest(system.encode())}
            generation.update(prompt_sha256=bridge.digest(prompt.encode()), system_instruction_sha256=bridge.digest(system.encode()),
                              request_config=request_config, request_config_sha256=bridge.digest(bridge.canonical(request_config)))
        contexts = [] if self.blocked else [{'chunk_id': 'synthetic-source', 'document_id': 'synthetic-doc',
                     'corpus_revision': 'SYNTHETIC', 'text': '합성 근거', 'preview': '합성 근거',
                     'text_sha256': bridge.digest('합성 근거'.encode())}]
        return {'answer': '근거가 차단되어 답변할 수 없습니다.' if self.blocked else '합성 답변입니다.',
                'cited_answer': '', 'institution': None, 'parser_profile': 'cascade', 'retrieval_mode': 'bm25',
                'generation': generation, 'claims': [] if self.blocked else [{'text': '합성 답변입니다.', 'supported': True}],
                'citations': [], 'postprocessing': {}, 'results': contexts,
                'retrieval': {} if self.mode == 'absent' else {'security_gate': copy.deepcopy(gate)},
                'security': None if self.mode == 'absent' else {'context_gate': gate, 'output_gate': output},
                'evaluation_trace': {'schema_version': 1, 'generation_input': None if self.blocked else {
                    'user_prompt': prompt, 'system_instruction': system,
                    'prompt_sha256': generation['prompt_sha256'], 'system_instruction_sha256': generation['system_instruction_sha256']},
                    'retrieval_stages': {'final_contexts': contexts},
                    'raw_draft': None if self.blocked else '합성 답변입니다.',
                    'sanitized_draft': None if self.blocked else '합성 답변입니다.',
                    'timing_ms': {'generation': 0.0 if self.blocked else 1.0}}}

    def transport(self, role):
        return bridge.guard.GuardedTransport(self.run.ledger, self.prefix + ':' + role, opener=self.fake,
                                              identity_check=lambda: self.identity)

    def chat(self, *args, **kwargs):
        self.chat_calls += 1
        if not self.blocked:
            transport = self.transport('generation')
            request = urllib.request.Request(transport.endpoint, data=json.dumps(self.body, ensure_ascii=False).encode(),
                                              method='POST')
            with transport.urlopen(request, timeout=30) as response:
                response.read()
        return copy.deepcopy(self.response)

    def collect(self, *, seal=True):
        with self.run.locked():
            self.run.begin(self.generation_id)
            with patch.object(collector, 'call_health', return_value=self.health), \
                 patch.object(collector, 'call_chat', side_effect=self.chat), \
                 patch.object(collector.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                bridge.collect_one(self.run, self.generation_id, collector, api_base='http://127.0.0.1:18933',
                                   security_mode=self.mode, server_config=self.projected_health(), transport_identity=self.identity)
            if seal:
                return self.run.finalize(self.generation_id, self.validator)

    def judge(self, *, seal=True):
        with self.run.locked():
            self.run.begin(self.judge_id)
            with patch.object(bridge.time, 'sleep'):
                bridge.judge_one(self.run, self.judge_id, self.validator, transport=self.transport('judge'),
                                  api_key='SYNTHETIC_OFFLINE_KEY')
            if seal:
                return self.run.finalize(self.judge_id, self.validator)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pnu-execution-bridge-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.s = Scenario(self.root)

    def test_frozen_collector_judge_roundtrip(self):
        self.assertEqual(self.s.collect()['provider_attempts'], 1)
        self.assertEqual(self.s.judge()['provider_attempts'], 1)
        self.assertEqual(len(self.s.run.ledger.summary()['sealed_slots']), 2)

    def test_saved_unsealed_resume_does_not_call_provider_again(self):
        self.s.collect(seal=False)
        sends = self.s.fake.sends
        resumed = bridge.Run(self.s.run.root, self.s.policy)
        with resumed.locked():
            first = resumed.finalize(self.s.generation_id, self.s.validator)
            second = resumed.finalize(self.s.generation_id, self.s.validator)
            with self.assertRaisesRegex(bridge.GuardError, 'already_started'):
                resumed.begin(self.s.generation_id)
        self.assertEqual(first, second)
        self.assertEqual(self.s.fake.sends, sends)

    def test_unfinished_execution_stops_other_slots(self):
        with self.s.run.locked():
            self.s.run.begin(self.s.generation_id)
            with self.assertRaisesRegex(bridge.GuardError, 'previous_execution_unfinished'):
                self.s.run.begin(self.s.judge_id)

    def test_run_lock_is_required_and_exclusive(self):
        with self.assertRaisesRegex(bridge.GuardError, 'run_lock_required'):
            self.s.run.begin(self.s.generation_id)
        other = bridge.Run(self.s.run.root, self.s.policy)
        with self.s.run.locked(), self.assertRaisesRegex(bridge.GuardError, 'run_already_owned'):
            with other.locked():
                pass

    def test_policy_or_run_directory_change_rejected(self):
        changed = copy.deepcopy(self.s.policy)
        changed['controls']['collector']['sleep'] = 0
        with self.assertRaises(bridge.GuardError):
            bridge.Run(self.s.run.root, changed)
        copied = self.root / 'copied-run'
        shutil.copytree(self.s.run.root, copied)
        with self.assertRaisesRegex(bridge.GuardError, 'run_directory_changed'):
            bridge.Run(copied, self.s.policy)

    def test_live_run_creation_disabled(self):
        changed = copy.deepcopy(self.s.policy)
        changed['experiment_id'] = 'security-live'
        with self.assertRaisesRegex(bridge.GuardError, 'live_run_creation_disabled'):
            bridge.Run.create_offline(self.root / 'not-created', changed)
        self.assertFalse((self.root / 'not-created').exists())

    def test_missing_or_partial_result_never_sealed(self):
        with self.s.run.locked():
            self.s.run.begin(self.s.generation_id)
            self.s.run.bind(self.s.generation_id, {'transport_identity_sha256': bridge.digest(bridge.canonical(self.s.identity))})
            bridge.write_new(self.s.run.output(self.s.generation_id), b'{"partial":')
            with self.assertRaisesRegex(bridge.GuardError, 'incomplete_or_large_artifact'):
                self.s.run.finalize(self.s.generation_id, self.s.validator)
        self.assertEqual(self.s.run.ledger.summary()['sealed_slots'], {})

    def mutate_answer(self, change):
        self.s.collect(seal=False)
        path = self.s.run.output(self.s.generation_id)
        record, _ = bridge.read_one(path)
        change(record)
        record = artifacts.build_answer_identity(record)
        # Deliberate tamper of a newly generated synthetic test artifact only.
        path.write_text(json.dumps(record, ensure_ascii=False) + '\n')
        return record

    def test_rehashed_changed_query_rejected(self):
        self.mutate_answer(lambda r: r.update(query='바뀐 합성 질문'))
        with self.s.run.locked(), self.assertRaisesRegex(bridge.GuardError, 'answer_query_mismatch'):
            self.s.run.finalize(self.s.generation_id, self.s.validator)

    def test_rehashed_changed_config_rejected(self):
        def change(record):
            record['collector_config']['inter_call_sleep_seconds'] = 0
            record['collector_config_sha256'] = bridge.digest(bridge.canonical(record['collector_config']))
        self.mutate_answer(change)
        with self.s.run.locked(), self.assertRaisesRegex(bridge.GuardError, 'collector_config_drift'):
            self.s.run.finalize(self.s.generation_id, self.s.validator)

    def test_rehashed_prompt_not_matching_sent_request_rejected(self):
        def change(record):
            trace = record['evaluation_trace']['generation_input']
            trace['user_prompt'] = 'ANOTHER_SYNTHETIC_PROMPT'
            trace['prompt_sha256'] = bridge.digest(trace['user_prompt'].encode())
            record['generation']['prompt_sha256'] = trace['prompt_sha256']
        self.mutate_answer(change)
        with self.s.run.locked(), self.assertRaisesRegex(bridge.GuardError, 'generation_transport_binding_mismatch'):
            self.s.run.finalize(self.s.generation_id, self.s.validator)

    def test_judge_requires_sealed_generation(self):
        self.s.collect(seal=False)
        with self.assertRaisesRegex(bridge.GuardError, 'judge_requires_sealed_answer'):
            bridge.judge_one(self.s.run, self.s.judge_id, self.s.validator,
                              transport=self.s.transport('judge'), api_key='SYNTHETIC')

    def test_judge_internal_retries_match_ledger(self):
        self.s.collect()
        self.s.fake.failures = 2
        self.assertEqual(self.s.judge()['provider_attempts'], 3)
        self.assertEqual(self.s.fake.sends, 4)

    def test_judge_saved_unsealed_resume(self):
        self.s.collect()
        self.s.judge(seal=False)
        resumed = bridge.Run(self.s.run.root, self.s.policy)
        sends = self.s.fake.sends
        with resumed.locked():
            resumed.finalize(self.s.judge_id, self.s.validator)
        self.assertEqual(self.s.fake.sends, sends)

    def test_unresolved_transport_prevents_finalization(self):
        self.s.collect(seal=False)
        attempts = self.s.run.ledger.summary()['attempts']
        self.s.run.ledger.reserve(self.s.generation_id, attempts[0]['request_sha'], attempts[0]['identity_sha'])
        with self.s.run.locked(), self.assertRaisesRegex(bridge.GuardError, 'unresolved_provider_attempt'):
            self.s.run.finalize(self.s.generation_id, self.s.validator)

    def test_successful_security_abstention_has_zero_generation_calls(self):
        folder = self.root / 'blocked'
        folder.mkdir()
        scenario = Scenario(folder, blocked=True)
        self.assertEqual(scenario.collect()['provider_attempts'], 0)
        self.assertEqual(scenario.judge()['provider_attempts'], 1)
        self.assertEqual(scenario.fake.sends, 1)

    def test_security_abstention_with_hidden_provider_call_rejected(self):
        folder = self.root / 'blocked-hidden-call'
        folder.mkdir()
        scenario = Scenario(folder, blocked=True)
        scenario.collect(seal=False)
        attempt = scenario.run.ledger.reserve(scenario.generation_id, 'a' * 64,
                                              bridge.digest(bridge.canonical(scenario.identity)))
        scenario.run.ledger.finish(attempt, 'response_closed', 200)
        with scenario.run.locked(), self.assertRaisesRegex(bridge.GuardError, 'security_abstention_has_provider_attempts'):
            scenario.run.finalize(scenario.generation_id, scenario.validator)

    def test_judge_answer_binding_tamper_rejected(self):
        self.s.collect()
        self.s.judge(seal=False)
        path = self.s.run.output(self.s.judge_id)
        record, _ = bridge.read_one(path)
        record['answers_artifact_sha256'] = '0' * 64
        path.write_text(json.dumps(artifacts.build_judgment_identity(record)) + '\n')
        with self.s.run.locked(), self.assertRaisesRegex(bridge.GuardError, 'judge_answer_binding_mismatch'):
            self.s.run.finalize(self.s.judge_id, self.s.validator)

    def test_judge_raw_verdict_tamper_rejected(self):
        self.s.collect()
        self.s.judge(seal=False)
        path = self.s.run.output(self.s.judge_id)
        record, _ = bridge.read_one(path)
        record['judge']['reason'] = 'CHANGED_REASON'
        path.write_text(json.dumps(artifacts.build_judgment_identity(record)) + '\n')
        with self.s.run.locked(), self.assertRaisesRegex(bridge.GuardError, 'judge_guard_mismatch'):
            self.s.run.finalize(self.s.judge_id, self.s.validator)

    def test_duplicate_keys_and_nonfinite_json_rejected(self):
        for value in ('{"a":1,"a":2}', '{"a":NaN}'):
            with self.assertRaises(bridge.GuardError):
                bridge.strict_json(value)

    def test_health_uses_nested_freeze_and_rejects_root_only(self):
        slot = self.s.policy['slots'][0]
        child = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
        nonce = 'a' * 64
        attestation = {'schema': 'pnu.security-bound-server-attestation.v1', 'pid': child.pid, 'nonce': nonce,
                       'root': COMMON['root'], 'snapshot_sha256': COMMON['snapshot_sha256'],
                       'index': slot['index'], 'listener': ['127.0.0.1', 18933],
                       'freeze': self.s.health['service_config']['freeze'], 'imported_modules': {'SYNTHETIC': {}}}
        bridge.validate_health(collector, self.s.health, slot, self.s.policy['controls'], COMMON, attestation, child, nonce)
        changed = copy.deepcopy(self.s.health)
        changed['freeze'] = changed['service_config'].pop('freeze')
        with self.assertRaisesRegex(bridge.GuardError, 'health_handshake_mismatch'):
            bridge.validate_health(collector, changed, slot, self.s.policy['controls'], COMMON, attestation, child, nonce)

    def test_health_rejects_extra_model_or_wrong_manifest(self):
        slot = self.s.policy['slots'][0]
        child = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
        nonce = 'a' * 64
        attestation = {'schema': 'pnu.security-bound-server-attestation.v1', 'pid': child.pid, 'nonce': nonce,
                       'root': COMMON['root'], 'snapshot_sha256': COMMON['snapshot_sha256'],
                       'index': slot['index'], 'listener': ['127.0.0.1', 18933],
                       'freeze': self.s.health['service_config']['freeze'], 'imported_modules': {'SYNTHETIC': {}}}
        changed = copy.deepcopy(self.s.health)
        changed['service_config']['generation']['allowed_models'].append('gemini-other')
        with self.assertRaisesRegex(bridge.GuardError, 'unexpected_model_fallback'):
            bridge.validate_health(collector, changed, slot, self.s.policy['controls'], COMMON, attestation, child, nonce)
        changed = copy.deepcopy(self.s.health)
        changed['parser_profiles'][0]['source_manifest_sha256'] = '0' * 64
        with self.assertRaisesRegex(bridge.GuardError, 'profile_source_manifest_mismatch'):
            bridge.validate_health(collector, changed, slot, self.s.policy['controls'], COMMON, attestation, child, nonce)
        missing = dict(slot)
        missing.pop('source_manifest_sha256')
        with self.assertRaisesRegex(bridge.GuardError, 'source_manifest_pin_required'):
            bridge.validate_health(collector, self.s.health, missing, self.s.policy['controls'], COMMON, attestation, child, nonce)

    def test_redirected_protected_bytecode_cache_is_not_read(self):
        with self.assertRaises(FileNotFoundError):
            offline_audit('open', ('/private/tmp/SYNTHETIC-cache/holdout_gold.cpython-39.pyc',))
        with self.assertRaisesRegex(RuntimeError, 'protected_data_disabled'):
            offline_audit('open', ('/private/tmp/SYNTHETIC-holdout.jsonl',))

    def test_private_pipe_pid_nonce_and_owned_cleanup(self):
        nonce = 'a' * 64
        command = [sys.executable, '-B', __file__, '--pipe-probe']
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHONDONTWRITEBYTECODE': '1'}
        with bridge.OwnedChild(command, env, nonce, self.root / 'child.log') as owned:
            record = owned.receive()
            self.assertEqual(record['pid'], owned.child.pid)
        self.assertIsNotNone(owned.child.poll())

    def test_wrong_nonce_from_child_rejected_and_child_stopped(self):
        command = [sys.executable, '-B', __file__, '--pipe-probe', '--bad-nonce']
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHONDONTWRITEBYTECODE': '1'}
        with bridge.OwnedChild(command, env, 'a' * 64, self.root / 'bad-child.log') as owned:
            with self.assertRaisesRegex(bridge.GuardError, 'child_identity_mismatch'):
                owned.receive()
        self.assertIsNotNone(owned.child.poll())

    def test_silent_child_timeout_stops_only_owned_child(self):
        command = [sys.executable, '-B', __file__, '--pipe-probe', '--silent']
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHONDONTWRITEBYTECODE': '1'}
        with bridge.OwnedChild(command, env, 'a' * 64, self.root / 'silent-child.log') as owned:
            with self.assertRaisesRegex(bridge.GuardError, 'child_handshake_timeout'):
                owned.receive(timeout=0.1)
        self.assertIsNotNone(owned.child.poll())

    def test_bootstrap_without_start_flag_refuses_before_input_read(self):
        import subprocess
        script = Path(__file__).with_name('local_mock_bootstrap.py')
        result = subprocess.run([sys.executable, '-B', str(script), '--run-root', '/private/tmp/SYNTHETIC-missing',
                                 '--slot-id', 'none', '--attestation-fd', '1', '--nonce', 'a' * 64],
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'explicit_local_test_server_start_required', result.stderr)
        self.assertNotIn(b'FileNotFoundError', result.stderr)

    def test_bootstrap_environment_excludes_inherited_secrets_and_controls(self):
        import local_mock_bootstrap as bootstrap
        policy = {'environment': NORMAL_DATA['nonsecret_environment']}
        with patch.dict(os.environ, {'HTTPS_PROXY': 'SYNTHETIC_PROXY', 'GEMINI_API_KEY': 'SYNTHETIC_INHERITED',
                                     'RAG_GEMINI_FALLBACK_MODELS': 'other-model', 'PYTHONPATH': '/wrong'}):
            env = bootstrap.child_environment(policy, COMMON)
        self.assertNotIn('HTTPS_PROXY', env)
        self.assertNotIn('GEMINI_API_KEY', env)
        self.assertNotIn('PYTHONPATH', env)
        self.assertEqual(env['RAG_GEMINI_API_KEY'], 'SYNTHETIC_OFFLINE_KEY')
        self.assertEqual(env['RAG_GEMINI_FALLBACK_MODELS'], 'gemini-3.5-flash-lite')


def verify(output):
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'unit-tests.log').open('x') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    counts = {'total': result.testsRun, 'skip': len(result.skipped), 'failures': len(result.failures), 'errors': len(result.errors)}
    bridge.write_new(output / 'test-counts.json', bridge.canonical(counts) + b'\n')
    if not result.wasSuccessful():
        raise SystemExit('FAILED: see new unit-tests.log; prior outputs untouched')
    probes = []
    for condition, blocked in [('c1-pre-security', False), ('c1-sec-merged', False), ('c1-sec-fixed', False),
                               ('c1-sec-merged', True), ('c1-sec-fixed', True)]:
        directory = output / (condition + ('-blocked' if blocked else '-generated'))
        directory.mkdir()
        scenario = Scenario(directory, condition=condition, blocked=blocked)
        answer = scenario.collect()
        judgment = scenario.judge()
        probes.append({'condition': condition, 'blocked': blocked, 'answer': answer, 'judgment': judgment,
                       'mock_provider_sends': scenario.fake.sends, 'chat_calls': scenario.chat_calls})
    for condition in NORMAL_DATA['code_conditions'].values():
        bridge.guard.verify_snapshot(condition)
    # Reuse original guard suite as an independent subprocess, no old outputs changed.
    import subprocess
    previous = subprocess.run([sys.executable, '-B', str(bridge.GUARD_DIR / 'test_runtime_guard.py')],
                              capture_output=True, timeout=30,
                              env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHONDONTWRITEBYTECODE': '1'})
    bridge.write_new(output / 'guard-regression.log', previous.stdout + previous.stderr)
    bridge.require(previous.returncode == 0, 'prior_guard_suite_failed')
    import ast
    for source in Path(__file__).parent.glob('*.py'):
        ast.parse(source.read_text())
    report = {'status': 'OFFLINE_BRIDGE_VERIFIED_REAL_SERVER_HANDSHAKE_PENDING', 'tests': counts,
              'prior_guard_tests': {'total': 30, 'skip': 0, 'failures': 0, 'errors': 0},
              'probes': probes, 'provider_mock_sends_in_probes': sum(p['mock_provider_sends'] for p in probes),
              'external_llm_calls': 0, 'http_server_starts': 0, 'holdout_read': False,
              'private_pipe_real_child_tested': True, 'real_listener_tested': False,
              'frozen_collector_and_judge_used': True, 'actual_frozen_service_handler_used': False,
              'live_execution_enabled': False, 'guard_sha256': bridge.GUARD_SHA,
              'tool_sha256': {p.name: bridge.file_sha(p) for p in Path(__file__).parent.glob('*.py')},
              'artifact_sha256': {p.relative_to(output).as_posix(): bridge.file_sha(p)
                                  for p in sorted(output.rglob('*')) if p.is_file()}}
    bridge.write_new(output / 'verification.json', json.dumps(report, ensure_ascii=False, indent=2).encode() + b'\n')
    print(json.dumps({k: report[k] for k in ('status', 'tests', 'provider_mock_sends_in_probes', 'external_llm_calls')}))


if __name__ == '__main__':
    if '--pipe-probe' in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument('--pipe-probe', action='store_true')
        parser.add_argument('--bad-nonce', action='store_true')
        parser.add_argument('--silent', action='store_true')
        parser.add_argument('--attestation-fd', type=int)
        parser.add_argument('--nonce')
        args = parser.parse_args()
        if args.silent:
            signal.pause()
        os.write(args.attestation_fd, bridge.canonical({'pid': os.getpid(),
                  'nonce': 'b' * 64 if args.bad_nonce else args.nonce, 'mode': 'SYNTHETIC_PIPE_ONLY'}) + b'\n')
        os.close(args.attestation_fd)
        signal.pause()
    elif '--output' in sys.argv:
        verify(Path(sys.argv[sys.argv.index('--output') + 1]))
    else:
        unittest.main(verbosity=2)
