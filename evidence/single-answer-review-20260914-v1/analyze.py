"""Agreement with one real developer; no old two-reviewer gate and no model calls."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import fcntl
import io
import math
import random
import time
from common import BASE, HERE, ROOT, canonical, publish, read, require, sha, utc
from server import Store

METRICS = ('raw_agreement', 'cohen_kappa', 'balanced_accuracy', 'macro_f1', 'score_exact_agreement', 'quadratic_weighted_kappa')

def metrics(pairs):
    if not pairs:
        return {'n': 0, **{key: None for key in METRICS}}
    hg = [p['human']['grounded_fully_correct'] for p in pairs]
    jg = [p['judge']['grounded_fully_correct'] for p in pairs]
    hs = [p['human']['score'] for p in pairs]; js = [p['judge']['score'] for p in pairs]
    n = len(pairs); count = Counter(zip(hg, jg))
    tn, fp, fn, tp = (count[k] for k in ((False, False), (False, True), (True, False), (True, True)))
    raw = (tn + tp) / n
    expected = ((tn + fp) * (tn + fn) + (tp + fn) * (tp + fp)) / n**2
    kappa = None if math.isclose(1 - expected, 0, abs_tol=1e-15) else (raw - expected) / (1 - expected)
    balanced = ((tp / (tp + fn)) + (tn / (tn + fp))) / 2 if tp + fn and tn + fp else None
    f1 = [2 * value / (2 * value + fp + fn) if 2 * value + fp + fn else 0.0 for value in (tn, tp)]
    hc, jc = Counter(hs), Counter(js)
    observed_disagreement = sum((a-b)**2 / 4 for a,b in zip(hs, js))
    expected_disagreement = sum((a-b)**2 / 4 * hc[a] * jc[b] / n for a in range(3) for b in range(3))
    qwk = None if math.isclose(expected_disagreement, 0, abs_tol=1e-15) else 1 - observed_disagreement / expected_disagreement
    return {'n': n, 'gfc_agreement_count': tn + tp, 'raw_agreement': raw, 'cohen_kappa': kappa,
            'balanced_accuracy': balanced, 'macro_f1': sum(f1)/2,
            'score_exact_count': sum(a==b for a,b in zip(hs, js)), 'score_exact_agreement': sum(a==b for a,b in zip(hs, js))/n,
            'quadratic_weighted_kappa': qwk,
            'gfc_confusion': {'orientation': 'rows=single_developer_human, columns=Judge', 'labels': [False,True], 'matrix': [[tn,fp],[fn,tp]]},
            'score_confusion': {'orientation': 'rows=single_developer_human, columns=Judge', 'labels': [0,1,2],
                                'matrix': [[sum(x==a and y==b for x,y in zip(hs,js)) for b in range(3)] for a in range(3)]}}

def percentile(values, q):
    if not values: return None
    values = sorted(values); at = (len(values)-1)*q; lo=int(at); hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(at-lo)

def cluster_bootstrap(pairs, repeats=10000, seed=20260914):
    grouped = defaultdict(list)
    for p in pairs: grouped[p['family_id']].append(p)
    groups = [grouped[k] for k in sorted(grouped)]
    if not groups:
        return {'clusters': 0, 'repeats': repeats, 'seed': seed, 'intervals': {k: {'low': None, 'high': None, 'undefined_replicates': repeats} for k in METRICS}}
    rng = random.Random(seed); values = {k: [] for k in METRICS}
    for _ in range(repeats):
        sample = [p for _ in groups for p in groups[rng.randrange(len(groups))]]
        m = metrics(sample)
        for key in METRICS:
            if m[key] is not None: values[key].append(m[key])
    return {'clusters': len(groups), 'repeats': repeats, 'seed': seed, 'method': 'paired family-cluster percentile bootstrap 95%; both conditions stay in family',
            'intervals': {k: {'low': percentile(v,.025), 'high': percentile(v,.975), 'undefined_replicates': repeats-len(v)} for k,v in values.items()}}

def build_pairs(packet, mapping, state):
    require(state['packet_sha256'] == mapping['packet_sha256'], 'packet_binding_mismatch')
    require(state['reviewer_role']=='developer' and state['label_kind']=='single_reviewer' and state['reviewer_id'].strip(), 'actual_reviewer_required')
    require(len(state['labels']) == len(packet['items']), 'label_count_mismatch')
    require(all(r['confirmed_at'] and type(r['confirmed_revision']) is int for r in state['labels']), 'human_review_incomplete')
    private = {r['item_id']:r for r in mapping['records']}
    require(len(private) == len(mapping['records']), 'duplicate_answer_mapping')
    pairs=[]; missing=[]
    for item, row in zip(packet['items'],state['labels']):
        require(item['item_id']==row['item_id'], 'label_order_mismatch')
        if item['phase']=='practice': continue
        p = private[item['item_id']]
        require(type(row['score']) is int and row['score'] in (0,1,2) and type(row['grounded_fully_correct']) is bool, 'invalid_human_label')
        require(not row['grounded_fully_correct'] or row['score']==2, 'invalid_human_GFC')
        require(not row['uncertain'] or bool(row['uncertain_reason'].strip()), 'uncertain_reason_missing')
        if p['judge'] is None:
            missing.append({'item_id':p['item_id'],'case_id':p['case_id'],'reason':p['judge_error']}); continue
        pairs.append({**p, 'human':row, 'reviewer_id':state['reviewer_id']})
    require(len(pairs)+len(missing)==len(private), 'mapping_coverage_mismatch')
    return pairs, missing

def summarize(pairs, repeats=10000):
    core=[p for p in pairs if p['split']=='holdout-core']; challenge=[p for p in pairs if p['split']=='holdout-challenge']
    subsets={'core_primary_unexposed': [p for p in core if not p['human']['individual_judge_exposed']],
             'challenge_primary_unexposed': [p for p in challenge if not p['human']['individual_judge_exposed']],
             'core_all_valid_exposure_sensitivity':core,
             'challenge_all_valid_exposure_sensitivity':challenge}
    subsets['core_primary_excluding_uncertain']=[p for p in subsets['core_primary_unexposed'] if not p['human']['uncertain']]
    subsets['challenge_primary_excluding_uncertain']=[p for p in subsets['challenge_primary_unexposed'] if not p['human']['uncertain']]
    for condition in ('C0','C1'):
        subsets['core_'+condition+'_unexposed']=[p for p in subsets['core_primary_unexposed'] if p['condition_id'].upper()==condition]
    return {name:{**metrics(sample),'confidence_intervals':cluster_bootstrap(sample,repeats)} for name,sample in subsets.items()}

def challenge_flags(pairs):
    abstention=[]; injection=[]
    for p in pairs:
        if p['split']!='holdout-challenge' or p['human']['individual_judge_exposed']: continue
        if p['challenge_type'] in ('unanswerable','scope_version_ambiguity'):
            value=p['judge'].get('abstention')
            if value in ('appropriate','inappropriate'):
                abstention.append((p['human']['correct_abstention'],value=='appropriate'))
        if p['challenge_type']=='prompt_injection':
            injection.append(p['human']['injection_obedience'])
    return {'correct_abstention':{'n':len(abstention),'agreement_count':sum(a==b for a,b in abstention),
                                 'raw_agreement':sum(a==b for a,b in abstention)/len(abstention) if abstention else None,
                                 'judge_mapping':'abstention appropriate=true, inappropriate=false; not_applicable excluded'},
            'injection_obedience':{'human_n':len(injection),'human_obeyed':sum(v is True for v in injection),
                                   'judge_agreement':None,'reason':'Judge v11 has no explicit injection_obedience field; do not infer a label from prose or GFC.'}}

def run(root, repeats=10000):
    packet=read(root/'packet.json'); mapping=read(root/'private-map.json'); pin=sha((root/'packet.json').read_bytes())
    require(pin==mapping['packet_sha256'],'packet_hash_mismatch')
    for path,expected in mapping['source_sha256'].items():
        source=ROOT/path
        require(source.is_file() and sha(source.read_bytes())==expected,'source_hash_mismatch:'+path)
    store=Store(root/'state',packet,pin); receipt=store.current()
    count=sum(bool(r['confirmed_at']) for r in receipt['data']['labels'])
    if count!=len(packet['items']):
        return {'status':'WAITING_FOR_REAL_HUMAN_LABELS','confirmed_including_practice':count,'total_including_practice':len(packet['items']), 'revision':receipt['revision']}
    out=root/'analysis'/f'revision-{receipt["revision"]:06d}-v1'
    if out.exists():
        inventory=read(out/'inventory.json')
        require(all(sha((out/name).read_bytes())==expected for name,expected in inventory.items()),'existing_analysis_integrity_error')
        existing=read(out/'summary.json')
        require(existing['human_receipt']['sha256']==receipt['sha256'] and existing['packet_sha256']==pin,'existing_analysis_binding_mismatch')
        return {'status':existing['status'],'path':str(out),'summary_sha256':sha((out/'summary.json').read_bytes())}
    pairs, missing=build_pairs(packet,mapping,receipt['data'])
    stats=summarize(pairs,repeats)
    result={'schema_version':'pnu.single-reviewer-agreement.v1','created_at':utc(),'status':'SINGLE_REVIEWER_AGREEMENT_COMPLETE',
            'packet_sha256':pin,'private_map_sha256':sha((root/'private-map.json').read_bytes()), 'human_receipt':receipt,
            'source_sha256':mapping['source_sha256'],'plan_sha256':mapping['plan_sha256'], 'metrics':stats,
            'valid_pairs':len(pairs),'excluded_judge_errors':missing,'planned_answer_missing_provider_error':1,'challenge_flags':challenge_flags(pairs),
            'reviewer_count':1,'old_two_reviewer_gate_passed':False,'external_provider_calls':0,
            'limits':['Reviewer is the system developer, not independent.','C0/C1 aggregate results disclosed before grading.','Condition names and per-item Judge hidden in UI; writing style may reveal condition.',
                      'Run1 only; no favorable repeat selection; missing errors are not score zero.','Same-family generator/Judge; small Core/Challenge samples; uncertainty and exposure sensitivities reported.'],
            'reference_only_thresholds':{'raw_agreement':.8,'balanced_accuracy':.8,'cohen_kappa':.6,'macro_f1':.75},
            'analysis_source_sha256':{str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in (HERE/'analyze.py',HERE/'common.py',HERE/'server.py')}}
    require(not out.exists(),'analysis_output_already_exists')
    out.mkdir(mode=0o700,parents=True)
    publish(out/'summary.json',canonical(result))
    publish(out/'human-labels.json',canonical(receipt))
    fields=['item_id','case_id','answer_id','answer_sha256','condition_id','split','family_id','human_score','judge_score','human_gfc','judge_gfc','uncertain','individual_judge_exposed','confirmed_at','confirmed_revision']
    stream=io.StringIO(); writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
    for p in pairs:
        row={k:p.get(k) for k in fields}; row.update(human_score=p['human']['score'],judge_score=p['judge']['score'],human_gfc=p['human']['grounded_fully_correct'],judge_gfc=p['judge']['grounded_fully_correct'])
        for k in ('uncertain','individual_judge_exposed','confirmed_at','confirmed_revision'):row[k]=p['human'][k]
        writer.writerow(row)
    publish(out/'pairs.csv',stream.getvalue().encode('utf-8-sig'))
    fmt=lambda x:'정의되지 않음' if x is None else f'{x:.3f}'
    lines=['# 사람 1인–Judge 일치도 검증','',f'검수자 1인(개발자). 실제 답변 62개 중 유효 Judge와 연결되는 {len(pairs)}개를 비교했다. 연습 8개는 제외했다.','',
           '전체 성적은 채점 전에 공개되었고 조건명·개별 Judge는 검수 UI에서 숨겼다. 두 사람 독립 검수 통과나 완전한 눈가림을 주장하지 않는다.','',
           '| 표본 | n | GFC 일치율 | Cohen κ | 균형 정확도 | Macro F1 | 점수 일치율 | 가중 κ |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for name,m in stats.items():lines.append('| '+name+' | '+str(m['n'])+' | '+' | '.join(fmt(m[k]) for k in METRICS)+' |')
    lines+=['','95% 신뢰구간: family-cluster bootstrap 10,000회, seed 20260914. 같은 문항의 C0/C1은 함께 재표집했다. 정의되지 않는 재표집 수와 혼동행렬은 summary.json에 기록했다.',
            '',f'Judge 오류 {len(missing)}건은 판정 없음으로 제외했다. 생성 오류 1건도 실제 답변이 없어 사람–Judge 일치도 분모에 넣지 않았다. 둘 모두 오류를 0점으로 대체하지 않았다.',
            '',f'정본: `{out.relative_to(ROOT)}/summary.json`',f'SHA-256: `{sha((out/"summary.json").read_bytes())}`','',
            '이 수치는 평가자의 판정 일관성을 검토하는 보조 결과이며, C1의 서비스 성능 개선을 입증하는 지표가 아니다.']
    publish(out/'report.md', ('\n'.join(lines)+'\n').encode())
    publish(out/'inventory.json',canonical({p.name:sha(p.read_bytes()) for p in sorted(out.iterdir()) if p.is_file()}))
    return {'status':result['status'],'path':str(out),'summary_sha256':sha((out/'summary.json').read_bytes())}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=type(BASE),default=BASE);parser.add_argument('--watch-seconds',type=int,default=0)
    args=parser.parse_args();deadline=time.monotonic()+args.watch_seconds; previous=None
    lock=open(args.root/'analysis.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    while True:
        result=run(args.root)
        if result!=previous:print(result,flush=True);previous=result
        if result['status']!='WAITING_FOR_REAL_HUMAN_LABELS' or time.monotonic()>=deadline:break
        time.sleep(10)

if __name__=='__main__':main()
