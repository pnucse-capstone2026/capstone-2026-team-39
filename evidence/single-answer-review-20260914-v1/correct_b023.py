"""Append-only transcription of the user's explicit B023 correction, not regrading.

Never writes SQLite, an original receipt, answer, Judge artifact, or old analysis.
The resulting effective labels are derived data, not a new first-confirmation receipt.
"""
from __future__ import annotations
import copy
import sqlite3
from analyze import build_pairs, metrics, summarize
from common import BASE, HERE, ROOT, canonical, publish, read, require, sha, utc

ORIGINAL = BASE / 'analysis/revision-000342-v1'
OUTPUT = BASE / 'corrections/b023-user-confirmed-v1'
SUMMARY_SHA = 'd194450bef089fb9b4cb59b15c12f6b2fe00c0b2aa2125a81c10e7fd517d1c1a'
RECEIPT_SHA = '1a7afe3670ba13a3425909d5293a83a99c2cabe5b208a6344bd5a0dcf3e88fe6'
CORRECTION_ID = 'pnu.single-reviewer.b023-user-confirmed.v1'


def apply_b023(state):
    """Pure, narrow correction. Caller must preserve the original state."""
    labels = state['labels']
    require(len({row['item_id'] for row in labels}) == len(labels), 'duplicate_label_id')
    require(all(row.get('confirmed_at') for row in labels), 'original_review_incomplete')
    target = [row for row in labels if row['item_id'] == 'B023']
    require(len(target) == 1, 'B023_missing')
    require(type(target[0]['score']) is int and target[0]['score'] == 2
            and target[0]['grounded_fully_correct'] is False, 'unexpected_B023_original')
    derived = copy.deepcopy(state)
    next(row for row in derived['labels'] if row['item_id'] == 'B023')['grounded_fully_correct'] = True
    return derived


