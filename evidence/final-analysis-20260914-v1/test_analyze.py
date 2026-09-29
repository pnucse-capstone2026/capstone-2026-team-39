import unittest
import analyze as a


def row(case='q1', condition='c0', run=1, gfc=True, score=2, **extra):
    return {'case_id': case, 'condition': condition, 'split': 'holdout-core', 'run': f'run{run}',
        'group': f'{condition}-run{run}', 'gfc': gfc, 'score': score, 'service_error': False,
        'judge_error': False, 'failure_causes': [], 'guard_rules': [], **extra}


class AnalysisTests(unittest.TestCase):
    def test_operational_failure_in_denominator_not_fake_score(self):
        result = a.group_metrics([row(), row(gfc=False, score=None, service_error=True)])
        self.assertEqual(result['gfc_rate_all_planned'], .5)
        self.assertEqual(result['valid_output_gfc_rate'], 1)
        self.assertEqual(result['valid_judgment_score_mean'], 2)
        self.assertEqual(result['service_errors'], 1)
        self.assertEqual(result['valid_judgments'], 1)

    def test_unknown_judge_is_not_silently_zero(self):
        result = a.group_metrics([row(), row(gfc=None, score=None, judge_error=True)])
        self.assertIsNone(result['gfc_rate_all_planned'])
        self.assertEqual(result['gfc_rate_bounds_all_planned'], [.5, 1])
        self.assertEqual(result['judge_errors'], 1)

    def test_three_runs_are_within_question_repeats(self):
        rows = [row(case=q, condition=c, run=n, gfc=(q == 'q2' or c == 'c1'))
                for q in ('q1', 'q2') for c in ('c0', 'c1') for n in (1, 2, 3)]
        result = a.paired_metrics(rows, {'q1': 'f1', 'q2': 'f2'}, 3, iterations=100)
        self.assertEqual(result['question_count'], 2)
        self.assertEqual(result['family_count'], 2)
        self.assertEqual(result['delta_c1_minus_c0'], .5)
        self.assertEqual(result['majority_sensitivity']['c0_successes'], 1)
        self.assertEqual(result['majority_sensitivity']['c1_successes'], 2)

    def test_repeated_families_do_not_count_as_extra_clusters(self):
        rows = [row(case=q, condition=c, gfc=(c == 'c1')) for q in ('q1', 'q2') for c in ('c0', 'c1')]
        result = a.paired_metrics(rows, {'q1': 'shared', 'q2': 'shared'}, 1, iterations=30)
        self.assertEqual(result['family_count'], 1)
        self.assertEqual(result['family_bootstrap']['clusters'], 1)
        self.assertEqual(result['family_sign_flip']['nonzero_pair_count'], 1)

    def test_missing_repeat_rejected(self):
        with self.assertRaises(ValueError):
            a.paired_metrics([row(), row(condition='c1')], {'q1': 'f1'}, 3, iterations=10)

    def test_unknown_judge_prevents_definitive_paired_effect(self):
        result = a.paired_metrics([row(), row(condition='c1', gfc=None, score=None, judge_error=True)], {'q1': 'f1'}, 1)
        self.assertFalse(result['available'])
        self.assertEqual(result['unknown_outcomes'], 1)

    def test_challenge_not_mixed_into_core(self):
        result = a.paired_metrics([row(), row(condition='c1'), row(case='challenge', split='holdout-challenge')], {'q1': 'f1'}, 1, iterations=30)
        self.assertEqual(result['question_count'], 1)
        self.assertEqual(result['delta_c1_minus_c0'], 0)

    def test_all_errors_mean_is_unavailable(self):
        result = a.group_metrics([row(score=None, gfc=False, service_error=True)])
        self.assertEqual(result['gfc_rate_all_planned'], 0)
        self.assertIsNone(result['valid_judgment_score_mean'])

    def test_checkpoint_requires_every_selected_slot_not_provider_count(self):
        slots = [{'slot_id': str(i)} for i in range(378)]
        states = {str(i): {'state': 'complete' if i < 125 else 'pending'} for i in range(378)}
        self.assertFalse(a.checkpoint_ready({'slots': slots}, states, 1))
        states['125']['state'] = 'complete'
        self.assertTrue(a.checkpoint_ready({'slots': slots}, states, 1))
        self.assertFalse(a.checkpoint_ready({'slots': slots}, states, 3))

    def test_report_does_not_claim_human_calibration(self):
        payload = {'generation_repeats': 1, 'conditions': {'synthetic': a.group_metrics([row()])},
            'core_paired': {'available': False}, 'plan_sha256': 'synthetic'}
        report = a.render(payload)
        self.assertIn('1회차 중간 결과', report)
        self.assertIn('사람 답변 채점·일치도 검증은 미완료', report)
        self.assertIn('주지표는 GFC', report)


if __name__ == '__main__': unittest.main()
