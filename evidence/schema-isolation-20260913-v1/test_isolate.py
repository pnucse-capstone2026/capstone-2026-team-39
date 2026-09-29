import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import isolate as s


class IsolationTests(unittest.TestCase):
    def test_only_predeclared_factor_changes_and_original_not_mutated(self):
        original = s.previous.inputs()[1]['provider_body']
        before = copy.deepcopy(original)
        first, second = s.contrasts(original)
        restored = copy.deepcopy(first['body'])
        restored['generationConfig']['responseJsonSchema'] = original['generationConfig']['responseJsonSchema']
        self.assertEqual(restored, original)
        restored = copy.deepcopy(second['body'])
        restored['contents'] = original['contents']
        self.assertEqual(restored, original)
        self.assertEqual(original, before)
        data = json.loads(second['body']['contents'][0]['parts'][0]['text'])['data']
        self.assertEqual(data['query'], '제7회 ALPHA 공모전에서 취소자 참가비 처리는?')
        self.assertEqual(set(data), {'query', 'raw_draft', 'draft_units', 'sources'})
        self.assertEqual(len(data['sources']), 1)
        self.assertEqual(data['sources'][0]['text'], '취소자는 참가비 환수 대상입니다.')

    def test_fixed_order_durable_cap_and_prior_success_required(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = s.Budget(folder)
            with self.assertRaises(ValueError): budget.reserve(s.SLOTS[1], 'extract', {})
            budget.reserve(s.SLOTS[0], 'extract', {})
            with self.assertRaises(ValueError): budget.reserve(s.SLOTS[1], 'extract', {})
            budget.finish(s.SLOTS[0], 'diagnostic_complete', 200)
            budget.reserve(s.SLOTS[1], 'extract', {})
            with self.assertRaises(ValueError): budget.reserve(s.SLOTS[1], 'extract', {})
            budget.db.close()
            with self.assertRaises(ValueError): s.Budget(folder)

    def test_http400_or429_on_first_prevents_second_and_sleep(self):
        for status in (400, 429):
            calls = []
            def call(item):
                calls.append(item['slot'])
                raise urllib.error.HTTPError('https://invalid/?key=SYNTHETIC_SECRET', status, 'SYNTHETIC_SECRET', {}, None)
            result = s.run_requests([{'slot': slot} for slot in s.SLOTS], call, lambda _: self.fail())
            self.assertEqual(calls, [s.SLOTS[0]])
            self.assertEqual(result['failure']['http_status'], status)
            self.assertNotIn('SYNTHETIC_SECRET', json.dumps(result))

    def test_second_failure_preserves_first_acceptance(self):
        calls, sleeps = [], []
        def call(item):
            calls.append(item['slot'])
            if item['slot'] == s.SLOTS[1]: raise ValueError('synthetic failure')
            return s.validate_diagnostic(item['slot'], '{"ok":true}')
        with contextlib.redirect_stdout(io.StringIO()):
            result = s.run_requests([{'slot': slot} for slot in s.SLOTS], call, sleeps.append)
        self.assertEqual(len(result['completed']), 1)
        self.assertEqual(calls, list(s.SLOTS))
        self.assertEqual(sleeps, [15])
        self.assertFalse(result['completed'][0]['valid_extraction'])

    def test_diagnostic_never_becomes_host_validation_or_service_result(self):
        for slot, raw in [(s.SLOTS[0], '{"ok":false}'), (s.SLOTS[1], '{"syntactic":"only"}')]:
            result = s.validate_diagnostic(slot, raw)
            self.assertFalse(result['valid_extraction'])
            self.assertFalse(result['eligible_for_service'])
            self.assertIsNone(result['candidate_gfc'])
        for raw in ('{}', '{"ok":1}', '{"ok":"true"}', '{"ok":true,"x":1}', '{"ok":true,"ok":false}'):
            with self.assertRaises(ValueError): s.validate_diagnostic(s.SLOTS[0], raw)
        with self.assertRaises(ValueError): s.validate_diagnostic(s.SLOTS[1], 'not JSON')

    def test_post_sends_exact_body_after_reservation_and_no_retry(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = s.Budget(folder)
            item = {'slot': s.SLOTS[0], 'body': {'synthetic': True}}
            class Opener:
                def open(self, request, timeout):
                    assert budget.rows()[0]['state'] == 'reserved'
                    assert json.loads(request.data) == item['body']
                    raise urllib.error.HTTPError(request.full_url, 400, 'error', {}, io.BytesIO(b'SYNTHETIC_KEY'))
            with patch.object(s.urllib.request, 'build_opener', return_value=Opener()) as opened:
                with self.assertRaises(urllib.error.HTTPError): s.post(budget, item, 'SYNTHETIC_KEY')
            self.assertEqual(opened.call_count, 1)
            self.assertEqual((Path(folder) / (s.SLOTS[0] + '.http-error.txt')).read_text(), '[REDACTED]')
            self.assertIsNone(s.prior.CAPABILITY['url'])
            budget.db.close()

    def test_http200_non_stop_is_saved_but_stops_before_second(self):
        class Response(io.BytesIO): status = 200
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = s.Budget(folder)
            with patch.object(s.urllib.request, 'build_opener') as opened:
                opened.return_value.open.return_value = Response(b'{"candidates":[{"finishReason":"MAX_TOKENS"}]}')
                with self.assertRaises(ValueError): s.post(budget, {'slot': s.SLOTS[0], 'body': {}}, 'SYNTHETIC_KEY')
            self.assertEqual(budget.rows()[0]['http_status'], 200)
            self.assertEqual(budget.rows()[0]['state'], 'failed')
            self.assertTrue((Path(folder) / (s.SLOTS[0] + '.receipt.json')).exists())
            with self.assertRaises(ValueError): budget.reserve(s.SLOTS[1], 'extract', {})
            budget.db.close()

    def test_wrong_order_and_extra_probe_rejected(self):
        for slots in (s.SLOTS[::-1], (*s.SLOTS, 'third'), (s.SLOTS[0],)):
            with self.assertRaises(ValueError):
                s.run_requests([{'slot': slot} for slot in slots], lambda _: self.fail(), lambda _: self.fail())


if __name__ == '__main__':
    unittest.main()