def main():
    require(not OUTPUT.exists(), 'correction_output_already_exists')
    require(sha((ORIGINAL / 'summary.json').read_bytes()) == SUMMARY_SHA, 'original_summary_changed')
    inventory = read(ORIGINAL / 'inventory.json')
    require(set(inventory) == {'summary.json', 'human-labels.json', 'pairs.csv', 'report.md'}, 'unexpected_inventory')
    protected = {ORIGINAL / name: digest for name, digest in inventory.items()}
    protected[ORIGINAL / 'inventory.json'] = sha((ORIGINAL / 'inventory.json').read_bytes())
    original = read(ORIGINAL / 'summary.json')
    receipt = original['human_receipt']
    require(receipt == read(ORIGINAL / 'human-labels.json'), 'receipt_file_mismatch')
    require(receipt['revision'] == 342 and receipt['sha256'] == RECEIPT_SHA
            and sha(canonical(receipt['data'])) == RECEIPT_SHA, 'original_receipt_mismatch')
    db = sqlite3.connect((BASE / 'state/review.sqlite3').as_uri() + '?mode=ro', uri=True)
    try:
        stored = db.execute('SELECT data, sha FROM revisions WHERE revision=342').fetchone()
        require(stored is not None and stored[1] == RECEIPT_SHA
                and sha(stored[0]) == RECEIPT_SHA, 'original_database_receipt_mismatch')
    finally:
        db.close()
    packet = read(BASE / 'packet.json')
    mapping = read(BASE / 'private-map.json')
    protected[BASE / 'packet.json'] = original['packet_sha256']
    protected[BASE / 'private-map.json'] = original['private_map_sha256']
    require(mapping['packet_sha256'] == original['packet_sha256'], 'mapping_packet_mismatch')
    protected.update({ROOT / path: digest for path, digest in original['source_sha256'].items()})
    protected.update({ROOT / path: digest for path, digest in original['analysis_source_sha256'].items()})
    for path, digest in protected.items():
        require(sha(path.read_bytes()) == digest, 'protected_hash_mismatch:' + str(path))

    initial_state = receipt['data']
    derived = apply_b023(initial_state)
    before_pairs, before_errors = build_pairs(packet, mapping, initial_state)
    after_pairs, after_errors = build_pairs(packet, mapping, derived)
    require(before_errors == after_errors and len(after_pairs) == 60, 'pair_population_changed')
    changed = [a['item_id'] for a, b in zip(initial_state['labels'], derived['labels']) if a != b]
    require(changed == ['B023'], 'unexpected_changed_items')
    target = next(p for p in before_pairs if p['item_id'] == 'B023')
    for split, key in [('holdout-core', 'core_primary_unexposed'), ('holdout-challenge', 'challenge_primary_unexposed')]:
        recalculated = metrics([p for p in before_pairs if p['split'] == split and not p['human']['individual_judge_exposed']])
        require(all(original['metrics'][key][k] == v for k, v in recalculated.items()), 'original_metrics_mismatch')
    after = summarize(after_pairs, repeats=10000)
    correction = {
        'schema_version': 'pnu.single-reviewer-user-correction.v1', 'correction_id': CORRECTION_ID,
        'recorded_at': utc(), 'item_id': 'B023', 'reviewer_id': initial_state['reviewer_id'],
        'provenance': 'Assistant transcription of explicit user confirmation in this conversation; not an AI-assigned judgment.',
        'confirmation_question': '23번은 원래 ‘2점·GFC 통과’로 입력하려던 게 맞아?',
        'user_confirmation': 'ㅇㅇ',
        'reason': 'User reported GFC pass button failure. Stale row.score after automatic save was reproduced, and the user confirmed their intended GFC pass.',
        'original_summary_sha256': SUMMARY_SHA, 'original_receipt_sha256': RECEIPT_SHA,
        'original_revision': 342, 'answer_id': target['answer_id'], 'answer_sha256': target['answer_sha256'],
        'before': {'score': 2, 'grounded_fully_correct': False},
        'after': {'score': 2, 'grounded_fully_correct': True},
        'changed_fields': ['grounded_fully_correct'], 'aggregate_agreement_disclosed_before_confirmation': True,
        'individual_judge_exposed_in_original_label': target['human']['individual_judge_exposed'],
        'individual_B023_judge_disclosed_by_assistant_before_confirmation': False,
        'original_confirmation_is_not_rewritten': True,
    }
    result = {
        'schema_version': 'pnu.single-reviewer-corrected-agreement.v1',
        'status': 'POST_SUBMISSION_USER_CORRECTION_ANALYZED', 'created_at': utc(),
        'correction_id': CORRECTION_ID, 'analysis_kind': 'post_submission_correction_not_first_blind_judgment',
        'original_summary_path': str((ORIGINAL / 'summary.json').relative_to(ROOT)),
        'original_summary_sha256': SUMMARY_SHA, 'original_receipt_sha256': RECEIPT_SHA,
        'before_metrics': original['metrics'], 'after_metrics': after, 'changed_item_ids': changed,
        'valid_pairs': len(after_pairs), 'excluded_judge_errors': after_errors,
        'external_provider_calls': 0, 'sqlite_writes': 0, 'other_labels_changed': 0,
        'old_two_reviewer_gate_passed': False,
        'limits': [*original['limits'],
                   'Only B023 is corrected from explicit user intent, after aggregate agreement disclosure.',
                   'Potential UI exposure does not justify changing other labels; B004 and B036 remain untouched.',
                   'Correction is reported alongside the original, never as an original blind first judgment.',
                   'Service generation, Judge outcomes and the final three-run performance comparison are unchanged.'],
        'source_sha256': {str(path.relative_to(ROOT)): digest for path, digest in protected.items()},
        'correction_source_sha256': sha((HERE / 'correct_b023.py').read_bytes()),
        'fixed_ui_sha256': sha((HERE / 'app.js').read_bytes()),
    }
    effective = {
        'schema_version': 'pnu.single-reviewer-derived-labels.v1',
        'original_receipt_path': str((ORIGINAL / 'human-labels.json').relative_to(ROOT)),
        'original_receipt_sha256': RECEIPT_SHA, 'correction_id': CORRECTION_ID,
        'is_original_receipt': False,
        'confirmation_timestamps_refer_to_original_labels_not_correction': True,
        'effective_labels': derived['labels'],
    }
    lines = ['# B023 사용자 확인 정정 및 일치도 재집계', '',
             '최초 제출값은 그대로 보존했다. GFC 버튼 오류를 보고한 사용자가 대화에서 B023의 의도한 값을',
             '2점·GFC 통과로 명시 확인하여, 별도 사후 정정 분석에만 반영했다. 다른 69개 라벨은 변경하지 않았다.', '',
             '| 항목 | 최초 저장본 | B023 정정본 |', '|---|---:|---:|']
    for key, title in [('core_primary_unexposed', '일반 문항'), ('challenge_primary_unexposed', '예외·공격 문항')]:
        before, now = original['metrics'][key], after[key]
        lines.append(f'| {title} GFC 일치율 | {before["gfc_agreement_count"]}/{before["n"]} ({before["raw_agreement"]:.1%}) | {now["gfc_agreement_count"]}/{now["n"]} ({now["raw_agreement"]:.1%}) |')
        lines.append(f'| {title} Cohen κ | {before["cohen_kappa"]:.3f} | {now["cohen_kappa"]:.3f} |')
        lines.append(f'| {title} 0~2점 일치율 | {before["score_exact_count"]}/{before["n"]} ({before["score_exact_agreement"]:.1%}) | {now["score_exact_count"]}/{now["n"]} ({now["score_exact_agreement"]:.1%}) |')
    ci = after['core_primary_unexposed']['confidence_intervals']['intervals']['raw_agreement']
    lines += ['', f'정정 후 일반 문항 GFC 일치율 95% CI: [{ci["low"]:.1%}, {ci["high"]:.1%}].',
              '기존 방식과 동일한 family-cluster bootstrap 10,000회, seed 20260914를 사용했다.', '',
              '이는 서비스 정답률이나 성능 개선 수치가 아니라 사람 1인과 Judge의 판정 일치도다.',
              '기존 생성·Judge·3회 최종 성능 비교 결과는 변경되지 않는다. Judge 형식 오류 2개는 비교에서 제외하며, 연습 8개도 제외한다.', '',
              '정정은 전체 일치도 공개 뒤 확인되었으므로 최초 비공개 판정과 구분해야 한다.',
              'B004/B036은 같은 UI 오류 노출 가능성 후보일 뿐 오입력이 확인되지 않아 그대로 두었다.',
              '검수 사이트와 원본 내려받기에는 최초 판정이 유지된다. 이 정정본을 원본과 함께 사용해야 한다.', '',
              f'최초 결과: `{(ORIGINAL / "summary.json").relative_to(ROOT)}`',
              f'최초 결과 SHA-256: `{SUMMARY_SHA}`',
              f'정정 결과 SHA-256: `{sha(canonical(result))}`']
    # Do not publish a derived result if any frozen input moved during computation.
    for path, digest in protected.items():
        require(sha(path.read_bytes()) == digest, 'protected_hash_changed_during_analysis')
    OUTPUT.mkdir(mode=0o700, parents=True)
    publish(OUTPUT / 'correction.json', canonical(correction))
    publish(OUTPUT / 'corrected-labels.json', canonical(effective))
    publish(OUTPUT / 'summary.json', canonical(result))
    publish(OUTPUT / 'report.md', ('\n'.join(lines) + '\n').encode())
    publish(OUTPUT / 'inventory.json', canonical({p.name: sha(p.read_bytes()) for p in sorted(OUTPUT.iterdir()) if p.is_file()}))
    print({'path': str(OUTPUT), 'summary_sha256': sha((OUTPUT / 'summary.json').read_bytes()),
           'before_core_gfc': original['metrics']['core_primary_unexposed']['raw_agreement'],
           'after_core_gfc': after['core_primary_unexposed']['raw_agreement'], 'originals_unchanged': True})


if __name__ == '__main__':
    main()
