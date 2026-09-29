"""Local-only Shadow review: durable revision history, verified saves, no model calls."""
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
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PACKET = ROOT/'processed/eval/preflight-20260907/shadow14-human-review-v1/packet.json'
TEMPLATE = PACKET.with_name('review-template.json')
PINS = {PACKET:'e469fb1ccc8457c57e5626a7839470d6eed465ecc97140f6210ab9cdac178212',
        TEMPLATE:'d99373a7ad0a333db8cfa9f480a8bdb0ab422679622e903e05ee00087433b727'}
STATE = ROOT/'processed/reviews/shadow14-durable-20260909-v1'
SCOPES = {
    'R08':'장학금·마일리지의 실적 충족 및 심사 조건은 이 질문의 필수 답변인가요?',
    'R09':'모집대상을 묻는 이 질문에 모집인원 25명도 필수 답변인가요?',
    'R12':'질문에서 직접 묻지 않은 수험표·확인서의 사진 부착 조건도 필수 답변인가요?',
}
ENUMS = {
    'support':['','supported','unsupported','no_factual_answer','uncertain'],
    'completeness':['','complete','partial','refusal','uncertain'],
    'postprocessing':['not_checked','no_material_change','lost_supported_claim','removed_unsupported_claim','mixed','uncertain'],
    'judge_agreement':['not_checked','agree','disagree','uncertain'],
}


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def strict_json(raw):
    def pairs(items):
        result = {}
        for k,v in items:
            require(k not in result,'duplicate_json_key')
            result[k] = v
        return result
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_json')))


