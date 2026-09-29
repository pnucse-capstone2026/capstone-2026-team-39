"""Evaluation-only collection/finalization bridge. No automatic live execution.

Pinned v1 runtime guard is reused unchanged. Live approval, real server startup,
and external credentials are deliberately not implemented by this CLI/library.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import secrets
import selectors
import subprocess
import sys
import time
from unittest.mock import patch

GUARD_DIR = Path(__file__).resolve().parents[1] / 'security-runtime-guard-20260908-v1'
GUARD_SHA = '4e4b34c6d3b63eaed9344ce7756e87462a331ed6b53249195260f62d5dc979a9'
import hashlib
if hashlib.sha256((GUARD_DIR / 'runtime_guard.py').read_bytes()).hexdigest() != GUARD_SHA:
    raise RuntimeError('pinned_runtime_guard_changed')
spec = importlib.util.spec_from_file_location('pnu_pinned_runtime_guard', GUARD_DIR / 'runtime_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
GuardError = guard.GuardError
canonical, digest, file_sha = guard.canonical, guard.digest, guard.file_sha


def require(ok, message):
    if not ok:
        raise GuardError(message)


def strict_json(data):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def constant(_):
        raise GuardError('nonfinite_json_number')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def read_one(path):
    before = file_sha(path)  # Protected paths/symlinks rejected before reading.
    raw = Path(path).read_bytes()
    require(len(raw) <= 16 * 1024 * 1024 and raw.endswith(b'\n'), 'incomplete_or_large_artifact')
    rows = raw.splitlines()
    require(len(rows) == 1 and bool(rows[0]), 'expected_single_record')
    value = strict_json(rows[0])
    require(isinstance(value, dict) and digest(raw) == before == file_sha(path), 'artifact_changed')
    return value, before


def write_new(path, data):
    """Exclusive durable output; incomplete output remains visible for diagnosis."""
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def provider_request_sha(model, body, *, ensure_ascii):
    endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent'
    return digest(endpoint.encode() + b'\0POST\0' + json.dumps(body, ensure_ascii=ensure_ascii).encode())


def validate_health(collector, health, slot, controls, condition, attestation, child, nonce):
    # Actual service health stores freeze INSIDE service_config, not at the root.
    config = health.get('service_config') or {}
    guard.verify_owned_handshake(attestation, child, nonce, condition, slot['index'],
                                 {'freeze': config.get('freeze')}, attestation['listener'][1])
    generation = config.get('generation') or {}
    require(generation.get('allowed_models') == [controls['model']], 'unexpected_model_fallback')
    projected = collector.validate_health_controls(
        health, parser_profile=controls['parser_profile'], retrieval_mode=controls['retrieval_mode'],
        expected_corpus_revision=slot['index']['corpus_revision'], expected_retrieval_tuning=True,
        expected_context_chunks_per_document=2, expected_generation_provider='frontier',
        expected_generation_model=controls['model'], expected_generation_max_context_chars=24000,
        expected_generation_max_output_tokens=900, expected_generation_sampling_parameters=[],
        require_eval_trace=True)
    require(projected['profile_index']['sha256'] == slot['index']['sha256'], 'profile_index_mismatch')
    require(isinstance(slot.get('source_manifest_sha256'), str) and len(slot['source_manifest_sha256']) == 64,
            'source_manifest_pin_required')
    require(projected['profile_index']['source_manifest_sha256'] == slot['source_manifest_sha256'],
            'profile_source_manifest_mismatch')
    return projected


def collector_arguments(slot, controls, *, experiment_id, api_base, output, security_mode):
    args = ['--cases', slot['case_file']['path'], '--out', str(output), '--api-base', api_base,
            '--experiment-id', experiment_id, '--condition-id', slot['condition_id'],
            '--generation-run-id', slot['generation_run_id'], '--provider', 'frontier',
            '--model', controls['model'], '--institution', 'none', '--context-k', '8',
            '--parser-profile', 'cascade', '--retrieval-mode', 'bm25', '--eval-trace',
            '--expected-security-mode', security_mode, '--expected-corpus-revision', slot['index']['corpus_revision'],
            '--expected-retrieval-tuning', 'on', '--expected-context-chunks-per-document', '2',
            '--expected-generation-max-context-chars', '24000', '--expected-generation-max-output-tokens', '900',
            '--expected-generation-sampling', 'absent', '--max-attempts', '3', '--retry-backoff', '3',
            '--sleep', '3', '--timeout', '180', '--only', slot['case_id'], '--allow-partial']
    # No --allow-unpinned, no clean-Git bypass, no --expected-index-sha256 on snapshots.
    # The owned-process handshake checks the index and full snapshot independently.
    return args


def collect_one(run, slot_id, collector, *, api_base, security_mode, server_config, transport_identity):
    """Call the unmodified collector once. Caller owns/attests/stops the server.

    Its normal build_record_base hook runs before /chat: pin the exact collector
    configuration there. Tests mock call_health/call_chat, not the collector.
    Must be under run.locked(), after begin() and a verified runtime handshake.
    """
    slot = run.ledger.slot(slot_id)
    require(slot['role'] == 'generation' and not run.output(slot_id).exists(), 'unsafe_collection_restart')
    guard.verify_file(slot['case_file']['path'], slot['case_file']['sha256'])
    controls = run.policy['controls']
    cases = collector.load_cases(Path(slot['case_file']['path']))
    expected = {
        'security_eval_contract': collector.SECURITY_EVAL_CONTRACT, 'expected_security_mode': security_mode,
        'collector_source_sha256': file_sha(collector.__file__), 'api_base': api_base.rstrip('/'),
        'cases_path': slot['case_file']['path'], 'cases_sha256': slot['case_file']['sha256'],
        'cases_canonical_sha256': digest(canonical(cases)), 'selected_case_ids_sha256': digest(canonical([slot['case_id']])),
        'provider': 'frontier', 'model': controls['model'], 'institution': None, 'context_k': 8,
        'parser_profile': 'cascade', 'retrieval_mode': 'bm25',
        'expected_corpus_revision': slot['index']['corpus_revision'], 'expected_retrieval_tuning': True,
        'expected_context_chunks_per_document': 2, 'expected_generation_max_context_chars': 24000,
        'expected_generation_max_output_tokens': 900, 'expected_generation_sampling_parameters': [],
        'expected_git_commit': None, 'expected_index_sha256': None, 'expected_source_manifest_sha256': None,
        'eval_trace': True, 'allow_unpinned': False, 'max_attempts': 3, 'retry_backoff_seconds': 3.0,
        'request_timeout_seconds': 180.0, 'inter_call_sleep_seconds': 3.0,
        'errors_path': str(run.output(slot_id).with_suffix('.errors.jsonl')),
        'attempt_journal_path': None, 'server_config': server_config,
    }
    original = collector.build_record_base
    def bind_before_chat(case, **kwargs):
        require(case['id'] == slot['case_id'], 'collector_selected_wrong_case')
        require(kwargs['collector_config'] == expected, 'collector_configuration_mismatch')
        run.bind(slot_id, {'collector_config': expected,
                           'transport_identity_sha256': digest(canonical(transport_identity))})
        return original(case, **kwargs)
    with patch.object(collector, 'build_record_base', side_effect=bind_before_chat):
        collector.main(collector_arguments(slot, controls, experiment_id=run.policy['experiment_id'],
                                           api_base=api_base, output=run.output(slot_id), security_mode=security_mode))
    require(run.output(slot_id).is_file(), 'collector_did_not_write_answer')


def judge_one(run, slot_id, validator, *, transport, api_key):
    """Frozen Judge input/prompt/call/guard/record code, with a metered transport.

    Credential must be injected only by a separately approved launcher. No env
    file reading here. Retry/sleep settings remain the already pinned values.
    """
    slot = run.ledger.slot(slot_id)
    require(slot['role'] == 'judge' and not run.output(slot_id).exists(), 'unsafe_judge_restart')
    generation_id = slot_id.rsplit(':', 1)[0] + ':generation'
    expected = run.ledger.summary()['sealed_slots'].get(generation_id)
    require(expected is not None, 'judge_requires_sealed_answer')
    answer, answer_sha = read_one(run.output(generation_id))
    require(answer_sha == expected, 'sealed_answer_changed')
    judge = validator.judge
    case = validator.case(slot)
    judge.validate_answer_case(answer, case)
    config = judge.build_judge_config(model=slot['model'], max_output_tokens=1600)
    require(config['judge_config_sha256'] == run.policy['controls']['judge']['config_sha256'], 'unpinned_judge_config')
    judge_input = judge.build_judge_input(case, answer)
    prompt = judge.render_judge_prompt(judge_input)
    identity = transport.identity_check()
    run.bind(slot_id, {'answer_file_sha256': answer_sha, 'judge_config': config,
                       'transport_identity_sha256': digest(canonical(identity))})
    with transport.installed():
        result = judge.call_gemini_judge(prompt=prompt, api_key=api_key, model=slot['model'],
                                         max_output_tokens=1600, timeout=180, retries=6, judge_input=judge_input)
    require(result['error'] is None, 'judge_terminal_error')
    record = judge.build_judgment_record(
        answer=answer, case=case, judge_run_id=slot['judge_run_id'], judge_config=config,
        judge_input=judge_input, rendered_prompt=prompt, judge=result['judge'],
        raw_judge_response=result['raw_judge_response'], attempts=result['attempts'],
        deterministic_guard=result['deterministic_guard'], answers_artifact_sha256=answer_sha)
    write_new(run.output(slot_id), json.dumps(record, ensure_ascii=False).encode() + b'\n')
    time.sleep(3)


class Validator:
    """Frozen schema/security/Judge validators plus exact transport/file binding."""
    def __init__(self, *, artifacts, collector, judge, policy):
        self.artifacts, self.collector, self.judge, self.policy = artifacts, collector, judge, policy

    def case(self, slot):
        guard.verify_file(slot['case_file']['path'], slot['case_file']['sha256'])
        cases = self.collector.load_cases(Path(slot['case_file']['path']))
        selected = [c for c in cases if c.get('id') == slot['case_id']]
        require(len(selected) == 1 and digest(canonical(selected[0])) == slot['case_sha256'], 'case_binding_mismatch')
        guard.verify_file(slot['case_file']['path'], slot['case_file']['sha256'])
        return selected[0]

    def common(self, record, slot):
        for key in ('condition_id', 'case_id', 'generation_run_id'):
            require(record.get(key) == slot[key], 'record_slot_identity_mismatch')
        require(record.get('experiment_id') == self.policy['experiment_id'], 'record_experiment_mismatch')
        require(not record.get('error'), 'error_artifact_not_sealable')

    def answer(self, record, slot, execution, attempts):
        self.common(record, slot)
        case = self.case(slot)
        self.judge.validate_answer_case(record, case)
        require(record.get('query') == case['query'], 'answer_query_mismatch')
        require(record.get('slot_outcome') == 'answer' and record.get('answer_eligible_for_judge') is True,
                'answer_not_eligible')
        config = record.get('collector_config')
        require(config == execution['collector_config'], 'collector_config_drift')
        require(record.get('collector_config_sha256') == digest(canonical(config)), 'collector_config_hash_mismatch')
        request = self.collector.build_chat_body(case, provider='frontier', model=self.policy['controls']['model'],
                                                top_k=8, parser_profile='cascade', retrieval_mode='bm25',
                                                institution=None, eval_trace=True)
        require(record.get('request') == request, 'answer_request_mismatch')
        response = {**record, **record.get('response_config', {}), 'results': record.get('sources')}
        observation = self.collector.validate_response_controls(
            response, provider='frontier', model=self.policy['controls']['model'], parser_profile='cascade',
            retrieval_mode='bm25', institution=None, expected_corpus_revision=slot['index']['corpus_revision'],
            require_eval_trace=True, expected_security_mode=config['expected_security_mode'])
        self.collector.validate_saved_security_record(record)
        self.collector.validate_answer_payload(response)
        require(record.get('collection_attempt_number') == 1, 'recollected_answer_forbidden')
        self.artifacts._validate_success_request_attempts(record)
        if observation['outcome'] == 'security_abstention':
            require(not attempts, 'security_abstention_has_provider_attempts')
        else:
            self.artifacts.validate_final_generation_provenance(
                record, provider='frontier', model=slot['model'], max_output_tokens=900,
                require_collection_attempts=True)
            trace = record['evaluation_trace']['generation_input']
            expected = provider_request_sha(slot['model'], {
                'systemInstruction': {'parts': [{'text': trace['system_instruction']}]},
                'contents': [{'role': 'user', 'parts': [{'text': trace['user_prompt']}]}],
                'generationConfig': {'maxOutputTokens': 900}}, ensure_ascii=False)
            require(bool(attempts) and attempts[-1]['state'] == 'response_closed', 'missing_successful_provider_attempt')
            require(all(a['request_sha'] == expected for a in attempts), 'generation_transport_binding_mismatch')

    def judgment(self, record, slot, execution, attempts, answer, answer_file_sha):
        self.common(record, slot)
        self.artifacts.validate_judgment_record(record)
        case = self.case(slot)
        self.judge.validate_answer_case(answer, case)
        require(record.get('judge_run_id') == slot['judge_run_id'], 'judge_run_mismatch')
        for key, expected in {'answer_id': answer['answer_id'], 'answer_sha256': answer['answer_sha256'],
                              'answer_record_sha256': digest(canonical(answer)),
                              'answers_artifact_sha256': answer_file_sha}.items():
            require(record.get(key) == expected, 'judge_answer_binding_mismatch')
        config = self.judge.build_judge_config(model=slot['model'], max_output_tokens=1600)
        require(config['judge_config_sha256'] == self.policy['controls']['judge']['config_sha256'], 'unpinned_judge_config')
        require(record.get('judge_config') == config, 'judge_config_drift')
        judge_input = self.judge.build_judge_input(case, answer)
        prompt = self.judge.render_judge_prompt(judge_input)
        require(record.get('judge_input_sha256') == digest(canonical(judge_input)), 'judge_input_mismatch')
        require(record.get('rendered_judge_prompt_sha256') == digest(prompt.encode()), 'judge_prompt_mismatch')
        parsed, deterministic = self.judge._parse_json_response(record['raw_judge_response'], judge_input=judge_input)
        require(parsed == record['judge'] and deterministic == record.get('deterministic_guard'), 'judge_guard_mismatch')
        claimed = record.get('attempts')
        require(isinstance(claimed, list) and len(claimed) == len(attempts) and bool(attempts)
                and len(attempts) <= slot['attempt_cap'], 'judge_attempt_count_mismatch')
        require(claimed[-1].get('status') == 'ok' and attempts[-1]['state'] == 'response_closed',
                'judge_no_successful_attempt')
        expected = provider_request_sha(slot['model'], {
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {'temperature': 0.0, 'maxOutputTokens': 1600,
                                 'responseMimeType': 'application/json',
                                 'responseJsonSchema': self.judge.JUDGE_RESPONSE_JSON_SCHEMA}}, ensure_ascii=True)
        require(all(a['request_sha'] == expected for a in attempts), 'judge_transport_binding_mismatch')
        require(execution['answer_file_sha256'] == answer_file_sha, 'judge_execution_input_changed')


class Run:
    """Persistent fixed-directory coordination. No automatic retry of started work."""
    @classmethod
    def create_offline(cls, root, policy):
        require(policy['experiment_id'].startswith('SYNTHETIC-'), 'live_run_creation_disabled')
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=False)
        write_new(root / 'policy.json', canonical(policy) + b'\n')
        ledger = guard.Ledger.create(root / 'provider-attempts.sqlite', policy)
        with ledger._transaction() as db:
            db.execute('CREATE TABLE executions(slot TEXT PRIMARY KEY, nonce TEXT NOT NULL, binding TEXT, artifact_sha TEXT)')
        write_new(root / 'run.json', canonical({'root': str(root), 'policy_sha': digest(canonical(policy)),
                                                'ledger_inode': ledger.path.stat().st_ino, 'mode': 'offline'}) + b'\n')
        (root / 'results').mkdir()
        return cls(root, policy)

    def __init__(self, root, policy):
        self.root = Path(root).resolve(strict=True)
        self.policy = json.loads(canonical(policy))
        metadata = strict_json((self.root / 'run.json').read_bytes())
        require(metadata['root'] == str(self.root), 'run_directory_changed')
        guard.verify_file(self.root / 'policy.json', digest(canonical(policy) + b'\n'))
        require(metadata['policy_sha'] == digest(canonical(policy)), 'run_policy_changed')
        require((self.root / 'provider-attempts.sqlite').stat().st_ino == metadata['ledger_inode'], 'ledger_replaced')
        self.ledger = guard.Ledger(self.root / 'provider-attempts.sqlite', policy)
        self._owns_lock = False

    @contextmanager
    def locked(self):
        fd = os.open(self.root / 'run.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise GuardError('run_already_owned') from None
            self._owns_lock = True
            yield self
        finally:
            self._owns_lock = False
            os.close(fd)

    def output(self, slot_id):
        slot = self.ledger.slot(slot_id)
        suffix = '.answers.jsonl' if slot['role'] == 'generation' else '.judgments.jsonl'
        return self.root / 'results' / (digest(slot_id.encode()) + suffix)

    def begin(self, slot_id):
        require(self._owns_lock, 'run_lock_required')
        self.ledger.slot(slot_id)
        with self.ledger._transaction() as db:
            self.ledger._validate(db)
            require(not db.execute('SELECT 1 FROM executions WHERE slot=?', (slot_id,)).fetchone(),
                    'slot_already_started_reconcile_first')
            require(not db.execute('SELECT 1 FROM executions WHERE artifact_sha IS NULL').fetchone(),
                    'previous_execution_unfinished')
            require(not self.output(slot_id).exists(), 'unregistered_artifact')
            nonce = secrets.token_hex(32)
            db.execute('INSERT INTO executions VALUES(?,?,NULL,NULL)', (slot_id, nonce))
            return nonce

    def bind(self, slot_id, binding):
        require(self._owns_lock, 'run_lock_required')
        with self.ledger._transaction() as db:
            self.ledger._validate(db)
            row = db.execute('SELECT binding FROM executions WHERE slot=?', (slot_id,)).fetchone()
            require(row is not None, 'slot_not_started')
            serialized = canonical(binding).decode()
            require(row['binding'] in (None, serialized), 'execution_binding_changed')
            db.execute('UPDATE executions SET binding=? WHERE slot=?', (serialized, slot_id))

    def finalize(self, slot_id, validator):
        """After owned worker exit: validate saved file then seal, including resume.

        Saved-but-unsealed results are revalidated, never regenerated. No file
        means the slot stays started, requiring an explicit operator decision.
        """
        require(self._owns_lock, 'run_lock_required')
        slot = self.ledger.slot(slot_id)
        summary = self.ledger.summary()
        require(summary['unresolved'] == 0, 'unresolved_provider_attempt')
        with self.ledger._transaction() as db:
            row = db.execute('SELECT binding FROM executions WHERE slot=?', (slot_id,)).fetchone()
        require(row is not None and row['binding'] is not None, 'missing_execution_binding')
        binding = strict_json(row['binding'])
        record, sha = read_one(self.output(slot_id))
        attempts = [r for r in summary['attempts'] if r['slot'] == slot_id]
        require(all(r['identity_sha'] == binding['transport_identity_sha256'] for r in attempts),
                'provider_identity_binding_mismatch')
        if slot['role'] == 'generation':
            validator.answer(record, slot, binding, attempts)
        else:
            generation_id = slot_id.rsplit(':', 1)[0] + ':generation'
            expected = summary['sealed_slots'].get(generation_id)
            require(expected is not None, 'judge_requires_sealed_answer')
            answer, answer_sha = read_one(self.output(generation_id))
            require(answer_sha == expected, 'sealed_answer_changed')
            validator.judgment(record, slot, binding, attempts, answer, answer_sha)
        guard.verify_file(self.output(slot_id), sha)
        self.ledger.seal_slot(slot_id, sha)
        # If killed after seal but before this bookkeeping write, finalize is idempotent.
        with self.ledger._transaction() as db:
            db.execute('UPDATE executions SET artifact_sha=? WHERE slot=?', (sha, slot_id))
        return {'slot_id': slot_id, 'artifact_sha256': sha, 'provider_attempts': len(attempts)}


class OwnedChild:
    """Private-pipe handshake and cleanup of ONLY the Popen child we created.

    The command is supplied by the reviewed launcher, never by question text.
    Tests use a synthetic pipe worker; no real service is launched here.
    """
    def __init__(self, command, env, nonce, log_path):
        self.command, self.env, self.nonce, self.log_path = command, env, nonce, Path(log_path)

    def __enter__(self):
        self.read_fd, write_fd = os.pipe()
        self.log = self.log_path.open('xb')
        try:
            self.child = subprocess.Popen([*self.command, '--attestation-fd', str(write_fd), '--nonce', self.nonce],
                                          env=self.env, pass_fds=(write_fd,), stdin=subprocess.DEVNULL,
                                          stdout=self.log, stderr=subprocess.STDOUT)
        except BaseException:
            os.close(self.read_fd)
            self.log.close()
            raise
        finally:
            os.close(write_fd)
        return self

    def receive(self, timeout=10):
        deadline = time.monotonic() + timeout
        raw = b''
        with selectors.DefaultSelector() as selector:
            selector.register(self.read_fd, selectors.EVENT_READ)
            while b'\n' not in raw:
                remaining = deadline - time.monotonic()
                require(remaining > 0 and bool(selector.select(max(0, remaining))), 'child_handshake_timeout')
                chunk = os.read(self.read_fd, 4096)
                require(bool(chunk), 'child_closed_attestation_pipe')
                raw += chunk
                require(len(raw) <= 1024 * 1024, 'child_attestation_too_large')
        require(raw.endswith(b'\n') and len(raw.splitlines()) == 1, 'invalid_child_message')
        record = strict_json(raw)
        require(record.get('pid') == self.child.pid and record.get('nonce') == self.nonce,
                'child_identity_mismatch')
        require(self.child.poll() is None, 'child_exited_before_handshake')
        return record

    def __exit__(self, exc_type, exc, tb):
        if self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)
        os.close(self.read_fd)
        self.log.close()
