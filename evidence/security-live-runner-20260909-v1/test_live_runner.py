"""No real credentials/provider: single-key quotas, approval and owned-child tests."""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import live_runner as live
bridge = live.bridge


def no_network(event,args):
    live.audit(event,args)
    if event.startswith(('socket.','urllib.')):
        raise bridge.GuardError('tests_never_use_network')


sys.addaudithook(no_network)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.proposal = {'intended_run_root':str(live.LIVE_ROOT),'slots':[{}]*204,'total_cap':918}
        self.approval = {'approved':True,'external_llm_calls':True,'proposal_sha256':live.PROPOSAL_SHA,
            'run_root':str(live.LIVE_ROOT),'generation_slots':102,'judge_slots':102,'total_attempt_cap':918,
            'single_key':True,'daily_soft_cap':450,'declared_rpd':500,'auto_key_switch':False,'auto_model_switch':False}

    def test_explicit_matching_approval(self):
        live.approve(self.proposal,self.approval)

    def test_no_implicit_approval(self):
        for key in ('approved','external_llm_calls'):
            with self.subTest(key=key):
                with self.assertRaises(bridge.GuardError):
                    live.approve(self.proposal,{**self.approval,key:False})

    def test_scope_changes_refused(self):
        for key,value in (('single_key',False),('daily_soft_cap',501),('auto_key_switch',True),
                          ('auto_model_switch',True),('total_attempt_cap',999),('proposal_sha256','a'*64)):
            with self.subTest(key=key):
                with self.assertRaises(bridge.GuardError):
                    live.approve(self.proposal,{**self.approval,key:value})

    def test_key_requires_approval_before_read(self):
        with self.assertRaisesRegex(bridge.GuardError,'key_read_requires_live_approval'):
            live.load_key({'execution_mode':'mock','api_execution_authorized':False})

    def test_key_parser_does_not_evaluate_other_settings(self):
        self.assertEqual(live.parse_key('UNRELATED_SECRET=not_used\nexport GEMINI_API_KEY="SYNTHETIC_OFFLINE_KEY"\n'), 'SYNTHETIC_OFFLINE_KEY')
        self.assertEqual(live.parse_key('GEMINI_API_KEY=SECOND_SYNTHETIC_KEY_123\nRAG_GEMINI_API_KEY=SYNTHETIC_OFFLINE_KEY'), 'SYNTHETIC_OFFLINE_KEY')

    def test_missing_invalid_or_expandable_key_refused(self):
        for value in ('UNRELATED_SECRET=x','GEMINI_API_KEY=','GEMINI_API_KEY=$(anything)', 'GEMINI_API_KEY="key with spaces"'):
            with self.subTest(value=value):
                with self.assertRaises(bridge.GuardError):
                    live.parse_key(value)


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pnu-live-unit-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()/'run'
        slots = [{'slot_id':'SYNTHETIC:'+role,'role':role,'model':'fake-model','attempt_cap':3 if role=='generation' else 6,
                  'timeout_ceiling':120 if role=='generation' else 180} for role in ('generation','judge')]
        self.policy = {'experiment_id':'SYNTHETIC-live-unit','execution_mode':'mock','slots':slots,'total_cap':9,
                       'daily_soft_cap':2,'transport_min_interval_seconds':0}
        self.run = live.open_run(self.root,self.policy,create=True)
        self.sid = slots[0]['slot_id']

    def test_daily_soft_cap_is_stricter_than_total(self):
        for _ in range(2):
            attempt = self.run.ledger.reserve(self.sid,'a'*64,'b'*64)
            self.run.ledger.finish(attempt,'response_closed',200)
        with self.assertRaisesRegex(bridge.GuardError,'daily_soft_cap_reached'):
            self.run.ledger.reserve(self.sid,'a'*64,'b'*64)
        self.assertEqual(self.run.ledger.summary()['reserved_total'],2)
        self.assertEqual(self.run.ledger.summary()['remaining_ceiling'],7)

    def test_daily_stop_reopen_does_not_reset(self):
        a = self.run.ledger.reserve(self.sid,'a'*64,'b'*64)
        self.run.ledger.finish(a,'transport_error',429)
        bridge.write_new(self.root/'quota-stop.json',b'{}\n')
        resumed = live.open_run(self.root,self.policy,create=False)
        with self.assertRaisesRegex(bridge.GuardError,'provider_quota_stop_active'):
            resumed.ledger.reserve(self.sid,'a'*64,'b'*64)
        self.assertEqual(resumed.ledger.summary()['reserved_total'],1)

    def test_uncertain_call_cannot_be_retried(self):
        self.run.ledger.reserve(self.sid,'a'*64,'b'*64)
        with self.assertRaisesRegex(bridge.GuardError,'unresolved_attempt'):
            self.run.ledger.reserve(self.sid,'a'*64,'b'*64)

    def test_pacific_midnight_accounting(self):
        before = int(datetime(2026,9,9,6,59,59,tzinfo=timezone.utc).timestamp()*1e9)
        after = int(datetime(2026,9,9,7,0,0,tzinfo=timezone.utc).timestamp()*1e9)
        self.assertEqual(live.pacific_day(before),'2026-09-08')
        self.assertEqual(live.pacific_day(after),'2026-09-09')
        for timestamp in (before,before,after):
            with patch.object(live.time,'time_ns',return_value=timestamp):
                a = self.run.ledger.reserve(self.sid,'a'*64,'b'*64)
                self.run.ledger.finish(a,'response_closed',200)
        self.assertEqual(self.run.ledger.summary()['reserved_total'],3)

    def test_next_slot_full_retry_allowance_before_begin(self):
        with self.assertRaisesRegex(bridge.GuardError,'pause_before_daily_soft_cap'):
            live.check_next_slot_budget(self.run,self.policy['slots'][0])
        with self.run.ledger._transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM executions').fetchone()[0],0)

    def test_no_live_backend_for_mock(self):
        with self.assertRaisesRegex(bridge.GuardError,'live_backend_not_authorized'):
            live.LiveProvider(self.run,self.sid)

    def test_first_quota_error_stops_without_a_second_send(self):
        self.run.policy.update({'execution_mode':'live','api_execution_authorized':True})
        backend = live.LiveProvider(self.run,self.sid)
        class QuotaResponse:
            calls = 0
            def open(self,request,timeout):
                self.calls += 1
                raise urllib.error.HTTPError(request.full_url,429,'SYNTHETIC',{},io.BytesIO(b'{}'))
        fake = QuotaResponse()
        backend.opener = fake
        transport = live.PacedTransport(self.run.ledger,self.sid,opener=backend,identity_check=lambda:{'mode':'SYNTHETIC'})
        request = urllib.request.Request(backend.endpoint,data=b'{}',method='POST')
        with patch.object(live,'verify_runtime'):
            with self.assertRaises(urllib.error.HTTPError):
                transport.urlopen(request,timeout=120)
            with self.assertRaisesRegex(bridge.GuardError,'provider_quota_stop_active'):
                transport.urlopen(request,timeout=120)
        self.assertEqual(fake.calls,1)
        self.assertEqual(self.run.ledger.summary()['reserved_total'],1)
        self.assertEqual(self.run.ledger.summary()['unresolved'],0)
        self.assertEqual(json.loads((self.root/'quota-stop.json').read_bytes())['http_status'],429)

    def test_network_outside_provider_is_denied(self):
        with self.assertRaisesRegex(bridge.GuardError,'unapproved_network_connect'):
            live.audit('socket.connect',(None,('203.0.113.1',443)))
        self.assertIsNone(getattr(live.CAPABILITY,'provider',None))

    def test_key_free_child_environment(self):
        env = live.clean_child_env({'environment':{'PYTHONDONTWRITEBYTECODE':'1'}},{'context_security_env':'enforce'})
        self.assertFalse(any('KEY' in k for k in env))
        self.assertEqual(env['RAG_CONTEXT_SECURITY_MODE'],'enforce')

    def test_owned_pipe_key_and_lease_cleanup(self):
        command = [sys.executable,'-B',str(Path(__file__).resolve()),'--pipe-probe']
        env = {'PATH':'/usr/bin:/bin:/usr/sbin:/sbin','PYTHONDONTWRITEBYTECODE':'1'}
        owned = live.PrivateChild(command,env,'a'*64,self.root/'worker.log','SYNTHETIC_OFFLINE_KEY')
        with owned:
            message = owned.receive(timeout=10)
            self.assertTrue(message['synthetic_key_received'])
            self.assertNotIn('SYNTHETIC_OFFLINE_KEY',' '.join(owned.child.args))
            owned.release_lease()
            self.assertEqual(owned.child.wait(timeout=5),86)
        self.assertNotIn(b'SYNTHETIC_OFFLINE_KEY',(self.root/'worker.log').read_bytes())


