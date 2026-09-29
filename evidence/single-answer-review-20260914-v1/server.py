"""Loopback-only blind review, durable SQLite history + immutable backups. No API calls."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.parse import urlsplit
from common import BASE, HERE, blank_state, canonical, publish, read, require, sha, utc, validate, validate_confirmation

class Store:
    def __init__(self, root, packet, pin, create=False):
        self.root, self.packet, self.pin = Path(root), packet, pin
        self.template = blank_state(packet, pin)
        require(not any(p.is_symlink() for p in (self.root, *self.root.parents)), 'symlink_not_allowed')
        if create:
            self.root.mkdir(mode=0o700)
            for sub in ('revisions', 'exports'):
                (self.root / sub).mkdir(mode=0o700)
            publish(self.root / 'token', secrets.token_urlsafe(32).encode())
            fd = os.open(self.root / 'review.sqlite3', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with self.connect() as db:
                db.execute('CREATE TABLE revisions (revision INTEGER PRIMARY KEY, mutation TEXT UNIQUE, request_sha TEXT, data BLOB, sha TEXT, previous_sha TEXT, created_at TEXT, action TEXT)')
                data = canonical(self.template)
                db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?)', (0, 'initial', '', data, sha(data), '', utc(), 'initial'))
        self.token = (self.root / 'token').read_text()
        require(bool(re.fullmatch(r'[A-Za-z0-9_-]{40,64}', self.token)), 'invalid_token')
        self.verify()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(f'file:{self.root / "review.sqlite3"}?mode=rw', uri=True, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL'); db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally:
            db.close()

    def backup(self, row):
        path = self.root / 'revisions' / f'{row["revision"]:08d}-{row["sha"]}.json'
        publish(path, row['data'])
        require(sha(path.read_bytes()) == row['sha'], 'backup_readback_failed')
        return str(path)

    def receipt(self, row):
        return {'revision': row['revision'], 'sha256': row['sha'], 'saved_at': row['created_at'],
                'backup_path': self.backup(row), 'data': json.loads(row['data'])}

    def verify(self):
        with self.connect() as db:
            require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'sqlite_integrity_error')
            previous = ''
            for n, row in enumerate(db.execute('SELECT * FROM revisions ORDER BY revision')):
                require(row['revision'] == n and row['previous_sha'] == previous and sha(row['data']) == row['sha'], 'history_chain_error')
                validate(json.loads(row['data']), self.template, self.packet)
                self.backup(row); previous = row['sha']

    def current(self):
        with self.connect() as db:
            return self.receipt(db.execute('SELECT * FROM revisions ORDER BY revision DESC LIMIT 1').fetchone())

    def save(self, request):
        require(type(request) is dict and set(request) == {'data', 'base_revision', 'mutation', 'confirm_item'}, 'invalid_request_fields')
        data, base, mutation, confirm = (request[k] for k in ('data', 'base_revision', 'mutation', 'confirm_item'))
        require(type(base) is int and base >= 0 and type(mutation) is str and bool(re.fullmatch(r'[a-zA-Z0-9_-]{8,100}', mutation)), 'invalid_revision_or_mutation')
        require(confirm is None or type(confirm) is str, 'invalid_confirmation')
        request_sha = sha(canonical(request))
        with self.connect() as db:
            old_request = db.execute('SELECT * FROM revisions WHERE mutation=?', (mutation,)).fetchone()
            if old_request:
                require(old_request['request_sha'] == request_sha, 'mutation_reused_with_different_content')
                return self.receipt(old_request)
            old = db.execute('SELECT * FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()
            require(base == old['revision'], 'revision_conflict')
            previous = json.loads(old['data'])
            validate(data, self.template, self.packet, previous)
            new_data = json.loads(canonical(data))
            if confirm is not None:
                indexes = [n for n, x in enumerate(new_data['labels']) if x['item_id'] == confirm]
                require(len(indexes) == 1, 'unknown_item')
                index = indexes[0]
                require(not new_data['labels'][index]['confirmed_at'], 'already_confirmed')
                validate_confirmation(new_data, index, self.packet)
                new_data['labels'][index].update(confirmed_at=utc(), confirmed_revision=base + 1)
            raw = canonical(new_data)
            db.execute('INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?)', (base + 1, mutation, request_sha, raw, sha(raw), old['sha'], utc(), 'confirm:' + confirm if confirm else 'save'))
            row = db.execute('SELECT * FROM revisions WHERE revision=?', (base + 1,)).fetchone()
        # Read-back follows the committed transaction. A crash before backup is
        # repaired from SQLite at restart; HTTP success is never sent early.
        with self.connect() as db:
            row = db.execute('SELECT * FROM revisions WHERE mutation=?', (mutation,)).fetchone()
            require(sha(row['data']) == row['sha'], 'database_readback_failed')
            return self.receipt(row)

    def export(self):
        receipt = self.current()
        raw = canonical({'schema_version': 'pnu.single-answer-export.v1', 'packet_sha256': self.pin, 'receipt': receipt})
        name = f'answer-review-r{receipt["revision"]}-{secrets.token_hex(5)}.json'
        publish(self.root / 'exports' / name, raw)
        return {'name': name, 'sha256': sha(raw), 'revision': receipt['revision']}

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def setup(self):
        super().setup(); self.connection.settimeout(15)
    def log_message(self, *args):
        pass
    def reply(self, value, status=200, kind='application/json; charset=utf-8', attachment=None):
        data = value if isinstance(value, bytes) else canonical(value)
        self.send_response(status)
        for key, val in {'Content-Type': kind, 'Content-Length': str(len(data)), 'Cache-Control': 'no-store',
                         'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                         'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}.items():
            self.send_header(key, val)
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.end_headers(); self.wfile.write(data)
    def boundary(self, authenticated=True):
        require(self.client_address[0] == '127.0.0.1' and self.headers.get('Host') == self.server.authority, 'loopback_host_required')
        require(self.headers.get('Origin') in (None, 'http://' + self.server.authority), 'origin_rejected')
        require(self.headers.get('Sec-Fetch-Site') != 'cross-site', 'cross_site_rejected')
        parts = urlsplit(self.path)
        require(not parts.query and not parts.fragment, 'query_not_allowed')
        if authenticated:
            require(secrets.compare_digest(self.headers.get('X-Review-Token', ''), self.server.store.token), 'token_required')
        return parts.path
    def do_GET(self):
        try:
            path = self.boundary(not self.path in ('/', '/app.js'))
            store = self.server.store
            if path in ('/', '/app.js'):
                self.reply((HERE / ('index.html' if path == '/' else 'app.js')).read_bytes(), kind='text/html; charset=utf-8' if path == '/' else 'text/javascript; charset=utf-8')
            elif path == '/api/bootstrap':
                self.reply({'packet': store.packet, 'packet_sha256': store.pin, 'receipt': store.current()})
            elif path == '/api/state':
                self.reply(store.current())
            elif path == '/api/history':
                with store.connect() as db:
                    self.reply({'history': [dict(r) for r in db.execute('SELECT revision,sha,created_at,action FROM revisions ORDER BY revision DESC LIMIT 100')]})
            elif re.fullmatch(r'/api/exports/answer-review-r\d+-[a-f0-9]{10}\.json', path):
                name = path.rsplit('/', 1)[1]
                self.reply((store.root / 'exports' / name).read_bytes(), attachment=name)
            else:
                self.reply({'error': 'not_found'}, 404)
        except (ValueError, OSError) as exc:
            self.reply({'error': str(exc) if isinstance(exc, ValueError) else 'file_unavailable'}, 403)
    def do_POST(self):
        try:
            path = self.boundary()
            require(self.headers.get('Transfer-Encoding') is None, 'transfer_encoding_rejected')
            require(self.headers.get('Content-Type') == 'application/json', 'json_required')
            length = int(self.headers.get('Content-Length', '0'))
            require(0 < length <= 2000000, 'invalid_length')
            request = json.loads(self.rfile.read(length))
            if path == '/api/save':
                self.reply(self.server.store.save(request))
            elif path == '/api/export':
                require(request == {}, 'empty_export_request_required'); self.reply(self.server.store.export())
            else:
                self.reply({'error': 'not_found'}, 404)
        except (ValueError, OSError, sqlite3.Error) as exc:
            message = str(exc) if isinstance(exc, ValueError) else 'storage_error_keep_browser_draft'
            self.reply({'error': message}, 409 if message == 'revision_conflict' else 400)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8773)
    parser.add_argument('--root', type=Path, default=BASE)
    args = parser.parse_args()
    packet = read(args.root / 'packet.json')
    pin = sha((args.root / 'packet.json').read_bytes())
    require(read(args.root / 'private-map.json')['packet_sha256'] == pin, 'packet_pin_mismatch')
    lock = open(args.root / 'server.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = args.root / 'state'
    store = Store(state, packet, pin, create=not state.exists())
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    server.store, server.authority = store, f'127.0.0.1:{args.port}'
    print(f'http://{server.authority}/#token={store.token}', flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
