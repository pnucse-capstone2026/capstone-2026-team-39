import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import array_schema as s
import run_array as r
from test_quote_adapter import chain, extraction, request


def restore(schema, removed):
    result = copy.deepcopy(schema)
    for item in removed:
        parts = [x.replace('~1', '/').replace('~0', '~') for x in item['pointer'].split('/')[1:]]
        node = result
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        node[parts[-1]] = item['value']
    return result


class Response(io.BytesIO):
    status = 200


def response(value, finish='STOP'):
    return Response(json.dumps({'candidates': [{'finishReason': finish,
        'content': {'parts': [{'text': s.q.v1.encode(value)}]}}]}).encode())


class ArraySchemaTests(unittest.TestCase):
    def test_maxitems_only_difference_and_exact_restoration_both_stages(self):
        req, _, sem, _ = chain()
        for item in (req, sem):
            old = s.p.envelope(item)
            before = copy.deepcopy(item)
            new = s.envelope(item)
            removed = new['removed_array_constraints']
            self.assertGreater(len(removed), 0)
            self.assertEqual({x['keyword'] for x in removed}, {'maxItems'})
            self.assertEqual(len({x['pointer'] for x in removed}), len(removed))
            restored = copy.deepcopy(new['provider_body'])
            config = restored['generationConfig']
            config['responseJsonSchema'] = restore(config['responseJsonSchema'], removed)
            self.assertEqual(restored, old['provider_body'])
            self.assertEqual(item, before)
            self.assertEqual(new['host_request'], old['host_request'])
            metrics = s.p.schema_metrics(config['responseJsonSchema'])
            self.assertEqual(metrics, s.p.schema_metrics(old['provider_body']['generationConfig']['responseJsonSchema']))

    def test_regression_no_maxitems_at_schema_nodes(self):
        schema, _ = s.p.project(s.q.EXTRACT_SCHEMA)
        changed, removed = s.without_array_bounds(schema)
        def count(node):
            return int('maxItems' in node) + sum(count(x) for x in node.get('properties', {}).values()) + (count(node['items']) if 'items' in node else 0) + sum(count(x) for x in node.get('anyOf', []))
        self.assertGreater(count(schema), 0)
        self.assertEqual(count(changed), 0)
        self.assertEqual(len(removed), count(schema))

    def test_property_names_and_escaped_pointers_preserved(self):
        raw = s.q.obj({'maxItems': s.q.S(enum=('maxItems',)), 'a/b~c': s.q.arr(s.q.S())})
        projected, _ = s.p.project(raw)
        result, removed = s.without_array_bounds(projected)
        self.assertEqual(set(result['properties']), {'maxItems', 'a/b~c'})
        self.assertEqual(result['properties']['maxItems']['enum'], ['maxItems'])
        self.assertEqual(restore(result, removed), projected)
        self.assertEqual(removed[0]['pointer'], '/properties/a~1b~0c/maxItems')

    def test_pinned_inputs_and_exact_previous_synthetic_body(self):
        pins, synthetic, dev = s.inputs()
        self.assertTrue(pins)
        self.assertEqual(synthetic, s.synthetic_request())
        self.assertEqual(s.previous.CASE, 'shadow_emp_01')
        self.assertEqual(s.q.model_bundle(dev), s.q.model_bundle(s.envelope(dev)['host_request']))

    def test_host_still_rejects_array_limits_in_both_stages(self):
        req, ex, sem, rev = chain()
        for item, value in ((req, ex), (sem, rev)):
            changed = copy.deepcopy(value)
            changed['units'] *= 33
            with self.assertRaisesRegex(ValueError, 'schema_array_size'):
                s.validate_response(s.envelope(item), s.q.v1.encode(changed))
        changed = copy.deepcopy(ex)
        changed['units'][0]['atoms'] *= 13
        with self.assertRaisesRegex(ValueError, 'schema_array_size'):
            s.validate_response(s.envelope(req), s.q.v1.encode(changed))

    def test_exact_quote_string_type_and_extra_fields_not_repaired(self):
        req = request()
        for mode in ('quote', 'unit', 'extra', 'length'):
            value = extraction(req)
            atom = value['units'][0]['atoms'][0]
            if mode == 'quote': atom['evidence'][0]['value_span']['quote'] = 'invented'
            if mode == 'unit': atom['claim']['value']['measurement_unit'] = None
            if mode == 'extra': value['extra'] = 'ignored?'
            if mode == 'length': value['units'][0]['reason'] = 'x' * 2001
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                s.validate_response(s.envelope(req), s.q.v1.encode(value))

    def test_tampered_body_audit_or_host_schema_rejected(self):
        req = request()
        for key in ('provider_body_sha256', 'removed_array_constraints', 'host_schema_sha256'):
            wrapped = s.envelope(req)
            wrapped[key] = 'changed'
            with self.assertRaisesRegex(ValueError, 'transport_envelope_changed'):
                s.validate_response(wrapped, s.q.v1.encode(extraction(req)))

    def test_parsers_and_semantic_limitations_unchanged(self):
        req, ex, sem, rev = chain(wrong=True)
        for item, value, name in ((req, ex, 'parse_extraction'), (sem, rev, 'parse_semantic')):
            original = getattr(s.q, name)
            with patch.object(s.q, name, wraps=original) as parser:
                result = s.validate_response(s.envelope(item), s.q.v1.encode(value))
            parser.assert_called_once_with(item, s.q.v1.encode(value))
            self.assertFalse(result['eligible_for_service'])
        decision = s.q.combine(req, s.q.v1.encode(ex), s.q.v1.encode(rev))
        self.assertTrue(decision['units'][0]['offline_candidate_only'])
        self.assertFalse(decision['eligible_for_service'])
        self.assertIsNone(decision['candidate_gfc'])

    def test_budget_fixed_order_validation_gates_and_cap(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            budget = r.Budget(directory)
            for index, slot in enumerate(r.SLOTS):
                with self.assertRaises(ValueError): budget.reserve('other', r.STAGES[index], {})
                budget.reserve(slot, r.STAGES[index], {})
                with self.assertRaises(ValueError): budget.reserve(slot, r.STAGES[index], {})
                if index < 2:
                    with self.assertRaises(ValueError): budget.reserve(r.SLOTS[index+1], r.STAGES[index+1], {})
                budget.finish(slot, 'validated', 200)
            with self.assertRaises(ValueError): budget.reserve('more', 'extract', {})
            budget.db.close()

    def test_three_call_mock_uses_projected_body_and_full_host(self):
        req, ex, _, rev = chain()
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root = Path(directory)
            budget, sleeps = r.Budget(root), []
            payloads = [ex, ex, rev]
            class Opener:
                def open(self, sent, timeout):
                    body = json.loads(sent.data)
                    self_index = len(budget.rows()) - 1
                    self_expected = req if self_index < 2 else s.q.build_semantic_request(req, s.q.v1.encode(ex))
                    assert body == s.envelope(self_expected)['provider_body']
                    assert budget.rows()[-1]['state'] == 'reserved'
                    assert timeout == 45
                    return response(payloads[self_index])
            with patch.object(r.urllib.request, 'build_opener', return_value=Opener()), contextlib.redirect_stdout(io.StringIO()):
                result = r.pipeline(req, req, root, lambda slot, stage, item: r.post(budget, slot, stage, item, 'SYNTHETIC_KEY'), sleeps.append)
            self.assertEqual(result['status'], 'PILOT_COMPLETE')
            self.assertEqual(sleeps, [15, 15])
            self.assertEqual(len(budget.rows()), 3)
            self.assertFalse(result['decision']['eligible_for_service'])
            diagnostic = r.prior.read(root / (r.SLOTS[0] + '.host-validation.json'))
            self.assertFalse(diagnostic['valid_extraction'])
            budget.db.close()

    def test_http_errors_stop_redacted_without_retry(self):
        for code in (400, 429):
            with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
                root, req = Path(directory), request()
                budget = r.Budget(root)
                with patch.object(r.urllib.request, 'build_opener') as opened:
                    opened.return_value.open.side_effect = urllib.error.HTTPError('https://invalid/?key=SYNTHETIC_KEY', code, 'fail', {}, io.BytesIO(b'SYNTHETIC_KEY'))
                    result = r.pipeline(req, req, root, lambda slot, stage, item: r.post(budget, slot, stage, item, 'SYNTHETIC_KEY'), lambda _: self.fail())
                self.assertEqual(opened.return_value.open.call_count, 1)
                self.assertEqual(result['failure']['http_status'], code)
                self.assertEqual((root / (r.SLOTS[0] + '.http-error.txt')).read_text(), '[REDACTED]')
                self.assertIsNone(r.prior.CAPABILITY['url'])
                budget.db.close()

    def test_http200_incomplete_preserves_receipt_and_stops(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root, req = Path(directory), request()
            budget = r.Budget(root)
            with patch.object(r.urllib.request, 'build_opener') as opened:
                opened.return_value.open.return_value = response(extraction(req), 'MAX_TOKENS')
                result = r.pipeline(req, req, root, lambda slot, stage, item: r.post(budget, slot, stage, item, 'SYNTHETIC_KEY'), lambda _: self.fail())
            self.assertEqual(result['status'], 'STOPPED_INCOMPLETE')
            self.assertEqual(opened.return_value.open.call_count, 1)
            self.assertTrue((root / (r.SLOTS[0] + '.receipt.json')).exists())
            self.assertEqual(budget.rows()[0]['http_status'], 200)
            budget.db.close()

    def test_invalid_dev_extraction_stops_semantic(self):
        req, ex, _, _ = chain()
        bad = copy.deepcopy(ex)
        bad['units'][0]['atoms'] *= 13
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            root, sleeps = Path(directory), []
            budget = r.Budget(root)
            with patch.object(r.urllib.request, 'build_opener') as opened, contextlib.redirect_stdout(io.StringIO()):
                opened.return_value.open.side_effect = [response(ex), response(bad)]
                result = r.pipeline(req, req, root, lambda slot, stage, item: r.post(budget, slot, stage, item, 'SYNTHETIC_KEY'), sleeps.append)
            self.assertEqual(opened.return_value.open.call_count, 2)
            self.assertEqual(sleeps, [15])
            self.assertIn('schema_array_size', result['failure']['reason'])
            budget.db.close()

    def test_no_approval_or_reused_output(self):
        for message in (None, ''):
            with self.assertRaisesRegex(ValueError, 'exact_approval'):
                r.live('irrelevant', r.AUTH, message)
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            with self.assertRaises(ValueError): s.previous.new_directory(Path(directory))


if __name__ == '__main__':
    unittest.main()
