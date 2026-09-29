import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import pilot as p
from test_quote_adapter import request, extraction, review


class PilotTests(unittest.TestCase):
    def test_transport_import_is_bound_to_pinned_file(self):
        self.assertEqual(Path(p.prior.__file__), p.ROOT / 'evidence/semantic-live-20260913-v1/run.py')
        self.assertTrue(hasattr(p.prior, 'Budget'))

    def test_explicit_schema_and_unmodified_model_input(self):
        req = request()
        bundle = p.q.model_bundle(req)
        body = p.body(req)
        self.assertEqual(body['generationConfig']['responseJsonSchema'], bundle['response_json_schema'])
        self.assertEqual(body['contents'][0]['parts'][0]['text'], bundle['messages'][1]['content'])
        self.assertEqual(body['generationConfig']['maxOutputTokens'], 8192)

    def test_fixed_case_two_attempts_no_duplicates(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = p.Budget(folder)
            with self.assertRaisesRegex(ValueError, 'only_fixed_case'):
                budget.reserve('other--extract', 'extract', {})
            budget.reserve(p.CASE + '--extract', 'extract', {})
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                budget.reserve(p.CASE + '--extract', 'extract', {})
            budget.finish(p.CASE + '--extract', 'validated', 200)
            budget.reserve(p.CASE + '--semantic_review', 'semantic_review', {})
            with self.assertRaisesRegex(ValueError, 'cap'):
                budget.reserve(p.CASE + '--semantic_review', 'semantic_review', {})
            self.assertEqual(len(budget.rows()), 2)
            budget.db.close()

    def test_semantic_requires_validated_extraction(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = p.Budget(folder)
            for before_extract in (True, False):
                if not before_extract:
                    budget.reserve(p.CASE + '--extract', 'extract', {})
                    budget.finish(p.CASE + '--extract', 'received', 200)
                with self.assertRaisesRegex(ValueError, 'requires_valid'):
                    budget.reserve(p.CASE + '--semantic_review', 'semantic_review', {})
            budget.db.close()

    def test_successful_two_stage_mock_still_not_service_result(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            calls, validations, sleeps = [], [], []
            def call(stage, req):
                calls.append(stage)
                return p.q.v1.encode(extraction(req) if stage == 'extract' else review(req))
            result = p.pipeline(request(), Path(folder), call, validations.append, sleeps.append)
            self.assertEqual(result['status'], 'PILOT_COMPLETE')
            self.assertEqual(calls, ['extract', 'semantic_review'])
            self.assertEqual(validations, calls)
            self.assertEqual(sleeps, [15])
            self.assertFalse(result['decision']['eligible_for_service'])
            self.assertIsNone(result['decision']['candidate_gfc'])

    def test_invalid_extraction_prevents_second_call(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            calls = []
            def call(stage, req):
                calls.append(stage)
                return '{}'
            with contextlib.redirect_stdout(io.StringIO()):
                result = p.pipeline(request(), Path(folder), call, lambda _: self.fail(), lambda _: self.fail())
            self.assertEqual(calls, ['extract'])
            self.assertEqual(result['failure']['stage'], 'extract')

    def test_invalid_semantic_stops_and_retains_extraction(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            def call(stage, req):
                return p.q.v1.encode(extraction(req)) if stage == 'extract' else '{}'
            with contextlib.redirect_stdout(io.StringIO()):
                result = p.pipeline(request(), Path(folder), call, lambda _: None, lambda _: None)
            self.assertEqual(result['failure']['stage'], 'semantic_review')
            self.assertTrue((Path(folder) / 'extraction-validation.json').exists())
            self.assertFalse((Path(folder) / 'decision.json').exists())

    def test_quota_or_schema_http_error_no_retry_and_no_url_secret_logging(self):
        for status in (400, 429):
            with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
                calls = []
                def call(stage, req):
                    calls.append(stage)
                    raise urllib.error.HTTPError('https://invalid/?key=SYNTHETIC_SECRET', status, 'SYNTHETIC_SECRET', {}, None)
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    result = p.pipeline(request(), Path(folder), call, lambda _: self.fail(), lambda _: self.fail())
                self.assertEqual(len(calls), 1)
                self.assertEqual(result['failure']['http_status'], status)
                self.assertNotIn('SYNTHETIC_SECRET', captured.getvalue())

    def test_transport_reserves_before_network_and_does_not_retry(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = p.Budget(folder)
            class Opener:
                def open(self, req, timeout):
                    assert budget.rows()[0]['state'] == 'reserved'
                    raise urllib.error.HTTPError(req.full_url, 400, 'schema', {}, io.BytesIO(b'{"error":"schema"}'))
            with patch.object(p.urllib.request, 'build_opener', return_value=Opener()) as opened:
                with self.assertRaises(urllib.error.HTTPError):
                    p.post(budget, 'extract', request(), 'SYNTHETIC_KEY_ONLY')
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(budget.rows()[0]['http_status'], 400)
            self.assertIsNone(p.prior.CAPABILITY['url'])
            self.assertTrue((Path(folder) / (p.CASE + '--extract.http-error.txt')).exists())
            budget.db.close()


if __name__ == '__main__':
    unittest.main()
