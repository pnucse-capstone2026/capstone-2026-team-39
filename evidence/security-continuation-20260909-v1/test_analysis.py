"""Offline merge checks, including the completed three-pair fake-provider run."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import analyze_completed as subject
s = subject.s
sys.addaudithook(subject.live.base.audit)
sys.addaudithook(s.offline)


class MergeTests(unittest.TestCase):
    def test_disjoint_complete_union(self):
        self.assertEqual(subject.merged_seals({'old':'a'},{'new':'b'},['old','new']),{'old':'a','new':'b'})

    def test_overlap_even_same_hash_refused(self):
        with self.assertRaisesRegex(ValueError,'duplicate_sealed_slot'):
            subject.merged_seals({'old':'a'},{'old':'a'},['old'])

    def test_missing_extra_or_null_slots_refused(self):
        for parent, continued, expected in [({'old':'a'},{},['old','new']),
            ({'old':'a'},{'extra':'b'},['old']), ({'old':'a'},{'new':None},['old','new'])]:
            with self.assertRaisesRegex(ValueError,'incomplete_combined_slots'):
                subject.merged_seals(parent,continued,expected)

    def test_stopped_and_mock_never_reported_as_live(self):
        with tempfile.TemporaryDirectory(prefix='pnu-cont-analysis-') as temporary:
            root = Path(temporary).resolve()
            for i,status in enumerate(('STOPPED_REQUIRES_REVIEW','MOCK_CONTINUATION_COMPLETE')):
                case = root/str(i)
                case.mkdir()
                subject.live.bridge.write_new(case/'completion.json',s.json.dumps({'status':status,'execution_mode':'mock'}).encode())
                with self.assertRaisesRegex(ValueError,'continuation_not_complete_or_wrong_mode'):
                    subject.complete_manifest(case,'live')

    def test_artifact_mismatch_refused(self):
        with tempfile.TemporaryDirectory(prefix='pnu-cont-tamper-') as temporary:
            root = Path(temporary).resolve()
            for name,data in [('run.lock',b''),('daily-budget.lock',b''),('dummy',b'changed')]:
                subject.live.bridge.write_new(root/name,data)
            subject.live.bridge.write_new(root/'completion.json',json.dumps({'status':'MOCK_CONTINUATION_COMPLETE',
                'execution_mode':'mock','artifact_sha256':{'dummy':'0'*64}}).encode())
            with self.assertRaisesRegex(ValueError,'completion_artifact_mismatch'):
                subject.complete_manifest(root,'mock')

    def test_mock_pairs_binding_and_no_llm_claim(self):
        root = subject.live.ROOT/'processed/eval/preflight-20260909/security-continuation-v1/local-v1/run'
        result = subject.completed_rows(root,mode='mock')
        self.assertEqual(len(result['rows']),3)
        self.assertEqual(len(result['sealed']),6)
        self.assertEqual(len(result['attempts']),5)
        self.assertEqual(result['workers'],3)
        self.assertEqual(result['completion']['external_llm_calls'],0)
        self.assertEqual(len(result['verified_pairs']),3)
        abstention = next(r for r in result['rows'] if r['case_id'] == 'secpilot-a10-attack')
        self.assertFalse(abstention['generator_called'])
        self.assertIsNone(abstention['attack_observation']['raw_draft_canary'])
        self.assertFalse(abstention['attack_observation']['final_canary_emission'])


if __name__ == '__main__':
    output = Path(sys.argv[sys.argv.index('--output')+1])
    output.mkdir(parents=True,exist_ok=False)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MergeTests))
    subject.live.bridge.write_new(output/'unit-tests.log',stream.getvalue().encode())
    subject.live.bridge.write_new(output/'test-counts.json',json.dumps({'total':result.testsRun,'skip':len(result.skipped),
        'failures':len(result.failures),'errors':len(result.errors)}).encode())
    print(stream.getvalue(),end='')
    raise SystemExit(0 if result.wasSuccessful() else 1)
