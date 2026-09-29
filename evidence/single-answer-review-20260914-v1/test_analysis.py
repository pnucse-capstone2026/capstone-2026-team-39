import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from analyze import build_pairs, cluster_bootstrap, metrics, summarize, challenge_flags
from common import BASE, ROOT, blank_state, read

def pair(i, hg, jg, hs=None, js=None):
    return {'item_id':f'B{i:03d}','family_id':str(i),'condition_id':'C0','split':'holdout-core','challenge_type':None,
            'human':{'score':int(hg)*2 if hs is None else hs,'grounded_fully_correct':hg,'uncertain':False,'individual_judge_exposed':False},
            'judge':{'score':int(jg)*2 if js is None else js,'grounded_fully_correct':jg}}

class MetricsTests(unittest.TestCase):
    def test_perfect_both_classes(self):
        m=metrics([pair(1,False,False),pair(2,True,True)])
        for key in ('raw_agreement','cohen_kappa','balanced_accuracy','macro_f1','score_exact_agreement','quadratic_weighted_kappa'):self.assertEqual(m[key],1)
    def test_perfect_single_class_is_degenerate(self):
        m=metrics([pair(1,True,True),pair(2,True,True)])
        self.assertEqual(m['raw_agreement'],1);self.assertIsNone(m['cohen_kappa']);self.assertIsNone(m['balanced_accuracy']);self.assertIsNone(m['quadratic_weighted_kappa']);self.assertEqual(m['macro_f1'],.5)
    def test_all_wrong(self):
        m=metrics([pair(1,False,True),pair(2,True,False)])
        self.assertEqual(m['raw_agreement'],0);self.assertEqual(m['cohen_kappa'],-1);self.assertEqual(m['balanced_accuracy'],0)
    def test_hand_calculated_quarters(self):
        m=metrics([pair(1,False,False),pair(2,False,True),pair(3,True,False),pair(4,True,True)])
        self.assertEqual(m['raw_agreement'],.5);self.assertEqual(m['cohen_kappa'],0);self.assertEqual(m['macro_f1'],.5)
        self.assertEqual(m['gfc_confusion']['matrix'],[[1,1],[1,1]])
    def test_score_not_gfc(self):
        m=metrics([pair(1,False,False,1,2),pair(2,True,True,2,2)])
        self.assertEqual(m['raw_agreement'],1);self.assertEqual(m['score_exact_agreement'],.5)
    def test_empty(self):
        m=metrics([]);self.assertEqual(m['n'],0);self.assertIsNone(m['raw_agreement'])
    def test_bootstrap_reproducible(self):
        p=[pair(1,False,False),pair(2,True,True),pair(3,False,True)]
        self.assertEqual(cluster_bootstrap(p,100),cluster_bootstrap(p,100))
    def test_paired_family_stays_together(self):
        p=[pair(1,False,False),pair(1,False,True)]
        ci=cluster_bootstrap(p,50)
        self.assertEqual(ci['clusters'],1)
        self.assertEqual(ci['intervals']['raw_agreement']['low'],.5);self.assertEqual(ci['intervals']['raw_agreement']['high'],.5)
    def test_degenerate_bootstrap_counts(self):
        ci=cluster_bootstrap([pair(1,True,True)],40)
        self.assertEqual(ci['intervals']['cohen_kappa']['undefined_replicates'],40)
        self.assertIsNone(ci['intervals']['balanced_accuracy']['low'])
    def test_exposure_and_uncertainty_separated(self):
        p=[pair(1,True,True),pair(2,False,False),pair(3,False,True)]
        p[1]['human']['uncertain']=True;p[2]['human']['individual_judge_exposed']=True
        m=summarize(p,10)
        self.assertEqual(m['core_primary_unexposed']['n'],2)
        self.assertEqual(m['core_primary_excluding_uncertain']['n'],1)
        self.assertEqual(m['core_all_valid_exposure_sensitivity']['n'],3)
    def test_agrees_with_existing_pure_helpers(self):
        sys.path.insert(0,str(ROOT/'scripts'))
        path=ROOT/'scripts/analyze_judge_human_calibration.py';spec=importlib.util.spec_from_file_location('existing_calibration_math',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        p=[pair(1,False,False,0,1),pair(2,False,True,1,2),pair(3,True,True,2,2),pair(4,False,False,1,0)]
        m=metrics(p);hg=[x['human']['grounded_fully_correct'] for x in p];jg=[x['judge']['grounded_fully_correct'] for x in p]
        old=module._binary_comparison(actual=hg,predicted=jg)
        for key in ('raw_agreement','cohen_kappa','balanced_accuracy','macro_f1'):self.assertAlmostEqual(m[key],old[key])
        self.assertAlmostEqual(m['quadratic_weighted_kappa'],module._quadratic_weighted_kappa([x['human']['score'] for x in p],[x['judge']['score'] for x in p]))
    def test_challenge_no_invented_injection_judge(self):
        p=pair(1,False,False);p.update(split='holdout-challenge',challenge_type='prompt_injection');p['human']['injection_obedience']=False
        self.assertIsNone(challenge_flags([p])['injection_obedience']['judge_agreement'])

class BindingTests(unittest.TestCase):
    def test_real_blank_packet_is_pending_not_zero(self):
        p=read(BASE/'packet.json');m=read(BASE/'private-map.json');state=blank_state(p,m['packet_sha256']);state['reviewer_id']='SYNTHETIC-NOT-SAVED'
        with self.assertRaisesRegex(ValueError,'human_review_incomplete'):build_pairs(p,m,state)
    def test_fixture_only_all_labels_join_and_errors_excluded(self):
        p=read(BASE/'packet.json');m=read(BASE/'private-map.json');state=blank_state(p,m['packet_sha256']);state['reviewer_id']='SYNTHETIC-NOT-SAVED'
        # In-memory test fixture only. Never persisted or counted as human labels.
        for row in state['labels']:row.update(score=0,grounded_fully_correct=False,confirmed_at='synthetic',confirmed_revision=1)
        pairs,errors=build_pairs(p,m,state)
        self.assertEqual(len(pairs),60);self.assertEqual(len(errors),2)
        self.assertEqual(sum(r['split']=='holdout-core' for r in pairs),53)
        self.assertEqual(sum(r['split']=='holdout-challenge' for r in pairs),7)
        stats=summarize(pairs,10)
        self.assertEqual(stats['core_C0_unexposed']['n'],27)
        self.assertEqual(stats['core_C1_unexposed']['n'],26)
    def test_no_answer_pair_reuse(self):
        p=read(BASE/'packet.json');m=read(BASE/'private-map.json');state=blank_state(p,m['packet_sha256']);state['reviewer_id']='SYNTHETIC-NOT-SAVED'
        for row in state['labels']:row.update(score=0,grounded_fully_correct=False,confirmed_at='synthetic',confirmed_revision=1)
        m['records'][1]['item_id']=m['records'][0]['item_id']
        with self.assertRaisesRegex(ValueError,'duplicate_answer_mapping'):build_pairs(p,m,state)

class B023CorrectionTests(unittest.TestCase):
    def fixture(self):
        return {'reviewer_id':'SYNTHETIC-NOT-SAVED','labels':[
            {'item_id':'B022','score':0,'grounded_fully_correct':False,'confirmed_at':'synthetic'},
            {'item_id':'B023','score':2,'grounded_fully_correct':False,'confirmed_at':'synthetic','notes':'preserve'},
            {'item_id':'B024','score':2,'grounded_fully_correct':True,'confirmed_at':'synthetic'}]}
    def test_only_authorized_field_changes_and_input_is_preserved(self):
        from correct_b023 import apply_b023
        original=self.fixture();before=copy.deepcopy(original);corrected=apply_b023(original)
        expected=copy.deepcopy(before);expected['labels'][1]['grounded_fully_correct']=True
        self.assertEqual(original,before);self.assertEqual(corrected,expected)
    def test_missing_target_rejected(self):
        from correct_b023 import apply_b023
        state=self.fixture();state['labels'].pop(1)
        with self.assertRaisesRegex(ValueError,'B023_missing'):apply_b023(state)
    def test_duplicate_target_rejected(self):
        from correct_b023 import apply_b023
        state=self.fixture();state['labels'].append(copy.deepcopy(state['labels'][1]))
        with self.assertRaisesRegex(ValueError,'duplicate_label_id'):apply_b023(state)
    def test_unexpected_initial_values_rejected(self):
        from correct_b023 import apply_b023
        for score,gfc in [(1,False),(2,True),(2,0),(True,False)]:
            with self.subTest(score=score,gfc=gfc):
                state=self.fixture();state['labels'][1].update(score=score,grounded_fully_correct=gfc)
                with self.assertRaisesRegex(ValueError,'unexpected_B023_original'):apply_b023(state)
    def test_incomplete_review_rejected(self):
        from correct_b023 import apply_b023
        state=self.fixture();state['labels'][0]['confirmed_at']=None
        with self.assertRaisesRegex(ValueError,'original_review_incomplete'):apply_b023(state)
    def test_reapplying_correction_rejected(self):
        from correct_b023 import apply_b023
        corrected=apply_b023(self.fixture())
        with self.assertRaisesRegex(ValueError,'unexpected_B023_original'):apply_b023(corrected)

if __name__=='__main__':unittest.main()
