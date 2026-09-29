"""Offline-only code snapshots and score-blind normal-pilot input preparation."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

WORK = Path('/Users/leehyunwoo/project/pnu-docs-chatbot')
CLONE = Path('/private/tmp/pnu-security-review-20260908.HzXJKS/repo')
SNAPSHOTS = Path('/private/tmp/pnu-security-pilot-20260908.fa9QFA')
OUT = WORK / 'processed/eval/preflight-20260908/security-pilot-preparation-v1'
COLLECTOR_ROOT = Path('/private/tmp/pnu-security-collector-20260908.SGmlEr/repo')
OLD = '5f8230329196a6f007c78098642d0e658720fe30'
MERGED = 'c3e581bc708f2811ce73dec4a278657a72f7fff1'
CASE_SHA = '0906e6c8de40040915e668e6cf61639306657e6f94f41e5bcdf62197a6d90754'
SEED = 'security-normal-pilot-v1:20260908'
CONDITIONS = ('c1-pre-security', 'c1-sec-merged', 'c1-sec-fixed')
RUNTIME_ENV = {
    'PYTHONDONTWRITEBYTECODE': '1',
    'RAG_GENERATION_MODE': 'gemini', 'RAG_EVAL_TRACE': '1',
    'RAG_GEMINI_MODEL': 'gemini-3.5-flash-lite',
    'RAG_GEMINI_FALLBACK_MODELS': 'gemini-3.5-flash-lite',
    'RAG_GEMINI_BASE_URL': 'https://generativelanguage.googleapis.com/v1beta',
    'RAG_GENERATION_MAX_CONTEXT_CHARS': '24000',
    'RAG_GENERATION_MAX_OUTPUT_TOKENS': '900', 'RAG_GEMINI_MAX_OUTPUT_TOKENS': '900',
    'RAG_GENERATION_DEADLINE_SECONDS': '150', 'RAG_GEMINI_TIMEOUT_SECONDS': '120',
    'RAG_MAX_CONCURRENT_GENERATIONS': '1',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def select_normal(cases):
    strata = {}
    for case in cases:
        if case.get('split') == 'shadow-core' and case.get('shadow_bucket') in {'simple', 'multi'}:
            key = (case['category'], case['shadow_bucket'])
            strata.setdefault(key, []).append(case)
    if len(strata) != 14:
        raise ValueError('expected seven categories by two difficulty buckets')
    return [min(strata[key], key=lambda row: digest((SEED + ':' + row['id']).encode()))
            for key in sorted(strata)]


def code_identity(root):
    files = {str(path.relative_to(root)): digest(path.read_bytes())
             for path in sorted(root.rglob('*')) if path.is_file()}
    return {'files_sha256': files, 'snapshot_sha256': digest(canonical(files))}


def checked_bytes(path, expected):
    data = path.read_bytes()
    if digest(data) != expected:
        raise ValueError(f'input fingerprint mismatch: {path}')
    return data


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)


def materialize(commit, target):
    if target.exists():
        raise ValueError(f'snapshot already exists: {target}')
    listing = subprocess.run(['git', '-C', str(CLONE), 'ls-tree', '-r', '--name-only', commit, 'scripts'],
                             check=True, capture_output=True, text=True).stdout.splitlines()
    allowed = [name for name in listing if name.endswith('.py')]
    allowed += ['config/pnu-crawl-scope.json', 'config/pnu-service-dev-source-manifest.json', 'requirements.txt']
    archive = subprocess.run(['git', '-C', str(CLONE), 'archive', commit, *allowed],
                             check=True, capture_output=True).stdout
    target.mkdir(parents=True)
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            if member.isdir():
                continue
            if not member.isfile() or member.name not in allowed or '..' in Path(member.name).parts:
                raise ValueError('unexpected archive member')
            stream = tar.extractfile(member)
            assert stream is not None
            write_new(target / member.name, stream.read())
            seen.add(member.name)
    if seen != set(allowed):
        raise ValueError('archive omitted an allowed file')


def main():
    if OUT.exists():
        raise SystemExit('refusing to overwrite preparation artifacts')
    cases_path = WORK / 'config/pnu-service-shadow60-v1.jsonl'
    cases = [json.loads(line) for line in checked_bytes(cases_path, CASE_SHA).splitlines() if line.strip()]
    selected = select_normal(cases)
    collector_manifest = json.loads((WORK / 'evidence/security-eval-collector-20260908-v1/manifest.json').read_bytes())
    collector_rel = 'scripts/evaluate_security_service_answers.py'
    collector = checked_bytes(COLLECTOR_ROOT / collector_rel, collector_manifest['new_files_sha256'][collector_rel])
    fixed_relatives = ('scripts/rag/context_fields.py', 'scripts/rag/generators.py',
                       'scripts/rag/security/context_gate.py', 'scripts/rag/security/output_gate.py')
    version_pins = {}
    for ordinal, condition in enumerate(CONDITIONS):
        root = SNAPSHOTS / condition
        commit = OLD if ordinal == 0 else MERGED
        materialize(commit, root)
        if ordinal == 2:
            # Replace only the fresh snapshot's files with the already approved,
            # SHA-checked implementation. Never modify the preserved source roots.
            for relative in fixed_relatives:
                data = checked_bytes(COLLECTOR_ROOT / relative,
                                     collector_manifest['tested_code_sha256'][relative])
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('wb') as stream:
                    stream.write(data)
        write_new(root / collector_rel, collector)
        version_pins[condition] = {
            'root': str(root), 'base_commit': commit,
            'expected_security_mode': 'absent' if ordinal == 0 else 'enforce',
            'context_security_env': None if ordinal == 0 else 'enforce',
            'candidate_port': 18931 + ordinal,
            'runtime_identity_verified': False,
            **code_identity(root),
        }
    input_pins = {}
    for path in (
        WORK / 'processed/index/pnu-20260725-curated-cascade-v5-allow-suspect.sqlite',
        WORK / 'processed/curation/20260725-pnu-curated-v5/curated-manifest.jsonl',
        cases_path,
    ):
        input_pins[str(path)] = digest(path.read_bytes())
    normal_path = OUT / 'normal14.cases.jsonl'
    normal_data = b''.join(json.dumps(row, ensure_ascii=False).encode() + b'\n' for row in selected)
    write_new(normal_path, normal_data)
    shared = {
        'provider': 'frontier', 'model': 'gemini-3.5-flash-lite',
        'institution': None, 'parser_profile': 'cascade', 'retrieval_mode': 'bm25',
        'context_k': 8, 'context_chunks_per_document': 2, 'retrieval_tuning': True,
        'corpus_revision': '20260725-pnu-curated-cascade-v5:cascade:5d1b5fee3d2eafa1a7c77d33',
        'max_context_chars': 24000, 'max_output_tokens': 900, 'sampling_parameters': [],
        'collector': {'max_attempts': 3, 'retry_backoff': 3, 'sleep': 3, 'timeout': 180},
        'judge': {'model': 'gemini-3.1-flash-lite', 'max_output_tokens': 1600,
                  'retries': 6, 'sleep': 3, 'timeout': 180,
                  'config_sha256': 'c165059daa5903b74d9b7fe260856419b697721ecc1e869a701aa0c85dc18fce'},
    }
    condition_order = sorted(CONDITIONS, key=lambda value: digest((SEED + ':condition:' + value).encode()))
    schedule = []
    for condition in condition_order:
        for case in selected:
            schedule.append({'ordinal': len(schedule) + 1, 'condition_id': condition,
                             'generation_run_id': 'pilot-run1', 'case_id': case['id'],
                             'case_sha256': digest(canonical(case))})
    metadata = {
        'schema_version': 'pnu.security-normal-pilot-preparation.v1',
        'status': 'normal_inputs_prepared_attack_design_pending', 'external_llm_calls': 0,
        'api_execution_authorized': False, 'server_started': False, 'holdout_read': False,
        'selection_seed': SEED, 'selection_rule': 'one SHA-ranked case per category/bucket, no scores used',
        'selected_case_ids': [case['id'] for case in selected],
        'selected_metadata': [{'id': case['id'], 'category': case['category'], 'bucket': case['shadow_bucket'],
                               'family_id': case['family_id']} for case in selected],
        'normal_cases_path': str(normal_path), 'normal_cases_sha256': digest(normal_data),
        'common_controls': shared, 'nonsecret_environment': RUNTIME_ENV,
        'environment_policy': 'clean child environment; no inherited RAG_/GEMINI_/PYTHONPATH controls; key injection only after approval',
        'source_input_sha256': input_pins, 'code_conditions': version_pins,
        'normal_schedule': schedule, 'normal_schedule_sha256': digest(canonical(schedule)),
        'normal_call_budget': {'generation_slots': 42, 'judge_slots': 42, 'logical_slots': 84,
                               'generation_http_ceiling': 126, 'judge_http_ceiling': 252,
                               'total_provider_http_ceiling': 378},
        'budget_scope': 'normal pilot only; excludes attack experiment and future core42 x3 experiment',
        'attack_design': 'awaiting user choice; no attack inputs/index created',
        'runtime_attestation': 'pending: owned PID/listener, process root, imported module paths, full snapshot and health pins',
        'model_availability': 'not probed; names reused from saved artifacts, no availability guarantee',
        'comparison_limit': 'condition-block pilot; descriptive only, no significance or latency superiority claim',
        'preparer_sha256': digest(Path(__file__).read_bytes()),
    }
    write_new(OUT / 'preparation.json', json.dumps(metadata, indent=2, ensure_ascii=False).encode() + b'\n')
    print(json.dumps({'out': str(OUT), 'cases': len(selected), 'condition_order': condition_order,
                      'normal_budget': metadata['normal_call_budget'],
                      'snapshot_sha256': {key: value['snapshot_sha256'] for key, value in version_pins.items()},
                      'preparation_sha256': digest((OUT / 'preparation.json').read_bytes())}, indent=2))


if __name__ == '__main__':
    main()
