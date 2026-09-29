"""Evaluation-only identity checks and a conservative provider-attempt ledger.

This is NOT a service launcher or an approval mechanism. A future owned-process
runner must install GuardedTransport in each generation/Judge process, perform
the identity handshake, and validate artifacts before calling seal_slot().
No frozen service, prompt, gate, collector or Judge implementation is changed.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.request
from unittest.mock import patch


class GuardError(RuntimeError):
    """Safe diagnostic code only: never include a request, key or response."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def file_sha(path: Path | str) -> str:
    path = Path(path)
    if path.name.startswith('.env') or ('holdout' in str(path).lower() and path.suffix != '.py'):
        raise GuardError('protected_input')
    if path.is_symlink():
        raise GuardError('symlink_input')
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        hasher = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
        value = hasher.hexdigest()
        after = os.fstat(stream.fileno())
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise GuardError('input_changed_during_hash')
    current = path.stat()
    if (after.st_ino, after.st_size, after.st_mtime_ns) != (
            current.st_ino, current.st_size, current.st_mtime_ns):
        raise GuardError('input_replaced_during_hash')
    return value


def verify_file(path, expected):
    if file_sha(path) != expected:
        raise GuardError('file_pin_mismatch')


def verify_snapshot(condition):
    root = Path(condition['root']).resolve(strict=True)
    expected = condition['files_sha256']
    actual_paths = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise GuardError('snapshot_symlink')
        if path.is_file():
            actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != set(expected):
        raise GuardError('snapshot_file_set_changed')
    actual = {}
    for relative, pin in expected.items():
        path = root / relative
        if not path.resolve().is_relative_to(root):
            raise GuardError('snapshot_path_escape')
        verify_file(path, pin)
        actual[relative] = pin
    if digest(canonical(actual)) != condition['snapshot_sha256']:
        raise GuardError('snapshot_manifest_mismatch')
    return {'root': str(root), 'files': len(actual),
            'snapshot_sha256': condition['snapshot_sha256']}


def attest_bound_server(api, server, condition, index, nonce, modules):
    """Child-side hook AFTER bind and handler configuration, BEFORE serving.

    The future runner must receive this via its private child pipe, not accept
    an arbitrary JSON file as proof. Unit tests simulate the bound server.
    """
    if not re.fullmatch(r'[0-9a-f]{64}', nonce):
        raise GuardError('invalid_nonce')
    verified = verify_snapshot(condition)
    root = Path(verified['root'])
    expected_api = root / 'scripts/search_api.py'
    if Path(api.__file__).resolve() != expected_api:
        raise GuardError('wrong_api_import')
    if server.RequestHandlerClass is not api.SearchHandler:
        raise GuardError('wrong_handler')
    targets = api.SearchHandler.parser_targets
    if set(targets) != {'cascade'} or api.SearchHandler.default_parser_profile != 'cascade':
        raise GuardError('unexpected_parser_targets')
    target = targets['cascade']
    if Path(target.index_path).resolve() != Path(index['path']).resolve():
        raise GuardError('wrong_handler_index')
    verify_file(index['path'], index['sha256'])
    if api.SearchHandler.context_chunks_per_document != 2:
        raise GuardError('wrong_context_limit')
    host, port = server.socket.getsockname()[:2]
    if host != '127.0.0.1' or not 0 < port < 65536 or server.socket.fileno() < 0:
        raise GuardError('invalid_listener')
    top_names = {Path(p).parts[1].split('.')[0] for p in condition['files_sha256']
                 if p.startswith('scripts/')}
    imported = {}
    for name, module in modules.items():
        if name.split('.')[0] not in top_names:
            continue
        filename = getattr(module, '__file__', None)
        if not filename:
            raise GuardError('unattested_namespace_module')
        path = Path(filename).resolve()
        if not path.is_relative_to(root):
            raise GuardError('module_from_wrong_root')
        relative = path.relative_to(root).as_posix()
        if relative not in condition['files_sha256']:
            raise GuardError('unlisted_module')
        verify_file(path, condition['files_sha256'][relative])
        imported[name] = {'path': str(path), 'sha256': condition['files_sha256'][relative]}
    if 'search_api' not in imported:
        raise GuardError('api_missing_from_modules')
    freeze = dict(api.freeze_runtime_metadata(Path(index['path'])))
    if freeze.get('startup_code_sha256') != condition['files_sha256']['scripts/search_api.py']:
        raise GuardError('health_code_mismatch')
    if freeze.get('index_sha256') != index['sha256']:
        raise GuardError('health_index_mismatch')
    return {'schema': 'pnu.security-bound-server-attestation.v1', 'pid': os.getpid(),
            'nonce': nonce, 'root': str(root), 'snapshot_sha256': verified['snapshot_sha256'],
            'index': dict(index), 'listener': [host, port], 'freeze': freeze,
            'imported_modules': imported}


