#!/usr/bin/env python3
"""Stage-wise decomposition of the final holdout (Core 27 x C0/C1 x 3 runs).

Reads only immutable artifacts of final-single-reviewer-20260914-v1 and the
single-answer review revision; never calls the service or an LLM.

Definitions
- doc_hit: a required claim's evidence document (document_id of any evidence
  option) is present among the delivered final contexts (k=8) / candidate pool.
- ev_hit: the stored atomic evidence matcher result at k=8 (matcher v2).
- draft_all / final_all: every critical value of the claim (NFKC, whitespace and
  punctuation removed) is contained in the raw draft / final answer. This is a
  string-containment heuristic (paraphrases are not counted).
- gate_rejected_gold: an output-gate-rejected draft sentence that contains all
  critical values of some required claim (lower bound of correct rejections).

Usage: python3 -B stage_decomposition.py [repo_root] [--json-out PATH]
"""
import csv, json, os, re, statistics, sys, unicodedata, hashlib
from collections import Counter, defaultdict

ROOT = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else os.path.expanduser('~/project/pnu-docs-chatbot')
JSON_OUT = None
if '--json-out' in sys.argv:
    JSON_OUT = sys.argv[sys.argv.index('--json-out') + 1]
E = f'{ROOT}/processed/eval/final-single-reviewer-20260914-v1'
REV = f'{ROOT}/processed/reviews/single-answer-20260914-v1/analysis/revision-000342-v1'
GROUPS = ['c0-run1', 'c0-run2', 'c0-run3', 'c1-run1', 'c1-run2', 'c1-run3']


def norm(s):
    return re.sub(r'[\s\W_]+', '', unicodedata.normalize('NFKC', s or ''))


