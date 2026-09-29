"""Approved ephemeral loopback integration of frozen service -> collector/Judge.

Only existing synthetic attack fixtures, fake generation/Judge providers and new
output paths. Never starts a live provider or touches an existing server.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
from pathlib import Path
import socket
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_DIR = ROOT / 'evidence/security-execution-bridge-20260908-v1'
sys.path.insert(0, str(BRIDGE_DIR))
import execution_bridge as bridge
import local_mock_bootstrap as bootstrap

LOCAL_BOOTSTRAP = Path(__file__).with_name('local_mock_bootstrap_v2.py')

PINS = {
    BRIDGE_DIR / 'execution_bridge.py': '93da28ecd67822c63532fa2c1c7df07a412677ca3454dac1196a7e587d81f7d0',
    BRIDGE_DIR / 'local_mock_bootstrap.py': 'cc9a1dfc034ff69b318f8b22651b86c7bc03abc5cf74a23e790d294071d69bc2',
    ROOT / 'processed/eval/preflight-20260908/security-runtime-guard-v1/verification-v1/policy.json':
        '3c36f78b2b5e73e213998e30a4fd0a91b63adb810eaab51fdf5c680f793c48bb',
    ROOT / 'processed/eval/preflight-20260908/security-attack-preparation-v2/manifest.json':
        'a1b39cb042e1c930a48268973c34f48826cbcdcb00932f1010ca79bb06445eb5',
}
ALLOWED_PORTS = set()


def protected_files(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if 'holdout' in str(path).lower() and path.suffix == '.pyc':
            raise FileNotFoundError('protected_bytecode_cache_not_read')
        if path.name.startswith('.env') or ('holdout' in str(path).lower() and path.suffix != '.py'):
            raise RuntimeError('protected_data_disabled')


def parent_audit(event, args):
    protected_files(event, args)
    if event == 'socket.connect':
        address = args[1]
        bridge.require(isinstance(address, tuple) and address[0] == '127.0.0.1'
                       and address[1] in ALLOWED_PORTS, 'only_owned_loopback_connect_allowed')
    elif event == 'socket.getaddrinfo':
        bridge.require(args[0] == '127.0.0.1' and int(args[1]) in ALLOWED_PORTS,
                       'only_owned_loopback_resolution_allowed')
    elif event == 'urllib.Request':
        url = urllib.parse.urlsplit(args[0])
        bridge.require(url.scheme == 'http' and url.hostname == '127.0.0.1'
                       and url.port in ALLOWED_PORTS and url.path in ('/health', '/chat')
                       and not url.query and not url.fragment and not url.username,
                       'only_owned_health_chat_allowed')


class FakeJudgeResponse(io.BytesIO):
    status = 200


class FakeJudgeProvider:
    def __init__(self, case):
        self.calls = 0
        self.verdict = {
            'score': 0, 'grounded_fully_correct': False,
            'claim_checks': [{'claim_id': c['claim_id'], 'status': 'missing', 'answer_quote': None,
                              'reason': 'SYNTHETIC_MOCK_JUDGE_NOT_A_QUALITY_LABEL'} for c in case['required_claims']],
            'unsupported_facts': [], 'contradictions': [], 'citation_support': 'none',
            'abstention': 'not_applicable', 'uncertain': False,
            'reason': 'SYNTHETIC_MOCK_JUDGE_NOT_A_QUALITY_LABEL',
        }

    def open(self, request, timeout):
        self.calls += 1
        return FakeJudgeResponse(json.dumps({'candidates': [{'content': {'parts': [
            {'text': json.dumps(self.verdict, ensure_ascii=False)}]}}]}).encode())


def build_policy():
    for path, pin in PINS.items():
        bridge.guard.verify_file(path, pin)
    base = json.loads((ROOT / 'processed/eval/preflight-20260908/security-runtime-guard-v1/verification-v1/policy.json').read_bytes())
    attack = json.loads((ROOT / 'processed/eval/preflight-20260908/security-attack-preparation-v2/manifest.json').read_bytes())
    for path, pin in base['input_sha256'].items():
        bridge.guard.verify_file(path, pin)
    for condition in base['conditions'].values():
        bridge.guard.verify_snapshot(condition)
    fixtures = {f['case_id']: f for f in attack['fixtures']}
    selected_ids = {'secpilot-a03-clean', 'secpilot-a10-attack'}
    slots = [copy.deepcopy(s) for s in base['slots'] if s['phase'] == 'attack-v2' and s['case_id'] in selected_ids]
    for slot in slots:
        slot['source_manifest_sha256'] = fixtures[slot['case_id']]['documents_sha256']
    bridge.require(len(slots) == 12, 'expected_six_generation_judge_pairs')
    return {**base, 'experiment_id': 'SYNTHETIC-local-listener-20260909-v1', 'slots': slots, 'total_cap': 54,
            'api_execution_authorized': False, 'local_mock_server_start_authorized': True,
            'authorization_scope': '2026-09-09 user approved ephemeral local mock servers; no external API',
            'live_runner_ready': False, 'status': 'LOCAL_MOCK_ONLY',
            'selection': 'a03 clean normal path + a10 attack no-generation path, all three frozen conditions',
            'limits': 'Scores are fake; do not report as GFC/ASR or spend the real pilot budget.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--start-local-mock-servers', action='store_true')
    args = parser.parse_args()
    bridge.require(args.start_local_mock_servers, 'explicit_local_start_flag_required')
    sys.addaudithook(parent_audit)
    policy = build_policy()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    run = bridge.Run.create_offline(output / 'run', policy)
    common = policy['conditions']['c1-sec-fixed']
    sys.path.insert(0, str(Path(common['root']) / 'scripts'))
    import service_eval_artifacts as artifacts
    import evaluate_security_service_answers as collector
    import judge_service_answers as judge
    for module in (artifacts, collector, judge):
        relative = Path(module.__file__).resolve().relative_to(Path(common['root'])).as_posix()
        bridge.guard.verify_file(module.__file__, common['files_sha256'][relative])
    validator = bridge.Validator(artifacts=artifacts, collector=collector, judge=judge, policy=policy)
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({}), bridge.guard.NoRedirect()))
    probes, children = [], []
    try:
        for ordinal, slot in enumerate([s for s in policy['slots'] if s['role'] == 'generation'], 1):
            condition = policy['conditions'][slot['condition_id']]
            stem = '%02d-%s-%s' % (ordinal, slot['condition_id'], slot['case_id'])
            with run.locked():
                nonce = run.begin(slot['slot_id'])
                command = [sys.executable, '-B', str(LOCAL_BOOTSTRAP),
                           '--run-root', str(run.root), '--slot-id', slot['slot_id'], '--start-local-mock-server']
                port = None
                owned = bridge.OwnedChild(command, bootstrap.child_environment(policy, condition), nonce, output / (stem + '.server.log'))
                try:
                    with owned:
                        children.append(owned.child.pid)
                        attestation = owned.receive(timeout=20)
                        bridge.require(attestation['listener'][0] == '127.0.0.1', 'non_loopback_listener')
                        port = attestation['listener'][1]
                        ALLOWED_PORTS.add(port)
                        api_base = 'http://127.0.0.1:' + str(port)
                        health = collector.call_health(api_base, timeout=10)
                        server_config = bridge.validate_health(collector, health, slot, policy['controls'], condition,
                                                                attestation, owned.child, nonce)
                        bridge.write_new(output / (stem + '.attestation.json'), bridge.canonical(attestation) + b'\n')
                        bridge.write_new(output / (stem + '.health.json'), bridge.canonical(health) + b'\n')
                        bridge.collect_one(run, slot['slot_id'], collector, api_base=api_base,
                                            security_mode=condition['expected_security_mode'], server_config=server_config,
                                            transport_identity=attestation)
                finally:
                    if hasattr(owned, 'child'):
                        bridge.require(owned.child.poll() is not None, 'owned_child_not_stopped')
                # Reopen durable state before finalization: saved-but-unsealed resume path.
                resumed = bridge.Run(run.root, policy)
                bridge.require(resumed.ledger.summary()['unresolved'] == 0, 'unresolved_after_child_exit')
                answer_result = run.finalize(slot['slot_id'], validator)
                sends_before = run.ledger.summary()['reserved_total']
                bridge.require(run.finalize(slot['slot_id'], validator) == answer_result, 'non_idempotent_answer_seal')
                bridge.require(run.ledger.summary()['reserved_total'] == sends_before, 'resume_caused_provider_call')
                answer, answer_sha = bridge.read_one(run.output(slot['slot_id']))
                expected_blocked = slot['case_id'] == 'secpilot-a10-attack' and slot['condition_id'] != 'c1-pre-security'
                bridge.require(answer_result['provider_attempts'] == (0 if expected_blocked else 1), 'unexpected_generation_call_count')
                bridge.require((answer['security_evaluation']['outcome'] == 'security_abstention') == expected_blocked,
                               'unexpected_security_abstention_contract')
                with socket.socket() as check:
                    check.settimeout(0.25)
                    listener_closed = check.connect_ex(('127.0.0.1', port)) != 0
                ALLOWED_PORTS.discard(port)
                bridge.require(listener_closed, 'listener_still_open_after_owned_exit')
                judge_id = slot['slot_id'].rsplit(':', 1)[0] + ':judge'
                judge_nonce = run.begin(judge_id)
                case = validator.case(slot)
                fake_judge = FakeJudgeProvider(case)
                judge_identity = {'mode': 'MOCK_JUDGE_ONLY', 'pid': os.getpid(), 'nonce': judge_nonce,
                                  'judge_source_sha256': bridge.file_sha(judge.__file__), 'answer_file_sha256': answer_sha}
                transport = bridge.guard.GuardedTransport(run.ledger, judge_id, opener=fake_judge,
                                                          identity_check=lambda: judge_identity)
                bridge.judge_one(run, judge_id, validator, transport=transport, api_key='SYNTHETIC_OFFLINE_KEY')
                judgment_result = run.finalize(judge_id, validator)
                bridge.require(fake_judge.calls == 1, 'unexpected_mock_judge_retry')
                probe = {'condition': slot['condition_id'], 'case_id': slot['case_id'], 'pid': owned.child.pid,
                         'listener': ['127.0.0.1', port], 'listener_closed': listener_closed,
                         'child_exit_code': owned.child.returncode, 'answer': answer_result, 'judgment': judgment_result,
                         'outcome': answer['security_evaluation']['outcome'], 'resume_additional_calls': 0}
                probes.append(probe)
                bridge.write_new(output / (stem + '.result.json'), bridge.canonical(probe) + b'\n')
                print(json.dumps({'completed': ordinal, 'total': 6, 'condition': slot['condition_id'],
                                  'case': slot['case_id'], 'generation_mock_calls': answer_result['provider_attempts'],
                                  'judge_mock_calls': fake_judge.calls, 'server_stopped': True}), flush=True)
        bridge.require(len(probes) == 6 and len(run.ledger.summary()['sealed_slots']) == 12, 'incomplete_mock_run')
        bridge.require(run.ledger.summary()['reserved_total'] == 10, 'unexpected_total_mock_calls')
        build_policy()  # Recheck all original source/input pins after serving.
        result = {'status': 'LOCAL_HTTP_MOCK_INTEGRATION_PASSED_NOT_LLM_EVALUATION',
                  'external_llm_calls': 0, 'servers_started': len(children), 'all_owned_servers_stopped': True,
                  'holdout_read': False, 'real_service_handler_used': True, 'real_http_listener_tested': True,
                  'generation_mock_calls': 4, 'judge_mock_calls': 6, 'sealed_slots': 12,
                  'original_pilot_executed': False, 'live_api_backend_enabled': False, 'probes': probes,
                  'runner_sha256': bridge.file_sha(__file__),
                  'local_bootstrap_sha256': bridge.file_sha(LOCAL_BOOTSTRAP),
                  'artifact_sha256': {p.relative_to(output).as_posix(): bridge.file_sha(p)
                                      for p in sorted(output.rglob('*')) if p.is_file()}}
        bridge.write_new(output / 'verification.json', json.dumps(result, ensure_ascii=False, indent=2).encode() + b'\n')
        print(json.dumps({'status': result['status'], 'servers_started': len(children), 'external_llm_calls': 0}), flush=True)
    except BaseException as exc:
        bridge.write_new(output / 'FAILED.json', bridge.canonical({'status': 'FAILED_LOCAL_MOCK',
            'error_type': type(exc).__name__, 'error': str(exc), 'completed_probes': probes,
            'owned_child_pids': children, 'external_llm_calls': 0, 'ledger': run.ledger.summary()}) + b'\n')
        raise


if __name__ == '__main__':
    main()
