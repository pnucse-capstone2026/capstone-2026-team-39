"""Offline replay of one pinned failed DEV artifact, with no LLM capability."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import sys

import parent_bound as p

OUT=p.ROOT/'processed/eval/preflight-20260914/parent-bound-v1/offline-v1'
INVENTORY_SHA='5cbc5c120a84bedf2a00e925d62481bfeeb69dd8f5444569dadcd60f755fa296'


def inputs():
    pins,_,request=p.r.s.inputs()
    inventory_path=p.r.LIVE/'output-sha256.json'
    if p.r.prior.sha(inventory_path)!=INVENTORY_SHA:
        raise ValueError('old_live_inventory_changed')
    inventory=p.r.prior.read(inventory_path)
    if set(inventory)|{'output-sha256.json'}!={path.name for path in p.r.LIVE.iterdir()}:
        raise ValueError('old_live_file_set_changed')
    pins.update({str(p.r.LIVE/name):value for name,value in inventory.items()})
    pins[str(inventory_path)]=INVENTORY_SHA
    pins.update({str(path):p.r.prior.sha(path) for path in Path(__file__).parent.glob('*.py')})
    if any(p.r.prior.sha(path)!=expected for path,expected in pins.items()):
        raise ValueError('pinned_replay_input_changed')
    completion=p.r.prior.read(p.r.LIVE/'completion.json')
    if (completion['extraction_valid'] or completion['semantic_valid']
            or completion['status']!='STOPPED_INCOMPLETE'
            or completion['failure']['reason']!='quote_ambiguous'):
        raise ValueError('expected_original_failed_run')
    raw=(p.r.LIVE/(p.r.CASE+'--extract.response.txt')).read_text()
    return pins,request,raw


def run():
    pins,request,raw=inputs()
    try:
        p.q.parse_extraction(request,raw)
    except ValueError as error:
        old_outcome={'status':'rejected','error_type':type(error).__name__,'reason':str(error)}
    else:
        raise ValueError('original_failure_not_reproduced')
    analysis=p.analyze(request,raw)
    units=analysis['structural_validation']['units']
    atoms=[a for u in units for a in u['atoms']]
    scoped=[x for x in analysis['binding_audit'] if x['global_occurrences']>1]
    summary={'analysis_contract':p.VERSION,'case_id':p.r.CASE,'old_outcome':old_outcome,
             'counterfactual_structural_parse':'pass','units':len(units),'atoms':len(atoms),
             'query_scope':analysis['structural_validation']['query_scope'],
             'contract_status_counts':dict(Counter(a['contract']['status'] for a in atoms)),
             'contract_reason_counts':dict(Counter(a['contract']['reason'] for a in atoms)),
             'parent_bound_body_references':len(analysis['binding_audit']),
             'globally_repeated_locally_unique':len(scoped),'disambiguated_paths':[x['path'] for x in scoped],
             'matched_atoms':sum(a['contract']['status']=='matched' for a in atoms),
             'original_run_reclassified':False,'new_model_outputs':0,'external_calls':0,
             'semantic_verified':False,'eligible_for_service':False,'candidate_gfc':None,
             'next_live_call_prepared':False,
             'limitation':'Post-hoc replay, not a fresh model run, extraction accuracy or GFC evaluation.'}
    manifest={'analysis_contract':p.VERSION,'created_at':p.r.prior.now(),'source_pins':pins,
              'user_message':'계속해','scope':'Offline parent-bound counterfactual only; existing artifacts, host modules and service unchanged.',
              'input_request_id':request['request_id'],'input_response_sha256':analysis['original_response_sha256'],
              'external_calls':0,'known_previous_project_attempts':172,'account_daily_usage_verified':False}
    p.r.s.previous.new_directory(OUT)
    for name,value in [('manifest.json',manifest),('input-sha256.json',pins),
                       ('analysis.json',analysis),('summary.json',summary)]:
        p.r.prior.write_new(OUT/name,value)
    p.r.prior.write_new(OUT/'output-sha256.json',{path.name:p.r.prior.sha(path) for path in OUT.iterdir() if path.is_file()})
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print('MANIFEST_SHA256',p.r.prior.sha(OUT/'manifest.json'))


if __name__=='__main__':
    sys.addaudithook(p.r.prior.audit)
    run()
