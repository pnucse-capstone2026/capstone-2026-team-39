"""Offline answer calibration: separate from frozen service and prior gold reviews."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = ROOT / 'processed/reviews/single-answer-20260914-v1'
EVAL = ROOT / 'processed/eval/final-single-reviewer-20260914-v1'
SCHEMA = 'pnu.single-reviewer-answer.v1'

def require(ok, message):
    if not ok:
        raise ValueError(message)

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def utc():
    return datetime.now(timezone.utc).isoformat()

def read(path):
    return json.loads(Path(path).read_bytes())

def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]

def publish(path, data):
    """Immutable output, fsync and atomic no-clobber link; identical replay is safe."""
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink_not_allowed')
    if path.exists():
        require(path.read_bytes() == data, 'existing_output_mismatch')
        return
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            require(path.read_bytes() == data, 'concurrent_output_mismatch')
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.unlink(temporary)

def blank_label(item):
    return {'item_id': item['item_id'], 'score': None, 'grounded_fully_correct': None,
            'uncertain': False, 'uncertain_reason': '', 'notes': '',
            'individual_judge_exposed': False, 'correct_abstention': None,
            'injection_obedience': None, 'confirmed_at': None, 'confirmed_revision': None}

def blank_state(packet, pin):
    return {'schema_version': SCHEMA, 'packet_sha256': pin, 'reviewer_id': '',
            'reviewer_role': 'developer', 'label_kind': 'single_reviewer',
            'aggregate_results_previously_disclosed': True,
            'labels': [blank_label(i) for i in packet['items']]}

def validate(data, template, packet, previous=None):
    require(type(data) is dict and set(data) == set(template), 'invalid_state_fields')
    for key in set(template) - {'labels', 'reviewer_id'}:
        require(data[key] == template[key], 'state_binding_mismatch')
    require(type(data['reviewer_id']) is str and len(data['reviewer_id']) <= 120, 'invalid_reviewer')
    require(type(data['labels']) is list and len(data['labels']) == len(template['labels']), 'invalid_count')
    if previous and any(x['confirmed_at'] for x in previous['labels']):
        require(data['reviewer_id'] == previous['reviewer_id'], 'reviewer_locked')
    for index, (row, base, item) in enumerate(zip(data['labels'], template['labels'], packet['items'])):
        require(type(row) is dict and set(row) == set(base), 'invalid_label_fields')
        require(row['item_id'] == base['item_id'], 'item_binding_mismatch')
        require(row['score'] is None or type(row['score']) is int and row['score'] in (0, 1, 2), 'invalid_score')
        for key in ('grounded_fully_correct', 'correct_abstention', 'injection_obedience'):
            require(row[key] is None or type(row[key]) is bool, 'invalid_boolean')
        for key in ('uncertain', 'individual_judge_exposed'):
            require(type(row[key]) is bool, 'invalid_boolean')
        for key in ('notes', 'uncertain_reason'):
            require(type(row[key]) is str and len(row[key]) <= 10000, 'invalid_text')
        require(row['grounded_fully_correct'] is not True or row['score'] == 2, 'GFC_requires_score_2')
        for key in ('correct_abstention', 'injection_obedience'):
            require(item['applicable'][key] or row[key] is None, 'not_applicable_field_must_be_null')
        if previous:
            old = previous['labels'][index]
            require(not old['individual_judge_exposed'] or row['individual_judge_exposed'], 'exposure_cannot_be_erased')
            if old['confirmed_at']:
                require(row == old, 'first_confirmed_label_locked')
            else:
                require(row['confirmed_at'] is None and row['confirmed_revision'] is None, 'server_only_confirmation')

def validate_confirmation(data, index, packet):
    row = data['labels'][index]
    require(bool(data['reviewer_id'].strip()), 'reviewer_id_required')
    require(row['score'] is not None and row['grounded_fully_correct'] is not None, 'score_and_GFC_required')
    require(not row['uncertain'] or row['uncertain_reason'].strip(), 'uncertain_reason_required')
    for key, applicable in packet['items'][index]['applicable'].items():
        require(not applicable or type(row[key]) is bool, key + '_required')
    if packet['items'][index]['phase'] == 'main':
        require(all(r['confirmed_at'] for r, i in zip(data['labels'], packet['items']) if i['phase'] == 'practice'), 'complete_8_practice_first')
