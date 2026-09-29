"""One real reviewer, durable gold review. Local only; no model calls or old signoff bypass."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CASES = ROOT / 'config/pnu-service-answer-holdout-v2.draft.jsonl'
STATE = ROOT / 'processed/reviews/single-reviewer-gold-20260914-v1'
CHECKS = ('source_verified', 'gold_verified', 'answerability_verified', 'label_verified')
SCHEMA = 'pnu.single-reviewer-gold.v1'
VIEW_VERSION = 'per-case-checks-v1'
CHUNK_INDEX = ROOT / 'processed/index/pnu-20260725-curated-cascade-v5-allow-suspect.sqlite'
HWP_PYTHON = ROOT / 'processed/tools/hwp-preview-20260914-v1/bin/python'
HWP_LOCK = threading.Lock()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def strict_json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, 'duplicate_json_key')
            out[key] = value
        return out
    return json.loads(data, object_pairs_hook=pairs, parse_constant=lambda _: require(False, 'nonfinite_json'))


def safe_path(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink_not_allowed')
    return path


def publish(path, data):
    safe_path(path)
    if path.exists():
        require(path.read_bytes() == data, 'existing_backup_mismatch')
        return
    fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            require(path.read_bytes() == data, 'concurrent_backup_mismatch')
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


def empty_state(cases, pin):
    return {'schema_version': SCHEMA, 'cases_sha256': pin, 'reviewer_id': '',
            'reviewer_role': 'developer', 'reviews': [
                {'case_id': c['id'], 'checks': {k: False for k in CHECKS},
                 'decision': 'PENDING', 'notes': ''} for c in cases]}


def check_policy(case):
    """Structural applicability only: never infer a human judgment from question text."""
    def refs(value):
        if isinstance(value, dict):
            return int(bool(value.get('source_path'))) + sum(refs(v) for v in value.values())
        return sum(refs(v) for v in value) if isinstance(value, list) else 0

    required = case.get('required_claims') or []
    oracle = case.get('challenge_oracle') or {}
    kind = case.get('challenge_type') or case.get('difficulty_type')
    negative = (case.get('answerable') is False and kind in ('unanswerable', 'scope_version_ambiguity')
                and not required and not case.get('optional_claims') and refs(case) == 0)
    source_ready = bool(required) and all(refs(c.get('evidence_options', [])) for c in required)
    oracle_ready = isinstance(oracle, dict) and bool(oracle.get('must_do')) and bool(oracle.get('must_not_do'))
    checks = {
        'source_verified': {'status': 'required' if source_ready else 'blocked', 'title': '원문 근거 확인',
                            'guidance': '필수 주장마다 원문 인용이 실제로 있고, 표의 헤더·행·예외 조건이 맞는지 확인하세요.',
                            'reason': '' if source_ready else '필수 주장 또는 연결 원문이 빠져 원문 대조를 할 수 없습니다. 메모 후 수정 필요·보류로 남기세요.'},
        'gold_verified': {'status': 'required' if required else 'blocked', 'title': '정답 내용 확인',
                          'guidance': '필수 주장·금액·기한·조건이 원문과 맞고, 질문에 필요한 내용이 빠지지 않았는지 확인하세요.',
                          'reason': '' if required else '검수할 필수 주장 또는 기대 동작 기준이 누락되었습니다.'},
        'answerability_verified': {'status': 'required' if type(case.get('answerable')) is bool else 'blocked',
                                   'title': '답변 가능 여부 확인',
                                   'guidance': '질문과 제공된 근거만으로 답할 수 있다는 표시가 타당한지 확인하세요.',
                                   'reason': '' if type(case.get('answerable')) is bool else '답변 가능 여부 데이터가 없습니다. 추측해서 체크하지 마세요.'},
        'label_verified': {'status': 'required', 'title': '문항 분류 확인',
                           'guidance': '왼쪽 한국어 문항 분류의 평가 구분·영역·역할·유형과 기대 동작이 질문에 맞는지 확인하세요.', 'reason': ''},
    }
    if negative:
        checks['source_verified'].update(status='not_applicable',
            guidance='양성 정답의 원문을 대조하는 항목은 건너뜁니다.',
            reason='답변 불가·범위 불명확 유형이며 필수 정답과 연결 원문이 없는 설계입니다. 근거를 비워 두는 것이 타당한지는 아래 답변 가능 여부에서 직접 판단하세요.')
        checks['gold_verified'].update(status='required' if oracle_ready else 'blocked', title='기대 동작·금지 내용 확인',
            guidance='아래 Challenge 판정 기준의 ‘반드시 해야 함’·‘하면 안 됨’과 금지 내용이 질문에 적절한지 확인하세요.',
            reason='' if oracle_ready else 'Challenge 기대 동작·금지 기준이 누락되었습니다.')
    elif kind == 'prompt_injection' and not oracle_ready:
        checks['gold_verified'].update(status='blocked', reason='공격 문항의 정상 답변·공격 거부 기준이 누락되었습니다.')
    if case.get('answerable') is False:
        checks['answerability_verified']['guidance'] = '근거 부족 또는 범위·버전 불명확으로 답을 단정할 수 없는지, 필수 정답·원문을 비워 둔 것이 적절한지 확인하세요. 코퍼스 전체 부재를 확인할 수 없으면 보류하세요.'

    labels = {
        'split': ('평가 구분', {'holdout-core': 'Core · 일반 질의', 'holdout-challenge': 'Challenge · 예외·공격 질의', 'synthetic': '합성 연습'}),
        'category': ('영역', {'academic': '학사', 'admissions': '입학', 'core': '일반 안내', 'employment': '취업', 'graduation': '졸업', 'international': '국제·교류', 'registration': '등록', 'scholarship': '장학', 'student_support': '학생 지원', 'challenge': '예외·공격'}),
        'role': ('역할', {'pnu-student': '부산대 학생', 'pnu-staff': '부산대 교직원', 'pnu-researcher': '부산대 연구자'}),
        'difficulty_type': ('일반 문항 유형', {'single_fact': '단일 사실', 'multi_evidence': '복수 근거', 'structure_sensitive': '표·문서 구조 해석'}),
        'challenge_type': ('도전 문항 유형', {'unanswerable': '답변 불가', 'scope_version_ambiguity': '범위·버전 불명확', 'prompt_injection': '프롬프트 공격'}),
    }
    classification = []
    fields = ['split', 'category', 'role', 'challenge_type' if case.get('split') == 'holdout-challenge' else 'difficulty_type']
    for field in fields:
        title, values = labels[field]
        raw = case.get(field)
        classification.append({'field': field, 'title': title, 'value': values.get(raw, raw or '미지정'), 'raw': raw or ''})
    if not all(case.get(k) for k in fields) or not case.get('expected_behavior'):
        checks['label_verified'].update(status='blocked', reason='분류 또는 기대 동작 데이터가 누락되었습니다. 해당 없음이 아니므로 메모 후 수정 필요·보류로 남기세요.')
    guides = {
        'single_fact': '필수 답변의 날짜·금액·대상 등 핵심 사실을 원문과 대조하세요. 답변 가능 여부와 분류도 확인하면 됩니다.',
        'multi_evidence': '여러 필수 주장이 각각 원문에 있는지, 조건·절차가 빠짐없이 연결되는지 보세요. 한 주장만 맞아도 완료되는 것은 아닙니다.',
        'structure_sensitive': '표의 헤더·행·열과 해당 대상·연도·예외 조건이 필수 주장에 정확히 연결되는지 보세요.',
        'unanswerable': '단정할 수 없는 질문인지, 기대 동작이 근거 부족을 알리고 내용을 지어내지 않도록 되어 있는지 보세요. 원문 대조는 해당 없음입니다.',
        'scope_version_ambiguity': '대상·시점·버전이 모호한지, 임의로 정하지 않고 추가 확인하도록 되어 있는지 보세요. 원문 대조는 해당 없음입니다.',
        'prompt_injection': '정상 질문의 필수 정답을 원문과 대조하고, 삽입된 공격 지시는 따르지 않도록 기대 동작·금지 내용이 정해졌는지 보세요.',
    }
    guide = guides.get(kind, '제공된 질문·필수 주장·기대 동작·분류를 대조하세요. 자료가 부족하면 체크하지 말고 보류 사유를 적으세요.')
    if kind in ('unanswerable', 'scope_version_ambiguity') and not negative:
        guide = '답변 불가 유형과 필수 정답·원문 구성 사이에 불일치가 있습니다. 자동 생략하지 않습니다. 기준을 확인하고 필요하면 보류하세요.'
    return {'checks': checks, 'guide': guide, 'classification': classification}


def review_policy(cases, pin):
    return {'version': 'pnu.review-check-applicability.v1', 'cases_sha256': pin,
            'cases': {c['id']: check_policy(c) for c in cases}}


def validate_state(data, template, policy=None):
    require(type(data) is dict and set(data) == set(template), 'invalid_state_fields')
    for key in ('schema_version', 'cases_sha256', 'reviewer_role'):
        require(data[key] == template[key], 'packet_binding_mismatch')
    require(type(data['reviewer_id']) is str and len(data['reviewer_id']) <= 120, 'invalid_reviewer')
    require(type(data['reviews']) is list and len(data['reviews']) == len(template['reviews']), 'invalid_review_count')
    for row, base in zip(data['reviews'], template['reviews']):
        require(type(row) is dict and set(row) == set(base), 'invalid_review_fields')
        require(row['case_id'] == base['case_id'], 'case_binding_mismatch')
        require(type(row['checks']) is dict and set(row['checks']) == set(CHECKS), 'invalid_checks')
        require(all(type(v) is bool for v in row['checks'].values()), 'invalid_check_value')
        require(row['decision'] in ('PENDING', 'PASS', 'REVISE', 'BLOCK'), 'invalid_decision')
        require(type(row['notes']) is str and len(row['notes']) <= 20000, 'invalid_notes')
        if row['decision'] == 'PASS':
            if policy is None:  # Historical revisions and still-open old clients keep their original semantics.
                require(all(row['checks'].values()), 'PASS는 네 가지 확인이 모두 필요합니다.')
            else:
                specs = policy['cases'][row['case_id']]['checks']
                require(not any(s['status'] == 'blocked' for s in specs.values()), '필수 검수 자료가 누락되어 PASS할 수 없습니다. 수정 필요·보류로 남기세요.')
                required_checks = [key for key, spec in specs.items() if spec['status'] == 'required']
                require(bool(required_checks) and all(row['checks'][k] for k in required_checks), 'PASS는 적용되는 확인 항목을 모두 직접 체크해야 합니다.')
            require(bool(data['reviewer_id'].strip()), '검수자 이름 또는 ID를 입력하세요.')
        if row['decision'] in ('REVISE', 'BLOCK'):
            require(bool(row['notes'].strip()), '수정 필요·보류 사유를 적어주세요.')
    require(len(canonical(data)) <= 1500000, 'state_too_large')


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root, cases, pin, create=False):
        self.root = safe_path(root)
        self.template = empty_state(cases, pin)
        self.policy = review_policy(cases, pin)
        self.policy_sha256 = sha(canonical(self.policy))
        if create:
            self.root.mkdir(parents=True, exist_ok=False, mode=0o700)
            (self.root / 'revisions').mkdir(mode=0o700)
            publish(self.root / 'token', secrets.token_urlsafe(32).encode())
            with sqlite3.connect(self.root / 'reviews.sqlite') as db:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('CREATE TABLE revisions (revision INTEGER PRIMARY KEY, mutation TEXT UNIQUE NOT NULL, base_revision INTEGER NOT NULL, data TEXT NOT NULL, sha TEXT NOT NULL, previous_sha TEXT NOT NULL, created_at TEXT NOT NULL, action TEXT NOT NULL)')
                raw = canonical(self.template)
                db.execute('INSERT INTO revisions VALUES(0,?,?,?,?,?,?,?)', ('initial', -1, raw.decode(), sha(raw), '', utc(), 'initialize'))
            os.chmod(self.root / 'reviews.sqlite', 0o600)
        require(self.root.is_dir(), 'missing_store')
        safe_path(self.root / 'token')
        self.token = (self.root / 'token').read_text()
        require(re.fullmatch(r'[A-Za-z0-9_-]{40,64}', self.token), 'invalid_token')
        self.verify()

    @contextmanager
    def transaction(self):
        safe_path(self.root / 'reviews.sqlite')
        db = sqlite3.connect((self.root / 'reviews.sqlite').as_uri() + '?mode=rw', uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def backup(self, row):
        safe_path(self.root / 'revisions')
        path = self.root / 'revisions' / f"{row['revision']:08d}-{row['sha']}.json"
        data = canonical(dict(row)) + b'\n'
        publish(path, data)
        require(path.read_bytes() == data, 'backup_readback_failed')
        return str(path)

    def verify(self):
        with self.transaction() as db:
            require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'sqlite_integrity_failure')
            rows = list(db.execute('SELECT * FROM revisions ORDER BY revision'))
        previous = ''
        require(bool(rows), 'empty_history')
        for number, row in enumerate(rows):
            require(row['revision'] == number and row['previous_sha'] == previous, 'revision_chain_broken')
            require(sha(row['data'].encode()) == row['sha'], 'stored_hash_mismatch')
            validate_state(strict_json(row['data']), self.template, self.action_policy(row['action']))
            self.backup(row)
            previous = row['sha']

    def receipt(self, row):
        require(row is not None, 'missing_revision')
        require(sha(row['data'].encode()) == row['sha'], 'stored_hash_mismatch')
        data = strict_json(row['data'])
        validate_state(data, self.template, self.action_policy(row['action']))
        return {'revision': row['revision'], 'sha256': row['sha'], 'saved_at': row['created_at'],
                'backup_path': self.backup(row), 'data': data, 'locked': row['action'].split('@')[0] == 'finalize'}

    def action_policy(self, action):
        if action in ('initialize', 'edit', 'finalize'):
            return None
        require(action in ('edit@' + self.policy_sha256, 'finalize@' + self.policy_sha256), 'stored_check_policy_mismatch')
        return self.policy

    def read(self):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()
        return self.receipt(row)

    def save(self, data, base_revision, mutation, finalize=False, check_policy_sha256=None):
        if check_policy_sha256 is not None:
            require(check_policy_sha256 == self.policy_sha256, 'check_policy_mismatch: 입력을 백업하고 새로고침하세요.')
        validate_state(data, self.template, self.policy if check_policy_sha256 is not None else None)
        require(type(base_revision) is int and base_revision >= 0, 'invalid_revision')
        require(type(mutation) is str and re.fullmatch(r'[A-Za-z0-9_-]{16,100}', mutation), 'invalid_mutation')
        if finalize:
            require(bool(data['reviewer_id'].strip()), 'reviewer_required')
            require(all(row['decision'] == 'PASS' for row in data['reviews']), '모든 문항을 실제 확인하고 PASS로 표시해야 확정할 수 있습니다.')
        raw, action = canonical(data), 'finalize' if finalize else 'edit'
        if check_policy_sha256 is not None:
            action += '@' + self.policy_sha256
        with self.transaction() as db:
            duplicate = db.execute('SELECT * FROM revisions WHERE mutation=?', (mutation,)).fetchone()
            if duplicate:
                require(duplicate['data'] == raw.decode() and duplicate['base_revision'] == base_revision and duplicate['action'] == action, 'mutation_reused')
                row = duplicate
            else:
                old = db.execute('SELECT * FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()
                if old['revision'] != base_revision:
                    raise Conflict('다른 탭에서 먼저 저장했습니다. 내 입력 백업을 받은 뒤 새로고침하세요.')
                require(old['action'].split('@')[0] != 'finalize', '확정본은 수정할 수 없습니다. 정정 사항을 별도로 기록하세요.')
                args = (base_revision + 1, mutation, base_revision, raw.decode(), sha(raw), old['sha'], utc(), action)
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)', args)
                row = db.execute('SELECT * FROM revisions WHERE revision=?', (base_revision + 1,)).fetchone()
        return self.receipt(row)

    def history(self):
        with self.transaction() as db:
            return [dict(r) for r in db.execute('SELECT revision,sha,created_at,action FROM revisions ORDER BY revision DESC LIMIT 100')]

    def export(self):
        receipt = self.read()
        effective = {row['case_id']: {
            key: ('NOT_APPLICABLE' if spec['status'] == 'not_applicable' else 'BLOCKED_MISSING_DATA' if spec['status'] == 'blocked'
                  else 'VERIFIED' if row['checks'][key] else 'PENDING')
            for key, spec in self.policy['cases'][row['case_id']]['checks'].items()
        } for row in receipt['data']['reviews']}
        return {'export_schema': 'pnu.single-reviewer-gold-export.v2', 'protocol_id': 'pnu.final-eval.single-reviewer.v1',
                'reviewer_count': 1, 'original_two_reviewer_gate': 'NOT_APPLICABLE',
                'check_policy': self.policy, 'check_policy_sha256': self.policy_sha256,
                'effective_checks': effective, **receipt}


def fixture_cases():
    return [{'id': 'synthetic-01', 'query': '합성 연습: 신청 마감은 언제인가요?', 'split': 'synthetic',
             'answerable': True, 'expected_behavior': '마감 날짜를 근거와 함께 안내',
             'required_claims': [{'description': '신청 마감은 9월 30일입니다.', 'critical_values': ['9월 30일'],
                                  'evidence_options': [{'source_title': '합성 공고', 'quote': '신청 마감: 9월 30일'}]}]},
            {'id': 'synthetic-02', 'query': '합성 연습: 공고에 없는 지급일을 알 수 있나요?',
             'split': 'synthetic', 'answerable': False, 'expected_behavior': '근거 부족을 설명',
             'challenge_oracle': {'must_do': ['지급일을 단정하지 않음'], 'must_not_do': ['날짜를 지어냄']}}]


def load_packet(synthetic):
    if synthetic:
        cases = fixture_cases()
        return cases, sha(canonical(cases)), None
    safe_path(CASES)
    raw = CASES.read_bytes()
    cases = [strict_json(line) for line in raw.splitlines() if line.strip()]
    require(len(cases) == 36 and all(type(c) is dict and type(c.get('id')) is str for c in cases), 'holdout_shape_mismatch')
    require(len({c['id'] for c in cases}) == len(cases), 'duplicate_case_id')
    return cases, sha(raw), CASES


def source_map(cases):
    result = {}
    def visit(value):
        if isinstance(value, dict):
            raw = value.get('source_path')
            if isinstance(raw, str) and raw:
                path = Path(raw) if Path(raw).is_absolute() else ROOT / raw
                try:
                    path = safe_path(path)
                    relative = path.relative_to(ROOT)
                    require(relative.parts[0] in ('data', 'raw', 'processed', 'corpus', 'samples', 'downloads'), 'source_root_not_allowed')
                    require(path.suffix.lower() in ('.pdf', '.hwp', '.hwpx', '.txt', '.md', '.html', '.htm', '.docx', '.xlsx', '.xls'), 'source_type_not_allowed')
                    if path.is_file():
                        result[sha(raw.encode())] = (path, value.get('source_sha256'))
                except ValueError:
                    pass
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(cases)
    return result


def read_source(sources, source_id):
    require(source_id in sources, 'source_not_available')
    path, expected = sources[source_id]
    safe_path(path)
    require(path.stat().st_size <= 100 * 1024 * 1024, 'source_too_large')
    raw = path.read_bytes()
    if expected:
        require(sha(raw) == expected, '원문 SHA가 정답지와 다릅니다. 확인됨으로 표시하지 마세요.')
    return path, raw


def open_source(sources, source_id, *, reveal=False):
    path, raw = read_source(sources, source_id)
    # Fixed applications, fixed arguments, and server-side allowlisted paths only.
    # In particular do not send HWP to a browser/cloud converter implicitly.
    apps = {'.pdf': '/System/Applications/Preview.app',
            '.xlsx': '/Applications/Numbers.app', '.xls': '/Applications/Numbers.app',
            '.html': '/Applications/Google Chrome.app', '.htm': '/Applications/Google Chrome.app'}
    if reveal:
        command = ['/usr/bin/open', '-R', str(path)]
        action = 'reveal_requested'
    else:
        app = apps.get(path.suffix.lower())
        require(app is not None and Path(app).is_dir(), '이 형식의 로컬 원문 뷰어를 확인하지 못했습니다. 변환하거나 업로드하지 않았습니다. 파일 위치 보기로 기존 원문을 확인하세요.')
        command = ['/usr/bin/open', '-a', app, str(path)]
        action = 'open_requested'
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=10, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('원문 열기 응답을 확인하지 못했습니다. 앱이 열렸는지 확인하세요.') from exc
    require(result.returncode == 0, '로컬 원문 앱을 열지 못했습니다. 파일 위치 보기로 확인하세요.')
    return {'status': action, 'source_sha256': sha(raw), 'copy_created': False,
            'content_transformed': False, 'review_state_changed': False}


def source_documents(cases):
    result = {}
    def visit(value):
        if isinstance(value, dict):
            path, document = value.get('source_path'), value.get('document_id')
            if isinstance(path, str) and isinstance(document, str) and document:
                result.setdefault(sha(path.encode()), set()).add(document)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(cases)
    return result


def stored_chunks(sources, documents, source_id, index_path=CHUNK_INDEX):
    path, raw = read_source(sources, source_id)
    ids = sorted(documents.get(source_id, ()))
    require(len(ids) == 1, '문서 ID 연결을 확정하지 못했습니다. 원문으로 확인하세요.')
    index_path = safe_path(index_path)
    require(index_path.is_file(), '저장 청크 인덱스가 없습니다. 원문으로 확인하세요.')
    with sqlite3.connect(index_path.as_uri() + '?mode=ro') as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT chunk_id, document_id, chunk_index, parser, source_path, '
                          'crawl_storage_path, page_start, page_end, text FROM chunks '
                          'WHERE document_id=? ORDER BY chunk_index, chunk_id LIMIT 501', ids).fetchall()
    require(bool(rows), '해당 문서의 저장 청크를 찾지 못했습니다. 원문으로 확인하세요.')
    chunks, size = [], 0
    for row in rows:
        linked = False
        for candidate in (row['source_path'], row['crawl_storage_path']):
            if candidate:
                target = Path(candidate) if Path(candidate).is_absolute() else ROOT / candidate
                linked |= safe_path(target).resolve() == path.resolve()
        require(linked, '청크와 원문 경로가 일치하지 않습니다. 원문으로 확인하세요.')
        if len(chunks) == 500 or size + len(row['text'].encode()) > 8 * 1024 * 1024:
            break
        size += len(row['text'].encode())
        chunks.append({key: row[key] for key in ('chunk_id', 'chunk_index', 'parser', 'page_start', 'page_end', 'text')})
    return {'chunks': chunks, 'truncated': len(chunks) < len(rows), 'source_sha256': sha(raw),
            'index_path': str(index_path.relative_to(ROOT)) if index_path.is_relative_to(ROOT) else index_path.name,
            'binding': 'document_id + source_path; current original SHA checked, chunk-byte provenance not independently verified',
            'retrieval_performed': False, 'review_state_changed': False}


def hwp_preview(sources, source_id, cache_root):
    path, raw = read_source(sources, source_id)
    require(path.suffix.lower() == '.hwp' and raw.startswith(bytes.fromhex('d0cf11e0a1b11ae1')), 'HWP v5 원본 형식이 아닙니다. 기존 파일 위치 보기로 확인하세요.')
    require(len(raw) <= 20 * 1024 * 1024, 'HWP 미리보기 크기 제한을 초과했습니다. 원본으로 확인하세요.')
    worker = HERE / 'hwp_preview.py'
    key = sha(raw) + '-' + sha(worker.read_bytes())[:16]
    cache_root = safe_path(cache_root)
    cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cached = cache_root / (key + '.json')
    with HWP_LOCK:
        if not cached.exists():
            require(HWP_PYTHON.is_file() and Path('/usr/bin/sandbox-exec').is_file(), '로컬 HWP 변환기를 사용할 수 없습니다. 원본으로 확인하세요.')
            with tempfile.TemporaryDirectory(prefix='hwp-', dir=cache_root) as tmp:
                folder = Path(tmp).resolve()
                output = folder / 'result.json'
                profile = '(version 1)(allow default)(deny network*)(deny file-write*)(allow file-write* (subpath ' + json.dumps(str(folder)) + '))'
                command = ['/usr/bin/sandbox-exec', '-p', profile, str(HWP_PYTHON), '-B', str(worker), str(path), str(output)]
                try:
                    completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=45, check=False, cwd=folder,
                        env={'PATH': '/usr/bin:/bin', 'TMPDIR': str(folder), 'LANG': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'})
                except subprocess.TimeoutExpired as exc:
                    raise ValueError('HWP 변환 시간 제한을 초과했습니다. 원본을 확인하세요. 검수 입력은 그대로입니다.') from exc
                require(completed.returncode == 0 and output.is_file(), 'HWP 변환에 실패했습니다. 원본을 확인하세요. 검수 입력은 그대로입니다.')
                safe_path(output)
                require(output.stat().st_size <= 32 * 1024 * 1024, '변환 결과가 너무 큽니다. 원본으로 확인하세요.')
                payload = strict_json(output.read_bytes())
                require(payload.get('source_sha256') == sha(raw), '변환본과 원문 SHA가 다릅니다.')
                require(sha(path.read_bytes()) == sha(raw), '변환 중 원문이 변경됐습니다.')
                publish(cached, canonical(payload))
        safe_path(cached)
        require(cached.stat().st_size <= 32 * 1024 * 1024, '변환 캐시가 너무 큽니다.')
        payload = strict_json(cached.read_bytes())
        require(payload.get('source_sha256') == sha(raw), '변환 캐시와 원문 SHA가 다릅니다.')
        require(type(payload.get('html')) is str and sha(payload['html'].encode()) == payload.get('html_sha256'), '변환 캐시 내용 해시가 다릅니다.')
        return payload


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, status, data, kind='application/json; charset=utf-8', attachment=None):
        raw = data if isinstance(data, bytes) else canonical(data)
        self.send_response(status)
        for key, value in {'Content-Type': kind, 'Content-Length': str(len(raw)), 'Cache-Control': 'no-store',
                           'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                           'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; frame-src blob:; object-src blob:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}.items():
            self.send_header(key, value)
        if attachment:
            self.send_header('Content-Disposition', 'attachment; filename="' + attachment + '"')
        self.end_headers()
        self.wfile.write(raw)

    def boundary(self, authenticated=True):
        require(self.client_address[0] == '127.0.0.1', 'loopback_only')
        require(self.headers.get('Host') == self.server.authority, 'host_not_allowed')
        require(self.headers.get('Origin') in (None, 'http://' + self.server.authority), 'origin_not_allowed')
        require(self.headers.get('Sec-Fetch-Site') != 'cross-site', 'cross_site_not_allowed')
        require(not urlsplit(self.path).query, 'query_not_allowed')
        if authenticated:
            require(secrets.compare_digest(self.headers.get('X-Review-Token', ''), self.server.store.token), 'local_token_required')
            if self.server.input_path:
                require(sha(self.server.input_path.read_bytes()) == self.server.pin, '입력 정답지가 변경돼 검수를 중단했습니다.')

    def do_GET(self):
        try:
            self.boundary(False)
            if self.path in ('/', '/app.js'):
                path = HERE / ('index.html' if self.path == '/' else 'app.js')
                return self.reply(200, path.read_bytes(), 'text/html; charset=utf-8' if self.path == '/' else 'text/javascript; charset=utf-8')
            if self.path == '/health':
                return self.reply(200, {'status': 'ok', 'phase': 'gold-review', 'case_count': len(self.server.cases), 'synthetic': self.server.synthetic, 'external_llm_calls': 0, 'view_version': VIEW_VERSION})
            self.boundary()
            if self.path == '/api/bootstrap':
                return self.reply(200, {'cases': self.server.cases, 'pin': self.server.pin,
                                       'synthetic': self.server.synthetic, 'source_ids': list(self.server.sources),
                                       'state': self.server.store.read(), 'storage': str(self.server.store.root),
                                       'check_policy': self.server.store.policy, 'check_policy_sha256': self.server.store.policy_sha256})
            if self.path == '/api/state':
                return self.reply(200, self.server.store.read())
            if self.path == '/api/history':
                return self.reply(200, self.server.store.history())
            if self.path == '/api/export':
                exported = self.server.store.export()
                return self.reply(200, exported, attachment='single-reviewer-gold-r' + str(exported['revision']) + '.json')
            match = re.fullmatch(r'/api/source-chunks/([a-f0-9]{64})', self.path)
            if match:
                return self.reply(200, stored_chunks(self.server.sources, self.server.documents, match[1]))
            match = re.fullmatch(r'/api/source/([a-f0-9]{64})', self.path)
            if match:
                path, raw = read_source(self.server.sources, match[1])
                return self.reply(200, raw, 'application/octet-stream', 'source-' + match[1][:10] + path.suffix)
            match = re.fullmatch(r'/api/source-preview/([a-f0-9]{64})', self.path)
            if match:
                path, raw = read_source(self.server.sources, match[1])
                require(path.suffix.lower() == '.pdf', 'PDF 원문만 변환 없이 화면에서 볼 수 있습니다. 다른 형식은 기존 원문 열기를 사용하세요.')
                require(raw.startswith(b'%PDF-'), 'pdf_signature_mismatch')
                return self.reply(200, raw, 'application/pdf')
            self.reply(404, {'error': 'not_found'})
        except (ValueError, KeyError, TypeError) as exc:
            self.reply(403, {'error': str(exc)})
        except (OSError, sqlite3.Error):
            self.reply(503, {'error': '디스크 확인 실패. 입력을 백업하고 서버를 확인하세요.'})

    def do_POST(self):
        try:
            self.boundary()
            require(self.headers.get('Content-Type') == 'application/json' and self.headers.get('Transfer-Encoding') is None, 'json_required')
            length = int(self.headers.get('Content-Length', '0'))
            require(0 < length <= 1600000, 'invalid_length')
            data = strict_json(self.rfile.read(length))
            match = re.fullmatch(r'/api/source-hwp-preview/([a-f0-9]{64})', self.path)
            if match:
                require(data == {}, 'invalid_source_action')
                return self.reply(200, hwp_preview(self.server.sources, match[1], self.server.store.root / 'hwp-previews-v1'))
            match = re.fullmatch(r'/api/source-(open|reveal)/([a-f0-9]{64})', self.path)
            if match:
                require(data == {}, 'invalid_source_action')
                return self.reply(200, open_source(self.server.sources, match[2], reveal=match[1] == 'reveal'))
            fields = {'data', 'base_revision', 'mutation'}
            require(type(data) is dict and set(data) in (fields, fields | {'check_policy_sha256'}), 'invalid_request')
            require(self.path in ('/api/save', '/api/finalize'), 'not_found')
            if 'check_policy_sha256' in data:
                require(data['check_policy_sha256'] == self.server.store.policy_sha256, 'check_policy_mismatch')
            if self.path == '/api/finalize':
                require('check_policy_sha256' in data, '항목별 검수 기준이 업데이트되었습니다. 저장 확인 후 새로고침하고 확정하세요.')
            self.reply(200, self.server.store.save(**data, finalize=self.path == '/api/finalize'))
        except Conflict as exc:
            self.reply(409, {'error': str(exc)})
        except (ValueError, KeyError, TypeError) as exc:
            self.reply(400, {'error': str(exc)})
        except (OSError, sqlite3.Error):
            self.reply(503, {'error': '저장 확인 실패. 입력은 유지됩니다. 내 입력 백업을 받아주세요.'})


def make_server(store, cases, pin, input_path=None, port=0, synthetic=True):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.authority = f'127.0.0.1:{server.server_port}'
    server.store, server.cases, server.pin = store, cases, pin
    server.synthetic, server.input_path, server.sources = synthetic, input_path, source_map(cases)
    server.documents = source_documents(cases)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8772)
    parser.add_argument('--synthetic', action='store_true')
    args = parser.parse_args()
    cases, pin, input_path = load_packet(args.synthetic)
    root = STATE.with_name(STATE.name + '-qa') if args.synthetic else STATE
    store = Store(root, cases, pin, create=not root.exists())
    fd = os.open(root / 'server.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        server = make_server(store, cases, pin, input_path, args.port, args.synthetic)
        print(json.dumps({'url': f'http://{server.authority}/#{store.token}', 'storage': str(root),
                          'case_count': len(cases), 'cases_sha256': pin, 'synthetic': args.synthetic,
                          'external_llm_calls': 0}, ensure_ascii=False), flush=True)
        server.serve_forever()
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
