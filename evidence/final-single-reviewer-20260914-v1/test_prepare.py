import copy
import json
from pathlib import Path
import tempfile
import unittest

import prepare as p


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.cases = [{'id': 'synthetic-a', 'role': 'pnu-researcher', 'query': 'SYNTHETIC QUESTION', 'required_claims': [{'text': 'SYNTHETIC GOLD'}]},
                      {'id': 'synthetic-b', 'role': 'pnu-student', 'query': 'SECOND SYNTHETIC'}]
        self.checks = {'source_verified': True, 'gold_verified': True, 'answerability_verified': True, 'label_verified': True}
        data = {'reviewer_id': 'synthetic-human', 'reviews': [
            {'case_id': 'synthetic-a', 'checks': {**self.checks, 'label_verified': False}, 'decision': 'REVISE', 'notes': '질문자는 대학원생임.'},
            {'case_id': 'synthetic-b', 'checks': dict(self.checks), 'decision': 'PASS', 'notes': ''}]}
        policy = {'cases': {c['id']: {'checks': {k: {'status': 'required'} for k in self.checks}} for c in self.cases}}
        self.export = {'revision': 181, 'data': data, 'sha256': p.digest(p.canonical(data)), 'check_policy': policy, 'check_policy_sha256': p.digest(p.canonical(policy))}
        self.proposal = {'proposed_changes': [{'case_id': 'synthetic-a', 'current_role': 'pnu-researcher', 'proposed_role': 'pnu-student', 'human_reported_subject_status': '대학원생', 'role_change': True}]}

    def reseal(self):
        self.export['sha256'] = p.digest(p.canonical(self.export['data']))

    def test_only_approved_metadata_changes_original_retained(self):
        before = copy.deepcopy((self.cases, self.export))
        cases, dispositions = p.corrected_cases(self.cases, self.export, self.proposal)
        self.assertEqual(cases[0]['role'], 'pnu-student')
        self.assertEqual(cases[0]['query'], self.cases[0]['query'])
        self.assertEqual(cases[0]['required_claims'], self.cases[0]['required_claims'])
        self.assertEqual(cases[1], self.cases[1])
        self.assertEqual((self.cases, self.export), before)
        self.assertEqual(dispositions[0]['original_decision'], 'REVISE')

    def test_no_unreviewed_source_or_gold(self):
        for key in ('source_verified', 'gold_verified', 'answerability_verified'):
            with self.subTest(key=key):
                self.export['data']['reviews'][0]['checks'][key] = False
                self.reseal()
                with self.assertRaisesRegex(ValueError, 'unconfirmed'):
                    p.corrected_cases(self.cases, self.export, self.proposal)
                self.export['data']['reviews'][0]['checks'][key] = True
                self.reseal()

    def test_unapproved_new_status_and_role_rejected(self):
        for key, value in [('human_reported_subject_status', '졸업생'), ('proposed_role', 'new-custom-role'), ('current_role', 'pnu-staff')]:
            proposal = copy.deepcopy(self.proposal)
            proposal['proposed_changes'][0][key] = value
            with self.assertRaises(ValueError):
                p.corrected_cases(self.cases, self.export, proposal)

    def test_unresolved_revise_or_pending_rejected(self):
        self.export['data']['reviews'][1]['decision'] = 'PENDING'
        self.reseal()
        with self.assertRaises(ValueError):
            p.corrected_cases(self.cases, self.export, self.proposal)
        with self.assertRaises(ValueError):
            p.corrected_cases(self.cases, self.export, {'proposed_changes': []})

    def test_hash_tampering_rejected(self):
        self.export['data']['reviews'][1]['notes'] = 'changed after export'
        with self.assertRaisesRegex(ValueError, 'review_hash'):
            p.corrected_cases(self.cases, self.export, self.proposal)

    def test_publish_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'test.json'
            p.publish(path, b'original')
            with self.assertRaises(FileExistsError):
                p.publish(path, b'replacement')
            self.assertEqual(path.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
