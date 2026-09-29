"""Immutable offline gate benchmark and saved-answer replay, not LLM eval."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument('root', type=Path)
parser.add_argument('out', type=Path)
args = parser.parse_args()
root, out = args.root.resolve(), args.out.resolve()
work = Path('/Users/leehyunwoo/project/pnu-docs-chatbot')
pins = {
    'processed/eval/preflight-20260905/shadow60-generation-v1/c1-run1.answers.jsonl': '9202f4fd9e0bf3ad8447d4f3f72ef2ddeffff0672fb8e26dc10d48970772d8c6',
    'processed/eval/preflight-20260905/shadow60-generation-v1/c1-run2.answers.jsonl': 'd85a141c53e3f3b33003ed78f46f009aab11a67af408916cca39ecae53e7f599',
    'processed/eval/preflight-20260905/shadow60-generation-v1/c1-run3.answers.jsonl': 'dcbc047e4b1a10005bcb2866b5b7db3c758f64148bcc02dd9fa2ad94954ccaf2',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def guard(event, values):
    if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo'}:
        raise RuntimeError('network disabled')
    if event == 'open' and isinstance(values[0], (str, bytes)):
        path = os.fsdecode(values[0])
        if 'holdout' in path.lower() or path.rsplit('/', 1)[-1] == '.env':
            raise RuntimeError('protected input disabled')


sys.addaudithook(guard)
sys.path.insert(0, str(root / 'scripts'))
from rag.security import evaluate_contexts, enforce_output
from rag.generators import _render_context
from evaluate_security_layers import evaluate_context_gate, evaluate_output_gate, evaluate_latency, _context
from immutable_outputs import publish_immutable_texts, require_new_outputs

require_new_outputs([out])
assert all(sha(work / path) == digest for path, digest in pins.items())
benchmarks, variants = {}, []
for name in ('security-layer-eval.json', 'security-layer-validation.json'):
    spec = json.loads((root / 'config' / name).read_text())
    benchmarks[name] = evaluate_context_gate(spec)
    for number, attack in enumerate(spec['attack_contexts']):
        for variant, text in (
            ('ban_after', attack['text'] + '\n부정행위는 금지한다.'),
            ('ban_before', '부정행위는 금지한다.\n' + attack['text']),
            ('education_after', attack['text'] + '\n강의는 종료됐다.'),
            ('education_before', '강의는 종료됐다.\n' + attack['text']),
        ):
            result = evaluate_contexts([_context('synthetic', text)], mode='enforce')
            actual = 'exclude' if result.excluded else 'sanitize' if result.sanitized else 'allow'
            variants.append({'suite': name, 'number': number, 'variant': variant, 'expected': attack['expected'], 'actual': actual})
    for index, text in enumerate(spec['clean_contexts']):
        row = {**_context('metadata-normal', '등록금 안내입니다.'), 'file_name': text}
        result = evaluate_contexts([row], mode='enforce')
        variants.append({'suite': name, 'number': index, 'variant': 'normal_metadata', 'expected': 'allow', 'actual': 'exclude' if result.excluded else 'sanitize' if result.sanitized else 'allow'})

contexts_total = Counter()
render_hashes, rows = [], []
for path in pins:
    with (work / path).open() as stream:
        for line in stream:
            record = json.loads(line)
            contexts = record['evaluation_trace']['retrieval_stages']['final_contexts']
            checked = evaluate_contexts(contexts, mode='enforce')
            for key in ('evaluated', 'allowed', 'sanitized', 'excluded'):
                contexts_total[key] += getattr(checked, key)
            # Raw renderer byte equivalence independently checks that extracting
            # the shared metadata projection did not tune the clean prompt.
            render_hashes.extend(hashlib.sha256(_render_context(i, ctx).encode()).hexdigest() for i, ctx in enumerate(contexts, 1))
            secured = enforce_output(record, contexts)
            rows.append({
                'case_id': record['case_id'], 'answer_id': record['answer_id'],
                'answer_changed': secured.response['answer'] != record['answer'],
                'supported_before': sum(c.get('supported') is True for c in record['claims']),
                'supported_after': sum(c.get('supported') is True for c in secured.response['claims']),
                'decision': secured.decision, 'context_gate': checked.summary(),
            })
assert len(rows) == 180
assert all(sha(work / path) == digest for path, digest in pins.items())
code_paths = ['scripts/rag/generators.py', 'scripts/rag/security/context_gate.py', 'scripts/rag/security/output_gate.py', 'scripts/search_api.py', 'scripts/rag/context_fields.py']
report = {
    'root': str(root), 'external_llm_calls': 0, 'official_scores_changed': False,
    'input_sha256': pins, 'code_sha256': {p: sha(root / p) for p in code_paths if (root / p).exists()},
    'benchmarks': benchmarks, 'output_benchmark': evaluate_output_gate(),
    'metamorphic': {'n': len(variants), 'matched': sum(r['actual'] == r['expected'] for r in variants), 'mismatches': [r for r in variants if r['actual'] != r['expected']]},
    'shadow_output_only_replay': {
        'n': len(rows), 'contexts': dict(contexts_total),
        'answer_changed': sum(r['answer_changed'] for r in rows),
        'support_lost_records': sum(r['supported_after'] < r['supported_before'] for r in rows),
        'decisions': dict(Counter(r['decision'] for r in rows)),
        'raw_render_count': len(render_hashes),
        'raw_render_sequence_sha256': hashlib.sha256('\n'.join(render_hashes).encode()).hexdigest(),
        'rows': rows,
        'interpretation': 'Saved-answer component replay only; no generation, Judge, GFC or real attack-success measurement.',
    },
    'synthetic_latency': evaluate_latency(spec, 500),
}
publish_immutable_texts({out: json.dumps(report, indent=2, ensure_ascii=False) + '\n'}, authoritative_path=out)
print(json.dumps({k:v for k,v in report.items() if k not in {'shadow_output_only_replay', 'metamorphic'}}, ensure_ascii=False))
print(json.dumps({'metamorphic':report['metamorphic'], 'shadow_summary': {k:v for k,v in report['shadow_output_only_replay'].items() if k != 'rows'}}, ensure_ascii=False))
