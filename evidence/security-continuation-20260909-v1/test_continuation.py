"""Offline continuation contracts; no real key, network or parent mutations."""
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import live_runner as live
bridge = live.bridge


def no_network(event, args):
    live.base.audit(event, args)
    if event.startswith(('socket.', 'urllib.')):
        raise bridge.GuardError('tests_never_use_network')


sys.addaudithook(no_network)


class ScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = live.parent_snapshot(full=True)

    def test_remaining_pairs_and_consumed_failure(self):
        policy, attempts, sealed = self.parent
        slots = live.pending_slots(policy, attempts, sealed)
        self.assertEqual(len(slots), 56)
        self.assertEqual(sum(s['attempt_cap'] for s in slots), 251)
        self.assertEqual(slots[0]['attempt_cap'], 2)
        self.assertEqual(slots[0]['slot_id'], attempts[-1]['slot'])
        self.assertFalse(set(sealed) & {s['slot_id'] for s in slots})
        self.assertEqual(len(sealed)+len(slots), 204)
        original = {s['slot_id']: s for s in policy['slots']}
        for slot in slots:
            expected = dict(original[slot['slot_id']])
            if slot['slot_id'] == attempts[-1]['slot']:
                expected['attempt_cap'] -= 1
            self.assertEqual(slot, expected)

    def test_no_remaining_attempts_fails_closed(self):
        with self.assertRaisesRegex(bridge.GuardError, 'slot_attempts_exhausted'):
            live.pending_slots({'slots':[{'slot_id':'x','attempt_cap':1}]}, [{'slot':'x'}], {})

    def test_parent_lock_read_only_exclusive(self):
        before = bridge.file_sha(live.PARENT/'run.lock')
        with live.parent_lock():
            with self.assertRaisesRegex(bridge.GuardError, 'parent_run_already_owned'):
                with live.parent_lock():
                    self.fail('second owner acquired lock')
        self.assertEqual(bridge.file_sha(live.PARENT/'run.lock'), before)
        self.assertEqual(live.parent_snapshot(full=True), self.parent)

    def test_explicit_approval_and_scope_rejections(self):
        original = json.loads((live.HERE/'approval-20260909.json').read_bytes())
        with tempfile.TemporaryDirectory(prefix='pnu-cont-approval-') as temporary:
            for key, value in ((None,None), ('external_content_export',False), ('approved',False),
                               ('additional_attempt_cap',252), ('pending_slots_sha256','0'*64),
                               ('run_root','/tmp/not-approved'), ('auto_key_switch',True),
                               ('auto_model_switch',True), ('previous_attempts',147)):
                with self.subTest(key=key):
                    record = dict(original)
                    if key:
                        record[key] = value
                    path = Path(temporary)/(str(key)+'.json')
                    bridge.write_new(path, bridge.canonical(record))
                    with patch.object(live, 'parent_snapshot', return_value=self.parent), patch.object(live, 'verify_runtime'):
                        kwargs = {'mock':False, 'approval_path':path, 'approval_sha':bridge.file_sha(path)}
                        if key:
                            with self.assertRaisesRegex(bridge.GuardError,'continuation_approval_mismatch'):
                                live.make_policy(**kwargs)
                        else:
                            policy = live.make_policy(**kwargs)
                            self.assertEqual(policy['total_cap'],251)
                            self.assertEqual(len(policy['carried_attempts']),148)

    def test_key_and_outside_network_denied(self):
        with self.assertRaisesRegex(bridge.GuardError,'key_read_requires_live_approval'):
            live.base.load_key({'execution_mode':'mock','api_execution_authorized':False})
        with self.assertRaisesRegex(bridge.GuardError,'unapproved_network_connect'):
            live.base.audit('socket.connect',(None,('203.0.113.1',443)))
        with self.assertRaisesRegex(bridge.GuardError,'holdout_data_disabled'):
            live.base.audit('open',('/tmp/synthetic-holdout.jsonl','r',0))


class CarryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='pnu-cont-unit-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()/'run'
        self.before = int(datetime(2026,9,9,6,59,59,tzinfo=timezone.utc).timestamp()*1e9)
        self.after = int(datetime(2026,9,9,7,0,0,tzinfo=timezone.utc).timestamp()*1e9)
        self.sid = 'SYNTHETIC:generation'
        self.policy = {'experiment_id':'SYNTHETIC-continuation-unit', 'execution_mode':'mock',
            'slots':[{'slot_id':self.sid, 'role':'generation', 'model':'fake-model', 'attempt_cap':2, 'timeout_ceiling':120},
                     {'slot_id':'SYNTHETIC:judge', 'role':'judge', 'model':'fake-model', 'attempt_cap':6, 'timeout_ceiling':180}],
            'total_cap':8, 'daily_soft_cap':3, 'transport_min_interval_seconds':0,
            'carried_attempts':[{'slot':self.sid,'started_ns':self.before-1000,'request_sha':'a'*64,'state':'transport_error'}],
            'carried_sealed_slots':{'previous':'b'*64}}
        self.run = live.base.open_run(self.root,self.policy,create=True)

    def reserve_finish(self, state='response_closed', status=200):
        attempt = self.run.ledger.reserve(self.sid,'a'*64,'b'*64)
        self.run.ledger.finish(attempt,state,status)

    def test_failed_attempts_remain_consumed_after_reopen(self):
        with patch.object(live.time,'time_ns',return_value=self.before):
            self.reserve_finish('transport_error',None)
            self.run = live.base.open_run(self.root,self.policy,create=False)
            self.reserve_finish()
            with self.assertRaisesRegex(bridge.GuardError,'daily_soft_cap_reached_with_carry'):
                self.run.ledger.reserve(self.sid,'a'*64,'b'*64)
            self.assertEqual(live.budget_status(self.run)['combined_provider_attempts'],3)

    def test_slot_cap_survives_daily_reset(self):
        with patch.object(live.time,'time_ns',return_value=self.before):
            self.reserve_finish()
            self.reserve_finish()
        with patch.object(live.time,'time_ns',return_value=self.after):
            with self.assertRaisesRegex(bridge.GuardError,'slot_budget_exhausted'):
                self.run.ledger.reserve(self.sid,'a'*64,'b'*64)

    def test_pacific_reset_not_lifetime_reset(self):
        with patch.object(live.time,'time_ns',return_value=self.before):
            self.reserve_finish('transport_error',None)
            before = live.budget_status(self.run)
        with patch.object(live.time,'time_ns',return_value=self.after):
            self.reserve_finish()
            after = live.budget_status(self.run)
        self.assertEqual((before['carried_today'],before['pilot_today_total']), (1,2))
        self.assertEqual((after['carried_today'],after['pilot_today_total']), (0,1))
        self.assertEqual(after['combined_provider_attempts'],3)

    def test_next_slot_reserves_full_budget_before_start(self):
        with patch.object(live.time,'time_ns',return_value=self.before):
            self.reserve_finish()
            with self.assertRaisesRegex(bridge.GuardError,'pause_before_daily_cap_with_carry'):
                live.check_next_slot_budget(self.run,self.policy['slots'][0])
        with self.run.ledger._transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM executions').fetchone()[0],0)

    def test_ambiguous_attempt_never_resent(self):
        with patch.object(live.time,'time_ns',return_value=self.before):
            self.run.ledger.reserve(self.sid,'a'*64,'b'*64)
            with self.assertRaisesRegex(bridge.GuardError,'unresolved_attempt'):
                self.run.ledger.reserve(self.sid,'a'*64,'b'*64)

    def test_parent_request_hash_must_match_live_retry(self):
        policy = {**self.policy,'execution_mode':'live','daily_soft_cap':450}
        path = self.root/'synthetic-live-ledger.sqlite'
        ledger = live.CarryLedger.create(path,policy)
        with self.assertRaisesRegex(bridge.GuardError,'parent_retry_request_changed'):
            ledger.reserve(self.sid,'c'*64,'b'*64)
        self.assertEqual(ledger.summary()['reserved_total'],0)
        attempt = ledger.reserve(self.sid,'a'*64,'b'*64)
        ledger.finish(attempt,'response_closed',200)

    def test_first_429_stops_and_diagnostics_do_not_leak(self):
        self.run.policy.update({'execution_mode':'live','api_execution_authorized':True})
        backend = live.DiagnosticProvider(self.run,self.sid)
        class FakeQuota:
            calls = 0
            def open(self,request,timeout):
                self.calls += 1
                raise urllib.error.HTTPError(request.full_url,429,'SECRET_SHOULD_NOT_APPEAR',{},io.BytesIO(b'{}'))
        fake = FakeQuota()
        backend.opener = fake
        transport = live.CarryPacedTransport(self.run.ledger,self.sid,opener=backend,identity_check=lambda:{'mode':'SYNTHETIC'})
        with patch.object(live.base,'verify_runtime'):
            request = urllib.request.Request(backend.endpoint,data=b'{}',method='POST')
            with self.assertRaises(urllib.error.HTTPError):
                transport.urlopen(request,timeout=120)
            with self.assertRaisesRegex(bridge.GuardError,'provider_quota_stop_active'):
                transport.urlopen(request,timeout=120)
        self.assertEqual(fake.calls,1)
        diagnostic = next((self.root/'provider-diagnostics').glob('*.json')).read_bytes()
        self.assertNotIn(b'SECRET_SHOULD_NOT_APPEAR',diagnostic)
        self.assertNotIn(b'https',diagnostic)
        self.assertEqual(json.loads(diagnostic)['http_status'],429)

    def test_metadata_type_only_not_messages(self):
        secret = 'https://secret.invalid/?key=DO_NOT_WRITE'
        for exc in (TimeoutError(secret), urllib.error.URLError(OSError(54,secret)), RuntimeError(secret)):
            encoded = bridge.canonical(live.exception_metadata(exc))
            self.assertNotIn(b'DO_NOT_WRITE',encoded)
            self.assertNotIn(b'secret.invalid',encoded)
        self.assertEqual(live.exception_metadata(urllib.error.URLError(TimeoutError(secret)))['reason_type'],'TimeoutError')


if __name__ == '__main__':
    output = Path(sys.argv[sys.argv.index('--output')+1])
    output.mkdir(parents=True,exist_ok=False)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    bridge.write_new(output/'unit-tests.log',stream.getvalue().encode())
    bridge.write_new(output/'test-counts.json',bridge.canonical({'total':result.testsRun,'skip':len(result.skipped),
        'failures':len(result.failures),'errors':len(result.errors)})+b'\n')
    print(stream.getvalue(),end='')
    raise SystemExit(0 if result.wasSuccessful() else 1)
