"""Owned frozen-service bootstrap for an explicitly approved LOCAL MOCK test.

No live provider backend exists. Default invocation refuses to start a server.
This file is prepared, not executed against a real listener in this revision.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import sys

import execution_bridge as bridge


def offline_provider_audit(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'urllib.Request'):
        raise RuntimeError('outbound_network_disabled_in_mock_server')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if 'holdout' in str(path).lower() and path.suffix == '.pyc':
            raise FileNotFoundError('protected_bytecode_not_read')
        if path.name.startswith('.env') or ('holdout' in str(path).lower() and path.suffix != '.py'):
            raise RuntimeError('protected_data_disabled')


class MockResponse(io.BytesIO):
    status = 200


class MockProvider:
    def open(self, request, timeout):
        # Deliberately canned. Neither a semantic evaluation nor a real model.
        payload = {'modelVersion': 'gemini-3.5-flash-lite', 'candidates': [{'content': {'parts': [
            {'text': '평가용 가상 열람실의 평일 운영시간은 오전 9시부터 오후 6시까지입니다.'}]}}]}
        return MockResponse(json.dumps(payload, ensure_ascii=False).encode())


def child_environment(policy, condition):
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8',
           **policy['environment'], 'RAG_GEMINI_API_KEY': 'SYNTHETIC_OFFLINE_KEY'}
    if condition['context_security_env']:
        env['RAG_CONTEXT_SECURITY_MODE'] = condition['context_security_env']
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--slot-id', required=True)
    parser.add_argument('--attestation-fd', type=int, required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--start-local-mock-server', action='store_true')
    args = parser.parse_args()
    # Approval checkpoint before even reading the run directory.
    bridge.require(args.start_local_mock_server, 'explicit_local_test_server_start_required')
    sys.addaudithook(offline_provider_audit)
    policy_path = args.run_root / 'policy.json'
    bridge.file_sha(policy_path)
    policy = bridge.strict_json(policy_path.read_bytes())
    bridge.require(policy['experiment_id'].startswith('SYNTHETIC-'), 'only_synthetic_mock_runs_supported')
    run = bridge.Run(args.run_root, policy)
    slot = run.ledger.slot(args.slot_id)
    bridge.require(slot['role'] == 'generation' and slot['phase'] == 'attack-v2', 'mock_fixture_slot_required')
    condition = policy['conditions'][slot['condition_id']]
    bridge.guard.verify_snapshot(condition)
    bridge.guard.verify_file(slot['index']['path'], slot['index']['sha256'])
    expected_env = child_environment(policy, condition)
    actual_controls = {k: v for k, v in os.environ.items()
                       if k.startswith(('RAG_', 'GEMINI_', 'GOOGLE_', 'PYTHON')) or k.lower().endswith('_proxy')}
    expected_controls = {k: v for k, v in expected_env.items()
                         if k.startswith(('RAG_', 'GEMINI_', 'GOOGLE_', 'PYTHON'))}
    bridge.require(actual_controls == expected_controls, 'child_environment_not_clean_or_pinned')
    with run.ledger._transaction() as db:
        execution = db.execute('SELECT nonce,binding,artifact_sha FROM executions WHERE slot=?', (args.slot_id,)).fetchone()
    bridge.require(execution is not None and execution['nonce'] == args.nonce
                   and execution['binding'] is None and execution['artifact_sha'] is None, 'child_slot_not_fresh')
    identity = {}
    bound = []
    def identity_check():
        bridge.require(bool(bound), 'server_not_attested')
        fresh = bridge.guard.attest_bound_server(api, bound[0], condition, slot['index'], args.nonce, dict(sys.modules))
        bridge.require(fresh == identity, 'runtime_changed_before_provider_request')
        return fresh
    transport = bridge.guard.GuardedTransport(run.ledger, args.slot_id, opener=MockProvider(), identity_check=identity_check)
    sys.path.insert(0, str(Path(condition['root']) / 'scripts'))
    with transport.installed():
        import search_api as api
        original_server = api.ThreadingHTTPServer
        def bound_server(address, handler):
            bridge.require(address == ('127.0.0.1', 0), 'only_ephemeral_loopback_listener_allowed')
            server = original_server(address, handler)
            try:
                identity.update(bridge.guard.attest_bound_server(api, server, condition, slot['index'], args.nonce, dict(sys.modules)))
                bound.append(server)
                frame = bridge.canonical(identity) + b'\n'
                while frame:
                    count = os.write(args.attestation_fd, frame)
                    bridge.require(count > 0, 'attestation_pipe_write_failed')
                    frame = frame[count:]
                os.close(args.attestation_fd)
            except BaseException:
                server.server_close()
                raise
            return server
        # Runtime adapter only. Frozen files and behavior inside the handler stay untouched.
        api.ThreadingHTTPServer = bound_server
        missing = run.root / 'intentionally-absent-dense-index'
        bridge.require(not missing.exists(), 'unexpected_dense_artifact')
        sys.argv = [str(Path(api.__file__)), '--host', '127.0.0.1', '--port', '0',
                    '--index', slot['index']['path'], '--default-parser-profile', 'cascade',
                    '--context-chunks-per-document', '2', '--dense-index', str(missing),
                    '--learned-dense-root', str(missing)]
        api.main()


if __name__ == '__main__':
    main()
