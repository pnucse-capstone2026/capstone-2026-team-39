"""Build an exclusive, hash-pinned handoff bundle. Never apply its patch."""
import ast
import difflib
import hashlib
import json
from pathlib import Path

work = Path('/Users/leehyunwoo/project/pnu-docs-chatbot')
scratch = Path('/private/tmp/pnu-security-fix-20260908.GRuL54')
base = Path('/private/tmp/pnu-security-review-20260908.HzXJKS/repo')
fixed = scratch / 'repo'
bundle = work / 'evidence/security-layer-fixes-20260908-v1'
artifacts = work / 'processed/eval/preflight-20260908/security-layer-fixes-v1'
changed = [
    'scripts/rag/context_fields.py', 'scripts/rag/generators.py',
    'scripts/rag/security/context_gate.py', 'scripts/rag/security/output_gate.py',
    'tests/test_security_regressions.py',
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def declaration(path, name):
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.dump(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.dump(node.value)
    raise AssertionError(name)


assert not bundle.exists(), 'refusing to overwrite a handoff bundle'
for relative, name in [('scripts/rag/security/context_gate.py', '_RULES'), ('scripts/rag/generators.py', 'SYSTEM_INSTRUCTION')]:
    assert declaration(base / relative, name) == declaration(fixed / relative, name)
assert sha(base / 'scripts/search_api.py') == sha(fixed / 'scripts/search_api.py')
assert sha(base / 'scripts/bm25_search.py') == sha(fixed / 'scripts/bm25_search.py')
assert sha(base / 'scripts/judge_service_answers.py') == sha(fixed / 'scripts/judge_service_answers.py')
before = json.loads((artifacts / 'baseline-components.json').read_text())
after = json.loads((artifacts / 'fixed-components.json').read_text())
assert before['shadow_output_only_replay']['raw_render_sequence_sha256'] == after['shadow_output_only_replay']['raw_render_sequence_sha256']
assert after['metamorphic']['matched'] == after['metamorphic']['n']
tests = json.loads((artifacts / 'fixed-full-ready.json').read_text())
assert tests['successful'] and tests['tests'] == 828 and tests['skipped'] == 6
patch, hashes = [], {}
for relative in changed:
    old = (base / relative).read_text() if (base / relative).exists() else ''
    new = (fixed / relative).read_text()
    ast.parse(new)
    assert all(not line.rstrip('\n\r').endswith((' ', '\t')) for line in new.splitlines(keepends=True)), relative
    hashes[relative] = {'before': sha(base / relative) if (base / relative).exists() else None, 'after': sha(fixed / relative)}
    patch.append(f'diff --git a/{relative} b/{relative}\n')
    if not (base / relative).exists():
        patch.append('new file mode 100644\n')
    patch.extend(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True), fromfile=f'a/{relative}' if (base / relative).exists() else '/dev/null', tofile=f'b/{relative}'))
outputs = {'changes.patch': ''.join(patch)}
for name in ('run_offline_tests.py', 'compare_components.py', 'offline.sb', 'package_fix.py'):
    outputs[name] = (scratch / name).read_text()
manifest = {
    'base_commit': 'c3e581bc708f2811ce73dec4a278657a72f7fff1',
    'status': 'offline_fix_verified_not_merged',
    'fixed_copy': str(fixed), 'changed_files': hashes,
    'declarations_unchanged': ['context_gate._RULES', 'generators.SYSTEM_INSTRUCTION'],
    'service_files_unchanged': ['scripts/search_api.py', 'scripts/bm25_search.py', 'scripts/judge_service_answers.py'],
    'external_llm_calls': 0, 'official_scores_changed': False,
    'input_sha256': after['input_sha256'],
    'local_test_fixture_sha256': {
        'processed/index/pnu-20260725-curated-cascade-v5-allow-suspect.sqlite': sha(work / 'processed/index/pnu-20260725-curated-cascade-v5-allow-suspect.sqlite'),
        'processed/curation/20260725-pnu-curated-v5/curated-manifest.jsonl': sha(work / 'processed/curation/20260725-pnu-curated-v5/curated-manifest.jsonl'),
    },
    'artifacts': {str(path.relative_to(work)): sha(path) for path in sorted(artifacts.iterdir()) if path.is_file()},
    'bundle_outputs': {name: hashlib.sha256(value.encode()).hexdigest() for name, value in outputs.items()},
}
bundle.mkdir(parents=True)
for name, text in outputs.items():
    with (bundle / name).open('x') as stream:
        stream.write(text)
with (bundle / 'manifest.json').open('x') as stream:
    json.dump(manifest, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
print(json.dumps({'bundle': str(bundle), 'manifest_sha256': sha(bundle / 'manifest.json'), 'patch_sha256': sha(bundle / 'changes.patch'), 'changed_files': hashes}, indent=2))
