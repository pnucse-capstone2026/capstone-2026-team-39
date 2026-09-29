"""Recovery planning only: completed slots and consumed calls never reset."""
import argparse
import io
from pathlib import Path
import sys
import unittest

import inspect_stop as subject

sys.addaudithook(subject.base.offline)


class RecoveryPlanTests(unittest.TestCase):
    def slot(self, sid, role='generation'):
        return {'slot_id': sid, 'role': role, 'condition_id': 'SYNTHETIC', 'case_id': sid,
                'attempt_cap': 3 if role == 'generation' else 6}

    def test_sealed_slot_is_not_retried(self):
        result = subject.remaining_plan([self.slot('done'), self.slot('next')], {'done': 'a'*64}, [{'slot': 'done'}])
        self.assertEqual([s['slot_id'] for s in result['pending_slots']], ['next'])
        self.assertEqual(result['combined_previous_plus_remaining_ceiling'], 4)
        self.assertFalse(result['api_execution_authorized'])

    def test_failed_attempt_consumes_one_of_original_cap(self):
        result = subject.remaining_plan([self.slot('failed'), self.slot('judge', 'judge')], {}, [{'slot': 'failed'}])
        self.assertEqual(result['pending_slots'][0]['remaining_attempt_cap'], 2)
        self.assertEqual(result['remaining_attempt_ceiling'], 8)
        self.assertEqual(result['combined_previous_plus_remaining_ceiling'], 9)

    def test_exhausted_failed_slot_stops_planning(self):
        with self.assertRaisesRegex(ValueError, 'remaining_slot_budget_exhausted'):
            subject.remaining_plan([self.slot('failed')], {}, [{'slot': 'failed'}]*3)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RecoveryPlanTests))
    with (args.output/'unit-tests.log').open('x') as f:
        f.write(stream.getvalue())
    print(stream.getvalue(), end='')
    raise SystemExit(0 if result.wasSuccessful() else 1)
