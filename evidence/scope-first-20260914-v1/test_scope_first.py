import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import scope_first as c
import run_pilot as r
from test_scope_audit import fixture, remove_fact_scope, declaration
from test_quote_adapter import chain, review


def candidate_fixture(wrong=False):
    if wrong:
        base, extraction, _, _ = chain(wrong=True)
        scope = declaration(base, {x['dimension']: x['quote'] for x in extraction['query_scope']})
    else:
        base, extraction, scope = fixture()
    scope.update(version=c.VERSION, request_id=c.scope_request(base)['request_id'])
    scope_text = c.v1.encode(scope)
    extraction.update(version=c.VERSION, request_id=c.extraction_request(base, scope_text)['request_id'])
    extraction_text = c.v1.encode(extraction)
    sem = c.review_request(base, scope_text, extraction_text)
    return base, scope_text, extraction_text, c.v1.encode(review(sem))


class ScopeFirstTests(unittest.TestCase):
    def test_scope_call_sees_only_original_query_no_draft_sources_or_gold(self):
        base, _, _ = fixture()
        req = c.scope_request(base)
        env = c.envelope(req, c.scope_request(base))
        data = json.loads(env['provider_body']['contents'][0]['parts'][0]['text'])['data']
        self.assertEqual(data, {'query': base['payload']['data']['query']})
        self.assertEqual(set(req['payload']['data']), {'query'})

    def test_new_version_and_stage_request_ids_separate_from_frozen_wire(self):
        base, scope, extraction, _ = candidate_fixture()
        self.assertNotEqual(c.scope_request(base)['request_id'], base['request_id'])
        self.assertNotEqual(c.extraction_request(base, scope)['request_id'], c.scope_request(base)['request_id'])
        self.assertEqual(json.loads(extraction)['version'], c.VERSION)

    def test_wire_migration_changes_metadata_only_no_scope_fill(self):
        base, scope, extraction, _ = candidate_fixture()
        raw = json.loads(extraction)
        parsed = c.extraction_bridge(base, scope, extraction)
        self.assertTrue(parsed['wire_metadata_migration_only'])
        self.assertEqual(parsed['scope_audit']['query_scope'], {x['dimension']: x['quote'] for x in raw['query_scope']})
        self.assertEqual(raw, json.loads(extraction))

    def test_full_synthetic_chain_completes_but_not_service_or_gfc(self):
        base, scope, extraction, sem = candidate_fixture()
        result = c.combine(base, scope, extraction, sem)
        self.assertEqual(result['decision']['status'], 'offline_review_complete')
        self.assertTrue(result['decision']['units'][0]['offline_candidate_only'])
        self.assertFalse(result['eligible_for_service'])
        self.assertFalse(result['original_run_reclassified'])
        self.assertIsNone(result['candidate_gfc'])

    def test_scope_missing_unresolved_or_all_absent_blocks_next_call(self):
        for mode in ('missing', 'unresolved', 'absent'):
            base, scope, _, _ = candidate_fixture()
            raw = json.loads(scope)
            if mode == 'missing': raw['dimensions'].pop('year')
            if mode == 'unresolved': raw['dimensions']['year'].update(status='unresolved', quote=None)
            if mode == 'absent':
                for item in raw['dimensions'].values(): item.update(status='not_specified', quote=None)
            with self.assertRaises(ValueError): c.extraction_request(base, c.v1.encode(raw))

    def test_scope_quote_must_be_exact_query_not_inferred_year(self):
        base, scope, _, _ = candidate_fixture()
        raw = json.loads(scope)
        raw['dimensions']['year']['quote'] = '2040년'
        with self.assertRaisesRegex(ValueError, 'quote_not_found'):
            c.extraction_request(base, c.v1.encode(raw))

    def test_scope_and_extraction_wrong_request_ids_rejected(self):
        base, scope, extraction, _ = candidate_fixture()
        raw = json.loads(scope)
        raw['request_id'] = base['request_id']
        with self.assertRaises(ValueError): c.scope_bridge(base, c.v1.encode(raw))
        raw = json.loads(extraction)
        raw['request_id'] = c.scope_request(base)['request_id']
        with self.assertRaises(ValueError): c.extraction_bridge(base, scope, c.v1.encode(raw))

    def test_original_draft_sources_preserved_and_scope_reason_not_forwarded(self):
        base, scope, _, _ = candidate_fixture()
        raw = json.loads(scope)
        raw['dimensions']['entity']['reason'] = 'UNTRUSTED_DECISION_REASON'
        request = c.extraction_request(base, c.v1.encode(raw))
        data = request['payload']['data']
        old_data = json.loads(c.q.model_bundle(base)['messages'][1]['content'])['data']
        self.assertEqual({k: v for k, v in data.items() if k != 'declared_query_scope'}, old_data)
        self.assertNotIn('UNTRUSTED_DECISION_REASON', c.v1.encode(request))

    def test_missing_query_scope_is_not_repaired_from_declaration(self):
        base, scope, extraction, _ = candidate_fixture()
        raw = json.loads(extraction)
        raw['query_scope'] = []
        changed = c.v1.encode(raw)
        result = c.extraction_bridge(base, scope, changed)
        self.assertFalse(result['scope_audit']['local_preconditions_satisfied'])
        self.assertEqual(result['scope_audit']['query_scope'], {})
        with self.assertRaisesRegex(ValueError, 'no_semantic_call'): c.review_request(base, scope, changed)

    def test_missing_fact_scope_not_filled_from_question(self):
        base, scope, extraction, _ = candidate_fixture()
        raw = json.loads(extraction)
        remove_fact_scope(raw)
        result = c.extraction_bridge(base, scope, c.v1.encode(raw))
        self.assertFalse(result['scope_audit']['local_preconditions_satisfied'])
        self.assertEqual(result['scope_audit']['empty_fact_scopes'], 1)

    def test_empty_claim_scope_inheritance_stays_allowed(self):
        base, scope, extraction, _ = candidate_fixture()
        raw = json.loads(extraction)
        raw['units'][0]['atoms'][0]['claim']['scope'] = {}
        self.assertTrue(c.extraction_bridge(base, scope, c.v1.encode(raw))['scope_audit']['local_preconditions_satisfied'])

    def test_missing_title_binding_raises_unchanged_validator(self):
        base, scope, extraction, _ = candidate_fixture()
        raw = json.loads(extraction)
        raw['units'][0]['atoms'][0]['evidence'][0]['title_scope'] = []
        with self.assertRaisesRegex(ValueError, 'scope_binding_incomplete'):
            c.extraction_bridge(base, scope, c.v1.encode(raw))

    def test_independent_review_has_no_scope_plan_tags_or_extractor_reasons(self):
        base, scope, extraction, _ = candidate_fixture()
        raw = json.loads(extraction)
        raw['units'][0]['reason'] = 'EXTRACTOR_VERDICT_SHOULD_NOT_LEAK'
        req = c.review_request(base, scope, c.v1.encode(raw))
        data = req['payload']['data']
        self.assertEqual(set(data), {'query', 'raw_draft', 'draft_units', 'sources', 'proposed_sources'})
        self.assertNotIn('EXTRACTOR_VERDICT_SHOULD_NOT_LEAK', c.v1.encode(req))
        self.assertEqual(req['payload']['instructions'], c.q.REVIEW_INSTRUCTIONS)

    def test_negative_independent_review_is_retained_not_overridden(self):
        base, scope, extraction, _ = candidate_fixture()
        req = c.review_request(base, scope, extraction)
        negative = c.v1.encode(review(req, verdict='contradicted'))
        result = c.combine(base, scope, extraction, negative)
        self.assertFalse(result['decision']['units'][0]['offline_candidate_only'])

    def test_joint_wrong_semantics_remain_known_limit(self):
        result = c.combine(*candidate_fixture(wrong=True))
        self.assertTrue(result['decision']['units'][0]['offline_candidate_only'])
        self.assertFalse(result['eligible_for_service'])
        self.assertIsNone(result['candidate_gfc'])

    def test_schema_prompt_query_tampering_rejected(self):
        base, _, _ = fixture()
        expected = c.scope_request(base)
        for field in ('instructions', 'response_schema', 'data'):
            changed = copy.deepcopy(expected)
            changed['payload'][field] = 'changed'
            changed['request_id'] = c.v1.digest(changed['payload'])
            with self.assertRaises(ValueError): c.envelope(changed, expected)

    def test_parent_binding_instruction_replaces_only_old_global_child_constraint(self):
        self.assertNotIn('Each quote must occur exactly ONCE in the entire selected source field;', c.EXTRACT_INSTRUCTIONS)
        self.assertIn('inside that parent', c.EXTRACT_INSTRUCTIONS)
        self.assertIn('Each quote must occur exactly ONCE in the entire selected source field;', c.q.EXTRACT_INSTRUCTIONS)

    def test_provider_projection_does_not_weaken_full_host_schema(self):
        base, scope, _, _ = candidate_fixture()
        request = c.extraction_request(base, scope)
        before = copy.deepcopy(request)
        env = c.envelope(request, c.extraction_request(base, scope))
        self.assertEqual(request, before)
        self.assertNotIn('maxItems', env['provider_body']['generationConfig']['responseJsonSchema']['properties']['units'])
        self.assertIn('maxItems', request['payload']['response_schema']['properties']['units'])
        self.assertEqual(env['provider_body_sha256'], c.v1.digest(env['provider_body']))

    def test_full_pipeline_order_and_15_second_spacing_in_mock(self):
        base, *responses = candidate_fixture()
        calls, pauses = [], []
        def call(slot, request, history):
            calls.append(slot)
            return responses[len(calls) - 1]
        result = r.pipeline(base, call, pauses.append)
        self.assertEqual(result['status'], 'PILOT_COMPLETE')
        self.assertEqual(calls, list(r.SLOTS))
        self.assertEqual(pauses, [15, 15])

    def test_first_failure_in_each_slot_stops_without_retry(self):
        base, *responses = candidate_fixture()
        for fail_at in range(3):
            calls = []
            def call(slot, request, history):
                calls.append(slot)
                if len(calls) - 1 == fail_at: raise ValueError('synthetic_failure')
                return responses[len(calls) - 1]
            result = r.pipeline(base, call, lambda _: None)
            self.assertEqual(result['status'], 'STOPPED_INCOMPLETE')
            self.assertEqual(len(calls), fail_at + 1)

    def test_pipeline_holds_missing_scope_without_extra_call(self):
        base, scope, _, _ = candidate_fixture()
        raw = json.loads(scope)
        for item in raw['dimensions'].values(): item.update(status='not_specified', quote=None)
        calls = []
        def call(slot, request, history):
            calls.append(slot)
            return c.v1.encode(raw)
        self.assertEqual(r.pipeline(base, call, lambda _: None)['status'], 'STOPPED_HELD')
        self.assertEqual(calls, ['scope'])

    def test_budget_fixed_order_requires_validation_and_caps_at_three(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = r.Budget(Path(directory))
            with self.assertRaises(ValueError): budget.reserve('extract', 'extract', {})
            budget.reserve('scope', 'extract', {})
            with self.assertRaises(ValueError): budget.reserve('extract', 'extract', {})
            budget.finish('scope', 'validated', 200)
            budget.reserve('extract', 'extract', {})
            budget.finish('extract', 'validated', 200)
            budget.reserve('semantic_review', 'semantic_review', {})
            budget.finish('semantic_review', 'validated', 200)
            with self.assertRaises(ValueError): budget.reserve('scope', 'extract', {})
            budget.db.close()

    def test_budget_cannot_resume_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            budget = r.Budget(Path(directory))
            budget.db.close()
            with self.assertRaisesRegex(ValueError, 'existing_ledger'): r.Budget(Path(directory))

    def test_invalid_approval_stops_before_key_loading(self):
        with patch.object(r.prior, 'load_key') as key:
            with self.assertRaises(ValueError): r.live('unused', 'invalid', 'continue')
            key.assert_not_called()

    def test_mock_http_receipt_saved_before_invalid_response_parsing(self):
        base, _, _ = fixture()
        req = c.scope_request(base)
        payload = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{}'}]}}]}
        class Received(io.BytesIO):
            status = 200
        class Opener:
            def open(self, request, timeout):
                self.request = request
                return Received(json.dumps(payload).encode())
        with tempfile.TemporaryDirectory() as directory:
            budget = r.Budget(Path(directory).resolve())
            with patch.object(r.urllib.request, 'build_opener', return_value=Opener()):
                with self.assertRaises(ValueError): r.post(budget, 'scope', req, base, {}, 'synthetic-test-key-not-real', {})
            self.assertTrue((Path(directory) / 'scope.receipt.json').exists())
            self.assertTrue((Path(directory) / 'scope.response.txt').exists())
            self.assertEqual(budget.rows()[0]['state'], 'failed')
            self.assertEqual(budget.rows()[0]['http_status'], 200)
            self.assertIsNone(r.prior.CAPABILITY['url'])
            budget.db.close()

    def test_quota_error_stops_and_is_not_a_retry_or_key_switch(self):
        base, _, _ = fixture()
        req = c.scope_request(base)
        class Opener:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 429, 'synthetic quota', {}, io.BytesIO(b'quota'))
        with tempfile.TemporaryDirectory() as directory:
            budget = r.Budget(Path(directory).resolve())
            with patch.object(r.urllib.request, 'build_opener', return_value=Opener()):
                with self.assertRaises(urllib.error.HTTPError): r.post(budget, 'scope', req, base, {}, 'synthetic-test-key-not-real', {})
            self.assertEqual(len(budget.rows()), 1)
            self.assertEqual(budget.rows()[0]['state'], 'http_error')
            self.assertEqual(budget.rows()[0]['http_status'], 429)
            self.assertIsNone(r.prior.CAPABILITY['url'])
            budget.db.close()


if __name__ == '__main__':
    unittest.main()
