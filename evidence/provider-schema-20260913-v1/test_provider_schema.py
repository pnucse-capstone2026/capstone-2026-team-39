import copy
import unittest
from unittest.mock import patch

import provider_schema as p
from test_quote_adapter import request, extraction, chain


def restore(projected, removed):
    result = copy.deepcopy(projected)
    for row in removed:
        parts = [part.replace('~1', '/').replace('~0', '~') for part in row['pointer'].split('/')[1:]]
        parent = result
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        parent[parts[-1]] = row['value']
    return result


class ProviderSchemaTests(unittest.TestCase):
    def test_only_length_constraints_removed_and_exactly_restorable(self):
        for schema in (p.q.EXTRACT_SCHEMA, p.q.REVIEW_SCHEMA):
            before = copy.deepcopy(schema)
            projected, removed = p.project(schema)
            self.assertTrue(removed)
            self.assertEqual({r['keyword'] for r in removed}, p.HOST_ONLY)
            self.assertEqual(len({r['pointer'] for r in removed}), len(removed))
            self.assertEqual(restore(projected, removed), schema)
            self.assertEqual(schema, before)
            self.assertEqual(p.schema_metrics(projected)['max_schema_depth'], p.schema_metrics(schema)['max_schema_depth'])
        self.assertEqual(len(p.project(p.q.EXTRACT_SCHEMA)[1]), 144)

    def test_properties_named_like_keywords_and_enum_strings_are_not_removed(self):
        schema = p.q.obj({'minLength': p.q.S(enum=('minLength', 'maxLength')),
                         'a/b~c': p.q.S(empty=True)})
        projected, removed = p.project(schema)
        self.assertEqual(set(projected['properties']), {'minLength', 'a/b~c'})
        self.assertEqual(projected['properties']['minLength']['enum'], ['minLength', 'maxLength'])
        self.assertEqual(restore(projected, removed), schema)

    def test_future_unknown_keywords_and_unhandled_types_fail_closed(self):
        for key, value in [('pattern', '^x'), ('$ref', '#/a'), ('description', 'future')]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'unsupported_schema_keys'):
                p.project({**p.q.S(), key: value})
        with self.assertRaisesRegex(ValueError, 'unsupported_schema_type'):
            p.project({'type': 'integer'})

    def test_malformed_bounds_and_schema_shapes_rejected(self):
        for node in [None, {'anyOf': []}, {'anyOf': [p.q.S()], 'type': 'string'},
                     {'type': 'string', 'minLength': True, 'maxLength': 20},
                     {'type': 'string', 'minLength': 10, 'maxLength': 2},
                     {'type': 'array', 'items': p.q.S(), 'maxItems': True},
                     {'type': 'object', 'properties': {}, 'required': ['missing'], 'additionalProperties': False}]:
            with self.subTest(node=node), self.assertRaises(ValueError):
                p.project(node)

    def test_excessive_schema_nesting_rejected_locally(self):
        node = p.q.S()
        for _ in range(65):
            node = p.q.arr(node)
        with self.assertRaisesRegex(ValueError, 'unsupported_schema_shape'):
            p.project(node)

    def test_messages_host_request_and_generation_settings_unchanged(self):
        req = request()
        before = copy.deepcopy(req)
        wrapped = p.envelope(req)
        body = wrapped['provider_body']
        bundle = p.q.model_bundle(req)
        self.assertEqual(wrapped['host_request'], before)
        self.assertEqual(body['contents'][0]['parts'][0]['text'], bundle['messages'][1]['content'])
        self.assertEqual(body['systemInstruction']['parts'][0]['text'], bundle['messages'][0]['content'])
        self.assertEqual({k: v for k, v in body['generationConfig'].items() if k != 'responseJsonSchema'},
                         {'temperature': 0.0, 'maxOutputTokens': 8192, 'responseMimeType': 'application/json'})
        body['generationConfig']['responseJsonSchema']['properties'].clear()
        self.assertEqual(req, before)
        self.assertEqual(wrapped['host_request'], before)

    def test_both_stages_call_the_unchanged_host_parser(self):
        req, ex, sem, rev = chain()
        for stage_request, value, parser_name in [(req, ex, 'parse_extraction'), (sem, rev, 'parse_semantic')]:
            original = getattr(p.q, parser_name)
            with patch.object(p.q, parser_name, wraps=original) as parser:
                result = p.validate_response(p.envelope(stage_request), p.q.v1.encode(value))
            parser.assert_called_once_with(stage_request, p.q.v1.encode(value))
            self.assertEqual(result['host_validation'], original(stage_request, p.q.v1.encode(value)))
            self.assertFalse(result['eligible_for_service'])
            self.assertIsNone(result['candidate_gfc'])

    def test_removed_string_bounds_still_rejected_by_host_both_stages(self):
        req, ex, sem, rev = chain()
        for stage_request, value in ((req, ex), (sem, rev)):
            for reason in ('', ' ', 'x' * 2001):
                changed = copy.deepcopy(value)
                changed['units'][0]['reason'] = reason
                with self.subTest(stage=stage_request['payload']['stage'], length=len(reason)):
                    with self.assertRaisesRegex(ValueError, 'schema_string_size'):
                        p.validate_response(p.envelope(stage_request), p.q.v1.encode(changed))

    def test_nullable_measurement_unit_and_missing_fields_not_repaired(self):
        req = request()
        for mode in ('null', 'missing', 'extra', 'array_limit'):
            value = extraction(req)
            atom = value['units'][0]['atoms'][0]
            if mode == 'null': atom['claim']['value']['measurement_unit'] = None
            if mode == 'missing': del value['units'][0]['reason']
            if mode == 'extra': atom['claim']['value']['extra'] = 'not allowed'
            if mode == 'array_limit': value['units'] *= 33
            before = copy.deepcopy(value)
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                p.validate_response(p.envelope(req), p.q.v1.encode(value))
            self.assertEqual(value, before)

    def test_literal_quote_and_source_failures_still_rejected(self):
        req = request()
        for mode in ('missing_quote', 'unknown_source', 'operator', 'scope'):
            value = extraction(req)
            fact = value['units'][0]['atoms'][0]['evidence'][0]
            if mode == 'missing_quote': fact['value_span']['quote'] = 'invented value'
            if mode == 'unknown_source': fact['evidence_quote']['source_id'] = 'unknown'
            if mode == 'operator': fact['operator_span'] = None
            if mode == 'scope': fact['title_scope'] = []
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                p.validate_response(p.envelope(req), p.q.v1.encode(value))

    def test_transport_tampering_or_host_schema_relaxation_rejected(self):
        req = request()
        for mode in ('provider', 'hash', 'removed', 'host_schema'):
            wrapped = p.envelope(req)
            if mode == 'provider': wrapped['provider_body']['generationConfig']['temperature'] = 1
            if mode == 'hash': wrapped['envelope_sha256'] = 'fake'
            if mode == 'removed': wrapped['removed_constraints'] = []
            if mode == 'host_schema': wrapped['host_request']['payload']['response_schema']['properties']['version']['minLength'] = 0
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                p.validate_response(wrapped, p.q.v1.encode(extraction(req)))

    def test_malformed_json_wrong_request_and_unknown_stage_rejected(self):
        req = request()
        ex = extraction(req)
        ex['request_id'] = 'stale'
        for raw in ('{}', 'not json', p.q.v1.encode(ex)):
            with self.assertRaises(ValueError):
                p.validate_response(p.envelope(req), raw)
        req['payload']['stage'] = 'unknown'
        with self.assertRaises(ValueError):
            p.envelope(req)

    def test_semantic_disagreement_remains_non_candidate(self):
        req, ex, sem, rev = chain(verdict='contradicted')
        p.validate_response(p.envelope(req), p.q.v1.encode(ex))
        p.validate_response(p.envelope(sem), p.q.v1.encode(rev))
        result = p.q.combine(req, p.q.v1.encode(ex), p.q.v1.encode(rev))
        self.assertFalse(result['units'][0]['offline_candidate_only'])
        self.assertFalse(result['eligible_for_service'])

    def test_semantic_false_positive_limitation_is_not_hidden(self):
        req, ex, sem, rev = chain(wrong=True)
        p.validate_response(p.envelope(req), p.q.v1.encode(ex))
        p.validate_response(p.envelope(sem), p.q.v1.encode(rev))
        result = p.q.combine(req, p.q.v1.encode(ex), p.q.v1.encode(rev))
        self.assertTrue(result['units'][0]['offline_candidate_only'])
        self.assertFalse(result['eligible_for_service'])
        self.assertIsNone(result['candidate_gfc'])


if __name__ == '__main__':
    unittest.main()
