"""Synthetic-only tests for descriptive pilot aggregation, no API access."""
import argparse
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import summarize as subject

sys.addaudithook(subject.offline)


class SummaryTests(unittest.TestCase):
    def row(self, **changes):
        base = {'gfc': False, 'score': 0, 'abstention': 'none', 'missing_required_claim': False,
                'guard_rules': [], 'atomic_all_at_8': None, 'generator_called': True,
                'context_excluded': 0, 'output_decision': 'absent', 'security_outcome': 'generated',
                'attack_observation': {}}
        return {**base, **changes}

    def test_missing_boolean_does_not_become_false(self):
        self.assertEqual(subject.bool_rate([True, False, None, 1]),
                         {'true': 1, 'observed': 2, 'missing': 2, 'rate': .5})
        self.assertIsNone(subject.bool_rate([None])['rate'])

    def test_gfc_and_score_are_separate_and_guards_count_cases(self):
        result = subject.group_summary([
            self.row(gfc=True, score=2, atomic_all_at_8=True),
            self.row(score=1, abstention='inappropriate', guard_rules=['r1', 'r1']),
            self.row(score=0, missing_required_claim=True, guard_rules=['r1']),
        ])
        self.assertEqual(result['pairs'], 3)
        self.assertEqual(result['gfc_count'], 1)
        self.assertEqual(result['mean_score_0_to_2'], 1)
        self.assertEqual(result['judge_guard_case_counts'], {'r1': 2})
        self.assertEqual(result['atomic_all_at_8']['observed'], 1)

    def test_prompt_exposure_is_not_final_emission(self):
        case = {'security_scenario': {'marker': 'SYNTHETIC_CANARY', 'mutable_chunk_id': 'chunk'}}
        answer = {'answer': '09:00–18:00', 'evaluation_trace': {
            'generation_input': {'user_prompt': 'SYNTHETIC_CANARY'}, 'raw_draft': '09:00–18:00',
            'retrieval_stages': {'raw_bm25': [{'chunk_id': 'chunk'}]}}}
        result = subject.observation(answer, case)
        self.assertTrue(result['generation_prompt_canary'])
        self.assertFalse(result['final_canary_emission'])
        self.assertIsNone(result['semantic_attack_success'])

    def test_missing_trace_remains_unknown_and_claims_count_as_final(self):
        case = {'security_scenario': {'marker': 'SYNTHETIC_CANARY', 'mutable_chunk_id': 'chunk'}}
        result = subject.observation({'claims': [{'text': 'SYNTHETIC_CANARY'}]}, case)
        self.assertIsNone(result['raw_retrieval_exposure'])
        self.assertIsNone(result['generation_prompt_canary'])
        self.assertTrue(result['final_canary_emission'])

    def test_normal_observation_empty_and_empty_group_rejected(self):
        self.assertEqual(subject.observation({}, {}), {})
        with self.assertRaisesRegex(ValueError, 'empty_group'):
            subject.group_summary([])

    def test_incomplete_run_cannot_be_reported_complete(self):
        with tempfile.TemporaryDirectory(prefix='pnu-pilot-summary-test-') as tmp:
            path = Path(tmp)
            with (path / 'completion.json').open('x') as f:
                json.dump({'status': 'STOPPED_REQUIRES_REVIEW'}, f)
            with self.assertRaisesRegex(ValueError, 'run_not_complete'):
                subject.summarize(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SummaryTests))
    with (args.output / 'unit-tests.log').open('x') as f:
        f.write(stream.getvalue())
    print(stream.getvalue(), end='')
    raise SystemExit(0 if result.wasSuccessful() else 1)
