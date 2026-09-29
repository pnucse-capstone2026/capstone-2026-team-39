"""Build a blinded, allowlisted packet and private provenance; no model calls."""
from __future__ import annotations
import re
import sys
from common import BASE, EVAL, ROOT, canonical, publish, read, require, rows, sha

SEED = 20260914

def project(answer, case, item_id, phase):
    trace = answer.get('evaluation_trace') or {}
    prompt = (trace.get('generation_input') or {}).get('user_prompt', '')
    # These exact serialized blocks are from the saved generation request, not
    # reconstructed retrieval text. Preserve boundaries and content verbatim.
    blocks = re.findall(r'<UNTRUSTED_CONTEXT source_number="\d+"[^>]*>.*?</UNTRUSTED_CONTEXT>', prompt, re.S)
    if phase == 'main':
        require(bool(blocks) and len(blocks) == prompt.count('</UNTRUSTED_CONTEXT>'), 'incomplete_prompt_context_extraction')
    contexts = blocks or [str(x.get('text') or x.get('preview') or '') for x in (trace.get('retrieval_stages') or {}).get('final_contexts', answer.get('sources', []))]
    required = []
    for claim in case.get('required_claims') or []:
        required.append({'description': claim.get('description', ''), 'critical_values': claim.get('critical_values', []),
                         'evidence': [{'title': e.get('source_title', ''), 'quote': e.get('quote', '')} for e in claim.get('evidence_options', [])]})
    if not required and phase == 'practice':
        required = [{'description': case.get('reference_answer') or case.get('reference') or '', 'critical_values': [],
                     'evidence': [{'title': e.get('source_title', ''), 'quote': e.get('quote', '')} for e in case.get('evidence', [])]}]
    kind = case.get('challenge_type') or ''
    return {'item_id': item_id, 'phase': phase, 'question': case['query'], 'role': case.get('review_subject_status') or case.get('role', ''),
            'answer': answer.get('cited_answer') or answer.get('answer', ''), 'required': required,
            'answerable': case.get('answerable'), 'challenge_type': kind,
            'expected_behavior': case.get('expected_behavior', ''),
            'oracle': {k: (case.get('challenge_oracle') or {}).get(k, []) for k in ('must_do', 'must_not_do')},
            'forbidden_claims': case.get('forbidden_claims', []),
            'contexts': contexts, 'contexts_exact_prompt_blocks': bool(blocks),
            'citations': [{k: c.get(k) for k in ('source_number', 'claim_text', 'excerpt', 'source_title', 'page', 'section_path')} for c in answer.get('citations', [])],
            'applicable': {'correct_abstention': kind in ('unanswerable', 'scope_version_ambiguity'), 'injection_obedience': kind == 'prompt_injection'}}

def main():
    require(not BASE.exists(), 'new_packet_path_required')
    sys.path.insert(0, str(ROOT / 'evidence/final-single-reviewer-20260914-v1'))
    import runner_v3 as r
    plan = read(EVAL / 'execution-plan-v3.json')
    r.verify(plan, full=True)
    _, _, _, artifacts = r.modules()
    cases_path = EVAL / 'preparation-v1/reviewed-cases-v1.jsonl'
    cases = {c['id']: c for c in rows(cases_path)}
    provenance = {str(cases_path.relative_to(ROOT)): sha(cases_path.read_bytes())}
    records = []
    for group in ('c0-run1', 'c1-run1', 'challenge-c1-run1'):
        ap = EVAL / f'live-v3/answers/{group}.answers.jsonl'
        jp = EVAL / f'live-v3/judge/{group}-judge-v11-r1.jsonl'
        provenance.update({str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in (ap, jp)})
        judgments = {j['answer_id']: j for j in rows(jp)}
        for answer in rows(ap):
            artifacts.validate_answer_record(answer)
            j = judgments[answer['answer_id']]
            require(j['answer_sha256'] == answer['answer_sha256'] and j['answer_record_sha256'] == sha(canonical(answer)), 'judge_answer_binding_mismatch')
            require(j['record_sha256'] == artifacts.record_sha256(j), 'judgment_hash_mismatch')
            require(answer['generation_run_id'] == 'run1', 'run1_only')
            records.append((answer, cases[answer['case_id']], j))
    require(len(records) == 62 and len({a['answer_id'] for a, _, _ in records}) == 62, 'run1_count_mismatch')
    records.sort(key=lambda x: sha(f'{SEED}\0{x[0]["answer_id"]}'.encode()))
    public, private = [], []
    dev_a = ROOT / 'processed/eval/preflight-20260903/dev45-generation-current-v1/c1-run1.answers.jsonl'
    dev_c = ROOT / 'config/pnu-service-answer-eval-v1.jsonl'
    dev_cases = {c['id']: c for c in rows(dev_c)}
    dev = sorted(rows(dev_a), key=lambda a: sha(f'practice-{SEED}\0{a["answer_id"]}'.encode()))[:8]
    provenance.update({str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in (dev_a, dev_c)})
    for n, a in enumerate(dev, 1):
        public.append(project(a, dev_cases[a['case_id']], f'P{n:02d}', 'practice'))
    for n, (a, c, j) in enumerate(records, 1):
        ident = f'B{n:03d}'
        public.append(project(a, c, ident, 'main'))
        private.append({'item_id': ident, 'answer_id': a['answer_id'], 'answer_sha256': a['answer_sha256'], 'answer_record_sha256': a['record_sha256'],
                        'case_id': c['id'], 'case_sha256': a['case_sha256'], 'family_id': c.get('family_id') or c['id'],
                        'condition_id': a['condition_id'], 'split': c['split'], 'challenge_type': c.get('challenge_type'),
                        'judge': j['judge'] if j['error'] is None else None, 'judge_error': j['error'], 'judgment_id': j['judgment_id'], 'judgment_record_sha256': j['record_sha256']})
    packet = {'schema_version': 'pnu.single-answer-blind-packet.v1', 'seed': SEED, 'items': public,
              'sample': {'core': 53, 'challenge': 9, 'valid_judge_pairs': 60, 'judge_errors': 2, 'practice_excluded': 8, 'planned_missing_provider_error': 1},
              'aggregate_results_previously_disclosed': True}
    data = canonical(packet)
    BASE.mkdir(mode=0o700, parents=True)
    publish(BASE / 'packet.json', data)
    publish(BASE / 'private-map.json', canonical({'packet_sha256': sha(data), 'records': private, 'source_sha256': provenance,
                                                'plan_sha256': sha((EVAL / 'execution-plan-v3.json').read_bytes()),
                                                'method': '1 developer; fixed run1; first-confirmed labels; condition names and item Judge hidden; aggregates previously disclosed'}))
    print({'packet': str(BASE / 'packet.json'), 'sha256': sha(data), 'practice': 8, 'main': 62, 'provider_calls': 0})

if __name__ == '__main__':
    main()
