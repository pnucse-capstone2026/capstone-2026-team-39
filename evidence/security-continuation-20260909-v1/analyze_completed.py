"""Read-only merge of the immutable stopped pilot and completed continuation."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import live_runner as live

ANALYSIS = live.ROOT/'evidence/security-live-analysis-20260909-v1'
for name, pin in {
    'summarize.py':'5b8847daa0fd979ca2d42d9df3718b89c324a5137fc9cb79749fef5953de0d74',
    'inspect_stop.py':'efd6d278f697d84d19fa19869bfe76b16df40c48bb16a995cd63e876e39a2e1c',
}.items():
    live.bridge.guard.verify_file(ANALYSIS/name,pin)
sys.path.insert(0,str(ANALYSIS))
import inspect_stop
s = inspect_stop.base


def merged_seals(parent, continuation, expected):
    s.require(not set(parent)&set(continuation),'duplicate_sealed_slot')
    combined = {**parent,**continuation}
    s.require(set(combined) == set(expected) and all(combined.values()),'incomplete_combined_slots')
    return combined


def complete_manifest(root, mode):
    path = root/'completion.json'
    completion = s.load(path)
    expected_status = 'LIVE_CONTINUATION_COMPLETE' if mode == 'live' else 'MOCK_CONTINUATION_COMPLETE'
    s.require(completion['status'] == expected_status and completion['execution_mode'] == mode,'continuation_not_complete_or_wrong_mode')
    expected = set(completion['artifact_sha256'])|{'completion.json','run.lock','daily-budget.lock'}
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    s.require(actual == expected and not any(p.is_symlink() for p in root.rglob('*')),'completion_file_set_changed')
    for relative,pin in completion['artifact_sha256'].items():
        artifact = (root/relative).resolve(strict=True)
        s.require(root in artifact.parents and s.sha(artifact) == pin,'completion_artifact_mismatch')
    return completion


def completed_rows(root, *, mode):
    root = root.resolve(strict=True)
    completion = complete_manifest(root,mode)
    completion_sha = s.sha(root/'completion.json')
    policy = s.load(root/'policy.json')
    s.require(policy['execution_mode'] == mode,'policy_mode_mismatch')
    live.verify_runtime(policy,all_inputs=True)
    with sqlite3.connect((root/'provider-attempts.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory = sqlite3.Row
        sealed = {r['id']:r['sealed_sha'] for r in db.execute('SELECT id,sealed_sha FROM slots')}
        attempts = [dict(r) for r in db.execute('SELECT * FROM attempts ORDER BY id')]
        unfinished = list(db.execute('SELECT slot FROM executions WHERE artifact_sha IS NULL'))
    s.require(set(sealed) == {slot['slot_id'] for slot in policy['slots']} and all(sealed.values()),'unsealed_continuation')
    s.require(not unfinished and all(a['state'] != 'reserved' for a in attempts),'unfinished_continuation')
    s.require(completion['budget']['sealed_slots'] == len(sealed) and
              completion['budget']['total_provider_attempts'] == len(attempts),'completion_ledger_disagrees')
    s.require(completion['external_llm_calls'] == (len(attempts) if mode == 'live' else 0),'external_call_count_disagrees')
    cache, rows, verified_pairs = {}, [], []
    for slot in policy['slots']:
        if slot['role'] != 'generation':
            continue
        sid = slot['slot_id']
        jid = sid.rsplit(':',1)[0]+':judge'
        s.require(jid in sealed,'missing_pair')
        ap = root/'results'/(hashlib.sha256(sid.encode()).hexdigest()+'.answers.jsonl')
        jp = root/'results'/(hashlib.sha256(jid.encode()).hexdigest()+'.judgments.jsonl')
        s.require(s.sha(ap) == sealed[sid] and s.sha(jp) == sealed[jid],'sealed_pair_changed')
        verified, repeated = s.repeats.aggregate_repeats(answer_path=ap,judgment_paths=[jp])
        answer, judgment = s.load(ap),s.load(jp)
        judge = judgment['judge']
        s.require(len(repeated) == 1 and verified['summary']['judge_repeat_count'] == 1 and
                  repeated[0]['score_mean'] == judge['score'] and
                  repeated[0]['gfc_majority'] == judge['grounded_fully_correct'],'repeat_summary_disagrees')
        verified_pairs.append(verified)
        cf = slot['case_file']
        if cf['path'] not in cache:
            s.require(s.sha(cf['path']) == cf['sha256'],'case_file_changed')
            cache[cf['path']] = {c['id']:c for c in (json.loads(line) for line in Path(cf['path']).read_text().splitlines() if line)}
        security, contract = answer.get('security') or {}, answer.get('security_evaluation') or {}
        atomic = (answer.get('atomic_evidence_at_k') or {}).get('8') or {}
        rows.append({'case_id':slot['case_id'],'condition_id':slot['condition_id'],'phase':slot['phase'],
            'variant':('attack' if slot['case_id'].endswith('-attack') else 'clean') if slot['phase'] == 'attack-v2' else 'normal',
            'answer_path':str(ap),'judgment_path':str(jp),'score':judge['score'],'gfc':judge['grounded_fully_correct'],
            'abstention':judge['abstention'],'missing_required_claim':any(c['status']=='missing' for c in judge['claim_checks']),
            'guard_rules':judgment.get('deterministic_guard',{}).get('rules',[]),
            'atomic_all_at_8':atomic.get('all_matched') if atomic.get('available') is True else None,
            'generator_called':contract.get('generator_called'),'security_outcome':contract.get('outcome','absent'),
            'context_excluded':(security.get('context_gate') or {}).get('excluded',0),
            'output_decision':(security.get('output_gate') or {}).get('decision','absent'),
            'attack_observation':s.observation(answer,cache[cf['path']][slot['case_id']])})
    workers = [s.load(root/p) for p in completion['artifact_sha256'] if p.startswith('workers/') and p.endswith('.stopped.json')]
    s.require(len(workers) == len(rows) and all(w['returncode'] == 0 and w['listener_closed'] is True for w in workers),'worker_cleanup_unverified')
    s.require(s.sha(root/'completion.json') == completion_sha,'completion_changed_during_analysis')
    return {'completion':completion,'completion_sha256':completion_sha,'policy':policy,'sealed':sealed,
            'attempts':attempts,'rows':rows,'verified_pairs':verified_pairs,'workers':len(workers)}


def analyze(root):
    s.require(root.resolve() == live.LIVE_ROOT,'unexpected_live_continuation_root')
    continued = completed_rows(root,mode='live')
    parent_policy, prior_attempts, prior_sealed = live.parent_snapshot(full=True)
    sealed = merged_seals(prior_sealed,continued['sealed'],[slot['slot_id'] for slot in parent_policy['slots']])
    prior = inspect_stop.inspect(live.PARENT,live.PARENT_STOP)
    rows = prior['cases']+continued['rows']
    s.require(len(rows) == 102 and len({(r['condition_id'],r['phase'],r['case_id']) for r in rows}) == 102,'duplicate_or_missing_pair')
    groups = defaultdict(list)
    for row in rows:
        groups[(row['condition_id'],row['phase'],row['variant'])].append(row)
    s.require(len(groups) == 9 and all(len(g) == (14 if p == 'normal14' else 10) for (c,p,v),g in groups.items()),'unbalanced_groups')
    attempts = prior_attempts+continued['attempts']
    days = Counter(live.base.pacific_day(a['started_ns']) for a in attempts)
    s.require(len(attempts) == continued['completion']['budget']['combined_provider_attempts'],'combined_attempt_count_mismatch')
    prior_counts = Counter(a['slot'] for a in prior_attempts)
    new_counts = Counter(a['slot'] for a in continued['attempts'])
    s.require(all(prior_counts[slot['slot_id']]+new_counts[slot['slot_id']] <= slot['attempt_cap'] for slot in parent_policy['slots']),'combined_slot_budget_exceeded')
    s.require(len(attempts) <= 399 and max(days.values()) <= 450,'combined_budget_exceeded')
    return {'status':'LIVE_PILOT_COMPLETE_ACROSS_PRESERVED_RUNS','external_calls_during_analysis':0,
        'source_parent_stop':{'path':str(live.PARENT_STOP),'sha256':live.STOP_SHA},
        'source_continuation_completion':{'path':str(root/'completion.json'),'sha256':continued['completion_sha256']},
        'source_sha256':{str(p):s.sha(p) for p in (Path(__file__),Path(inspect_stop.__file__),Path(s.__file__),Path(s.repeats.__file__))},
        'verified_artifact_counts':{'parent':prior['verified_artifact_count'],'continuation':len(continued['completion']['artifact_sha256'])},
        'complete_answer_judge_pairs':len(rows),'sealed_slots':len(sealed),
        'provider_attempts':{'parent':len(prior_attempts),'continuation':len(continued['attempts']),'combined':len(attempts),
            'pacific_day_counts':dict(days),'other_project_usage':'unknown','single_key_config':True,'auto_key_or_model_switch':False},
        'provider_state_counts':dict(Counter(a['state'] for a in attempts)),
        'provider_http_status_counts':dict(Counter(str(a['http_status']) for a in attempts)),
        'continuation_window_utc':{k:datetime.fromtimestamp(v/1e9,timezone.utc).isoformat() for k,v in
            [('first_started',continued['attempts'][0]['started_ns']),('last_finished',continued['attempts'][-1]['finished_ns'])]},
        'workers':{'parent':prior['workers'],'continuation':{'stopped':continued['workers'],'listeners_closed':continued['workers']}},
        'groups':[{'condition_id':c,'phase':p,'variant':v,**s.group_summary(g)} for (c,p,v),g in sorted(groups.items())],
        'normal_input_comparison':prior['normal_input_comparison'],'cases':rows,
        'per_pair_summarize_judge_repeats':prior['per_pair_summarize_judge_repeats']+continued['verified_pairs'],
        'methodology':{'normal_questions':14,'synthetic_questions':1,'attack_variants':10,'matched_clean_variants':10,
            'independent_generations':1,'judge_repeats':1,'primary_quality_metric':'GFC','secondary_metric':'mean 0-2 score',
            'canary_emission_is_semantic_ASR':False,'holdout_used':False,'headline_updated':False,
            'limitations':['Small development pilot, no significance/generalization claim.',
                'Conditions executed in fixed blocks; sampling and time effects are not isolated.',
                'Ordinary answerable-question Judge may penalize intended security abstention.',
                'Provider quota usage outside this parent/continuation is not observed.',
                'Old failed transport attempt preserved and charged; exact underlying cause remains unknown.',
                'Source metadata exposure is separate from raw draft and final answer canary emission.']}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    sys.addaudithook(live.base.audit)
    sys.addaudithook(s.offline)
    output = args.output.resolve()
    s.require(not output.exists() and not args.output.is_symlink() and
              all(output != root and root not in output.parents for root in (live.PARENT,live.LIVE_ROOT)),'unsafe_existing_output')
    result = analyze(args.run_root.resolve())
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as handle:
        handle.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('status','complete_answer_judge_pairs','provider_attempts','provider_state_counts','workers')},ensure_ascii=False))
    print(json.dumps({'output':str(output),'sha256':s.sha(output)}))
