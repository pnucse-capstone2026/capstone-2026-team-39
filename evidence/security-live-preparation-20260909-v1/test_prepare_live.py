"""Offline recovery/proposal/restart tests with synthetic run metadata only."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import prepare_live as prep

bridge = prep.bridge
sys.addaudithook(prep.offline_audit)


class RecoveryTests(unittest.TestCase):
    def test_exact_edit_and_new_file(self):
        self.assertEqual(prep.apply_exact(b'a\nb\nc\n', ['@@ -1,3 +1,3 @@\n', ' a\n', '-b\n', '+B\n', ' c\n']), b'a\nB\nc\n')
        self.assertEqual(prep.apply_exact(b'', ['--- /dev/null\n', '+++ b/scripts/new.py\n', '@@ -0,0 +1,1 @@\n', '+new\n']), b'new\n')

    def test_multiple_hunks(self):
        lines = ['@@ -1 +1 @@\n', '-a\n', '+A\n', '@@ -3 +3,2 @@\n', '-c\n', '+C\n', '+D\n']
        self.assertEqual(prep.apply_exact(b'a\nb\nc\n', lines), b'A\nb\nC\nD\n')

    def test_patch_drift_is_not_fuzzed(self):
        for lines in (['@@ -1 +1 @@\n', '-wrong\n', '+B\n'],
                      ['@@ -1,2 +1 @@\n', '-a\n', '+B\n'],
                      ['@@ -1 +2 @@\n', '-a\n', '+B\n']):
            with self.subTest(lines=lines):
                with self.assertRaises(bridge.GuardError):
                    prep.apply_exact(b'a\n', lines)

    def test_unsafe_paths_refused(self):
        for name in ('../scripts/a.py', '/scripts/a.py', 'scripts/../a.py', '.env',
                     'config/pnu-service-answer-holdout-v2.draft.jsonl', 'scripts/a.sh'):
            with self.subTest(name=name):
                with self.assertRaises(bridge.GuardError):
                    prep.safe_relative(name)
        self.assertEqual(prep.safe_relative('scripts/holdout_gold.py'), 'scripts/holdout_gold.py')

    def test_pin_mismatch_before_writing(self):
        blobs = {'scripts/a.py': b'expected\n'}
        pins = {name: bridge.digest(data) for name,data in blobs.items()}
        condition = {'files_sha256': pins, 'snapshot_sha256': bridge.digest(bridge.canonical(pins))}
        with tempfile.TemporaryDirectory(prefix='pnu-restore-test-') as temporary:
            root = Path(temporary).resolve() / 'snapshot'
            with self.assertRaises(bridge.GuardError):
                prep.materialize(root, {'scripts/a.py': b'wrong\n'}, condition)
            self.assertFalse(root.exists())

    def test_idempotent_restore_and_changed_file_preserved(self):
        blobs = {'scripts/a.py': b'original\n'}
        pins = {name: bridge.digest(data) for name,data in blobs.items()}
        condition = {'files_sha256': pins, 'snapshot_sha256': bridge.digest(bridge.canonical(pins))}
        with tempfile.TemporaryDirectory(prefix='pnu-restore-test-') as temporary:
            root = Path(temporary).resolve() / 'snapshot'
            self.assertEqual(prep.materialize(root, blobs, condition), 'materialized_byte_identical')
            self.assertEqual(prep.materialize(root, blobs, condition), 'verified_existing_no_write')
            target = root / 'scripts/a.py'
            target.write_bytes(b'user change\n')
            with self.assertRaises(bridge.GuardError):
                prep.materialize(root, blobs, condition)
            self.assertEqual(target.read_bytes(), b'user change\n')

    def test_symlink_target_refused(self):
        pins = {'scripts/a.py': bridge.digest(b'a\n')}
        condition = {'files_sha256': pins, 'snapshot_sha256': bridge.digest(bridge.canonical(pins))}
        with tempfile.TemporaryDirectory(prefix='pnu-restore-test-') as temporary:
            target = Path(temporary).resolve() / 'link'
            target.symlink_to(Path(temporary).resolve() / 'missing')
            with self.assertRaises(bridge.GuardError):
                prep.materialize(target, {'scripts/a.py': b'a\n'}, condition)

    def test_patch_only_source_sections(self):
        raw = (b'diff --git a/scripts/a.py b/scripts/a.py\n--- /dev/null\n+++ b/scripts/a.py\n'
               b'@@ -0,0 +1 @@\n+a\n'
               b'diff --git a/tests/test_a.py b/tests/test_a.py\n--- /dev/null\n+++ b/tests/test_a.py\n@@ -0,0 +1 @@\n+b\n')
        self.assertEqual(set(prep.patch_sections(raw)), {'scripts/a.py'})


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pnu-resume-test-')
        self.addCleanup(self.temporary.cleanup)
        slots = [{'slot_id': 'synthetic:' + role, 'role': role,
                  'attempt_cap': 3 if role == 'generation' else 6, 'model': 'fake-model'}
                 for role in ('generation', 'judge')]
        self.policy = {'experiment_id': 'SYNTHETIC-restart-inspection', 'total_cap': 9, 'slots': slots}
        self.run = bridge.Run.create_offline(Path(self.temporary.name) / 'run', self.policy)
        self.sid = 'synthetic:generation'

    def test_fresh_run_is_read_only(self):
        before = bridge.file_sha(self.run.ledger.path)
        self.assertEqual(prep.inspect_run(self.run.root)['status'], 'NO_UNFINISHED_EXECUTION')
        self.assertEqual(before, bridge.file_sha(self.run.ledger.path))

    def test_started_missing_file_blocks_even_with_zero_calls(self):
        with self.run.locked():
            self.run.begin(self.sid)
        report = prep.inspect_run(self.run.root)
        self.assertEqual(report['status'], 'RECONCILIATION_REQUIRED')
        self.assertEqual(report['slots'][0]['state'], 'BLOCKED_STARTED_WITHOUT_ARTIFACT')
        self.assertEqual(report['provider_attempts'], 0)

    def test_saved_unsealed_does_not_auto_finish(self):
        with self.run.locked():
            self.run.begin(self.sid)
        bridge.write_new(self.run.output(self.sid), b'{"synthetic":true}\n')
        self.assertEqual(prep.inspect_run(self.run.root)['slots'][0]['state'], 'BLOCKED_SAVED_UNSEALED')
        self.assertEqual(self.run.ledger.summary()['sealed_slots'], {})

    def test_uncertain_reservation_is_not_refunded(self):
        with self.run.locked():
            self.run.begin(self.sid)
        self.run.ledger.reserve(self.sid, 'a'*64, 'b'*64)
        report = prep.inspect_run(self.run.root)
        self.assertEqual(report['slots'][0]['state'], 'BLOCKED_UNCERTAIN_PROVIDER_ATTEMPT')
        self.assertEqual(report['remaining_ceiling'], 8)
        self.assertEqual(self.run.ledger.summary()['unresolved'], 1)

    def test_sealed_file_hash_checked_without_new_calls(self):
        with self.run.locked():
            self.run.begin(self.sid)
            self.run.bind(self.sid, {'synthetic_test': True})
        bridge.write_new(self.run.output(self.sid), b'{"synthetic":true}\n')
        sha = bridge.file_sha(self.run.output(self.sid))
        self.run.ledger.seal_slot(self.sid, sha)
        self.assertEqual(prep.inspect_run(self.run.root)['slots'][0]['state'], 'SEALED_BOOKKEEPING_RECONCILE')
        with self.run.ledger._transaction() as db:
            db.execute('UPDATE executions SET artifact_sha=? WHERE slot=?', (sha, self.sid))
        self.assertEqual(prep.inspect_run(self.run.root)['slots'][0]['state'], 'SEALED_REVALIDATE_THEN_SKIP')
        self.run.output(self.sid).write_bytes(b'{"changed":true}\n')
        with self.assertRaises(bridge.GuardError):
            prep.inspect_run(self.run.root)

    def test_unregistered_artifact_refused(self):
        bridge.write_new(self.run.output(self.sid), b'{}\n')
        with self.assertRaises(bridge.GuardError):
            prep.inspect_run(self.run.root)

    def test_changed_policy_refused(self):
        path = self.run.root / 'policy.json'
        value = json.loads(path.read_bytes())
        value['total_cap'] = 999
        path.write_bytes(bridge.canonical(value)+b'\n')
        with self.assertRaises(bridge.GuardError):
            prep.inspect_run(self.run.root)


class ProposalTests(unittest.TestCase):
    def test_full_frozen_schedule_and_budget_preserved_unapproved(self):
        for path in (prep.BASE_POLICY, prep.ATTACK):
            bridge.guard.verify_file(path, prep.PINS[path])
        base = bridge.strict_json(prep.BASE_POLICY.read_bytes())
        attack = bridge.strict_json(prep.ATTACK.read_bytes())
        proposed = prep.make_proposal(base, attack, prep.ROOT / 'processed/eval/SYNTHETIC-uncreated-run')
        self.assertFalse(proposed['api_execution_authorized'])
        self.assertFalse(proposed['live_runner_ready'])
        self.assertEqual(proposed['total_cap'], 918)
        self.assertEqual(len(proposed['slots']), 204)
        for old, new in zip(base['slots'], proposed['slots']):
            self.assertEqual(old, {k: v for k,v in new.items() if k != 'source_manifest_sha256'})
        self.assertEqual(base['controls'], proposed['controls'])
        self.assertEqual(base['environment'], proposed['environment'])

    def test_network_and_protected_data_are_disabled(self):
        for event,args in (('socket.connect', (None, ('127.0.0.1',1))),
                           ('urllib.Request', ('https://example.invalid',)),
                           ('open', ('.env', 'r', 0)), ('open', ('holdout.jsonl','r',0))):
            with self.subTest(event=event):
                with self.assertRaises(bridge.GuardError):
                    prep.offline_audit(event,args)


if __name__ == '__main__':
    if '--output' in sys.argv:
        output = Path(sys.argv[sys.argv.index('--output')+1])
        output.mkdir(parents=True, exist_ok=False)
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
        bridge.write_new(output/'unit-tests.log', stream.getvalue().encode())
        counts = {'total':result.testsRun,'skip':len(result.skipped),'failures':len(result.failures),'errors':len(result.errors)}
        bridge.write_new(output/'test-counts.json', bridge.canonical(counts)+b'\n')
        print(stream.getvalue(), end='')
        raise SystemExit(0 if result.wasSuccessful() else 1)
    unittest.main(verbosity=2)
