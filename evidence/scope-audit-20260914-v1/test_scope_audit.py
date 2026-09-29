"""Synthetic contract tests only. These fixtures are not benchmark questions."""
import copy
import itertools
import unittest
from unittest.mock import patch

import scope_audit as s
from test_quote_adapter import chain

VALUES = {'entity': 'ALPHA', 'edition': '제7회', 'year': '2031년',
          'semester': '봄학기', 'audience': '학부생', 'track': '연구트랙', 'direction': '국외파견'}


def fixture(dimensions=s.DIMENSIONS):
    scope = {name: VALUES[name] for name in dimensions}
    title = ' · '.join(scope.values())
    body = '지원 금액은 10원입니다.'
    request = s.q.build_extraction_request(title + ' 지원 금액은?', body,
                                           [{'chunk_id': 's1', 'source_title': title, 'text': body}])
    def ref(text, field='text'):
        return {'source_id': 's1', 'field': field, 'quote': text}
    assertion = {'scope': scope, 'relation': 'support_amount',
                 'value': {'kind': 'amount', 'text': '10원', 'measurement_unit': '원', 'operator': 'eq'},
                 'conditions': []}
    fact = {'fact_id': 'f1', 'assertion': copy.deepcopy(assertion), 'evidence_quote': ref(body),
            'relation_span': ref('지원 금액'), 'value_span': ref('10원'), 'body_scope': [],
            'title_scope': [{'dimension': k, 'reference': ref(v, 'title')} for k, v in scope.items()],
            'condition_spans': [], 'operator_span': None, 'table': None}
    extraction = {'version': s.q.VERSION, 'request_id': request['request_id'],
                  'query_scope': [{'dimension': k, 'quote': v} for k, v in scope.items()],
                  'units': [{'unit_id': 'u000', 'coverage': 'complete', 'reason': 'synthetic fixture',
                             'atoms': [{'atom_id': 'a1', 'claim_quote': body, 'claim': copy.deepcopy(assertion),
                                        'evidence': [fact], 'requested_sources': ['s1']}]}]}
    return request, extraction, declaration(request, scope)


def declaration(request, values):
    return {'version': s.DECLARATION_VERSION, 'request_id': request['request_id'],
            'dimensions': {key: {'status': 'specified' if key in values else 'not_specified',
                                 'quote': values.get(key), 'reason': 'synthetic decision, not a model judgment'}
                           for key in s.DIMENSIONS}}


def run(request, extraction, declared):
    return s.analyze(request, s.v1.encode(extraction), None if declared is None else s.v1.encode(declared))


def remove_fact_scope(extraction, dimension=None):
    for unit in extraction['units']:
        for atom in unit['atoms']:
            for fact in atom['evidence']:
                if dimension is None:
                    fact['assertion']['scope'] = {}
                    fact['body_scope'] = []
                    fact['title_scope'] = []
                else:
                    fact['assertion']['scope'].pop(dimension)
                    for field in ('body_scope', 'title_scope'):
                        fact[field] = [item for item in fact[field] if item['dimension'] != dimension]