def verify_owned_handshake(attestation, child, nonce, condition, index, health, expected_port):
    """Parent-side validation using the Popen object it owns, not a claimed PID.

    Only call with a record received from the same child's private pipe. This
    checks identity, NOT all generation/security/collector configuration.
    """
    if child.poll() is not None or attestation.get('pid') != child.pid:
        raise GuardError('unowned_or_dead_child')
    expected = {'schema': 'pnu.security-bound-server-attestation.v1', 'nonce': nonce,
                'root': str(Path(condition['root']).resolve()),
                'snapshot_sha256': condition['snapshot_sha256'], 'index': index,
                'listener': ['127.0.0.1', expected_port]}
    if any(attestation.get(key) != value for key, value in expected.items()):
        raise GuardError('runtime_identity_mismatch')
    if not attestation.get('imported_modules') or health.get('freeze') != attestation.get('freeze'):
        raise GuardError('health_handshake_mismatch')
    return True


class Ledger:
    """One immutable-policy SQLite DB shared by all slots/processes in a run.

    A reservation is committed BEFORE transport. Never delete/refund attempts.
    Missing/corrupt databases fail closed; creation is a separate offline step.
    Crash-ambiguous attempts require explicit reconciliation, not auto-resume.
    """

    @classmethod
    def create(cls, path, policy):
        ids = [s['slot_id'] for s in policy['slots']]
        if len(ids) != len(set(ids)) or not ids:
            raise GuardError('invalid_slot_ids')
        if type(policy['total_cap']) is not int or policy['total_cap'] < 1:
            raise GuardError('invalid_total_cap')
        for slot in policy['slots']:
            if type(slot['attempt_cap']) is not int or slot['attempt_cap'] < 1:
                raise GuardError('invalid_slot_cap')
            if slot['role'] not in ('generation', 'judge') or not re.fullmatch(r'[a-zA-Z0-9._-]+', slot['model']):
                raise GuardError('invalid_slot_policy')
        path = Path(path)
        # Exclusive creation: an existing (even empty/corrupt) ledger is never reset.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with sqlite3.connect(path) as db:
            db.executescript('''
                PRAGMA synchronous=FULL;
                CREATE TABLE policy (id INTEGER PRIMARY KEY CHECK(id=1), sha TEXT NOT NULL, json TEXT NOT NULL);
                CREATE TABLE slots (id TEXT PRIMARY KEY, config TEXT NOT NULL, sealed_sha TEXT);
                CREATE TABLE attempts (id INTEGER PRIMARY KEY, slot TEXT NOT NULL REFERENCES slots(id),
                    request_sha TEXT NOT NULL, identity_sha TEXT NOT NULL, state TEXT NOT NULL,
                    started_ns INTEGER NOT NULL, finished_ns INTEGER, http_status INTEGER);
                CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, slot TEXT,
                    attempt INTEGER, detail_sha TEXT NOT NULL, time_ns INTEGER NOT NULL);
            ''')
            db.execute('INSERT INTO policy VALUES(1,?,?)', (digest(canonical(policy)), canonical(policy).decode()))
            db.executemany('INSERT INTO slots VALUES(?,?,NULL)',
                           [(s['slot_id'], canonical(s).decode()) for s in policy['slots']])
        return cls(path, policy)

    def __init__(self, path, policy):
        self.path = Path(path).resolve(strict=True)
        self.policy = json.loads(canonical(policy))
        self.policy_sha = digest(canonical(self.policy))
        with self._transaction() as db:
            self._validate(db)

    @contextmanager
    def _transaction(self):
        # mode=rw MUST NOT create a missing database on resume.
        db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _validate(self, db):
        row = db.execute('SELECT sha,json FROM policy WHERE id=1').fetchone()
        if row is None or row['sha'] != self.policy_sha or row['json'] != canonical(self.policy).decode():
            raise GuardError('ledger_policy_changed')
        stored = {r['id']: r['config'] for r in db.execute('SELECT id,config FROM slots')}
        if stored != {s['slot_id']: canonical(s).decode() for s in self.policy['slots']}:
            raise GuardError('ledger_slots_changed')

    def slot(self, slot_id):
        for slot in self.policy['slots']:
            if slot['slot_id'] == slot_id:
                return dict(slot)
        raise GuardError('unlisted_slot')

    def reserve(self, slot_id, request_sha, identity_sha):
        if not all(re.fullmatch('[0-9a-f]{64}', h) for h in (request_sha, identity_sha)):
            raise GuardError('invalid_digest')
        config = self.slot(slot_id)
        with self._transaction() as db:
            self._validate(db)
            slot = db.execute('SELECT sealed_sha FROM slots WHERE id=?', (slot_id,)).fetchone()
            if slot['sealed_sha'] is not None:
                raise GuardError('slot_sealed')
            # Pilot is a single stream across BOTH roles and all conditions.
            if db.execute("SELECT 1 FROM attempts WHERE state='reserved'").fetchone():
                raise GuardError('unresolved_attempt')
            rows = db.execute('SELECT request_sha FROM attempts WHERE slot=?', (slot_id,)).fetchall()
            if any(row['request_sha'] != request_sha for row in rows):
                raise GuardError('retry_request_changed')
            if len(rows) >= config['attempt_cap']:
                raise GuardError('slot_budget_exhausted')
            if db.execute('SELECT count(*) FROM attempts').fetchone()[0] >= self.policy['total_cap']:
                raise GuardError('total_budget_exhausted')
            return db.execute('INSERT INTO attempts(slot,request_sha,identity_sha,state,started_ns) VALUES(?,?,?,?,?)',
                              (slot_id, request_sha, identity_sha, 'reserved', time.time_ns())).lastrowid

    def finish(self, attempt, state, http_status=None):
        if state not in ('response_closed', 'transport_error', 'response_read_error'):
            raise GuardError('invalid_attempt_state')
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            raise GuardError('invalid_http_status')
        with self._transaction() as db:
            self._validate(db)
            result = db.execute("UPDATE attempts SET state=?,finished_ns=?,http_status=? WHERE id=? AND state='reserved'",
                                (state, time.time_ns(), http_status, attempt))
            if result.rowcount != 1:
                raise GuardError('attempt_not_reserved')

    def reconcile_uncertain(self, attempt, reason):
        """Explicit operator decision ONLY; consumes the attempt permanently."""
        if not isinstance(reason, str) or not reason.strip():
            raise GuardError('reconciliation_reason_required')
        with self._transaction() as db:
            self._validate(db)
            row = db.execute("SELECT slot FROM attempts WHERE id=? AND state='reserved'", (attempt,)).fetchone()
            if row is None:
                raise GuardError('attempt_not_reserved')
            db.execute("UPDATE attempts SET state='uncertain_acknowledged',finished_ns=? WHERE id=?",
                       (time.time_ns(), attempt))
            db.execute('INSERT INTO events(kind,slot,attempt,detail_sha,time_ns) VALUES(?,?,?,?,?)',
                       ('reconcile_no_refund', row['slot'], attempt, digest(reason.encode()), time.time_ns()))

    def seal_slot(self, slot_id, artifact_sha):
        """Caller MUST first perform strict artifact validation (not implemented here)."""
        self.slot(slot_id)
        if not re.fullmatch('[0-9a-f]{64}', artifact_sha):
            raise GuardError('invalid_artifact_digest')
        with self._transaction() as db:
            self._validate(db)
            if db.execute("SELECT 1 FROM attempts WHERE slot=? AND state='reserved'", (slot_id,)).fetchone():
                raise GuardError('unresolved_attempt')
            row = db.execute('SELECT sealed_sha FROM slots WHERE id=?', (slot_id,)).fetchone()
            if row['sealed_sha'] is not None and row['sealed_sha'] != artifact_sha:
                raise GuardError('sealed_artifact_changed')
            db.execute('UPDATE slots SET sealed_sha=? WHERE id=?', (artifact_sha, slot_id))

    def summary(self):
        with self._transaction() as db:
            self._validate(db)
            attempts = [dict(r) for r in db.execute('SELECT * FROM attempts ORDER BY id')]
            return {'policy_sha256': self.policy_sha, 'reserved_total': len(attempts),
                    'remaining_ceiling': self.policy['total_cap'] - len(attempts),
                    'unresolved': sum(r['state'] == 'reserved' for r in attempts),
                    'attempts': attempts,
                    'sealed_slots': {r['id']: r['sealed_sha'] for r in db.execute(
                        'SELECT id,sealed_sha FROM slots WHERE sealed_sha IS NOT NULL')}}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GuardError('provider_redirect_blocked')


