import copy
import json
import re
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
import uuid
from common import BASE, blank_state, canonical, read, sha
from server import Store

def fixture():
    items = [{'item_id': 'P01', 'phase': 'practice', 'applicable': {'correct_abstention': False, 'injection_obedience': False}},
             {'item_id': 'B001', 'phase': 'main', 'applicable': {'correct_abstention': False, 'injection_obedience': False}},
             {'item_id': 'B002', 'phase': 'main', 'applicable': {'correct_abstention': True, 'injection_obedience': False}}]
    return {'items': items}

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.packet = fixture(); self.pin = sha(canonical(self.packet))
        self.store = Store(Path(self.temp.name).resolve() / 'state', self.packet, self.pin, True)
    def tearDown(self):
        self.temp.cleanup()
    def request(self, change=None, confirm=None):
        r = self.store.current(); data = copy.deepcopy(r['data'])
        if change: change(data)
        return {'data': data, 'base_revision': r['revision'], 'mutation': str(uuid.uuid4()), 'confirm_item': confirm}
    def valid(self, data, index=0):
        data['reviewer_id'] = 'SYNTHETIC-QA-ONLY'
        data['labels'][index].update(score=2, grounded_fully_correct=True)
    def practice(self):
        return self.store.save(self.request(self.valid, 'P01'))
    def test_blank_no_actual_labels(self):
        self.assertTrue(all(r['score'] is None and r['confirmed_at'] is None for r in self.store.current()['data']['labels']))
    def test_save_restart_readback(self):
        r = self.store.save(self.request(lambda d: d['labels'][0].update(notes='합성 입력 테스트')))
        self.assertEqual(Store(self.store.root, self.packet, self.pin).current(), r)
        self.assertEqual(sha(Path(r['backup_path']).read_bytes()), r['sha256'])
    def test_stale_conflict(self):
        req = self.request(); self.store.save(self.request())
        with self.assertRaisesRegex(ValueError, 'revision_conflict'): self.store.save(req)
    def test_idempotent_retry(self):
        req = self.request(self.valid, 'P01'); r = self.store.save(req)
        self.assertEqual(r, self.store.save(req)); self.assertEqual(r['revision'], 1)
    def test_mutation_reuse_rejected(self):
        req = self.request(); self.store.save(req); req['data']['labels'][0]['notes'] = 'different'
        with self.assertRaisesRegex(ValueError, 'mutation_reused'): self.store.save(req)
    def test_first_label_locked(self):
        self.practice()
        with self.assertRaisesRegex(ValueError, 'first_confirmed_label_locked'):
            self.store.save(self.request(lambda d: d['labels'][0].update(score=1, grounded_fully_correct=False)))
    def test_identity_locked(self):
        self.practice()
        with self.assertRaisesRegex(ValueError, 'reviewer_locked'): self.store.save(self.request(lambda d: d.update(reviewer_id='FAKE')))
    def test_confirmation_requires_human_score(self):
        with self.assertRaises(ValueError): self.store.save(self.request(confirm='P01'))
    def test_practice_required(self):
        with self.assertRaisesRegex(ValueError, 'practice_first'): self.store.save(self.request(lambda d: self.valid(d, 1), 'B001'))
    def test_gfc_requires_two(self):
        with self.assertRaisesRegex(ValueError, 'GFC_requires'): self.store.save(self.request(lambda d: d['labels'][0].update(score=1, grounded_fully_correct=True)))
    def test_bool_score_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid_score'): self.store.save(self.request(lambda d: d['labels'][0].update(score=True)))
    def test_uncertain_needs_reason(self):
        req = self.request(self.valid, 'P01'); req['data']['labels'][0]['uncertain'] = True
        with self.assertRaisesRegex(ValueError, 'uncertain_reason'): self.store.save(req)
    def test_na_and_applicable_challenge(self):
        with self.assertRaisesRegex(ValueError, 'not_applicable'): self.store.save(self.request(lambda d: d['labels'][0].update(correct_abstention=True)))
        self.practice()
        with self.assertRaisesRegex(ValueError, 'correct_abstention_required'): self.store.save(self.request(lambda d: self.valid(d, 2), 'B002'))
    def test_exposure_cannot_erase(self):
        self.store.save(self.request(lambda d: d['labels'][0].update(individual_judge_exposed=True)))
        with self.assertRaisesRegex(ValueError, 'exposure_cannot'): self.store.save(self.request(lambda d: d['labels'][0].update(individual_judge_exposed=False)))
    def test_fake_confirmation_rejected(self):
        with self.assertRaisesRegex(ValueError, 'server_only'): self.store.save(self.request(lambda d: d['labels'][0].update(confirmed_at='made-up')))
    def test_export_is_durable_and_bound(self):
        e = self.store.export(); raw = (self.store.root / 'exports' / e['name']).read_bytes()
        self.assertEqual(sha(raw), e['sha256']); self.assertEqual(json.loads(raw)['packet_sha256'], self.pin)
    def test_committed_before_backup_failure_recovers(self):
        req = self.request(self.valid, 'P01')
        with patch.object(self.store, 'backup', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError): self.store.save(req)
        reopened = Store(self.store.root, self.packet, self.pin)
        self.assertEqual(reopened.current()['revision'], 1)
        self.assertEqual(reopened.current(), reopened.save(req))
    def test_tampered_history_detected(self):
        with self.store.connect() as db: db.execute("UPDATE revisions SET sha='bad' WHERE revision=0")
        with self.assertRaisesRegex(ValueError, 'history_chain'): self.store.verify()

class RealPacketReadOnlyTests(unittest.TestCase):
    def test_browser_citation_rendering_regressions(self):
        result = subprocess.run(['node','--test',str(Path(__file__).with_name('test_citations.cjs'))],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
    def test_citation_display_has_same_answer_content(self):
        from common import EVAL, rows
        def normalize(text):
            return re.sub(r'\s+', '', re.sub(r'(?m)^\s*[-*•]\s*', '', re.sub(r'\[\d+\]', '', text)))
        count=0
        for path in (EVAL/'live-v3/answers').glob('*run1.answers.jsonl'):
            for answer in rows(path):
                self.assertEqual(normalize(answer['answer']),normalize(answer.get('cited_answer') or answer['answer']))
                count+=1
        self.assertEqual(count,62)
    def test_public_packet_allowlist_and_counts(self):
        packet = read(BASE / 'packet.json'); private = read(BASE / 'private-map.json')
        self.assertEqual(sha((BASE / 'packet.json').read_bytes()), private['packet_sha256'])
        self.assertEqual(len(packet['items']), 70)
        self.assertEqual(sum(r['judge'] is not None for r in private['records']), 60)
        def keys(value):
            if isinstance(value, dict):
                for k, v in value.items(): yield k; yield from keys(v)
            elif isinstance(value, list):
                for v in value: yield from keys(v)
        self.assertFalse({'condition_id', 'judge', 'model', 'answer_id', 'generation_run_id', 'score', 'grounded_fully_correct'} & set(keys(packet)))
        self.assertTrue(all(i['contexts_exact_prompt_blocks'] for i in packet['items'] if i['phase']=='main'))
        self.assertTrue(all(i['required'][0]['description'] for i in packet['items'] if i['phase']=='practice'))

if __name__ == '__main__':
    unittest.main()