if __name__ == '__main__':
    if '--pipe-probe' in sys.argv:
        import argparse
        import live_worker
        parser = argparse.ArgumentParser()
        parser.add_argument('--pipe-probe',action='store_true')
        parser.add_argument('--attestation-fd',type=int)
        parser.add_argument('--lease-fd',type=int)
        parser.add_argument('--nonce')
        args = parser.parse_args()
        value = sys.stdin.buffer.readline(258)
        os.write(args.attestation_fd,bridge.canonical({'pid':os.getpid(),'nonce':args.nonce,
                 'synthetic_key_received':value==b'SYNTHETIC_OFFLINE_KEY\n'})+b'\n')
        os.close(args.attestation_fd)
        live_worker.lease_watch(args.lease_fd)
    elif '--output' in sys.argv:
        output = Path(sys.argv[sys.argv.index('--output')+1])
        output.mkdir(parents=True,exist_ok=False)
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
        bridge.write_new(output/'unit-tests.log',stream.getvalue().encode())
        bridge.write_new(output/'test-counts.json',bridge.canonical({'total':result.testsRun,'skip':len(result.skipped),
            'failures':len(result.failures),'errors':len(result.errors)})+b'\n')
        print(stream.getvalue(),end='')
        raise SystemExit(0 if result.wasSuccessful() else 1)
    else:
        unittest.main(verbosity=2)
