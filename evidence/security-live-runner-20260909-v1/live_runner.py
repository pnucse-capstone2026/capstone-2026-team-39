"""Single-key approved pilot launcher; frozen service/collector/Judge stay intact.

Default mode has no provider backend. Live execution needs the explicit approval
file hash and a passing local verification tied to this exact launcher source.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import io
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BRIDGE_DIR = ROOT / 'evidence/security-execution-bridge-20260908-v1'
LOCAL_DIR = ROOT / 'evidence/security-local-mock-20260909-v1'
sys.path[:0] = [str(BRIDGE_DIR), str(LOCAL_DIR)]
import execution_bridge as bridge
import local_mock_bootstrap_v2 as bootstrap
from run_local_mock import FakeJudgeProvider

PROPOSAL = ROOT / 'processed/eval/preflight-20260909/security-live-preparation-v1/preparation-v1/execution-proposal.json'
PROPOSAL_SHA = 'b2935ec8754c6d5052ff92174574a10ff04a95fffba377bd2909aa4d3bb6a7d2'
LIVE_ROOT = ROOT / 'processed/eval/preflight-20260909/security-pilot-live-v1/run'
PINS = {
    BRIDGE_DIR / 'execution_bridge.py': '93da28ecd67822c63532fa2c1c7df07a412677ca3454dac1196a7e587d81f7d0',
    LOCAL_DIR / 'local_mock_bootstrap_v2.py': '32af5afc5bd90eb47e0dd95af5e1e8efc919e2998b5ebe976a56e0e09ffda763',
    LOCAL_DIR / 'run_local_mock.py': 'f2cd518242bbe1dc02514a79b69f66e1107c9543a80a87a48e720448947eb8e9',
    PROPOSAL: PROPOSAL_SHA,
}
PACIFIC = ZoneInfo('America/Los_Angeles')
LOCAL_PORTS = set()
CAPABILITY = threading.local()


def audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if 'holdout' in str(path).lower() and path.suffix == '.pyc':
            raise FileNotFoundError('protected_bytecode_not_read')
        bridge.require('holdout' not in str(path).lower() or path.suffix == '.py', 'holdout_data_disabled')
        if path.name.startswith('.env'):
            bridge.require(getattr(CAPABILITY, 'key_read', False) and path == ROOT / '.env', 'secret_file_disabled')
    if event == 'urllib.Request':
        parsed = urllib.parse.urlsplit(args[0])
        if getattr(CAPABILITY, 'provider', None):
            bridge.require(args[0] == CAPABILITY.provider, 'provider_url_changed')
        else:
            bridge.require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                           and parsed.port in LOCAL_PORTS and parsed.path in ('/health', '/chat')
                           and not parsed.query and not parsed.fragment and not parsed.username,
                           'unapproved_http_route')
    if event in ('socket.connect', 'socket.getaddrinfo') and not getattr(CAPABILITY, 'provider', None):
        address = args[1] if event == 'socket.connect' else args[:2]
        bridge.require(isinstance(address, tuple) and address[0] == '127.0.0.1'
                       and int(address[1]) in LOCAL_PORTS, 'unapproved_network_connect')


def tool_pins():
    return {str(HERE / name): bridge.file_sha(HERE / name) for name in ('live_runner.py', 'live_worker.py')}


def verify_runtime(policy, *, all_inputs=False):
    for path, pin in {**PINS, **policy['launcher_sha256']}.items():
        bridge.guard.verify_file(path, pin)
    if policy['execution_mode'] == 'live':
        bridge.guard.verify_file(policy['approval_file'], policy['approval_sha256'])
    if all_inputs:
        for path, pin in policy['input_sha256'].items():
            bridge.guard.verify_file(path, pin)
        for condition in policy['conditions'].values():
            bridge.guard.verify_snapshot(condition)


def approve(proposal, approval):
    bridge.require(approval.get('approved') is True and approval.get('external_llm_calls') is True, 'explicit_api_approval_required')
    expected = {'proposal_sha256': PROPOSAL_SHA, 'run_root': str(LIVE_ROOT),
                'generation_slots': 102, 'judge_slots': 102, 'total_attempt_cap': 918,
                'single_key': True, 'daily_soft_cap': 450, 'declared_rpd': 500,
                'auto_key_switch': False, 'auto_model_switch': False}
    bridge.require(all(approval.get(k) == v for k,v in expected.items()), 'approval_scope_mismatch')
    bridge.require(proposal['intended_run_root'] == str(LIVE_ROOT) and len(proposal['slots']) == 204
                   and proposal['total_cap'] == 918, 'proposal_scope_mismatch')


def make_policy(*, mock, approval_path=None, approval_sha=None):
    for path, pin in PINS.items():
        bridge.guard.verify_file(path, pin)
    policy = bridge.strict_json(PROPOSAL.read_bytes())
    if mock:
        first_normal = next(s['case_id'] for s in policy['slots'] if s['phase'] == 'normal14')
        selected = {first_normal, 'secpilot-a03-clean', 'secpilot-a10-attack'}
        policy['slots'] = [s for s in policy['slots'] if s['case_id'] in selected]
        bridge.require(len(policy['slots']) == 18, 'expected_nine_mock_pairs')
        policy['total_cap'] = sum(s['attempt_cap'] for s in policy['slots'])
        policy['experiment_id'] = 'SYNTHETIC-live-launcher-verification-20260909-v1'
    else:
        bridge.require(approval_path is not None and approval_sha is not None, 'approval_file_required')
        bridge.guard.verify_file(approval_path, approval_sha)
        approval = bridge.strict_json(Path(approval_path).read_bytes())
        approve(policy, approval)
        policy.update({'approval_file': str(Path(approval_path).resolve()), 'approval_sha256': approval_sha})
    policy.update({'execution_mode': 'mock' if mock else 'live', 'api_execution_authorized': not mock,
                   'live_runner_ready': not mock, 'status': 'MOCK_VERIFICATION' if mock else 'APPROVED_LIVE_PILOT',
                   'launcher_sha256': tool_pins(), 'daily_soft_cap': 450, 'declared_rpd': 500,
                   'prior_project_usage': 'unknown; this ledger counts this run only',
                   'auto_key_switch': False, 'auto_model_switch': False,
                   'transport_min_interval_seconds': 0 if mock else 15})
    verify_runtime(policy, all_inputs=True)
    return policy


def pacific_day(timestamp_ns):
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, PACIFIC).date().isoformat()


class DailyLedger(bridge.guard.Ledger):
    def reserve(self, slot_id, request_sha, identity_sha):
        # A separate process lock makes daily check + original reservation atomic
        # relative to all generation/Judge senders using this launcher.
        fd = os.open(self.path.parent / 'daily-budget.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            bridge.require(not (self.path.parent / 'quota-stop.json').exists(), 'provider_quota_stop_active')
            summary = self.summary()
            today = pacific_day(time.time_ns())
            used = sum(pacific_day(a['started_ns']) == today for a in summary['attempts'])
            bridge.require(used < self.policy['daily_soft_cap'], 'daily_soft_cap_reached')
            return super().reserve(slot_id, request_sha, identity_sha)
        finally:
            os.close(fd)


def open_run(root, policy, *, create):
    root = Path(root)
    bridge.require(not any(p.is_symlink() for p in (root, *root.parents)), 'symlink_run_root')
    root = root.resolve()
    if create:
        if policy['execution_mode'] == 'live':
            bridge.require(root == LIVE_ROOT and policy['api_execution_authorized'], 'unapproved_live_root')
            verify_runtime(policy, all_inputs=True)
            root.mkdir(parents=True, exist_ok=False)
            bridge.write_new(root / 'policy.json', bridge.canonical(policy)+b'\n')
            ledger = bridge.guard.Ledger.create(root / 'provider-attempts.sqlite', policy)
            with ledger._transaction() as db:
                db.execute('CREATE TABLE executions(slot TEXT PRIMARY KEY, nonce TEXT NOT NULL, binding TEXT, artifact_sha TEXT)')
            bridge.write_new(root / 'run.json', bridge.canonical({'root':str(root),
                'policy_sha':bridge.digest(bridge.canonical(policy)), 'ledger_inode':ledger.path.stat().st_ino,
                'mode':'live', 'approval_sha256':policy['approval_sha256']})+b'\n')
            (root/'results').mkdir()
        else:
            bridge.Run.create_offline(root, policy)
        (root/'workers').mkdir()
    run = bridge.Run(root, policy)
    run.ledger = DailyLedger(run.ledger.path, policy)
    return run


def budget_status(run):
    summary = run.ledger.summary()
    today = pacific_day(time.time_ns())
    used = sum(pacific_day(a['started_ns']) == today for a in summary['attempts'])
    completed = len(summary['sealed_slots'])
    remaining = len(run.policy['slots'])-completed
    projected = used + math.ceil(summary['reserved_total']/completed * remaining) if completed else remaining
    return {'pacific_day':today,'this_run_today':used,'total_provider_attempts':summary['reserved_total'],
            'sealed_slots':completed,'remaining_slots':remaining,'projected_today_total':projected,
            'soft_cap':run.policy['daily_soft_cap'],'other_project_usage':'unknown','unresolved':summary['unresolved']}


def check_next_slot_budget(run, slot):
    status = budget_status(run)
    bridge.require(not (run.root/'quota-stop.json').exists(), 'provider_quota_stop_active')
    bridge.require(status['unresolved'] == 0, 'unresolved_provider_attempt')
    bridge.require(status['this_run_today']+slot['attempt_cap'] <= status['soft_cap'], 'pause_before_daily_soft_cap')
    if status['sealed_slots'] >= 12:
        bridge.require(status['projected_today_total'] <= status['soft_cap'], 'pause_projected_daily_quota')
    return status


def parse_key(text):
    values = {}
    for line in text.splitlines():
        match = re.fullmatch(r'\s*(?:export\s+)?(RAG_GEMINI_API_KEY|GOOGLE_API_KEY|GEMINI_API_KEY)\s*=\s*(.*?)\s*', line)
        if match:
            value = match[2]
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            values[match[1]] = value
    for name in ('RAG_GEMINI_API_KEY','GOOGLE_API_KEY','GEMINI_API_KEY'):
        if values.get(name):
            bridge.require(re.fullmatch(r'[A-Za-z0-9_-]{20,256}', values[name]) is not None, 'invalid_configured_key_format')
            return values[name]
    raise bridge.GuardError('configured_gemini_key_missing')


def load_key(policy):
    bridge.require(policy['execution_mode'] == 'live' and policy['api_execution_authorized'], 'key_read_requires_live_approval')
    verify_runtime(policy)
    # Existing task-local credentials, no discovery outside this repo.
    configured = '\n'.join(k+'='+os.environ[k] for k in ('RAG_GEMINI_API_KEY','GOOGLE_API_KEY','GEMINI_API_KEY') if os.environ.get(k))
    if configured:
        return parse_key(configured)
    path = ROOT/'.env'
    bridge.require(path.is_file() and not path.is_symlink(), 'configured_gemini_key_missing')
    CAPABILITY.key_read = True
    try:
        with path.open('r', encoding='utf-8') as stream:
            return parse_key(stream.read(1024*1024))
    finally:
        CAPABILITY.key_read = False


def clean_child_env(policy, condition):
    env = bootstrap.child_environment(policy, condition)
    env.pop('RAG_GEMINI_API_KEY')  # Sent over stdin's private pipe, never Popen env/argv.
    return env


class PrivateChild(bridge.OwnedChild):
    def __init__(self, command, env, nonce, log_path, key):
        super().__init__(command, env, nonce, log_path)
        self._key = key

    def __enter__(self):
        self.read_fd, write_fd = os.pipe()
        lease_read, self.lease_write = os.pipe()
        self.log = self.log_path.open('xb')
        try:
            self.child = subprocess.Popen([*self.command, '--attestation-fd',str(write_fd),
                '--lease-fd',str(lease_read),'--nonce',self.nonce], env=self.env,
                pass_fds=(write_fd,lease_read),stdin=subprocess.PIPE,stdout=self.log,stderr=subprocess.STDOUT)
            self.child.stdin.write((self._key+'\n').encode())
            self.child.stdin.close()
            self._key = None
        except BaseException:
            if hasattr(self,'child') and self.child.poll() is None:
                self.child.terminate()
                self.child.wait(timeout=5)
            os.close(self.read_fd)
            os.close(self.lease_write)
            self.log.close()
            raise
        finally:
            os.close(write_fd)
            os.close(lease_read)
        return self

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.release_lease()

    def release_lease(self):
        if self.lease_write is not None:
            os.close(self.lease_write)
            self.lease_write = None


class LiveProvider:
    """The only real network backend, used behind the durable metered transport."""
    def __init__(self, run, slot_id):
        bridge.require(run.policy['execution_mode'] == 'live' and run.policy['api_execution_authorized'], 'live_backend_not_authorized')
        self.run = run
        self.slot = run.ledger.slot(slot_id)
        self.endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/'+self.slot['model']+':generateContent'
        self.opener = bridge.guard.direct_opener()

    def open(self, request, timeout):
        bridge.require(request.full_url == self.endpoint and request.get_method() == 'POST', 'provider_request_changed')
        verify_runtime(self.run.policy)
        CAPABILITY.provider = self.endpoint
        try:
            return self.opener.open(request,timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (401,403,404,429):
                # Stop after the first auth/model/quota error. Do not burn daily
                # requests with repeated 429s or switch credentials/models.
                path = self.run.root/'quota-stop.json'
                if not path.exists():
                    bridge.write_new(path,bridge.canonical({'http_status':exc.code,
                        'slot_id':self.slot['slot_id'],'action':'STOP_AND_NOTIFY_USER',
                        'detail':'No automatic key/model switch; RPM/TPM/RPD distinction not inferred.'})+b'\n')
            raise
        finally:
            CAPABILITY.provider = None


class PacedTransport(bridge.guard.GuardedTransport):
    def urlopen(self, *args, **kwargs):
        interval = self.ledger.policy['transport_min_interval_seconds']
        history = self.ledger.summary()['attempts']
        if history and interval:
            remaining = interval-(time.time_ns()-history[-1]['started_ns'])/1_000_000_000
            if remaining > 0:
                time.sleep(min(remaining,interval))
        return super().urlopen(*args, **kwargs)


class MockGeneration:
    def __init__(self, policy, slot):
        # This is synthetic transport data, not a model evaluation.
        bridge.guard.verify_file(slot['case_file']['path'],slot['case_file']['sha256'])
        cases = [bridge.strict_json(line) for line in Path(slot['case_file']['path']).read_bytes().splitlines() if line]
        case = next(c for c in cases if c['id']==slot['case_id'])
        self.text = '\n'.join(c['description'] for c in case.get('required_claims',[])) or '합성 연결 검증 응답입니다.'
        self.model = slot['model']

    def open(self,request,timeout):
        response = io.BytesIO(json.dumps({'modelVersion':self.model,'candidates':[{'content':{'parts':[{'text':self.text}]}}]},ensure_ascii=False).encode())
        response.status = 200
        return response


def load_validator(policy):
    condition = policy['conditions']['c1-sec-fixed']
    sys.path.insert(0,str(Path(condition['root'])/'scripts'))
    import service_eval_artifacts as artifacts
    import evaluate_security_service_answers as collector
    import judge_service_answers as judge
    for module in (artifacts,collector,judge):
        path = Path(module.__file__).resolve()
        relative = path.relative_to(Path(condition['root'])).as_posix()
        bridge.guard.verify_file(path,condition['files_sha256'][relative])
    return bridge.Validator(artifacts=artifacts,collector=collector,judge=judge,policy=policy)


def execute(run,key):
    policy = run.policy
    validator = load_validator(policy)
    urllib.request.install_opener(bridge.guard.direct_opener())
    with run.locked():
        # Resume only previously sealed, fully revalidated slots; NEVER re-send a started slot.
        summary = run.ledger.summary()
        with run.ledger._transaction() as db:
            unfinished = db.execute('SELECT slot FROM executions WHERE artifact_sha IS NULL').fetchall()
        bridge.require(not unfinished, 'unfinished_execution_requires_manual_reconciliation')
        for slot in policy['slots']:
            if slot['slot_id'] in summary['sealed_slots']:
                run.finalize(slot['slot_id'],validator)
        for ordinal,slot in enumerate(policy['slots'],1):
            sid = slot['slot_id']
            if sid in run.ledger.summary()['sealed_slots']:
                continue
            check_next_slot_budget(run,slot)
            verify_runtime(policy)
            nonce = run.begin(sid)
            if slot['role'] == 'generation':
                condition = policy['conditions'][slot['condition_id']]
                stem = run.root/'workers'/bridge.digest(sid.encode())
                owned = PrivateChild([sys.executable,'-B',str(HERE/'live_worker.py'),
                    '--run-root',str(run.root),'--slot-id',sid,'--start-owned-server'],
                    clean_child_env(policy,condition),nonce,stem.with_suffix('.log'),key)
                port = None
                try:
                    with owned:
                        attestation = owned.receive(timeout=45)
                        port = attestation['listener'][1]
                        LOCAL_PORTS.add(port)
                        api_base = 'http://127.0.0.1:'+str(port)
                        health = validator.collector.call_health(api_base,timeout=15)
                        config = bridge.validate_health(validator.collector,health,slot,policy['controls'],condition,attestation,owned.child,nonce)
                        bridge.write_new(stem.with_suffix('.attestation.json'),bridge.canonical(attestation)+b'\n')
                        bridge.write_new(stem.with_suffix('.health.json'),bridge.canonical(health)+b'\n')
                        bridge.collect_one(run,sid,validator.collector,api_base=api_base,
                            security_mode=condition['expected_security_mode'],server_config=config,transport_identity=attestation)
                finally:
                    if hasattr(owned,'child'):
                        bridge.require(owned.child.poll() is not None,'owned_child_still_running')
                        closed = None
                        if port is not None:
                            with socket.socket() as sock:
                                sock.settimeout(.25)
                                closed = sock.connect_ex(('127.0.0.1',port)) != 0
                            LOCAL_PORTS.discard(port)
                        bridge.write_new(stem.with_suffix('.stopped.json'),bridge.canonical({
                            'pid':owned.child.pid,'returncode':owned.child.returncode,'listener_closed':closed})+b'\n')
                        bridge.require(closed is not False,'owned_listener_still_open')
            else:
                answer_path = run.output(sid.rsplit(':',1)[0]+':generation')
                answer_sha = bridge.file_sha(answer_path)
                def identity():
                    verify_runtime(policy)
                    bridge.guard.verify_file(answer_path,answer_sha)
                    return {'pid':os.getpid(),'nonce':nonce,'role':'judge','mode':policy['execution_mode'],
                            'judge_sha256':bridge.file_sha(validator.judge.__file__),'answer_file_sha256':answer_sha,
                            'launcher_sha256':policy['launcher_sha256']}
                opener = FakeJudgeProvider(validator.case(slot)) if policy['execution_mode']=='mock' else LiveProvider(run,sid)
                transport = PacedTransport(run.ledger,sid,opener=opener,identity_check=identity)
                bridge.judge_one(run,sid,validator,transport=transport,api_key=key)
            sealed = run.finalize(sid,validator)
            status = budget_status(run)
            print(json.dumps({'completed':ordinal,'total':len(policy['slots']),'condition':slot['condition_id'],
                'case':slot['case_id'],'role':slot['role'],'slot_attempts':sealed['provider_attempts'],**status}),flush=True)
        verify_runtime(policy,all_inputs=True)
    return budget_status(run)


def save_result(run,status,error=None):
    path = run.root/('completion.json' if error is None else 'stop-'+str(time.time_ns())+'.json')
    if path.exists():
        return
    result = {'status':status,'execution_mode':run.policy['execution_mode'],'budget':budget_status(run),
              'external_llm_calls':0 if run.policy['execution_mode']=='mock' else run.ledger.summary()['reserved_total'],
              'error_type':type(error).__name__ if error else None,
              'error_code':str(error) if isinstance(error,bridge.GuardError) else None,
              'launcher_sha256':tool_pins(),
              'artifact_sha256':{p.relative_to(run.root).as_posix():bridge.file_sha(p)
                  for p in sorted(run.root.rglob('*')) if p.is_file() and p.name not in ('run.lock','daily-budget.lock')}}
    bridge.write_new(path,json.dumps(result,ensure_ascii=False,indent=2).encode()+b'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--verify-local',type=Path)
    mode.add_argument('--execute-approved',action='store_true')
    parser.add_argument('--approval',type=Path)
    parser.add_argument('--approval-sha256')
    parser.add_argument('--verified-run',type=Path)
    args = parser.parse_args()
    sys.addaudithook(audit)
    mock = args.verify_local is not None
    policy = make_policy(mock=mock,approval_path=args.approval,approval_sha=args.approval_sha256)
    if mock:
        root,key = args.verify_local.resolve()/'run','SYNTHETIC_OFFLINE_KEY'
        bridge.require(not root.exists(),'mock_output_exists')
    else:
        bridge.require(args.verified_run is not None,'local_verification_required')
        verified = bridge.strict_json((args.verified_run/'completion.json').read_bytes())
        bridge.require(verified['status']=='MOCK_COMPLETE_NOT_LLM_EVAL' and verified['launcher_sha256']==tool_pins()
                       and verified['budget']['sealed_slots']==18 and verified['external_llm_calls']==0,'stale_or_incomplete_local_verification')
        for relative,pin in verified['artifact_sha256'].items():
            bridge.guard.verify_file(args.verified_run/relative,pin)
        root,key = LIVE_ROOT,load_key(policy)
    run = open_run(root,policy,create=not root.exists())
    try:
        status = execute(run,key)
        save_result(run,'MOCK_COMPLETE_NOT_LLM_EVAL' if mock else 'LIVE_PILOT_COMPLETE')
        print(json.dumps({'status':'MOCK_COMPLETE_NOT_LLM_EVAL' if mock else 'LIVE_PILOT_COMPLETE',**status}),flush=True)
    except BaseException as exc:
        save_result(run,'STOPPED_REQUIRES_REVIEW',exc)
        print(json.dumps({'status':'STOPPED_REQUIRES_REVIEW','error_type':type(exc).__name__,
                          'error_code':str(exc) if isinstance(exc,bridge.GuardError) else None,**budget_status(run)}),flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
