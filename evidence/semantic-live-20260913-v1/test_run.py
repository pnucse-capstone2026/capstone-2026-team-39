from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import run as r
from test_adapter import request, extraction, review


class RunnerTests(unittest.TestCase):
    def test_body_exactly_uses_prepared_messages(self):
        req = request()
        body = r.provider_body(req)
        self.assertEqual(body['contents'][0]['parts'][0]['text'], r.a.model_messages(req)[1]['content'])
        self.assertEqual(body['generationConfig'], {'temperature': 0.0, 'maxOutputTokens': 8192, 'responseMimeType': 'application/json'})
        self.assertNotIn('DO_NOT_FORWARD', r.a.encode(body))

    def test_reservation_is_durable_and_same_slot_cannot_repeat(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            b = r.Budget(folder)
            b.reserve('a--extract', 'extract', {'x': 1})
            other = sqlite3.connect(Path(folder) / 'provider-attempts.sqlite')
            self.assertEqual(other.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
            other.close()
            with self.assertRaises(sqlite3.IntegrityError):
                b.reserve('a--extract', 'extract', {'x': 1})
            b.db.close()
            with self.assertRaisesRegex(ValueError, 'no_automatic_resume'):
                r.Budget(folder)

    def test_global_and_stage_caps(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            b = r.Budget(folder)
            for i in range(42):
                b.reserve('e%d' % i, 'extract', {})
            with self.assertRaisesRegex(ValueError, 'stage_cap'):
                b.reserve('extra', 'extract', {})
            for i in range(42):
                b.reserve('s%d' % i, 'semantic_review', {})
            with self.assertRaisesRegex(ValueError, 'attempt_cap'):
                b.reserve('extra', 'semantic_review', {})
            self.assertEqual(len(b.rows()), 84)
            b.db.close()

    def test_existing_outputs_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            p = Path(folder) / 'result.json'
            r.write_new(p, {'x': 1})
            with self.assertRaises(FileExistsError):
                r.write_new(p, {'x': 2})
            self.assertEqual(json.loads(p.read_text()), {'x': 1})

    def test_success_two_stages_and_interval_preserved(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            calls, sleeps = [], []
            def call(slot, stage, req):
                calls.append((slot, stage))
                return r.a.encode(extraction(req) if stage == 'extract' else review(req))
            with contextlib.redirect_stdout(io.StringIO()):
                results, failures = r.run_cases([('a', request()), ('b', request())], Path(folder), call, sleeps.append)
            self.assertEqual(len(results), 2)
            self.assertEqual(failures, [])
            self.assertEqual(len(calls), 4)
            self.assertEqual(sleeps, [15, 15, 15])
            self.assertFalse(r.read(Path(folder) / 'a.decision.json')['eligible_for_service'])

    def test_invalid_extraction_stops_before_semantic_and_next_case(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            calls = []
            def call(slot, stage, req):
                calls.append(slot)
                return '{}'
            with contextlib.redirect_stdout(io.StringIO()):
                results, failures = r.run_cases([('a', request()), ('b', request())], Path(folder), call, lambda _: self.fail('unexpected delay'))
            self.assertEqual(len(calls), 1)
            self.assertEqual(results, [])
            self.assertEqual(failures[0]['stage'], 'extract')

    def test_invalid_review_stops_before_next_case(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            calls = []
            def call(slot, stage, req):
                calls.append(slot)
                return r.a.encode(extraction(req)) if stage == 'extract' else '{}'
            with contextlib.redirect_stdout(io.StringIO()):
                results, failures = r.run_cases([('a', request()), ('b', request())], Path(folder), call, lambda _: None)
            self.assertEqual(len(calls), 2)
            self.assertEqual(failures[0]['stage'], 'semantic_review')
            self.assertTrue((Path(folder) / 'a.extraction-validation.json').exists())

    def test_quota_stops_without_error_url_or_key_leak(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            def call(*args):
                raise urllib.error.HTTPError('https://example.invalid/?key=SYNTHETIC_SECRET', 429, 'SYNTHETIC_SECRET', {}, None)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                results, failures = r.run_cases([('a', request())], Path(folder), call, lambda _: None)
            self.assertEqual(failures[0]['http_status'], 429)
            self.assertNotIn('SYNTHETIC_SECRET', output.getvalue())
            self.assertNotIn('SYNTHETIC_SECRET', (Path(folder) / 'a.error.json').read_text())

    def test_non_stop_or_empty_provider_response_rejected(self):
        for payload in ({}, {'candidates': [{'finishReason': 'MAX_TOKENS'}]},
                        {'candidates': [{'finishReason': 'STOP', 'content': {'parts': []}}]}):
            with self.assertRaises(ValueError):
                r.response_text(payload)

    def test_thought_parts_not_used_as_answer(self):
        value = r.response_text({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'text': 'private thought', 'thought': True}, {'text': '{"ok":true}'}]}}]})
        self.assertEqual(value, '{"ok":true}')

    def test_redirect_disabled(self):
        with self.assertRaisesRegex(PermissionError, 'redirect_disabled'):
            r.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.invalid')

    def test_unapproved_network_and_protected_reads_denied(self):
        for event, args in [('urllib.Request', ('https://example.invalid',)),
                            ('socket.getaddrinfo', ('example.invalid',)),
                            ('open', (str(r.ROOT / '.env'),)),
                            ('open', (str(r.ROOT / 'config/pnu-service-answer-holdout-v2.draft.jsonl'),))]:
            with self.subTest(event=event), self.assertRaises(PermissionError):
                r.audit(event, args)

    def test_transport_reserves_once_before_send_and_resets_capability(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            b = r.Budget(folder)
            class BrokenOpener:
                def open(self, req, timeout):
                    self_rows = b.rows()
                    if len(self_rows) != 1 or self_rows[0]['state'] != 'reserved':
                        raise AssertionError('request not reserved first')
                    raise urllib.error.HTTPError(req.full_url, 429, 'quota', {}, None)
            with patch.object(r.urllib.request, 'build_opener', return_value=BrokenOpener()) as opened:
                with self.assertRaises(urllib.error.HTTPError):
                    r.post(b, 'a--extract', 'extract', request(), 'SYNTHETIC_KEY_NOT_A_CREDENTIAL')
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(b.rows()[0]['state'], 'http_error')
            self.assertEqual(b.rows()[0]['http_status'], 429)
            self.assertIsNone(r.CAPABILITY['url'])
            b.db.close()


if __name__ == '__main__':
    unittest.main()
