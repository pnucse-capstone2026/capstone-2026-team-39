import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

import runner as r


def synthetic_cases():
    return [{'id': f'synthetic-{i}', 'split': 'holdout-core' if i < 27 else 'holdout-challenge'} for i in range(36)]


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / 'run'
        self.slots = r.schedule(synthetic_cases(), runs=1)
        self.plan = {'slots': self.slots, 'provider_attempt_cap': 126, 'daily_soft_cap': 450,
            'minimum_inter_call_seconds': 0, 'prior_usage': {'pacific_day': '1900-01-01', 'known_project_today': 0}}
        self.ledger = r.Ledger(self.root, self.plan, create=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_schedule_one_run_63_answers_and_63_judgments(self):
        self.assertEqual(len(self.slots), 126)
        generation = self.slots[:63]
        self.assertTrue(all(s['phase'] == 'generation' for s in generation))
        self.assertTrue(all(s['phase'] == 'judge' for s in self.slots[63:]))
        self.assertEqual(len([s for s in generation if s['condition_id'] == 'c0']), 27)
        self.assertEqual(len([s for s in generation if s['condition_id'] == 'c1']), 36)
        for i in range(0, 54, 2):
            self.assertEqual(generation[i]['case_id'], generation[i+1]['case_id'])
            self.assertNotEqual(generation[i]['condition_id'], generation[i+1]['condition_id'])
        self.assertEqual(len(r.schedule(synthetic_cases(), 3)), 378)

    def test_no_dispatch_without_active_slot_and_wrong_model(self):
        with self.assertRaises(ValueError): self.ledger.reserve('c0', r.GEN_MODEL, b'{}')
        self.ledger.begin(self.slots[0])
        with self.assertRaises(ValueError): self.ledger.reserve('judge', r.JUDGE_MODEL, b'{}')
        with self.assertRaises(ValueError): self.ledger.reserve(self.slots[0]['condition_id'], 'other-model', b'{}')

    def test_reservation_is_durable_and_never_replayed_after_crash(self):
        slot = self.slots[0]; self.ledger.begin(slot)
        attempt = self.ledger.reserve(slot['condition_id'], r.GEN_MODEL, b'{"synthetic":true}')
        reopened = r.Ledger(self.root, self.plan)
        with self.assertRaises(ValueError): reopened.begin(slot)
        with self.assertRaises(ValueError): reopened.reserve(slot['condition_id'], r.GEN_MODEL, b'{}')
        with self.assertRaises(ValueError): reopened.seal(slot, 'not-resolved')
        reopened.finish_attempt(attempt, 200, 'received')
        with self.assertRaises(ValueError): reopened.reserve(slot['condition_id'], r.GEN_MODEL, b'{}')
        reopened.seal(slot, 'synthetic-sha')
        reopened.begin(self.slots[1])

    def test_out_of_order_and_plan_changes_rejected(self):
        with self.assertRaises(ValueError): self.ledger.begin(self.slots[1])
        altered = {**self.plan, 'provider_attempt_cap': 999}
        with self.assertRaises(ValueError): r.Ledger(self.root, altered)

    def test_daily_limit_before_provider(self):
        self.ledger.plan['prior_usage'] = {'pacific_day': r.datetime.now(r.PACIFIC).date().isoformat(), 'known_project_today': 450}
        self.ledger.begin(self.slots[0])
        with self.assertRaisesRegex(ValueError, 'daily_soft_cap'):
            self.ledger.reserve(self.slots[0]['condition_id'], r.GEN_MODEL, b'{}')

    def test_meter_rejects_wrong_url_without_network(self):
        meter = r.Metered(self.ledger, self.slots[0]['condition_id'])
        with patch.object(meter, 'original') as opener:
            with self.assertRaises(ValueError): meter.open(urllib.request.Request('https://example.com', b'{}'))
            opener.assert_not_called()

    def test_meter_reserves_before_call_and_buffers_response(self):
        slot = self.slots[0]; self.ledger.begin(slot)
        meter = r.Metered(self.ledger, slot['condition_id'])
        def opener(*args, **kwargs):
            with self.ledger.db() as db:
                self.assertEqual(db.execute('SELECT state FROM attempts').fetchone()[0], 'reserved')
            result = io.BytesIO(b'{"synthetic":true}'); result.status = 200
            return result
        with patch.object(r, 'verify'), patch.object(meter, 'original', side_effect=opener):
            result = meter.open(urllib.request.Request(f'https://generativelanguage.googleapis.com/v1beta/models/{r.GEN_MODEL}:generateContent', b'{}'))
        self.assertEqual(result.read(), b'{"synthetic":true}')
        with self.ledger.db() as db:
            self.assertEqual(db.execute('SELECT state FROM attempts').fetchone()[0], 'received')

    def test_timeout_retains_uncertain_reservation(self):
        slot = self.slots[0]; self.ledger.begin(slot)
        meter = r.Metered(self.ledger, slot['condition_id'])
        with patch.object(r, 'verify'), patch.object(meter, 'original', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                meter.open(urllib.request.Request(f'https://generativelanguage.googleapis.com/v1beta/models/{r.GEN_MODEL}:generateContent', b'{}'))
        with self.ledger.db() as db:
            self.assertEqual(db.execute('SELECT state FROM attempts').fetchone()[0], 'reserved')

    def test_append_preserves_both_records(self):
        path = self.root / 'synthetic.jsonl'
        r.append(path, {'order': 1}); first = path.read_bytes()
        r.append(path, {'order': 2})
        self.assertTrue(path.read_bytes().startswith(first))
        self.assertEqual(len(path.read_bytes().splitlines()), 2)

    def test_frozen_judge_record_signature_and_error_preservation(self):
        # Existing synthetic Judge fixture, not actual holdout or user labels.
        _, _, judge, artifacts = r.modules()
        case = {'id': 'synthetic-q', 'query': '합성 질문'}
        answer = artifacts.build_answer_identity({'schema_version': artifacts.ANSWER_SCHEMA_VERSION, 'record_type': 'answer',
            'experiment_id': 'SYNTHETIC-final', 'condition_id': 'c1', 'generation_run_id': 'run1',
            'case_id': case['id'], 'case_sha256': artifacts.sha256_json(case), 'answer': '합성 답변'})
        ji = judge.build_judge_input(case, answer)
        record = judge.build_judgment_record(answer=answer, case=case, judge_run_id='r1',
            judge_config=judge.build_judge_config(model=r.JUDGE_MODEL, max_output_tokens=1600),
            judge_input=ji, rendered_prompt=judge.render_judge_prompt(ji), judge={'score': None},
            raw_judge_response=None, attempts=[], error='SYNTHETIC_PARSE_ERROR')
        self.assertIsNone(record['judge']['score'])
        self.assertEqual(record['error'], 'SYNTHETIC_PARSE_ERROR')


if __name__ == '__main__': unittest.main()
