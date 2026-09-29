"""Package the separately tested collector without modifying the original repo code."""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
from pathlib import Path
import sys

WORK = Path('/Users/leehyunwoo/project/pnu-docs-chatbot')
SCRATCH = Path('/private/tmp/pnu-security-collector-20260908.SGmlEr')
REPO = SCRATCH / 'repo'
OUT = Path(__file__).resolve().parent
RESULTS = WORK / 'processed/eval/preflight-20260908/security-collector-v1'
FILES = ['scripts/evaluate_security_service_answers.py', 'tests/test_evaluate_security_service_answers.py']
EXPECTED_ORIGINAL = {
    'scripts/evaluate_service_answers.py': '24c1b03cb03d291b4562764f5523cd481db6c992885f2c071d2bf247d8c6445d',
    'scripts/search_api.py': '9a8865db361d6d3a044d26fc57698f56840540ed573a5f37bb50ef684c1cf4a5',
    'scripts/bm25_search.py': '6c474dc83a4f27ab3172de5b839f731e9954206b1d3cb1748dc08f3848cb1ad7',
    'scripts/rag/generators.py': '67cccfd602b808929c226fcbb659635495e50e05103592664020c6c3a725166b',
    'scripts/judge_service_answers.py': '95b653d336058bc8eb8829c64472a3ad94162bc3a08d87400f0391e7b06e7414',
}


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_new(name, text):
    with (OUT / name).open('x', encoding='utf-8') as stream:
        stream.write(text)


def main():
    names = ['changes.patch', 'collector-delta.diff', 'offline.sb', 'manifest.json']
    if any((OUT / name).exists() for name in names):
        raise SystemExit('refusing to overwrite package artifacts')
    originals = {relative: sha(WORK / relative) for relative in EXPECTED_ORIGINAL}
    if originals != EXPECTED_ORIGINAL:
        raise SystemExit('original frozen input changed')
    final = json.loads((RESULTS / 'full-final.json').read_text())
    if not final['successful'] or final['failures'] or final['errors']:
        raise SystemExit('final offline suite did not pass')
    patch = []
    for relative in FILES:
        content = (REPO / relative).read_text()
        ast.parse(content, filename=relative)
        if any(line.rstrip() != line for line in content.splitlines()):
            raise SystemExit(f'trailing whitespace: {relative}')
        patch.append(f'diff --git a/{relative} b/{relative}\nnew file mode 100644\n')
        patch.extend(difflib.unified_diff([], content.splitlines(keepends=True),
                                        fromfile='/dev/null', tofile=f'b/{relative}'))
    write_new('changes.patch', ''.join(patch))
    write_new('collector-delta.diff', ''.join(difflib.unified_diff(
        (WORK / 'scripts/evaluate_service_answers.py').read_text().splitlines(keepends=True),
        (REPO / FILES[0]).read_text().splitlines(keepends=True),
        fromfile='frozen/evaluate_service_answers.py', tofile='separate/evaluate_security_service_answers.py',
    )))
    write_new('offline.sb', (SCRATCH / 'offline.sb').read_text())
    code_pins = {str(path.relative_to(REPO)): sha(path)
                 for folder in ('scripts', 'tests')
                 for path in sorted((REPO / folder).rglob('*.py'))}
    preserved_names = [
        'before.json', 'before.log', 'after-regression.json', 'after-regression.log',
        'expanded-tests.json', 'expanded-tests.log', 'full-tests.json', 'full-tests.log',
        'full-final.json', 'full-final.log',
    ]
    manifest = {
        'schema_version': 'pnu.security-eval-collector-package.v1',
        'date': '2026-09-08', 'status': 'offline_verified_not_integrated',
        'python_version': sys.version.split()[0], 'external_llm_calls': 0,
        'actual_holdout_read': False, 'runtime_server_attestation_complete': False,
        'base_security_commit': 'c3e581bc708f2811ce73dec4a278657a72f7fff1',
        'security_fix_manifest_sha256': sha(WORK / 'evidence/security-layer-fixes-20260908-v1/manifest.json'),
        'collector_base_sha256': originals['scripts/evaluate_service_answers.py'],
        'original_unchanged_sha256': originals,
        'new_files_sha256': {relative: sha(REPO / relative) for relative in FILES},
        'tested_root': str(REPO), 'tested_code_sha256': code_pins,
        'full_suite': final,
        'test_artifacts_sha256': {str(RESULTS / name): sha(RESULTS / name) for name in preserved_names},
        'test_runner_sha256': sha(WORK / 'evidence/security-layer-fixes-20260908-v1/run_offline_tests.py'),
        'readonly_fixture_sha256': {
            str(path): sha(path) for path in (
                WORK / 'processed/index/pnu-20260725-curated-cascade-v5-allow-suspect.sqlite',
                WORK / 'processed/curation/20260725-pnu-curated-v5/curated-manifest.jsonl',
            )
        },
        'package_sha256': {name: sha(OUT / name) for name in names if name != 'manifest.json'},
        'packager_sha256': sha(Path(__file__)),
    }
    write_new('manifest.json', json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'manifest_sha256': sha(OUT / 'manifest.json'),
                      'new_files_sha256': manifest['new_files_sha256'],
                      'code_files_pinned': len(code_pins),
                      'tests': final['tests'], 'skipped': final['skipped']}, indent=2))


if __name__ == '__main__':
    main()
