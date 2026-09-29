"""Apply only chat-approved review metadata corrections; retain all original labels."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'processed/eval/final-single-reviewer-20260914-v1'
REVIEW_ROOT = ROOT / 'processed/reviews/single-reviewer-gold-20260914-v1'
ORIGINAL = ROOT / 'config/pnu-service-answer-holdout-v2.draft.jsonl'
INTAKE = REVIEW_ROOT / 'intake-r181-v1.json'
APPROVAL = '응, 그리고 빨리 최종평가 좀 해줘 보고서 내야해'
sys.path.insert(0, str(ROOT / 'scripts'))
from holdout_gold import collect_holdout_validation_errors


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def publish(path, raw):
    path = Path(path)
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink_output')
    with path.open('xb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def corrected_cases(cases, exported, proposal):
    """No question text is interpreted; exact human-note-linked metadata edits only."""
    require(digest(canonical(exported['data'])) == exported['sha256'], 'review_hash')
    require(digest(canonical(exported['check_policy'])) == exported['check_policy_sha256'], 'policy_hash')
    rows = exported['data']['reviews']
    require(len(rows) == len(cases) and [r['case_id'] for r in rows] == [c['id'] for c in cases], 'review_case_binding')
    changes = {r['case_id']: r for r in proposal['proposed_changes']}
    require(len(changes) == len(proposal['proposed_changes']), 'duplicate_change')
    require(set(changes) == {r['case_id'] for r in rows if r['decision'] == 'REVISE'}, 'unresolved_review')
    require(bool(exported['data']['reviewer_id'].strip()), 'missing_reviewer')
    out, dispositions = [], []
    for original, row in zip(cases, rows):
        case = copy.deepcopy(original)
        specs = exported['check_policy']['cases'][case['id']]['checks']
        require(not any(s['status'] == 'blocked' for s in specs.values()), 'blocked_review_material')
        require(all(row['checks'][k] for k, s in specs.items() if k != 'label_verified' and s['status'] == 'required'), 'unconfirmed_source_gold_answerability')
        change = changes.get(case['id'])
        if change:
            require(row['decision'] == 'REVISE' and row['notes'].strip(), 'correction_not_reviewed')
            require(change['human_reported_subject_status'] in row['notes'], 'subject_not_in_human_note')
            require(case['role'] == change['current_role'], 'original_role_mismatch')
            require(change['proposed_role'] == 'pnu-student', 'unapproved_new_role')
            require(change['role_change'] == (case['role'] != change['proposed_role']), 'role_change_flag')
            case['role'] = change['proposed_role']
            require('review_subject_status' not in case, 'annotation_already_exists')
            case['review_subject_status'] = change['human_reported_subject_status']
            disposition = 'USER_APPROVED_METADATA_CORRECTION'
        else:
            require(row['decision'] == 'PASS' and row['checks']['label_verified'], 'unconfirmed_unmodified_case')
            disposition = 'ORIGINAL_HUMAN_PASS'
        back = copy.deepcopy(case)
        back.pop('review_subject_status', None)
        back['role'] = original['role']
        require(back == original, 'unapproved_content_change')
        dispositions.append({'case_id': case['id'], 'original_decision': row['decision'],
            'original_case_sha256': digest(canonical(original)), 'corrected_case_sha256': digest(canonical(case)),
            'disposition': disposition, 'source_review_revision': exported['revision']})
        out.append(case)
    return out, dispositions


def main():
    proposal = json.loads(INTAKE.read_text())
    require(file_sha(INTAKE) == 'c390bcbddfe4e1c12c1e54a32631ce164a482b545afe1e472a47fc1fde46bf30', 'intake_changed')
    downloaded = [p for p in Path('/Users/leehyunwoo/Downloads').iterdir()
                  if unicodedata.normalize('NFC', p.name) == proposal['source_filename_nfc']]
    require(len(downloaded) == 1, 'review_download_missing')
    raw_review = downloaded[0].read_bytes()
    require(digest(raw_review) == proposal['source_file_sha256'], 'download_changed')
    exported = json.loads(raw_review)
    require(exported['revision'] == 181 and exported['sha256'] == proposal['state_sha256'], 'review_revision_changed')
    with sqlite3.connect((REVIEW_ROOT / 'reviews.sqlite').as_uri() + '?mode=ro', uri=True) as db:
        revision, data, state_sha = db.execute('SELECT revision,data,sha FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()
    require(revision == 181 and state_sha == exported['sha256'] and json.loads(data) == exported['data'], 'newer_review_requires_reconciliation')
    original_bytes = ORIGINAL.read_bytes()
    require(digest(original_bytes) == proposal['cases_sha256'], 'original_cases_changed')
    cases = [json.loads(line) for line in original_bytes.splitlines() if line.strip()]
    updated, dispositions = corrected_cases(cases, exported, proposal)
    errors = collect_holdout_validation_errors(updated)
    require(not errors, 'corrected_holdout_schema_errors:' + str(len(errors)))
    require(len(updated) == 36 and sum(d['disposition'] == 'USER_APPROVED_METADATA_CORRECTION' for d in dispositions) == 13, 'correction_counts')
    target = BASE / 'preparation-v1'
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    revised = b''.join(canonical(c) + b'\n' for c in updated)
    publish(target / 'reviewed-cases-v1.jsonl', revised)
    publish(target / 'source-review-r181.json', raw_review)
    publish(target / 'approved-changes.json', canonical(proposal['proposed_changes']) + b'\n')
    approval = {'schema_version': 'pnu.single-reviewer-chat-approval.v1', 'approved': True,
        'source': 'user_message_in_current_conversation', 'date': '2026-09-14', 'verbatim': APPROVAL,
        'scope': 'Apply the six proposed role corrections and seven same-role subject-status clarifications; proceed with final evaluation.',
        'prior_proposal_sha256': file_sha(INTAKE), 'source_review_file_sha256': digest(raw_review),
        'new_independent_human_review': False, 'original_review_mutated': False}
    publish(target / 'user-approval.json', canonical(approval) + b'\n')
    signoff = {'schema_version': 'pnu.single-reviewer-corrected-gold.v1',
        'protocol_id': 'pnu.final-eval.single-reviewer.v1', 'status': 'APPROVED_AFTER_METADATA_CORRECTION',
        'reviewer_count': 1, 'reviewer_id': exported['data']['reviewer_id'], 'reviewer_role': 'developer',
        'original_cases_sha256': digest(original_bytes), 'corrected_cases_sha256': digest(revised),
        'source_review_revision': 181, 'source_review_state_sha256': exported['sha256'],
        'source_review_file_sha256': digest(raw_review), 'user_approval_sha256': file_sha(target / 'user-approval.json'),
        'original_review_locked': False, 'original_decisions_retained': True,
        'two_reviewer_gate': 'NOT_APPLICABLE_SINGLE_REVIEWER_AMENDMENT', 'dispositions': dispositions,
        'subject_status_is_display_annotation_only': True, 'schema_validation_errors': 0,
        'label_confirmation_source': 'Original human PASS or explicit chat approval of human-note-derived correction; not a synthesized PASS.'}
    publish(target / 'single-reviewer-signoff.json', canonical(signoff) + b'\n')
    inventory = {p.name: file_sha(p) for p in target.iterdir()}
    publish(target / 'inventory.json', canonical(inventory) + b'\n')
    require(ORIGINAL.read_bytes() == original_bytes and downloaded[0].read_bytes() == raw_review, 'original_modified')
    print(json.dumps({'status': 'CORRECTED_DATA_PREPARED', 'path': str(target), 'cases_sha256': digest(revised),
        'cases': len(updated), 'role_changes': 6, 'subject_annotations': 13, 'schema_errors': 0,
        'original_review_unchanged': True, 'external_calls': 0}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
