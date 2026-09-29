import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import control as c


class ControlTests(unittest.TestCase):
    def test_pinned_source_is_only_literal_synthetic_data(self):
        _, item = c.inputs()
        self.assertEqual(item['body']['contents'], [{'role': 'user', 'parts': [{'text': 'Return exactly {"ok":true}.'}]}])
        self.assertEqual(item['model'], 'gemini-3.5-flash-lite')
        self.assertEqual(Path(c.prior.__file__), c.TRANSPORT)

    def test_reservation_is_durable_and_cap_one(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = c.Budget(folder)
            with self.assertRaises(ValueError): budget.reserve('dev--extract', 'extract', {})
            with self.assertRaises(ValueError): budget.reserve(c.SLOT, 'semantic_review', {})
            budget.reserve(c.SLOT, 'extract', {})
            with sqlite3.connect(Path(folder) / 'provider-attempts.sqlite') as observer:
                self.assertEqual(observer.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
            with self.assertRaises(ValueError): budget.reserve(c.SLOT, 'extract', {})
            budget.db.close()
            with self.assertRaises(ValueError): c.Budget(folder)

    def test_validation_is_exact_and_never_coerces_truthy_values(self):
        self.assertEqual(c.validate('{"ok":true}'), {'ok': True})
        for raw in ('{}', '{"ok":1}', '{"ok":"true"}', '{"ok":false}', '{"ok":true,"extra":1}',
                    '{"ok":false,"ok":true}', '```json\n{"ok":true}\n```', 'null'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): c.validate(raw)

    def test_changed_request_rejected_before_reservation(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = c.Budget(folder)
            item = c.inputs()[1]
            item['body']['contents'][0]['parts'][0]['text'] = 'changed'
            with self.assertRaises(ValueError): c.post(budget, item, 'SYNTHETIC_KEY')
            self.assertEqual(budget.rows(), [])
            budget.db.close()

    def test_mock_success_and_no_capability_left_enabled(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            budget = c.Budget(folder)
            class Response(io.BytesIO):
                status = 200
            response = Response(json.dumps({'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{"ok":true}'}]}}]}).encode())
            with patch.object(c.urllib.request, 'build_opener') as opened:
                opened.return_value.open.return_value = response
                result = c.post(budget, c.inputs()[1], 'SYNTHETIC_KEY')
            self.assertEqual(result, {'ok': True})
            self.assertEqual(opened.return_value.open.call_count, 1)
            self.assertEqual(budget.rows()[0]['state'], 'validated')
            self.assertIsNone(c.prior.CAPABILITY['url'])
            budget.db.close()

    def test_http400_or429_reserved_before_send_redacted_and_not_retried(self):
        for status in (400, 429):
            with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
                budget = c.Budget(folder)
                class Opener:
                    def open(self, request, timeout):
                        assert budget.rows()[0]['state'] == 'reserved'
                        raise urllib.error.HTTPError(request.full_url, status, 'error', {}, io.BytesIO(b'SYNTHETIC_KEY'))
                with patch.object(c.urllib.request, 'build_opener', return_value=Opener()) as opened:
                    with self.assertRaises(urllib.error.HTTPError): c.post(budget, c.inputs()[1], 'SYNTHETIC_KEY')
                self.assertEqual(opened.call_count, 1)
                self.assertEqual(budget.rows()[0]['http_status'], status)
                self.assertEqual((Path(folder) / 'http-error.txt').read_text(), '[REDACTED]')
                self.assertIsNone(c.prior.CAPABILITY['url'])
                budget.db.close()

    def test_existing_output_not_overwritten_and_no_resume(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as folder:
            with self.assertRaises(ValueError): c.new_directory(Path(folder))
            path = Path(folder) / 'artifact.json'
            c.prior.write_new(path, {'ok': True})
            with self.assertRaises(FileExistsError): c.prior.write_new(path, {'ok': False})
            self.assertEqual(c.prior.read(path), {'ok': True})


if __name__ == '__main__':
    unittest.main()