def direct_opener():
    """No inherited HTTP proxy, no redirect (which would be an uncounted send)."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


class _CountedResponse:
    def __init__(self, response, ledger, attempt):
        self.response, self.ledger, self.attempt = response, ledger, attempt
        self.closed = False
        self.failed = False

    def read(self, *args, **kwargs):
        try:
            return self.response.read(*args, **kwargs)
        except BaseException:
            self.failed = True
            raise

    def __getattr__(self, name):
        return getattr(self.response, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def close(self):
        if self.closed:
            return
        status = getattr(self.response, 'status', None)
        try:
            self.response.close()
        except BaseException:
            self.failed = True
            raise
        finally:
            self.ledger.finish(self.attempt, 'response_read_error' if self.failed else 'response_closed', status)
            self.closed = True


class GuardedTransport:
    """Install at urllib.request.urlopen, not at the outer collector retry loop.

    opener is explicit so offline verification cannot silently use the network.
    identity_check is run before EVERY send; it must return a verified runtime
    identity, never merely an unverified /health response.
    """
    def __init__(self, ledger, slot_id, *, opener, identity_check):
        self.ledger, self.slot_id = ledger, slot_id
        self.opener, self.identity_check = opener, identity_check
        self.config = ledger.slot(slot_id)
        self.endpoint = ('https://generativelanguage.googleapis.com/v1beta/models/'
                         + self.config['model'] + ':generateContent')

    def urlopen(self, request, data=None, timeout=None, **kwargs):
        if (not isinstance(request, urllib.request.Request) or request.full_url != self.endpoint
                or request.get_method() != 'POST' or data is not None or kwargs):
            raise GuardError('unapproved_provider_request')
        if not isinstance(request.data, bytes) or not request.data:
            raise GuardError('missing_request_body')
        if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
                or not math.isfinite(timeout) or timeout <= 0 or timeout > self.config['timeout_ceiling']):
            raise GuardError('unapproved_timeout')
        identity = self.identity_check()
        if not isinstance(identity, dict) or not identity:
            raise GuardError('missing_runtime_identity')
        identity_sha = digest(canonical(identity))
        # Headers (including keys) and raw prompt/response are never persisted.
        request_sha = digest(self.endpoint.encode() + b'\0POST\0' + request.data)
        attempt = self.ledger.reserve(self.slot_id, request_sha, identity_sha)
        try:
            response = self.opener.open(request, timeout=timeout)
        except BaseException as exc:
            status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
            self.ledger.finish(attempt, 'transport_error', status)
            raise
        return _CountedResponse(response, self.ledger, attempt)

    @contextmanager
    def installed(self):
        # Process-wide, including generator worker threads. One slot per process.
        # Must be installed BEFORE importing a script that could alias urlopen.
        with patch.object(urllib.request, 'urlopen', self.urlopen):
            yield self
