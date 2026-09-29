"""Preparation-only checks; synthetic selection inputs, no model calls."""
import tempfile
from pathlib import Path
import unittest

import prepare


def synthetic_cases():
    return [dict(id=f'{category}-{bucket}-{number}', category=f'cat-{category}',
                 shadow_bucket=bucket, split='shadow-core', query='synthetic')
            for category in range(7) for bucket in ('simple', 'multi') for number in range(2)]


class PreparationTests(unittest.TestCase):
    def test_selection_has_one_per_stratum(self):
        selected = prepare.select_normal(synthetic_cases())
        self.assertEqual(len(selected), 14)
        self.assertEqual(len({(row['category'], row['shadow_bucket']) for row in selected}), 14)

    def test_selection_is_order_independent(self):
        cases = synthetic_cases()
        self.assertEqual(prepare.select_normal(cases), prepare.select_normal(list(reversed(cases))))

    def test_selection_ignores_question_and_scores(self):
        cases = synthetic_cases()
        before = [row['id'] for row in prepare.select_normal(cases)]
        for row in cases:
            row.update(query='changed text', judge_score=0, failure_type='arbitrary')
        self.assertEqual(before, [row['id'] for row in prepare.select_normal(cases)])

    def test_noncore_and_other_buckets_are_ignored(self):
        cases = synthetic_cases()
        expected = prepare.select_normal(cases)
        cases.extend([dict(id='excluded', split='shadow-other', category='extra', shadow_bucket='simple'),
                      dict(id='excluded2', split='shadow-core', category='extra', shadow_bucket='unanswerable')])
        self.assertEqual(prepare.select_normal(cases), expected)

    def test_missing_stratum_fails(self):
        with self.assertRaises(ValueError):
            prepare.select_normal(synthetic_cases()[2:])

    def test_existing_artifact_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix='pnu-pilot-unit-') as folder:
            path = Path(folder) / 'artifact.json'
            prepare.write_new(path, b'original')
            with self.assertRaises(FileExistsError):
                prepare.write_new(path, b'replacement')
            self.assertEqual(path.read_bytes(), b'original')

    def test_input_fingerprint_mismatch_fails(self):
        with tempfile.TemporaryDirectory(prefix='pnu-pilot-unit-') as folder:
            path = Path(folder) / 'input.json'
            prepare.write_new(path, b'data')
            self.assertEqual(prepare.checked_bytes(path, prepare.digest(b'data')), b'data')
            with self.assertRaises(ValueError):
                prepare.checked_bytes(path, '0' * 64)

    def test_identity_detects_content_and_path_changes(self):
        with tempfile.TemporaryDirectory(prefix='pnu-pilot-unit-') as folder:
            root = Path(folder)
            prepare.write_new(root / 'a.py', b'one')
            before = prepare.code_identity(root)
            self.assertEqual(before, prepare.code_identity(root))
            prepare.write_new(root / 'b.py', b'one')
            self.assertNotEqual(before['snapshot_sha256'], prepare.code_identity(root)['snapshot_sha256'])

    def test_canonical_digest_ignores_mapping_insertion_order(self):
        self.assertEqual(prepare.digest(prepare.canonical({'a': 1, 'b': 2})),
                         prepare.digest(prepare.canonical({'b': 2, 'a': 1})))


if __name__ == '__main__':
    unittest.main()
