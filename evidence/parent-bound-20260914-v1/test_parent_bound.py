import copy
from dataclasses import asdict
import unittest
from unittest.mock import patch

import parent_bound as p
from test_quote_adapter import request, extraction, chain, fact_wire, assertion
from test_adapter import wire
from test_contract import table_fixture


def ref(quote, source='s', field='text'):
    return {'source_id': source, 'field': field, 'quote': quote}


def fixture(body='수수료 앞; 확정 근거: 수수료 10원; 수수료 뒤'):
    return {'s': p.v1.c.Source('s', '제목', body)}


class ParentBoundTests(unittest.TestCase):
    def test_global_repetition_resolved_only_in_unique_parent_not_first_match(self):
        sources = fixture()
        child, parent = ref('수수료'), ref('확정 근거: 수수료 10원')
        with self.assertRaisesRegex(p.q.QuoteBindingError, 'ambiguous'):
            p.q.resolve_reference(child, sources)
        resolved = p.resolve_child(child, parent, sources)
        self.assertGreater(resolved['start'], sources['s'].text.find('수수료'))
        self.assertEqual(sources['s'].text[resolved['start']:resolved['end']], child['quote'])
        self.assertEqual(resolved['start'], sources['s'].text.find('수수료 10원'))

    def test_duplicate_or_missing_parent_never_disambiguated_by_child(self):
        for body, parent in [('근거 A; 근거 A', '근거 A'), ('근거 B', '근거 A')]:
            with self.assertRaises(p.q.QuoteBindingError):
                p.resolve_child(ref('A'), ref(parent), fixture(body))

    def test_repeated_or_overlapping_child_inside_parent_rejected(self):
        for body, child in [('유일 근거: 한도 한도', '한도'), ('유일 근거: aaa', 'aa')]:
            with self.assertRaisesRegex(p.q.QuoteBindingError, 'ambiguous'):
                p.resolve_child(ref(child), ref(body), fixture(body))

    def test_child_outside_parent_not_found_even_if_global_unique(self):
        with self.assertRaisesRegex(p.q.QuoteBindingError, 'not_found'):
            p.resolve_child(ref('외부'), ref('내부 근거'), fixture('외부 문구; 내부 근거'))

    def test_cross_source_ref_rejected_even_with_identical_text(self):
        sources = fixture('내부 근거')
        sources['other'] = p.v1.c.Source('other', '제목', '내부 근거')
        with self.assertRaisesRegex(p.q.QuoteBindingError, 'source_or_field'):
            p.resolve_child(ref('근거', source='other'), ref('내부 근거'), sources)

    def test_parent_and_child_must_both_be_body_fields(self):
        sources = fixture('제목')
        for child, parent in [(ref('제목',field='title'),ref('제목')),
                              (ref('제목'),ref('제목',field='title'))]:
            with self.assertRaisesRegex(p.q.QuoteBindingError, 'source_or_field'):
                p.resolve_child(child, parent, sources)

    def test_no_supplied_offsets_hashes_or_prevalidated_flags(self):
        sources = fixture('유일 근거')
        for name in ('start', 'end', 'source_sha256', 'validated'):
            for parent_extra in (True, False):
                child, parent = ref('근거'), ref('유일 근거')
                (parent if parent_extra else child)[name] = 0
                with self.assertRaises(ValueError):
                    p.resolve_child(child, parent, sources)

    def test_unknown_source_and_empty_quotes_rejected(self):
        for child,parent in [(ref('근거',source='missing'),ref('유일 근거',source='missing')),
                             (ref(''),ref('유일 근거')),(ref('근거'),ref(''))]:
            with self.assertRaises(ValueError):
                p.resolve_child(child,parent,fixture('유일 근거'))

    def test_unicode_crlf_and_exact_absolute_coordinates(self):
        body = '앞😀\r\n유일 근거: 10만 원\r\n뒤'
        result = p.resolve_child(ref('10만 원'),ref('유일 근거: 10만 원'),fixture(body))
        self.assertEqual(result['start'],body.index('10만 원'))
        self.assertEqual(result['end'],body.index('10만 원')+5)

    def test_no_whitespace_punctuation_or_unicode_normalization(self):
        for body,child in [('유일: 10만 원','10만원'),('유일: 가','\u1100\u1161'),('유일: 카드⁺','카드+')]:
            with self.assertRaisesRegex(p.q.QuoteBindingError,'not_found'):
                p.resolve_child(ref(child),ref(body),fixture(body))

    def test_unmodified_valid_fixture_matches_legacy_bridge(self):
        req,ex,_,_=chain()
        before_req,before_ex=copy.deepcopy(req),copy.deepcopy(ex)
        raw=p.v1.encode(ex)
        legacy,converted,parsed,audit=p.extraction_bridge(req,raw)
        old_legacy,old_converted,old_parsed=p.q.extraction_bridge(req,raw)
        self.assertEqual((legacy,converted,parsed),(old_legacy,old_converted,old_parsed))
        self.assertTrue(audit)
        self.assertEqual((req,ex),(before_req,before_ex))

    def test_full_unchanged_typed_parser_is_still_called(self):
        req,ex,_,_=chain()
        original=p.v1.parse_extraction
        with patch.object(p.v1,'parse_extraction',wraps=original) as parser:
            p.analyze(req,p.v1.encode(ex))
        parser.assert_called_once()

    def test_empty_query_scope_is_uncertain_not_auto_filled(self):
        req,ex,_,_=chain()
        ex['query_scope']=[]
        result=p.analyze(req,p.v1.encode(ex))
        contract=result['structural_validation']['units'][0]['atoms'][0]['contract']
        self.assertEqual((contract['status'],contract['reason']),('uncertain','query_scope_required'))
        self.assertEqual(result['structural_validation']['query_scope'],{})

    def test_missing_operator_or_scope_binding_rejected(self):
        req=request()
        for field in ('operator_span','title_scope','condition_spans'):
            ex=extraction(req)
            ex['units'][0]['atoms'][0]['evidence'][0][field]=None if field=='operator_span' else []
            with self.assertRaises(ValueError):p.analyze(req,p.v1.encode(ex))

    def test_missing_claim_condition_or_value_conflict_not_matched(self):
        req=request()
        for mode in ('condition','operator'):
            ex=extraction(req)
            claim=ex['units'][0]['atoms'][0]['claim']
            if mode=='condition':claim['conditions']=[]
            else:claim['value']['operator']='payment'
            result=p.analyze(req,p.v1.encode(ex))
            self.assertNotEqual(result['structural_validation']['units'][0]['atoms'][0]['contract']['status'],'matched')

    def test_parent_choice_does_not_hide_conflicting_facts(self):
        req=request()
        ex=extraction(req)
        atom=ex['units'][0]['atoms'][0]
        other=copy.deepcopy(atom['evidence'][0])
        other['fact_id']='conflicting'
        other['assertion']['value']['kind']='text'
        atom['evidence'].append(other)
        result=p.analyze(req,p.v1.encode(ex))
        contract=result['structural_validation']['units'][0]['atoms'][0]['contract']
        self.assertEqual(contract['reason'],'same_scope_conflicting_evidence')

    def test_forged_request_revision_and_schema_not_accepted(self):
        for mode in ('query','revision','schema'):
            req=request()
            raw=p.v1.encode(extraction(req))
            if mode=='query':req['payload']['data']['query']+=' 변경'
            if mode=='revision':req['payload']['data']['sources'][0]['source_sha256']='forged'
            if mode=='schema':req['payload']['response_schema']['required']=[]
            with self.assertRaises(ValueError):p.analyze(req,raw)

    def test_response_schema_and_request_binding_remain_strict(self):
        req=request()
        for mode in ('stale','extra','array','string'):
            ex=extraction(req)
            if mode=='stale':ex['request_id']='stale'
            if mode=='extra':ex['extra']=True
            if mode=='array':ex['units']*=33
            if mode=='string':ex['units'][0]['reason']=''
            with self.assertRaises(ValueError):p.analyze(req,p.v1.encode(ex))

    def test_title_scope_keeps_global_uniqueness(self):
        old=request()
        data=old['payload']['data']
        req=p.q.build_extraction_request(data['query'],data['raw_draft'],[
            {'chunk_id':'s1','source_title':'제7회 ALPHA ALPHA 공모전','text':data['sources'][0]['text']}])
        with self.assertRaisesRegex(p.q.QuoteBindingError,'ambiguous'):
            p.analyze(req,p.v1.encode(extraction(req)))

    def test_analysis_not_new_model_response_or_semantic_success(self):
        req,ex,_,_=chain(wrong=True)
        result=p.analyze(req,p.v1.encode(ex))
        self.assertEqual(result['analysis_contract'],p.VERSION)
        self.assertEqual(result['original_wire_version'],p.q.VERSION)
        self.assertEqual(result['record_type'],'counterfactual_replay_of_v2_output')
        self.assertFalse(result['original_run_reclassified'])
        self.assertFalse(result['eligible_for_service'])
        self.assertFalse(result['semantic_verified'])
        self.assertIsNone(result['candidate_gfc'])
        # Known limit: two wrong typed interpretations can agree, even with real quotes.
        self.assertEqual(result['structural_validation']['units'][0]['atoms'][0]['contract']['status'],'matched')

    def test_table_row_header_and_cell_binding_unchanged(self):
        source,fact=table_fixture()
        req=p.q.build_extraction_request('ALPHA 마감은?','마감은 2028-08-05',[
            {'chunk_id':source.source_id,'source_title':source.title,'text':source.text}])
        ex={'version':p.q.VERSION,'request_id':req['request_id'],
            'query_scope':[{'dimension':'entity','quote':'ALPHA'}],
            'units':[{'unit_id':'u000','coverage':'complete','reason':'synthetic table','atoms':[
                {'atom_id':'a0','claim_quote':'마감은 2028-08-05','claim':assertion(wire(asdict(fact.assertion))),
                 'evidence':[fact_wire(wire(asdict(fact)))],'requested_sources':[source.source_id]}]}]}
        result=p.analyze(req,p.v1.encode(ex))
        self.assertEqual(result['structural_validation']['units'][0]['atoms'][0]['contract']['status'],'matched')
        self.assertEqual(result['binding_audit'],[])
        ex['units'][0]['atoms'][0]['evidence'][0]['table']['header']['quote']='결과 발표'
        with self.assertRaises(ValueError):p.analyze(req,p.v1.encode(ex))


if __name__=='__main__':unittest.main()
