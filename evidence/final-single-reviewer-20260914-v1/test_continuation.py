import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import runner_v3 as r
import continuation as c
import test_runner as original_tests


class ContinuedRunnerTests(original_tests.RunnerTests):
    def setUp(self):
        self.previous_runner = original_tests.r
        original_tests.r = r
        super().setUp()

    def tearDown(self):
        super().tearDown()
        original_tests.r = self.previous_runner

    def test_spacing_is_global_across_conditions(self):
        slot = self.slots[0]
        self.ledger.begin(slot)
        attempt = self.ledger.reserve(slot['condition_id'], r.GEN_MODEL, b'{}')
        self.ledger.finish_attempt(attempt, 200, 'received')
        self.ledger.seal(slot, 'synthetic')
        other = self.slots[1]
        self.ledger.begin(other)
        self.ledger.plan['minimum_inter_call_seconds'] = 15
        with patch.object(r.time, 'sleep') as sleep:
            self.ledger.reserve(other['condition_id'], r.GEN_MODEL, b'{}')
        self.assertGreater(sleep.call_args.args[0], 14)

    def test_http429_diagnostics_preserve_only_allowed_data_and_stop(self):
        slot = self.slots[0]
        self.ledger.begin(slot)
        body = json.dumps({'error': {'message': 'SECRET echoed question', 'status': 'RESOURCE_EXHAUSTED',
            'details': [{'violations': [{'quotaId': 'GenerateRequestsPerMinutePerProject'}]}, {'retryDelay': '38s'}]}}).encode()
        error = urllib.error.HTTPError('https://example.com/SECRET', 429, 'quota', {'Retry-After': '38'}, io.BytesIO(body))
        meter = r.Metered(self.ledger, slot['condition_id'])
        with patch.object(r, 'verify'), patch.object(meter, 'original', side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                meter.open(urllib.request.Request(f'https://generativelanguage.googleapis.com/v1beta/models/{r.GEN_MODEL}:generateContent', b'{}'))
        raw = (self.root / 'provider-error-0001.json').read_text()
        self.assertNotIn('SECRET', raw)
        self.assertIn('PerMinute', raw)
        self.assertIn('38s', raw)
        self.assertTrue((self.root / 'quota-stop.json').exists())
        with self.ledger.db() as db:
            self.assertEqual(db.execute('SELECT state,http_status FROM attempts').fetchone()[:], ('http_error', 429))


class ContinuationTests(unittest.TestCase):
    def test_same_key_guard_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            c.verify_same_key(root, 'SYNTHETIC-FIRST-KEY')
            original = (root / 'private-key-fingerprint.json').read_bytes()
            c.verify_same_key(root, 'SYNTHETIC-FIRST-KEY')
            with self.assertRaises(ValueError): c.verify_same_key(root, 'SYNTHETIC-DIFFERENT-KEY')
            self.assertEqual((root / 'private-key-fingerprint.json').read_bytes(), original)

    def test_skip_is_not_a_fake_judgment(self):
        plan = {'skip_judge_ordinals': [77]}
        row = c.skipped_judge_record(plan, {'ordinal': 77})
        self.assertEqual(row['record_type'], 'judge_not_called')
        self.assertIsNone(row['judge_score'])
        self.assertNotIn('judge', row)
        with self.assertRaises(ValueError): c.skipped_judge_record(plan, {'ordinal': 78})

    def test_redirects_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'redirect_rejected'):
            r.NoProviderRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com')

    def test_incomplete_import_does_not_reset_or_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(r, 'RUN', Path(directory).resolve()):
                with self.assertRaisesRegex(ValueError, 'incomplete_import'):
                    c.initialized_ledger(r, {})


if __name__ == '__main__': unittest.main()
