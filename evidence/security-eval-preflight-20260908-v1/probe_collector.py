"""Characterize the frozen collector, offline; passing tests confirm gaps, not fixes."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
MERGED = Path('/private/tmp/pnu-security-review-20260908.HzXJKS/repo')
FIXED = Path('/private/tmp/pnu-security-fix-20260908.GRuL54/repo')


def offline_guard(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'subprocess.Popen'}:
        raise RuntimeError('network and child processes disabled for this probe')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0])).resolve()
        if path.name == '.env' or (
            'holdout' in path.name.lower()
            and path.suffix in {'.json', '.jsonl', '.md', '.html'}
        ):
            raise RuntimeError('protected evaluation input disabled')


sys.addaudithook(offline_guard)
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location(
    'security_preflight_collector', ROOT / 'scripts/evaluate_service_answers.py'
)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


OBSERVATIONS = []


class CollectorPreflight(unittest.TestCase):
    def test_collector_is_identical_in_all_three_code_roots(self):
        values = [sha(root / 'scripts/evaluate_service_answers.py')
                  for root in (ROOT, MERGED, FIXED)]
        self.assertEqual(len(set(values)), 1)

    def test_search_api_fingerprint_cannot_distinguish_security_fix(self):
        self.assertEqual(sha(MERGED / 'scripts/search_api.py'),
                         sha(FIXED / 'scripts/search_api.py'))
        for name in ('context_gate.py', 'output_gate.py'):
            self.assertNotEqual(sha(MERGED / 'scripts/rag/security' / name),
                                sha(FIXED / 'scripts/rag/security' / name))

    def collect_synthetic(self, decision):
        health = {
            'ready': True,
            'parser_profiles': [{
                'id': 'cascade', 'ready': True, 'corpus_revision': 'synthetic',
                'retrieval_modes': [{'id': 'bm25', 'ready': True}],
            }],
            'service_config': {
                'retrieval_tuning': True, 'context_chunks_per_document': 2,
                'evaluation_trace_enabled': True,
            },
        }
        response = {
            'answer': '합성 답변', 'institution': None,
            'parser_profile': 'cascade', 'retrieval_mode': 'bm25',
            'generation': {'requested': 'extractive', 'used': 'extractive', 'model': None},
            'results': [], 'postprocessing': {},
            'retrieval': {'security_gate': {'mode': 'enforce', 'excluded': 0}},
            'evaluation_trace': {
                'schema_version': 1, 'generation_input': None,
                'retrieval_stages': {'final_contexts': []},
                'raw_draft': '합성 초안', 'timing_ms': {'e2e': 1.0},
            },
        }
        if decision is not None:
            response['security'] = {
                'context_gate': response['retrieval']['security_gate'],
                'output_gate': {'decision': decision, 'invalid_citations': int(decision == 'abstain')},
            }
        else:
            response['retrieval'] = {}
        with tempfile.TemporaryDirectory(prefix='pnu-collector-probe-') as directory:
            base = Path(directory)
            cases = base / 'synthetic-cases.jsonl'
            cases.write_text(json.dumps({'id': 'synthetic-1', 'query': '합성 질문'}) + '\n')
            out = base / 'synthetic.answers.jsonl'
            argv = [
                '--cases', str(cases), '--out', str(out), '--experiment-id', 'offline-probe',
                '--condition-id', 'synthetic', '--generation-run-id', 'run1',
                '--provider', 'extractive', '--parser-profile', 'cascade',
                '--retrieval-mode', 'bm25', '--allow-unpinned', '--eval-trace',
                '--max-attempts', '1', '--sleep', '0',
            ]
            with mock.patch.object(collector, 'call_health', return_value=health), \
                 mock.patch.object(collector, 'call_chat', return_value=response):
                collector.main(argv)
            records = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['answer'], response['answer'])
        self.assertEqual(record['evaluation_trace'], response['evaluation_trace'])
        self.assertEqual(record['retrieval'], response['retrieval'])
        self.assertNotIn('security', record)
        OBSERVATIONS.append({
            'probe': 'synthetic_response_projection', 'response_output_decision': decision,
            'saved_top_level_security': 'security' in record,
            'input_gate_summary_preserved': 'security_gate' in record['retrieval'],
            'raw_draft_and_timing_preserved': record['evaluation_trace'] == response['evaluation_trace'],
        })

    def test_answer_security_summary_is_dropped(self):
        self.collect_synthetic('answer')

    def test_abstain_security_summary_is_dropped(self):
        self.collect_synthetic('abstain')

    def test_pre_security_response_still_collects_without_security_field(self):
        self.collect_synthetic(None)

    def test_no_context_frontier_response_is_classified_as_provider_fallback(self):
        response = {
            'institution': None, 'parser_profile': 'cascade', 'retrieval_mode': 'bm25',
            'results': [], 'answer': '검증된 안전 컨텍스트에서 답변의 인용 근거를 확인하지 못했습니다.',
            'generation': {
                'requested': 'frontier', 'used': 'none', 'model': None,
                'fallback_reason': 'no_results', 'attempts': [],
            },
            'security': {
                'context_gate': {'status': 'blocked', 'evaluated': 1, 'excluded': 1},
                'output_gate': {'decision': 'abstain'},
            },
        }
        with self.assertRaisesRegex(RuntimeError, 'generation provider fallback') as caught:
            collector.validate_response_controls(
                response, provider='frontier', model='synthetic-model',
                parser_profile='cascade', retrieval_mode='bm25',
                expected_corpus_revision=None,
            )
        OBSERVATIONS.append({
            'probe': 'synthetic_all_contexts_excluded',
            'classification': str(caught.exception),
            'live_server_executed': False,
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    log_path = args.out.with_suffix('.log')
    if args.out.exists() or log_path.exists():
        raise SystemExit('refusing to overwrite outputs')
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(CollectorPreflight)
        result = unittest.TextTestRunner(stream=capture, verbosity=2).run(suite)
    paths = [ROOT / 'scripts/evaluate_service_answers.py', ROOT / 'scripts/service_eval_artifacts.py',
             ROOT / 'scripts/judge_service_answers.py', Path(__file__).resolve()]
    for root in (MERGED, FIXED):
        paths.extend(root / rel for rel in (
            'scripts/search_api.py', 'scripts/evaluate_service_answers.py',
            'scripts/rag/security/context_gate.py', 'scripts/rag/security/output_gate.py',
        ))
    summary = {
        'scope': 'offline characterization; NOT a passing adoption gate',
        'tests': result.testsRun, 'skipped': len(result.skipped),
        'failures': len(result.failures), 'errors': len(result.errors),
        'external_llm_calls': 0, 'real_holdout_read': False,
        'observations': OBSERVATIONS,
        'source_sha256': {str(path): sha(path) for path in paths},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x') as stream:
        stream.write(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    with log_path.open('x') as stream:
        stream.write(capture.getvalue())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