class ScopeAuditTests(unittest.TestCase):
    def test_persisted_histogram_comparison_preserves_json_key_semantics(self):
        from run_offline import persisted_equal
        current, saved = {'histogram': {2: 13, 1: 18}}, {'histogram': {'2': 13, '1': 18}}
        self.assertNotEqual(current, saved)
        self.assertTrue(persisted_equal(current, saved))
        saved['histogram']['2'] = 14
        self.assertFalse(persisted_equal(current, saved))

    def test_persisted_comparison_never_turns_missing_or_nan_into_valid(self):
        from run_offline import persisted_equal
        self.assertFalse(persisted_equal({'gfc': 13}, {'gfc': None}))
        self.assertFalse(persisted_equal({'gfc': 13}, {}))
        with self.assertRaises(ValueError): persisted_equal({'score': float('nan')}, {'score': None})

    def test_full_synthetic_scope_satisfies_local_preconditions_not_truth(self):
        result = run(*fixture())
        self.assertTrue(result['local_preconditions_satisfied'])
        self.assertEqual(result['typed_status_counts'], {'matched': 1})
        self.assertFalse(result['eligible_for_service'])
        self.assertFalse(result['semantic_verified'])
        self.assertFalse(result['original_run_reclassified'])
        self.assertIsNone(result['candidate_gfc'])
        self.assertEqual(result['external_calls'], 0)

    def test_regression_empty_scope_parses_but_readiness_is_held(self):
        req, ex, _ = fixture()
        ex['query_scope'] = []
        ex['units'][0]['atoms'][0]['claim']['scope'] = {}
        remove_fact_scope(ex)
        # Frozen schema and parser accept this shape; accepting shape as readiness
        # is the old failure pattern. No original runtime file is changed.
        s.q.response(req, s.v1.encode(ex), 'extract')
        s.p.extraction_bridge(req, s.v1.encode(ex))
        result = run(req, ex, None)
        self.assertFalse(result['local_preconditions_satisfied'])
        self.assertIn('query_scope_empty', result['issues'])
        self.assertEqual(result['typed_reason_counts'], {'query_scope_required': 1})

    def test_eight_presence_combinations_separate_claim_inheritance(self):
        for query, claim, evidence in itertools.product((False, True), repeat=3):
            with self.subTest(query=query, claim=claim, evidence=evidence):
                req, ex, dec = fixture()
                if not query: ex['query_scope'] = []
                if not claim: ex['units'][0]['atoms'][0]['claim']['scope'] = {}
                if not evidence: remove_fact_scope(ex)
                result = run(req, ex, dec)
                self.assertEqual(result['local_preconditions_satisfied'], query and evidence)

    def test_empty_claim_inherits_query_without_artificial_failure(self):
        req, ex, dec = fixture()
        ex['units'][0]['atoms'][0]['claim']['scope'] = {}
        result = run(req, ex, dec)
        self.assertTrue(result['local_preconditions_satisfied'])
        self.assertEqual(result['atom_audit'][0]['query_inherited_dimensions'], list(s.DIMENSIONS))

    def test_all_128_query_subsets_compared_to_explicit_declaration(self):
        for mask in itertools.product((False, True), repeat=len(s.DIMENSIONS)):
            req, ex, dec = fixture()
            keep = {name for name, present in zip(s.DIMENSIONS, mask) if present}
            ex['query_scope'] = [item for item in ex['query_scope'] if item['dimension'] in keep]
            result = run(req, ex, dec)
            self.assertEqual(result['local_preconditions_satisfied'], all(mask))
            if not all(mask):
                self.assertIn('declaration_extraction_scope_disagreement', result['issues'])

    def test_missing_each_fact_dimension_is_exposed(self):
        for name in s.DIMENSIONS:
            req, ex, dec = fixture()
            remove_fact_scope(ex, name)
            result = run(req, ex, dec)
            self.assertFalse(result['local_preconditions_satisfied'])
            self.assertEqual(result['atom_audit'][0]['facts'][0]['missing_dimensions'], [name])

    def test_query_dimension_absence_is_declared_not_forced_present(self):
        req, ex, dec = fixture(('entity',))
        result = run(req, ex, dec)
        self.assertTrue(result['local_preconditions_satisfied'])
        self.assertEqual(result['scope_declaration']['decision_status_counts']['not_specified'], 6)
        self.assertFalse(result['scope_declaration']['absence_assertions_verified'])

    def test_all_not_specified_does_not_bypass_frozen_query_requirement(self):
        req, ex, _ = fixture()
        result = run(req, ex, declaration(req, {}))
        self.assertIn('scope_declaration_has_no_specified_dimension', result['issues'])
        self.assertFalse(result['local_preconditions_satisfied'])

    def test_missing_declaration_never_invented_from_extraction(self):
        result = run(*fixture()[:2], None)
        self.assertIsNone(result['scope_declaration'])
        self.assertIn('explicit_scope_declaration_missing', result['issues'])
        self.assertFalse(result['local_preconditions_satisfied'])

    def test_each_unresolved_dimension_holds_without_converting_to_absent(self):
        for name in s.DIMENSIONS:
            req, ex, dec = fixture()
            dec['dimensions'][name].update(status='unresolved', quote=None)
            result = run(req, ex, dec)
            self.assertEqual(result['scope_declaration']['unresolved_dimensions'], [name])
            self.assertIn('scope_declaration_unresolved', result['issues'])

    def test_missing_or_unknown_dimension_rejected(self):
        for name in s.DIMENSIONS:
            req, _, dec = fixture()
            dec['dimensions'].pop(name)
            with self.assertRaises(ValueError): s.parse_declaration(req, s.v1.encode(dec))
        req, _, dec = fixture()
        dec['dimensions']['unknown'] = copy.deepcopy(dec['dimensions']['entity'])
        with self.assertRaises(ValueError): s.parse_declaration(req, s.v1.encode(dec))

    def test_duplicate_json_keys_and_nonfinite_json_rejected(self):
        req, _, dec = fixture()
        text = s.v1.encode(dec)
        for bad in [text.replace('"version":', '"version":"bad","version":', 1), '{"x":NaN}']:
            with self.assertRaises(ValueError): s.parse_declaration(req, bad)

    def test_version_request_and_extra_flags_rejected(self):
        for field in ('version', 'request_id', 'prevalidated'):
            req, _, dec = fixture()
            dec[field] = 'forged'
            with self.assertRaises(ValueError): s.parse_declaration(req, s.v1.encode(dec))

    def test_query_reference_cannot_be_source_only_or_fuzzy(self):
        for quote in ('지원 금액은 10원입니다.', 'alpha', '제 7회', ''):
            req, _, dec = fixture()
            dec['dimensions']['entity']['quote'] = quote
            with self.assertRaises(ValueError): s.parse_declaration(req, s.v1.encode(dec))

    def test_repeated_and_overlapping_query_quotes_not_first_match(self):
        for text, quote in [('ALPHA ALPHA', 'ALPHA'), ('aaa', 'aa')]:
            req = s.q.build_extraction_request(text, '초안', [{'chunk_id': 's', 'text': '근거'}])
            with self.assertRaisesRegex(ValueError, 'ambiguous'):
                s.parse_declaration(req, s.v1.encode(declaration(req, {'entity': quote})))

    def test_unicode_exact_query_spans_preserved(self):
        query = '앞😀\r\n가상 사업 문의'
        req = s.q.build_extraction_request(query, '초안', [{'chunk_id': 's', 'text': '근거'}])
        result = s.parse_declaration(req, s.v1.encode(declaration(req, {'entity': '😀\r\n가상 사업'})))
        span = result['exact_query_spans']['entity']
        self.assertEqual(query[span['start']:span['end']], span['quote'])

    def test_status_quote_pairing_required(self):
        for status, quote in [('specified', None), ('not_specified', 'ALPHA'), ('unresolved', 'ALPHA')]:
            req, _, dec = fixture()
            dec['dimensions']['entity'].update(status=status, quote=quote)
            with self.assertRaises(ValueError): s.parse_declaration(req, s.v1.encode(dec))

    def test_reason_not_optional_empty_or_nonstring(self):
        for reason in ('', ' ', None, True):
            req, _, dec = fixture()
            dec['dimensions']['entity']['reason'] = reason
            with self.assertRaises(ValueError): s.parse_declaration(req, s.v1.encode(dec))

    def test_fake_offsets_or_source_hashes_not_accepted(self):
        for field in ('start', 'end', 'source_sha256'):
            req, _, dec = fixture()
            dec['dimensions']['entity'][field] = 0
            with self.assertRaises(ValueError): s.parse_declaration(req, s.v1.encode(dec))

    def test_claim_query_conflict_stays_nonmatched(self):
        req, ex, dec = fixture()
        ex['units'][0]['atoms'][0]['claim']['scope']['entity'] = 'BETA'
        result = run(req, ex, dec)
        self.assertEqual(result['atom_audit'][0]['claim_query_conflicts'], ['entity'])
        self.assertEqual(result['typed_reason_counts'], {'claim_query_scope_conflict': 1})

    def test_unbound_fact_scope_still_raises_frozen_validator_error(self):
        req, ex, dec = fixture()
        ex['units'][0]['atoms'][0]['evidence'][0]['title_scope'] = []
        with self.assertRaisesRegex(ValueError, 'scope_binding_incomplete'): run(req, ex, dec)

    def test_uncertain_and_abstention_not_reported_as_ready(self):
        for coverage in ('uncertain', 'abstention'):
            req, ex, dec = fixture()
            ex['units'][0].update(coverage=coverage, atoms=[])
            result = run(req, ex, dec)
            self.assertFalse(result['local_preconditions_satisfied'])
            self.assertIn('unit_coverage_' + coverage, result['issues'])

    def test_missing_units_and_duplicate_atoms_still_rejected(self):
        for mode in ('unit', 'atom'):
            req, ex, dec = fixture()
            if mode == 'unit': ex['units'] = []
            else: ex['units'][0]['atoms'] *= 2
            with self.assertRaises(ValueError): run(req, ex, dec)

    def test_mutated_request_or_source_hash_still_rejected(self):
        for mode in ('query', 'source'):
            req, ex, dec = fixture()
            if mode == 'query': req['payload']['data']['query'] += ' changed'
            else: req['payload']['data']['sources'][0]['source_sha256'] = 'forged'
            with self.assertRaises(ValueError): run(req, ex, dec)

    def test_original_inputs_schema_and_prompt_unchanged(self):
        req, ex, dec = fixture()
        before = copy.deepcopy((req, ex, dec, s.q.EXTRACT_SCHEMA, s.q.EXTRACT_INSTRUCTIONS))
        run(req, ex, dec)
        self.assertEqual((req, ex, dec, s.q.EXTRACT_SCHEMA, s.q.EXTRACT_INSTRUCTIONS), before)

    def test_contract_has_no_provider_body_or_prompt(self):
        req, _, _ = fixture()
        spec = s.declaration_contract(req)
        self.assertEqual(set(spec), {'version', 'request_id', 'query_sha256', 'response_schema',
                                     'provider_compatibility_tested', 'external_calls'})
        spec['response_schema']['required'] = []
        self.assertTrue(s.declaration_contract(req)['response_schema']['required'])

    def test_frozen_typed_parser_invoked_not_trusting_ready_flag(self):
        original = s.v1.parse_extraction
        with patch.object(s.v1, 'parse_extraction', wraps=original) as parser:
            run(*fixture())
        parser.assert_called_once()

    def test_false_absence_shared_by_all_stages_remains_known_limit(self):
        req, ex, dec = fixture()
        name = 'year'
        ex['query_scope'] = [x for x in ex['query_scope'] if x['dimension'] != name]
        ex['units'][0]['atoms'][0]['claim']['scope'].pop(name)
        remove_fact_scope(ex, name)
        dec['dimensions'][name].update(status='not_specified', quote=None)
        result = run(req, ex, dec)
        self.assertTrue(result['local_preconditions_satisfied'])
        self.assertFalse(result['scope_declaration']['absence_assertions_verified'])
        self.assertFalse(result['semantic_verified'])

    def test_wrong_shared_operator_still_requires_independent_review(self):
        req, ex, _, _ = chain(wrong=True)
        dec = declaration(req, {x['dimension']: x['quote'] for x in ex['query_scope']})
        result = run(req, ex, dec)
        self.assertTrue(result['local_preconditions_satisfied'])
        self.assertFalse(result['semantic_verified'])
        self.assertFalse(result['eligible_for_service'])


if __name__ == '__main__':
    unittest.main()
