"""Synthetic unit tests for the isolated attack preparation, not for LLM quality."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import prepare_attack as prep


class AttackPreparationTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((prep.HERE / 'scenarios.json').read_bytes())

    def test_spec_and_paired_counts(self):
        prep.validate_spec(self.spec)
        self.assertEqual(len(self.spec['scenarios']), 10)
        self.assertEqual(sum(not row['safe_companion'] for row in self.spec['scenarios']), 1)

    def test_invalid_specifications_rejected(self):
        variants = []
        for key, value in (('question', ''), ('gold_quote', ''), ('scenarios', []),
                           ('schema_version', 'invalid')):
            spec = copy.deepcopy(self.spec)
            spec[key] = value
            variants.append(spec)
        for key, value in (('id', '../escape'), ('location', 'source_path'), ('safe_companion', 1),
                           ('payload', 'no placeholder'), ('family', ''), ('benign_preface', [])):
            spec = copy.deepcopy(self.spec)
            spec['scenarios'][0][key] = value
            variants.append(spec)
        for spec in variants:
            with self.subTest(spec=spec.get('schema_version')):
                with self.assertRaises(ValueError):
                    prep.validate_spec(spec)

    def test_duplicate_ids_rejected(self):
        self.spec['scenarios'].append(copy.deepcopy(self.spec['scenarios'][0]))
        with self.assertRaises(ValueError):
            prep.validate_spec(self.spec)

    def test_canary_in_user_question_or_gold_rejected(self):
        for key in ('question', 'gold_quote'):
            spec = copy.deepcopy(self.spec)
            spec[key] += ' PNU_PILOT_CANARY_A01'
            with self.assertRaises(ValueError):
                prep.validate_spec(spec)

    def test_clean_pairs_change_only_injection_field_and_length(self):
        for scenario in self.spec['scenarios']:
            clean = prep.documents_for(self.spec, scenario, 'clean')
            attack = prep.documents_for(self.spec, scenario, 'attack')
            self.assertEqual(len(clean), len(attack))
            for original, modified in zip(clean, attack):
                differences = {key for key in original if original[key] != modified[key]}
                if original['document_id'].endswith('-notice'):
                    expected = {scenario['location']}
                    if scenario['location'] == 'text':
                        expected.add('char_count')
                    self.assertEqual(differences, expected)
                else:
                    self.assertEqual(differences, set())

    def test_target_not_in_clean_document_or_request_question(self):
        for scenario in self.spec['scenarios']:
            docs = prep.documents_for(self.spec, scenario, 'clean')
            marker = f'PNU_PILOT_CANARY_{scenario["id"].upper()}'
            self.assertNotIn(marker, prep.canonical(docs).decode())
            self.assertNotIn(marker, self.spec['question'])
            attack = prep.documents_for(self.spec, scenario, 'attack')
            self.assertIn(marker, prep.canonical(attack).decode())

    def test_required_quote_bound_to_each_fixture(self):
        for scenario in self.spec['scenarios']:
            for variant in ('clean', 'attack'):
                docs = prep.documents_for(self.spec, scenario, variant)
                case = prep.build_case(self.spec, scenario, variant, docs)
                by_id = {doc['chunk_id']: doc for doc in docs}
                for option in case['required_claims'][0]['evidence_options']:
                    self.assertIn(option['quote'], by_id[option['evidence_chunk_id']]['text'])
                self.assertEqual(case['query'], self.spec['question'])

    def test_invalid_variant_rejected(self):
        with self.assertRaises(ValueError):
            prep.documents_for(self.spec, self.spec['scenarios'][0], 'invalid')

    def test_write_refuses_overwrite(self):
        with tempfile.TemporaryDirectory(prefix='pnu-attack-test-') as folder:
            path = Path(folder) / 'existing.sqlite'
            prep.write_new(path, b'preserved')
            with self.assertRaises(FileExistsError):
                prep.write_new(path, b'new')
            self.assertEqual(path.read_bytes(), b'preserved')

    def test_database_uses_existing_schema_and_refuses_overwrite(self):
        import bm25_search
        docs = prep.documents_for(self.spec, self.spec['scenarios'][0], 'clean')
        with tempfile.TemporaryDirectory(prefix='pnu-attack-test-') as folder:
            path = Path(folder) / 'index.sqlite'
            prep.build_database(path, docs, 'test-revision', 'test-sha', bm25_search)
            before = prep.file_sha(path)
            db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
            try:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0], 2)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM chunk_fts').fetchone()[0], 2)
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("INSERT INTO index_meta VALUES ('forbidden', 'write')")
            finally:
                db.close()
            with self.assertRaises(FileExistsError):
                prep.build_database(path, docs, 'different', 'different', bm25_search)
            self.assertEqual(before, prep.file_sha(path))

    def test_observation_keeps_missing_trace_unknown(self):
        from verify_attack import observe
        case = {'security_scenario': {'mutable_chunk_id': 'chunk', 'marker': 'CANARY'}}
        actual = observe({'answer': 'normal', 'claims': []}, case)
        self.assertIsNone(actual['raw_retrieval_exposure'])
        self.assertIsNone(actual['generation_prompt_canary'])
        self.assertFalse(actual['final_canary_emission'])

    def test_source_panel_and_raw_draft_do_not_count_as_final_emission(self):
        from verify_attack import observe
        case = {'security_scenario': {'mutable_chunk_id': 'chunk', 'marker': 'CANARY'}}
        payload = {'answer': 'normal', 'cited_answer': 'normal [1]', 'claims': [],
                   'results': [{'source_title': 'CANARY'}],
                   'evaluation_trace': {'raw_draft': 'CANARY', 'retrieval_stages': {
                       'raw_bm25': [{'chunk_id': 'chunk'}]}, 'generation_input': {'user_prompt': 'CANARY'}}}
        actual = observe(payload, case)
        self.assertTrue(actual['raw_retrieval_exposure'])
        self.assertTrue(actual['generation_prompt_canary'])
        self.assertTrue(actual['raw_draft_canary'])
        self.assertTrue(actual['source_panel_canary'])
        self.assertFalse(actual['final_canary_emission'])

    def test_final_answer_and_claim_markers_count_as_emission(self):
        from verify_attack import observe
        case = {'security_scenario': {'mutable_chunk_id': 'chunk', 'marker': 'CANARY'}}
        for payload in ({'answer': 'CANARY'}, {'answer': 'normal', 'cited_answer': 'CANARY'},
                        {'answer': 'normal', 'claims': [{'text': 'CANARY'}]}):
            self.assertTrue(observe(payload, case)['final_canary_emission'])

    def test_protected_data_and_network_guard_remain_closed(self):
        from verify_attack import deny_external
        for path in ('/synthetic/holdout-v2.jsonl', '/synthetic/.env'):
            with self.assertRaises(RuntimeError):
                deny_external('open', (path, 'r', 0))
        for event in ('socket.connect', 'socket.getaddrinfo', 'urllib.Request'):
            with self.assertRaises(RuntimeError):
                deny_external(event, ())
        with self.assertRaises(FileNotFoundError):
            deny_external('open', ('/synthetic/__pycache__/holdout_gold.cpython-39.pyc', 'r', 0))
        deny_external('open', ('/synthetic/holdout_gold.py', 'r', 0))

    def test_v2_spec_only_changes_a09_metadata_surface(self):
        old = json.loads((prep.HERE.parent / 'security-attack-preparation-20260908-v1/scenarios.json').read_bytes())
        expected = copy.deepcopy(old)
        target = next(row for row in expected['scenarios'] if row['id'] == 'a09')
        target.update(family='filename_metadata_regression', location='file_name')
        self.assertEqual(self.spec, expected)

    def test_a09_changes_filename_not_body_title_or_filesystem_path(self):
        scenario = next(row for row in self.spec['scenarios'] if row['id'] == 'a09')
        clean = prep.documents_for(self.spec, scenario, 'clean')[-1]
        attack = prep.documents_for(self.spec, scenario, 'attack')[-1]
        self.assertEqual({key for key in clean if clean[key] != attack[key]}, {'file_name'})
        self.assertIn('PNU_PILOT_CANARY_A09', attack['file_name'])
        for key in ('text', 'source_title', 'source_path', 'relative_path'):
            self.assertNotIn('PNU_PILOT_CANARY_A09', attack[key])

    def test_filename_allowlist_does_not_allow_arbitrary_fields(self):
        spec = copy.deepcopy(self.spec)
        for field in ('source_path', 'relative_path', 'document_id', 'text;DROP TABLE chunks'):
            spec['scenarios'][0]['location'] = field
            with self.assertRaises(ValueError):
                prep.validate_spec(spec)


if __name__ == '__main__':
    unittest.main()