def load(p):
    return [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


gold = {g['id']: g for g in load(f'{E}/preparation-v1/reviewed-cases-v1.jsonl')}
inputs = {'preparation-v1/reviewed-cases-v1.jsonl': sha(f'{E}/preparation-v1/reviewed-cases-v1.jsonl')}
claims, answers = [], []
for g in GROUPS:
    cond = g[:2]
    ap, jp = f'{E}/live-v3/answers/{g}.answers.jsonl', f'{E}/live-v3/judge/{g}-judge-v11-r1.jsonl'
    inputs[f'live-v3/answers/{g}.answers.jsonl'] = sha(ap)
    inputs[f'live-v3/judge/{g}-judge-v11-r1.jsonl'] = sha(jp)
    jud = {j['case_id']: j for j in load(jp)}
    for a in load(ap):
        if a.get('generation', {}).get('fallback_reason'):
            continue  # operational error slot (C1 run1): no real answer
        gd, j = gold[a['case_id']], jud.get(a['case_id'])
        jj = (j or {}).get('judge') or {}
        jc = {c['claim_id']: c for c in jj.get('claim_checks', [])}
        ev = {c['claim_id']: c for c in a['atomic_evidence_at_k']['8'].get('claims', [])}
        st = a['evaluation_trace']['retrieval_stages']
        final_docs = {c['document_id'] for c in st['final_contexts']}
        pool_docs = {c['document_id'] for c in st['raw_bm25']}
        raw, fin = norm(a['evaluation_trace'].get('raw_draft', '')), norm(a['answer'])
        crit_all = []
        for rc in gd['required_claims']:
            cvs = [norm(v) for v in rc['critical_values']]
            docs = {o['document_id'] for o in rc['evidence_options']}
            crit_all.append(cvs)
            claims.append(dict(cond=cond, run=a['generation_run_id'], case=a['case_id'], claim=rc['claim_id'],
                               doc_hit_final=bool(docs & final_docs), doc_hit_pool128=bool(docs & pool_docs),
                               ev_hit=bool(ev.get(rc['claim_id'], {}).get('matched')),
                               draft_all=all(v in raw for v in cvs), final_all=all(v in fin for v in cvs),
                               judge_status=jc.get(rc['claim_id'], {}).get('status')))
        rejected = [c for c in a['claims'] if not c['supported']]
        rej_gold = [c for c in rejected if any(cv and all(v in norm(c['text']) for v in cv) for cv in crit_all)]
        answers.append(dict(cond=cond, run=a['generation_run_id'], case=a['case_id'],
                            all_ev=bool(a['atomic_evidence_at_k']['8'].get('all_matched')),
                            all_docs=all(bool({o['document_id'] for o in rc['evidence_options']} & final_docs) for rc in gd['required_claims']),
                            draft_all=all(cv and all(v in raw for v in cv) for cv in crit_all),
                            final_all=all(cv and all(v in fin for v in cv) for cv in crit_all),
                            gfc=jj.get('grounded_fully_correct'), score=jj.get('score'),
                            gate_decision=a['security']['output_gate'].get('decision'),
                            gate_checked=len(a['claims']), gate_rejected=len(rejected), gate_rejected_gold=len(rej_gold),
                            gate_rejected_gold_reasons=Counter(c['validation_reason'] for c in rej_gold)))


def frac(n, d):
    return {'n': n, 'd': d, 'pct': round(100 * n / d, 1) if d else None}


out = {'schema_version': 'pnu.report.holdout-stage-decomposition.v1', 'inputs_sha256': inputs, 'conditions': {}}
for cond in ['c0', 'c1']:
    C = [c for c in claims if c['cond'] == cond]
    A = [a for a in answers if a['cond'] == cond]
    C1 = [c for c in C if c['run'] == 'run1']
    evc = [c for c in C if c['ev_hit']]
    nev = [c for c in C if not c['ev_hit']]
    allev = [a for a in A if a['all_ev']]
    notev = [a for a in A if not a['all_ev']]
    nong = [a for a in A if a['gfc'] is False]
    reasons = Counter()
    for a in A:
        reasons.update(a['gate_rejected_gold_reasons'])
    out['conditions'][cond] = {
        'claims': {
            'total': len(C),
            'doc_in_candidate_pool128_run1': frac(sum(c['doc_hit_pool128'] for c in C1), len(C1)),
            'doc_in_final_context8': frac(sum(c['doc_hit_final'] for c in C), len(C)),
            'evidence_chunk_in_final_context8': frac(sum(c['ev_hit'] for c in C), len(C)),
            'judge_supported_given_evidence': frac(sum(c['judge_status'] == 'supported' for c in evc), len(evc)),
            'judge_supported_without_evidence': frac(sum(c['judge_status'] == 'supported' for c in nev), len(nev)),
            'judge_status_given_evidence': dict(Counter(c['judge_status'] for c in evc)),
        },
        'answers': {
            'total': len(A),
            'all_required_docs_in_context8': frac(sum(a['all_docs'] for a in A), len(A)),
            'all_required_evidence_in_context8': frac(len(allev), len(A)),
            'gfc_given_all_evidence': frac(sum(bool(a['gfc']) for a in allev), len(allev)),
            'gfc_given_incomplete_evidence': frac(sum(bool(a['gfc']) for a in notev), len(notev)),
            'mean_judge_score_given_all_evidence': round(statistics.mean(a['score'] for a in allev), 3),
            'mean_judge_score_given_incomplete_evidence': round(statistics.mean(a['score'] for a in notev), 3),
            'gate_decisions': dict(Counter(a['gate_decision'] for a in A)),
            'gate_checked_sentences': sum(a['gate_checked'] for a in A),
            'gate_rejected_sentences': frac(sum(a['gate_rejected'] for a in A), sum(a['gate_checked'] for a in A)),
            'gate_rejected_with_gold_critical_values_lower_bound': frac(sum(a['gate_rejected_gold'] for a in A), sum(a['gate_rejected'] for a in A)),
            'gate_rejected_with_gold_critical_values_reasons': dict(reasons),
            'non_gfc_total': len(nong),
            'non_gfc_incomplete_evidence': sum(1 for a in nong if not a['all_ev']),
            'non_gfc_all_evidence_draft_missing_values': sum(1 for a in nong if a['all_ev'] and not a['draft_all']),
            'non_gfc_all_evidence_draft_complete_gate_loss_lower_bound': sum(1 for a in nong if a['all_ev'] and a['draft_all']),
        },
    }

# human labels (run1) vs evidence completeness
pairs = {(r['case_id'], r['condition_id']): r for r in csv.DictReader(open(f'{REV}/pairs.csv', encoding='utf-8-sig'))}
inputs['reviews/analysis/revision-000342-v1/pairs.csv'] = sha(f'{REV}/pairs.csv')
inputs['reviews/analysis/revision-000342-v1/summary.json'] = sha(f'{REV}/summary.json')
hum = defaultdict(list)
for a in answers:
    if a['run'] != 'run1':
        continue
    p = pairs.get((a['case'], a['cond']))
    if p:
        hum[a['all_ev']].append((p['human_gfc'] == 'True', int(p['human_score'])))
out['human_run1_core'] = {
    'all_evidence': {'n': len(hum[True]), 'human_gfc': sum(g for g, _ in hum[True]), 'mean_score': round(statistics.mean(s for _, s in hum[True]), 3)},
    'incomplete_evidence': {'n': len(hum[False]), 'human_gfc': sum(g for g, _ in hum[False]), 'mean_score': round(statistics.mean(s for _, s in hum[False]), 3)},
}
out['notes'] = [
    'Retrieval is deterministic: final contexts are identical across the three generation runs.',
    'draft_all/final_all use string containment of critical values and undercount paraphrased correct claims; gate loss is therefore a lower bound and draft-missing an upper bound.',
    'The C1 run1 slot that fell back to an extractive answer after HTTP 429 is excluded from claim/answer rows (kept as an operational failure in the official GFC denominator).',
]
txt = json.dumps(out, ensure_ascii=False, indent=1)
if JSON_OUT:
    open(JSON_OUT, 'w', encoding='utf-8').write(txt + '\n')
print(txt)
