"""Offline recovery of byte-identical snapshots and an UNAPPROVED live proposal.

Reads pinned Git objects/patches and run metadata, not secrets or holdout data.
Never starts a server, creates a live call ledger, or performs provider calls.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_DIR = ROOT / 'evidence/security-execution-bridge-20260908-v1'
sys.path.insert(0, str(BRIDGE_DIR))
import execution_bridge as bridge

PREPARATION = ROOT / 'processed/eval/preflight-20260908/security-pilot-preparation-v1/preparation.json'
BASE_POLICY = ROOT / 'processed/eval/preflight-20260908/security-runtime-guard-v1/verification-v1/policy.json'
ATTACK = ROOT / 'processed/eval/preflight-20260908/security-attack-preparation-v2/manifest.json'
FIX_PATCH = ROOT / 'evidence/security-layer-fixes-20260908-v1/changes.patch'
COLLECTOR_PATCH = ROOT / 'evidence/security-eval-collector-20260908-v1/changes.patch'
PINS = {
    PREPARATION: '5cddcf2a9cc69f9ee702c078db0d0fe3b74aae3f66f3919a45f8177c10bc66ca',
    BASE_POLICY: '3c36f78b2b5e73e213998e30a4fd0a91b63adb810eaab51fdf5c680f793c48bb',
    ATTACK: 'a1b39cb042e1c930a48268973c34f48826cbcdcb00932f1010ca79bb06445eb5',
    FIX_PATCH: 'a84da3a9646b63e36e6e20da9a70d5c5c67abf26f4b48db850088f5d84dcabcb',
    COLLECTOR_PATCH: '649b00c5b04241d96db28f38ccd0a6e9847ed664c9f0542483d0e172210736db',
    BRIDGE_DIR / 'execution_bridge.py': '93da28ecd67822c63532fa2c1c7df07a412677ca3454dac1196a7e587d81f7d0',
}


def offline_audit(event, args):
    if event.startswith(('socket.', 'urllib.')):
        raise bridge.GuardError('offline_preparation_network_disabled')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if 'holdout' in str(path).lower() and path.suffix == '.pyc':
            raise FileNotFoundError('protected_bytecode_not_read')
        if path.name.startswith('.env') or ('holdout' in str(path).lower() and path.suffix != '.py'):
            raise bridge.GuardError('protected_data_disabled')


def safe_relative(name):
    p = PurePosixPath(name)
    bridge.require(not p.is_absolute() and '..' not in p.parts and p.as_posix() == name,
                   'unsafe_relative_path')
    bridge.require(not p.name.startswith('.env') and ('holdout' not in name.lower() or p.suffix == '.py'),
                   'protected_snapshot_path')
    bridge.require((name.startswith('scripts/') and p.suffix == '.py') or name in (
        'config/pnu-crawl-scope.json', 'config/pnu-service-dev-source-manifest.json', 'requirements.txt'),
        'non_snapshot_file')
    return name


def patch_sections(raw):
    """Select only source sections; no patch command, fuzzy matching or path writes."""
    sections, current = {}, None
    for line in raw.decode('utf-8').splitlines(keepends=True):
        if line.startswith('diff --git '):
            match = re.fullmatch(r'diff --git a/(\S+) b/(\S+)\n', line)
            bridge.require(match and match[1] == match[2], 'unsupported_patch_header')
            name = match[1]
            # Test sections are not part of the pinned serving snapshot.
            current = None if name.startswith('tests/') else safe_relative(name)
            if current is not None:
                bridge.require(current not in sections, 'duplicate_patch_section')
                sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def apply_exact(original, lines):
    """Apply a pinned UTF-8 unified diff in memory, checking all hunk coordinates."""
    source, output, cursor, i, hunks = original.decode('utf-8').splitlines(keepends=True), [], 0, 0, 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith('@@ '):
            bridge.require(hunks == 0, 'unexpected_patch_trailer')
            i += 1
            continue
        match = re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n', line)
        bridge.require(match is not None, 'invalid_patch_hunk')
        old_start, old_n, new_start, new_n = (int(match[1]), int(match[2] or 1), int(match[3]), int(match[4] or 1))
        start = old_start if old_n == 0 else old_start - 1
        bridge.require(cursor <= start <= len(source), 'patch_position_mismatch')
        output.extend(source[cursor:start])
        cursor = start
        bridge.require(len(output) == (new_start if new_n == 0 else new_start - 1), 'patch_new_position_mismatch')
        old_seen = new_seen = 0
        i += 1
        while i < len(lines) and not lines[i].startswith('@@ '):
            item = lines[i]
            bridge.require(item[:1] in (' ', '+', '-'), 'unsupported_patch_line')
            if item[0] in ' -':
                bridge.require(cursor < len(source) and source[cursor] == item[1:], 'patch_context_mismatch')
                cursor += 1
                old_seen += 1
            if item[0] in ' +':
                output.append(item[1:])
                new_seen += 1
            i += 1
        bridge.require((old_seen, new_seen) == (old_n, new_n), 'patch_count_mismatch')
        hunks += 1
    bridge.require(hunks > 0, 'missing_patch_hunk')
    return ''.join(output + source[cursor:]).encode('utf-8')


def git_sources(commit, expected_paths, added_paths):
    bridge.require(re.fullmatch('[0-9a-f]{40}', commit) is not None, 'invalid_commit')
    paths = sorted(set(expected_paths) - set(added_paths))
    for name in paths:
        safe_relative(name)
    result = subprocess.run(['git', '-C', str(ROOT), 'archive', commit, *paths],
                            check=True, capture_output=True, timeout=30)
    blobs = {}
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        for member in archive:
            if member.isdir():
                continue
            bridge.require(member.isfile() and member.name in paths and member.name not in blobs,
                           'unexpected_archive_member')
            safe_relative(member.name)
            with archive.extractfile(member) as stream:
                blobs[member.name] = stream.read()
    bridge.require(set(blobs) == set(paths), 'incomplete_git_archive')
    return blobs


def verify_blobs(blobs, condition):
    expected = condition['files_sha256']
    bridge.require(set(blobs) == set(expected), 'snapshot_file_set_mismatch')
    actual = {}
    for name, data in blobs.items():
        safe_relative(name)
        actual[name] = bridge.digest(data)
        bridge.require(actual[name] == expected[name], 'reconstruction_sha_mismatch')
    bridge.require(bridge.digest(bridge.canonical(actual)) == condition['snapshot_sha256'], 'snapshot_sha_mismatch')


def materialize(root, blobs, condition):
    """Create missing snapshot only, or verify and reuse an existing exact one."""
    verify_blobs(blobs, condition)
    root = Path(root)
    bridge.require(not any(p.is_symlink() for p in (root, *root.parents)), 'symlink_snapshot_target')
    if root.exists():
        bridge.guard.verify_snapshot({**condition, 'root': str(root)})
        return 'verified_existing_no_write'
    root.mkdir(parents=True, exist_ok=False)
    for name, data in sorted(blobs.items()):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        bridge.write_new(target, data)
    bridge.guard.verify_snapshot({**condition, 'root': str(root)})
    return 'materialized_byte_identical'


def make_proposal(base, attack, run_root):
    proposal = copy.deepcopy(base)
    fixtures = {f['case_id']: f for f in attack['fixtures']}
    normal_manifests = [p for p in base['input_sha256'] if p.endswith('/curated-manifest.jsonl')]
    bridge.require(len(normal_manifests) == 1, 'normal_manifest_ambiguous')
    for slot in proposal['slots']:
        if slot['phase'] == 'normal14':
            pin = base['input_sha256'][normal_manifests[0]]
        else:
            pin = fixtures[slot['case_id']]['documents_sha256']
        slot['source_manifest_sha256'] = pin
        db_path = Path(slot['index']['path']).resolve(strict=True)
        with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute("SELECT value FROM index_meta WHERE key='source_manifest_sha256'").fetchone()
        bridge.require(row is not None and row[0] == pin, 'index_source_manifest_mismatch')
    bridge.require(len(proposal['slots']) == 204 and sum(s['attempt_cap'] for s in proposal['slots']) == 918,
                   'unexpected_pilot_budget')
    proposal.update({
        'api_execution_authorized': False, 'live_runner_ready': False,
        'status': 'UNAPPROVED_LIVE_EXECUTION_PROPOSAL', 'intended_run_root': str(Path(run_root).resolve()),
        'authorization': 'Fresh explicit user API approval required; this file does not grant it.',
        'limitation': 'Live credential injection, provider launcher and end-to-end offline validation still required.',
        'resume_policy': 'Same fixed DB/policy; sealed outputs skip; ANY unfinished execution requires explicit reconciliation. No automatic regeneration/refund.',
    })
    return proposal


def inspect_run(run_root):
    """Read-only restart assessment; never opens artifacts as JSON or seals them."""
    root = Path(run_root).resolve(strict=True)
    bridge.require(not any(p.is_symlink() for p in (Path(run_root), *Path(run_root).parents)), 'symlink_run')
    bridge.file_sha(root / 'run.json')
    bridge.file_sha(root / 'policy.json')
    metadata = bridge.strict_json((root / 'run.json').read_bytes())
    policy = bridge.strict_json((root / 'policy.json').read_bytes())
    policy_sha = bridge.digest(bridge.canonical(policy))
    bridge.require(metadata['root'] == str(root) and metadata['policy_sha'] == policy_sha, 'run_identity_mismatch')
    path = root / 'provider-attempts.sqlite'
    bridge.require(not path.is_symlink() and path.stat().st_ino == metadata['ledger_inode'], 'ledger_replaced')
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        stored = db.execute('SELECT sha,json FROM policy WHERE id=1').fetchone()
        bridge.require(stored['sha'] == policy_sha and stored['json'] == bridge.canonical(policy).decode(), 'ledger_policy_changed')
        slots = {r['id']: dict(r) for r in db.execute('SELECT * FROM slots')}
        bridge.require({k: v['config'] for k,v in slots.items()} == {
            s['slot_id']: bridge.canonical(s).decode() for s in policy['slots']}, 'ledger_slots_changed')
        executions = {r['slot']: dict(r) for r in db.execute('SELECT * FROM executions')}
        attempts = [dict(r) for r in db.execute('SELECT slot,state FROM attempts')]
    bridge.require(set(executions) <= set(slots), 'unknown_execution')
    bridge.require(all(a['slot'] in executions for a in attempts), 'attempt_without_execution')
    report = []
    for slot in policy['slots']:
        sid = slot['slot_id']
        execution = executions.get(sid)
        suffix = '.answers.jsonl' if slot['role'] == 'generation' else '.judgments.jsonl'
        artifact = root / 'results' / (bridge.digest(sid.encode()) + suffix)
        attempts_for_slot = [a for a in attempts if a['slot'] == sid]
        bridge.require(len(attempts_for_slot) <= slot['attempt_cap'], 'slot_budget_exceeded')
        sha = bridge.file_sha(artifact) if artifact.exists() or artifact.is_symlink() else None
        sealed = slots[sid]['sealed_sha']
        if sealed:
            bridge.require(not any(a['state'] == 'reserved' for a in attempts_for_slot), 'sealed_unresolved_attempt')
            bridge.require(sha == sealed and execution and execution['binding'], 'sealed_artifact_or_execution_changed')
            bridge.require(execution['artifact_sha'] in (None, sealed), 'execution_seal_mismatch')
            state = 'SEALED_REVALIDATE_THEN_SKIP' if execution['artifact_sha'] else 'SEALED_BOOKKEEPING_RECONCILE'
        elif any(a['state'] == 'reserved' for a in attempts_for_slot):
            state = 'BLOCKED_UNCERTAIN_PROVIDER_ATTEMPT'
        elif execution:
            state = 'BLOCKED_SAVED_UNSEALED' if sha else 'BLOCKED_STARTED_WITHOUT_ARTIFACT'
        else:
            bridge.require(sha is None, 'unregistered_artifact')
            state = 'NOT_STARTED'
        report.append({'slot_id': sid, 'state': state, 'provider_attempts': len(attempts_for_slot)})
    bridge.require(len(attempts) <= policy['total_cap'], 'total_budget_exceeded')
    blocked = any(r['state'] not in ('NOT_STARTED', 'SEALED_REVALIDATE_THEN_SKIP') for r in report)
    return {'run_root': str(root), 'status': 'RECONCILIATION_REQUIRED' if blocked else 'NO_UNFINISHED_EXECUTION',
            'policy_sha256': policy_sha, 'read_only': True, 'external_llm_calls': 0,
            'provider_attempts': len(attempts), 'remaining_ceiling': policy['total_cap']-len(attempts),
            'semantic_artifact_validation': 'Not performed here; pinned bridge validator required before live resume.',
            'slots': report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--restore-missing-original-paths', action='store_true')
    parser.add_argument('--inspect-run', action='append', type=Path, default=[])
    args = parser.parse_args()
    sys.addaudithook(offline_audit)
    for path, pin in PINS.items():
        bridge.guard.verify_file(path, pin)
    preparation = bridge.strict_json(PREPARATION.read_bytes())
    base = bridge.strict_json(BASE_POLICY.read_bytes())
    attack = bridge.strict_json(ATTACK.read_bytes())
    for path, pin in base['input_sha256'].items():
        bridge.guard.verify_file(path, pin)
    fixes, collector = patch_sections(FIX_PATCH.read_bytes()), patch_sections(COLLECTOR_PATCH.read_bytes())
    bridge.require(set(collector) == {'scripts/evaluate_security_service_answers.py'}, 'unexpected_collector_patch')
    args.output.mkdir(parents=True, exist_ok=False)
    recovered = {}
    for name, condition in preparation['code_conditions'].items():
        changes = {**(fixes if name == 'c1-sec-fixed' else {}), **collector}
        added = [p for p, lines in changes.items() if '--- /dev/null\n' in lines]
        blobs = git_sources(condition['base_commit'], condition['files_sha256'], added)
        for relative, lines in changes.items():
            blobs[relative] = apply_exact(blobs.get(relative, b''), lines)
        verify_blobs(blobs, condition)
        durable = args.output.resolve() / 'snapshots' / name
        durable_action = materialize(durable, blobs, condition)
        original_action = 'not_requested'
        if args.restore_missing_original_paths:
            original_action = materialize(Path(condition['root']), blobs, condition)
        recovered[name] = {'durable_root': str(durable), 'durable_action': durable_action,
                           'original_root': condition['root'], 'original_action': original_action,
                           'files': len(blobs), 'snapshot_sha256': condition['snapshot_sha256']}
    proposal = make_proposal(base, attack, ROOT / 'processed/eval/preflight-20260909/security-pilot-live-v1/run')
    bridge.write_new(args.output / 'execution-proposal.json', bridge.canonical(proposal) + b'\n')
    approval = {
        'status': 'APPROVAL_REQUIRED_NOT_GRANTED', 'approved': False,
        'proposal_sha256': bridge.file_sha(args.output / 'execution-proposal.json'),
        'experiment_id': proposal['experiment_id'], 'run_root': proposal['intended_run_root'],
        'normal_questions': 14, 'attack_variants': 20, 'attack_independent_questions': 1, 'conditions': 3,
        'generation_slots': 102, 'judge_slots': 102, 'total_provider_attempt_cap': 918,
        'generation_model': proposal['controls']['model'], 'judge_model': proposal['controls']['judge']['model'],
        'model_fallback': 'none', 'free_tier_guaranteed': False,
        'transmission': 'Google Gemini receives normal/fictional questions, retrieved evidence, generated answers and Judge gold/rubric. No actual holdout or secrets in prompts.',
        'credential_policy': 'Explicitly approved key source only; no .env reads during preparation; never print/store keys or pass them in argv.',
        'remaining_gates': ['explicit user approval', 'live launcher and key delivery implementation/verification'],
    }
    bridge.write_new(args.output / 'approval-request.json', bridge.canonical(approval) + b'\n')
    assessments = [inspect_run(path) for path in args.inspect_run]
    bridge.write_new(args.output / 'resume-assessments.json', bridge.canonical(assessments) + b'\n')
    for path, pin in base['input_sha256'].items():
        bridge.guard.verify_file(path, pin)
    result = {'status': 'SNAPSHOTS_RECOVERED_PROPOSAL_UNAPPROVED', 'external_llm_calls': 0,
              'servers_started': 0, 'actual_holdout_read': False, 'live_ledger_created': False,
              'verified_input_files': len(base['input_sha256']), 'snapshots': recovered,
              'recovery_tool_sha256': bridge.file_sha(__file__),
              'artifact_sha256': {p.relative_to(args.output).as_posix(): bridge.file_sha(p)
                                  for p in sorted(args.output.rglob('*')) if p.is_file()}}
    bridge.write_new(args.output / 'preparation.json', json.dumps(result, ensure_ascii=False, indent=2).encode()+b'\n')
    print(json.dumps({k:result[k] for k in ('status','external_llm_calls','servers_started','verified_input_files','snapshots')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
