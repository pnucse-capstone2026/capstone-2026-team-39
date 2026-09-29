import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import run_projected as r
from test_quote_adapter import chain, request, extraction


class Response(io.BytesIO):
    status = 200


def response(value):
    return Response(json.dumps({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': r.p.q.v1.encode(value)}]}}]}).encode())


class ProjectedPilotTests(unittest.TestCase):
    def test_inputs_match_exact_prepared_first_case(self):
        _, prepared = r.inputs()
        self.assertEqual(prepared, r.p.envelope(prepared['host_request']))
        self.assertEqual(r.CASE, 'shadow_emp_01')
        self.assertEqual(len(prepared['removed_constraints']), 144)
        self.assertIs(r.Budget, r.pilot.Budget)

    def test_budget_rejects_other_case_second_extraction_and_early_semantic(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = r.Budget(folder)
            for slot, stage in [('other--extract', 'extract'), (r.CASE + '--semantic_review', 'semantic_review')]:
                with self.assertRaises(ValueError): budget.reserve(slot, stage, {})
            budget.reserve(r.CASE + '--extract', 'extract', {})
            with self.assertRaises(ValueError): budget.reserve(r.CASE + '--extract', 'extract', {})
            budget.finish(r.CASE + '--extract', 'validated', 200)
            budget.reserve(r.CASE + '--semantic_review', 'semantic_review', {})
            with self.assertRaises(ValueError): budget.reserve(r.CASE + '--semantic_review', 'semantic_review', {})
            budget.db.close()

    def test_real_transport_path_sends_projected_body_and_validates_full_host(self):
        req, ex, _, _ = chain()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = r.Budget(folder)
            class Opener:
                def open(self, sent, timeout):
                    assert budget.rows()[0]['state'] == 'reserved'
                    assert json.loads(sent.data) == r.p.envelope(req)['provider_body']
                    return response(ex)
            with patch.object(r.urllib.request, 'build_opener', return_value=Opener()) as opened:
                self.assertEqual(r.post(budget, 'extract', req, 'SYNTHETIC_KEY'), r.p.q.v1.encode(ex))
            self.assertEqual(opened.call_count, 1)
            self.assertTrue((Path(folder) / (r.CASE + '--extract.host-validation.json')).exists())
            self.assertIsNone(r.prior.CAPABILITY['url'])
            budget.db.close()

    def test_two_stage_mock_completes_with_15s_pause_and_no_service_candidate(self):
        req, ex, _, rev = chain()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = r.Budget(folder)
            sleeps = []
            with patch.object(r.urllib.request, 'build_opener') as opened:
                opened.return_value.open.side_effect = [response(ex), response(rev)]
                result = r.pilot.pipeline(req, Path(folder), lambda stage, item: r.post(budget, stage, item, 'SYNTHETIC_KEY'),
                                          lambda stage: budget.finish(r.CASE + '--' + stage, 'validated', 200), sleeps.append)
            self.assertEqual(result['status'], 'PILOT_COMPLETE')
            self.assertEqual(opened.return_value.open.call_count, 2)
            self.assertEqual(sleeps, [15])
            self.assertFalse(result['decision']['eligible_for_service'])
            self.assertIsNone(result['decision']['candidate_gfc'])
            budget.db.close()

    def test_removed_wire_bounds_still_stop_semantic_call(self):
        req = request()
        ex = extraction(req)
        ex['units'][0]['reason'] = ''
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = r.Budget(folder)
            with patch.object(r.urllib.request, 'build_opener') as opened, contextlib.redirect_stdout(io.StringIO()):
                opened.return_value.open.return_value = response(ex)
                result = r.pilot.pipeline(req, Path(folder), lambda stage, item: r.post(budget, stage, item, 'SYNTHETIC_KEY'),
                                          lambda _: self.fail(), lambda _: self.fail())
            self.assertEqual(result['status'], 'STOPPED_INCOMPLETE')
            self.assertIn('schema_string_size', result['failure']['reason'])
            self.assertEqual(opened.return_value.open.call_count, 1)
            self.assertEqual(budget.rows()[0]['http_status'], 200)
            budget.db.close()

    def test_http400_and429_stop_once_and_redact_error_detail(self):
        for status in (400, 429):
            with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
                budget = r.Budget(folder)
                with patch.object(r.urllib.request, 'build_opener') as opened, contextlib.redirect_stdout(io.StringIO()):
                    opened.return_value.open.side_effect = urllib.error.HTTPError('https://invalid/?key=SYNTHETIC_KEY', status, 'error', {}, io.BytesIO(b'SYNTHETIC_KEY'))
                    result = r.pilot.pipeline(request(), Path(folder), lambda stage, item: r.post(budget, stage, item, 'SYNTHETIC_KEY'),
                                              lambda _: self.fail(), lambda _: self.fail())
                self.assertEqual(result['failure']['http_status'], status)
                self.assertEqual(opened.return_value.open.call_count, 1)
                self.assertEqual((Path(folder) / (r.CASE + '--extract.http-error.txt')).read_text(), '[REDACTED]')
                self.assertIsNone(r.prior.CAPABILITY['url'])
                budget.db.close()

    def test_wrong_stage_and_existing_directory_rejected(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = r.Budget(folder)
            with self.assertRaises(ValueError): r.post(budget, 'semantic_review', request(), 'SYNTHETIC_KEY')
            self.assertEqual(budget.rows(), [])
            with self.assertRaises(ValueError): r.new_directory(Path(folder))
            budget.db.close()


if __name__ == '__main__':
    unittest.main()
