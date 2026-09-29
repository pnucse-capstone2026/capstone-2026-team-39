import copy
import unittest
import resume as m


class PauseResumeTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'slots': [{'slot_id': f'synthetic-{i}'} for i in range(378)]}
        self.slots = [{'id': f'synthetic-{i}', 'ordinal': i,
            'state': 'complete' if i < 43 else 'started' if i == 43 else 'pending',
            'artifact_sha': 'synthetic-sha' if i < 43 else None} for i in range(378)]
        self.attempts = [{'slot': f'synthetic-{i}', 'state': 'received', 'http_status': 200, 'model': m.r.GEN_MODEL} for i in range(15, 43)]
        self.active = ['synthetic-43']

    def test_only_proven_undispatched_pause_is_accepted(self):
        self.assertEqual(m.validate_state(self.slots, self.attempts, self.active, self.plan), 'synthetic-43')

    def test_even_one_active_slot_attempt_is_rejected(self):
        self.attempts[-1]['slot'] = 'synthetic-43'
        with self.assertRaises(ValueError): m.validate_state(self.slots, self.attempts, self.active, self.plan)

    def test_uncertain_transport_is_rejected(self):
        self.attempts[-1]['state'] = 'reserved'
        with self.assertRaises(ValueError): m.validate_state(self.slots, self.attempts, self.active, self.plan)

    def test_changed_completed_boundary_is_rejected(self):
        self.slots[42]['state'] = 'started'
        with self.assertRaises(ValueError): m.validate_state(self.slots, self.attempts, self.active, self.plan)

    def test_missing_or_multiple_active_markers_rejected(self):
        for active in ([], ['synthetic-43', 'synthetic-44']):
            with self.assertRaises(ValueError): m.validate_state(self.slots, self.attempts, active, self.plan)

    def test_pending_artifact_is_rejected(self):
        self.slots[43]['artifact_sha'] = 'unexpected'
        with self.assertRaises(ValueError): m.validate_state(self.slots, self.attempts, self.active, self.plan)


if __name__ == '__main__': unittest.main()