def atomic_new(path, data):
    """Fsync and publish a new name without replacing any existing backup."""
    if path.exists():
        require(not path.is_symlink() and path.read_bytes() == data,'existing_backup_mismatch')
        return
    fd, name = tempfile.mkstemp(prefix='.pending-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(name,path)
        except FileExistsError:
            require(not path.is_symlink() and path.read_bytes() == data,'concurrent_backup_mismatch')
        directory = os.open(path.parent,os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(name)


def load_inputs():
    for path,pin in PINS.items():
        require(not path.is_symlink() and sha(path.read_bytes()) == pin,'frozen_review_input_changed')
    packet, template = strict_json(PACKET.read_bytes()),strict_json(TEMPLATE.read_bytes())
    require(len(packet['items']) == 14 and len(template['reviews']) == 42,'review_scope_changed')
    return packet,template


def empty_state(template):
    return {'schema_version':'pnu.durable-shadow-review.v1','labels':template,
            'scope_reviews':{rid:{'decision':'','reason':''} for rid in SCOPES}}


def reviewed(row):
    return 'REVIEWED' if row['support'] and row['completeness'] and row['postprocessing'] != 'not_checked' and row['note'].strip() else 'PENDING'


def validate_state(data, template):
    require(type(data) is dict and set(data) == {'schema_version','labels','scope_reviews'},'invalid_state_fields')
    require(data['schema_version'] == 'pnu.durable-shadow-review.v1','invalid_state_schema')
    labels = data['labels']
    require(type(labels) is dict and set(labels) == set(template),'invalid_label_fields')
    for key in ('schema_version','packet_sha256','purpose'):
        require(labels[key] == template[key],'label_packet_mismatch')
    require(type(labels['reviewer']) is str and len(labels['reviewer']) <= 120,'invalid_reviewer')
    require(type(labels['reviews']) is list and len(labels['reviews']) == len(template['reviews']),'invalid_review_count')
    for row,base in zip(labels['reviews'],template['reviews']):
        require(type(row) is dict and set(row) == set(base),'invalid_review_fields')
        for key in ('review_id','case_id','generation_run_id','answer_id','answer_sha256'):
            require(row[key] == base[key],'answer_binding_mismatch')
        for key,values in ENUMS.items():
            require(row[key] in values,'invalid_review_value')
        require(type(row['note']) is str and len(row['note']) <= 20000,'invalid_note')
        require(row['status'] == reviewed(row),'invalid_review_status')
        if row['judge_revealed_at'] is None:
            require(row['labels_before_judge'] is None and row['judge_agreement'] == 'not_checked','invalid_unrevealed_judge')
        else:
            value = row['judge_revealed_at']
            require(type(value) is str and len(value) <= 40,'invalid_reveal_time')
            require(datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is not None,'invalid_reveal_timezone')
            before = row['labels_before_judge']
            require(type(before) is dict and set(before) == {'support','completeness','postprocessing','note'},'invalid_prejudge_record')
            for key in ('support','completeness','postprocessing'):
                require(before[key] in ENUMS[key],'invalid_prejudge_value')
            require(type(before['note']) is str and len(before['note']) <= 20000,'invalid_prejudge_note')
    require(type(data['scope_reviews']) is dict and set(data['scope_reviews']) == set(SCOPES),'invalid_scope_questions')
    for row in data['scope_reviews'].values():
        require(type(row) is dict and set(row) == {'decision','reason'},'invalid_scope_fields')
        require(row['decision'] in ('','required','optional','uncertain'),'invalid_scope_decision')
        require(type(row['reason']) is str and len(row['reason']) <= 20000,'invalid_scope_reason')
    require(len(canonical(data)) <= 1500000,'state_too_large')
    return data


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root, template, *, create=False):
        self.root, self.template = Path(root).resolve(),template
        require(not any(p.is_symlink() for p in (Path(root),*Path(root).parents)),'symlink_state_path')
        if create:
            self.root.mkdir(parents=True,exist_ok=False,mode=0o700)
            (self.root/'revisions').mkdir(mode=0o700)
            (self.root/'exports').mkdir(mode=0o700)
            atomic_new(self.root/'token',secrets.token_urlsafe(32).encode())
            with sqlite3.connect(self.root/'reviews.sqlite') as db:
                db.execute('PRAGMA synchronous=FULL')
                db.executescript('CREATE TABLE revisions(revision INTEGER PRIMARY KEY, mutation TEXT UNIQUE NOT NULL, base_revision INTEGER NOT NULL, data TEXT NOT NULL, sha TEXT NOT NULL, previous_sha TEXT NOT NULL, created_at TEXT NOT NULL, action TEXT NOT NULL);')
                initial = canonical(empty_state(template))
                db.execute('INSERT INTO revisions VALUES(0,?,?,?,?,?,?,?)',('initial',-1,initial.decode(),sha(initial),'',utc(),'initialize'))
            os.chmod(self.root/'reviews.sqlite',0o600)
        require(self.root.is_dir() and not (self.root/'reviews.sqlite').is_symlink(),'missing_or_unsafe_store')
        self.safe_directories()
        require(not (self.root/'token').is_symlink(),'unsafe_token')
        self.token = (self.root/'token').read_text()
        require(re.fullmatch(r'[A-Za-z0-9_-]{40,64}',self.token) is not None,'invalid_local_token')
        self.verify()

    def safe_directories(self):
        for name in ('revisions','exports'):
            path = self.root/name
            require(path.is_dir() and not path.is_symlink(),'unsafe_backup_directory')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect((self.root/'reviews.sqlite').as_uri()+'?mode=rw',uri=True,timeout=10)
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

    def backup(self,row):
        self.safe_directories()
        value = dict(row)
        data = canonical(value)+b'\n'
        path = self.root/'revisions'/f"{row['revision']:08d}-{row['sha']}.json"
        atomic_new(path,data)
        require(path.read_bytes() == data,'backup_readback_failed')
        return path

    def verify(self):
        with self.transaction() as db:
            require(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok','store_integrity_failure')
            rows = list(db.execute('SELECT * FROM revisions ORDER BY revision'))
        require(bool(rows),'empty_store')
        previous = ''
        for number,row in enumerate(rows):
            require(row['revision'] == number and row['previous_sha'] == previous,'revision_chain_broken')
            require(sha(row['data'].encode()) == row['sha'],'stored_data_hash_mismatch')
            validate_state(strict_json(row['data']),self.template)
            self.backup(row)  # Safely materialize a DB-committed, not-yet-exported revision after a crash.
            previous = row['sha']

    def read(self, revision=None):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM revisions ORDER BY revision DESC LIMIT 1').fetchone() if revision is None else db.execute('SELECT * FROM revisions WHERE revision=?',(revision,)).fetchone()
        require(row is not None,'revision_not_found')
        require(sha(row['data'].encode()) == row['sha'],'stored_data_hash_mismatch')
        validate_state(strict_json(row['data']),self.template)
        path = self.backup(row)
        return {'revision':row['revision'],'sha256':row['sha'],'saved_at':row['created_at'],
                'backup_path':str(path),'data':strict_json(row['data'])}

    def save(self, data, base_revision, mutation, action='edit'):
        validate_state(data,self.template)
        require(type(base_revision) is int and base_revision >= 0,'invalid_base_revision')
        require(type(mutation) is str and re.fullmatch(r'[A-Za-z0-9_-]{16,100}',mutation),'invalid_mutation')
        raw = canonical(data)
        with self.transaction() as db:
            duplicate = db.execute('SELECT * FROM revisions WHERE mutation=?',(mutation,)).fetchone()
            if duplicate:
                require(duplicate['data'] == raw.decode() and duplicate['base_revision'] == base_revision,'mutation_reused_for_different_data')
                revision = duplicate['revision']
            else:
                previous = db.execute('SELECT * FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()
                if previous['revision'] != base_revision:
                    raise Conflict('다른 탭에서 먼저 저장했습니다. 내 입력은 유지됐습니다. 백업 후 최신 기록을 확인하세요.')
                # Judge exposure is an audit fact, not an editable opinion, even on restore.
                prior = strict_json(previous['data'])['labels']['reviews']
                for old,new in zip(prior,data['labels']['reviews']):
                    if old['judge_revealed_at'] is not None:
                        require(new['judge_revealed_at'] == old['judge_revealed_at'] and new['labels_before_judge'] == old['labels_before_judge'],'judge_exposure_cannot_be_erased')
                revision = base_revision+1
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?)',(revision,mutation,base_revision,raw.decode(),sha(raw),previous['sha'],utc(),action))
        return self.read(revision)

    def history(self):
        with self.transaction() as db:
            return [dict(row) for row in db.execute('SELECT revision,sha,created_at,action FROM revisions ORDER BY revision DESC LIMIT 100')]

    def restore(self, target, base_revision, mutation):
        require(type(target) is int and 0 <= target < base_revision,'invalid_restore_target')
        old = self.read(target)['data']
        latest = self.read()['data']
        for src,dst in zip(latest['labels']['reviews'],old['labels']['reviews']):
            if src['judge_revealed_at'] is not None:
                dst['judge_revealed_at'],dst['labels_before_judge'] = src['judge_revealed_at'],src['labels_before_judge']
        return self.save(old,base_revision,mutation,f'restore:{target}')

    def export(self):
        self.safe_directories()
        receipt = self.read()
        data = canonical({'schema_version':'pnu.durable-shadow-review-export.v1',**receipt})+b'\n'
        name = f"shadow14-review-r{receipt['revision']}-{secrets.token_hex(8)}.json"
        path = self.root/'exports'/name
        atomic_new(path,data)
        require(path.read_bytes() == data,'export_readback_failed')
        return {'filename':name,'path':str(path),'sha256':sha(data),'revision':receipt['revision']}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self,*args):
        pass  # No review text or local capability in access logs.

    def reply(self,status,data,kind='application/json; charset=utf-8',attachment=None):
        payload = canonical(data) if not isinstance(data,bytes) else data
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        if attachment:
            self.send_header('Content-Disposition',f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(payload)

    def boundary(self,authenticated=False):
        require(self.client_address[0] == '127.0.0.1','loopback_only')
        require(self.headers.get('Host') == self.server.authority,'host_not_allowed')
        origin = self.headers.get('Origin')
        require(origin in (None,'http://'+self.server.authority),'origin_not_allowed')
        require(self.headers.get('Sec-Fetch-Site') not in ('cross-site',),'cross_site_not_allowed')
        require(not urlsplit(self.path).query,'query_not_allowed')
        if authenticated:
            require(secrets.compare_digest(self.headers.get('X-Review-Token',''),self.server.store.token),'local_token_required')

    def do_GET(self):
        try:
            self.boundary()
            if self.path in ('/','/app.js'):
                name = 'index.html' if self.path == '/' else 'app.js'
                return self.reply(200,(HERE/name).read_bytes(),'text/html; charset=utf-8' if name.endswith('html') else 'text/javascript; charset=utf-8')
            self.boundary(authenticated=True)
            if self.path == '/api/bootstrap':
                return self.reply(200,{'packet':self.server.packet,'scopes':SCOPES,'state':self.server.store.read()})
            if self.path == '/api/state':
                return self.reply(200,self.server.store.read())
            if self.path == '/api/history':
                return self.reply(200,self.server.store.history())
            match = re.fullmatch(r'/api/revision/(\d+)',self.path)
            if match:
                return self.reply(200,self.server.store.read(int(match[1])))
            match = re.fullmatch(r'/api/export/(shadow14-review-r\d+-[0-9a-f]{16}\.json)',self.path)
            if match:
                self.server.store.safe_directories()
                path = self.server.store.root/'exports'/match[1]
                require(not path.is_symlink() and path.is_file(),'export_not_found')
                return self.reply(200,path.read_bytes(),attachment=match[1])
            self.reply(404,{'error':'not_found'})
        except (ValueError,TypeError,KeyError) as exc:
            self.reply(403,{'error':str(exc)})
        except (OSError,sqlite3.Error):
            self.reply(503,{'error':'파일을 읽거나 저장하지 못했습니다. 입력을 유지하고 서버를 확인하세요.'})

    def do_POST(self):
        try:
            self.boundary(authenticated=True)
            require(self.headers.get('Content-Type') == 'application/json','json_required')
            require(self.headers.get('Transfer-Encoding') is None,'transfer_encoding_not_allowed')
            length = int(self.headers.get('Content-Length','0'))
            require(0 < length <= 1600000,'invalid_body_length')
            value = strict_json(self.rfile.read(length))
            if self.path == '/api/save':
                require(set(value) == {'data','base_revision','mutation'},'invalid_save_fields')
                return self.reply(200,self.server.store.save(**value))
            if self.path == '/api/restore':
                require(set(value) == {'target','base_revision','mutation'},'invalid_restore_fields')
                return self.reply(200,self.server.store.restore(**value))
            if self.path == '/api/export':
                require(value == {},'invalid_export_fields')
                return self.reply(200,self.server.store.export())
            self.reply(404,{'error':'not_found'})
        except Conflict as exc:
            self.reply(409,{'error':str(exc)})
        except (ValueError,TypeError,KeyError) as exc:
            self.reply(400,{'error':str(exc)})
        except (OSError,sqlite3.Error):
            self.reply(503,{'error':'디스크 저장 확인에 실패했습니다. 입력은 유지됩니다. 다시 저장하거나 긴급 백업하세요.'})


def make_server(store,packet,port):
    httpd = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    httpd.daemon_threads = True
    httpd.authority = '127.0.0.1:'+str(httpd.server_port)
    httpd.store,httpd.packet = store,packet
    return httpd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--qa-state',type=Path,help='separate test-only state under processed/reviews/qa-*')
    args = parser.parse_args()
    packet,template = load_inputs()
    root = STATE
    if args.qa_state:
        root = args.qa_state.resolve()
        require(root.parent == STATE.parent and root.name.startswith('qa-'),'qa_state_out_of_scope')
    store = Store(root,template,create=not root.exists())
    fd = os.open(root/'server.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        httpd = make_server(store,packet,args.port)
        print(json.dumps({'url':f'http://{httpd.authority}/#{store.token}','storage':str(root),'qa_only':bool(args.qa_state),'external_llm_calls':0}),flush=True)
        try:
            httpd.serve_forever()
        finally:
            httpd.server_close()
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
