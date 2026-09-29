"""Isolated synthetic review-state tests; never modify the user's live store."""
from copy import deepcopy
import http.client
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

import server


def local_only(event,args):
    if event in ('socket.connect','socket.bind'):
        if not isinstance(args[1],tuple) or args[1][0] != '127.0.0.1':
            raise RuntimeError('tests_allow_loopback_only')
    if event == 'socket.getaddrinfo' and args[0] not in ('127.0.0.1','localhost'):
        raise RuntimeError('tests_forbid_external_dns')
    if event == 'open' and isinstance(args[0],(str,bytes)):
        path = Path(args[0].decode() if isinstance(args[0],bytes) else args[0])
        if path.name == '.env' or ('holdout' in str(path).lower() and path.suffix not in ('.py','.pyc')):
            raise RuntimeError('tests_forbid_protected_inputs')


sys.addaudithook(local_only)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pnu-review-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()/'store'
        self.packet, self.template = server.load_inputs()
        self.store = server.Store(self.root,deepcopy(self.template),create=True)

    def edited(self,text='합성 QA 입력: 복원 대상 🙂\n다음 줄'):
        value = self.store.read()['data']
        value['scope_reviews']['R08'] = {'decision':'uncertain','reason':text}
        return value

    def save(self,data=None,base=0,mutation=None):
        return self.store.save(data or self.edited(),base,mutation or str(uuid.uuid4()))

    def test_initial_blank_no_human_impersonation(self):
        value = self.store.read()
        self.assertEqual(value['revision'],0)
        self.assertTrue(all(not r['reason'] and not r['decision'] for r in value['data']['scope_reviews'].values()))
        self.assertEqual(value['data']['labels'],self.template)

    def test_restart_and_readback_sha(self):
        saved = self.save()
        loaded = server.Store(self.root,self.template).read()
        self.assertEqual(saved,loaded)
        backup = json.loads(Path(saved['backup_path']).read_text())
        self.assertEqual(json.loads(backup['data']),saved['data'])
        self.assertEqual(server.sha(backup['data'].encode()),saved['sha256'])

    def test_idempotent_lost_ack_retry(self):
        data,mutation = self.edited(),str(uuid.uuid4())
        first = self.save(data,mutation=mutation)
        self.assertEqual(first,self.save(data,mutation=mutation))
        self.assertEqual(len(self.store.history()),2)

    def test_commit_before_backup_failure_repaired_on_retry(self):
        data,mutation = self.edited(),str(uuid.uuid4())
        with patch.object(self.store,'backup',side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                self.save(data,mutation=mutation)
        retry = self.save(data,mutation=mutation)
        self.assertEqual(retry['revision'],1)
        self.assertTrue(Path(retry['backup_path']).is_file())

    def test_concurrent_tabs_do_not_overwrite(self):
        original = self.edited('tab A synthetic')
        stale = self.edited('tab B synthetic')
        self.save(original)
        with self.assertRaises(server.Conflict):
            self.save(stale)
        self.assertEqual(self.store.read()['data'],original)

    def test_mutation_reuse_different_data_rejected(self):
        mutation = str(uuid.uuid4())
        self.save(mutation=mutation)
        with self.assertRaisesRegex(ValueError,'mutation_reused'):
            self.save(self.edited('different'),mutation=mutation)

    def test_restore_is_new_version_with_old_history_retained(self):
        first = self.save()
        self.save(self.edited('second'),base=1)
        restored = self.store.restore(1,2,str(uuid.uuid4()))
        self.assertEqual(restored['data'],first['data'])
        self.assertEqual(restored['revision'],3)
        self.assertEqual(self.store.read(2)['data']['scope_reviews']['R08']['reason'],'second')

    def test_judge_exposure_cannot_be_removed_by_edit_or_restore(self):
        data = self.edited()
        row = data['labels']['reviews'][0]
        row['judge_revealed_at'] = '2026-09-09T10:00:00+00:00'
        row['labels_before_judge'] = {k:row[k] for k in ('support','completeness','postprocessing','note')}
        self.save(data)
        with self.assertRaisesRegex(ValueError,'judge_exposure'):
            self.save(server.empty_state(deepcopy(self.template)),base=1)
        restored = self.store.restore(0,1,str(uuid.uuid4()))
        self.assertEqual(restored['data']['labels']['reviews'][0]['judge_revealed_at'],row['judge_revealed_at'])

    def test_corrupt_backup_never_overwritten(self):
        path = Path(self.save()['backup_path'])
        path.write_text('synthetic corruption')
        with self.assertRaisesRegex(ValueError,'existing_backup_mismatch'):
            server.Store(self.root,self.template)
        self.assertEqual(path.read_text(),'synthetic corruption')

    def test_corrupt_db_is_not_reset(self):
        with sqlite3.connect(self.root/'reviews.sqlite') as db:
            db.execute("UPDATE revisions SET sha='bad' WHERE revision=0")
        with self.assertRaisesRegex(ValueError,'stored_data_hash_mismatch'):
            server.Store(self.root,self.template)

    def test_missing_store_not_seeded_without_create(self):
        with self.assertRaises(ValueError):
            server.Store(self.root/'missing',self.template)
        self.assertFalse((self.root/'missing').exists())

    def test_export_is_verified_immutable_and_repeated_names_unique(self):
        self.save()
        one,two = self.store.export(),self.store.export()
        self.assertNotEqual(one['path'],two['path'])
        raw = Path(one['path']).read_bytes()
        self.assertEqual(server.sha(raw),one['sha256'])
        self.assertEqual(json.loads(raw)['data'],self.store.read()['data'])

    def test_schema_answer_binding_and_size_rejections(self):
        for modify in (
            lambda d:d['labels']['reviews'][0].update(answer_id='wrong'),
            lambda d:d['scope_reviews']['R08'].update(decision='approved'),
            lambda d:d['scope_reviews']['R08'].update(reason='x'*20001),
            lambda d:d['labels']['reviews'][0].update(judge_agreement='agree'),
        ):
            with self.subTest(modify=modify):
                data = self.edited(); modify(data)
                with self.assertRaises(ValueError): self.save(data)
        self.assertEqual(self.store.read()['revision'],0)

    def test_symlink_backup_directory_rejected(self):
        (self.root/'exports').rmdir()
        (self.root/'exports').symlink_to(self.root/'revisions',target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'unsafe_backup_directory'):
            self.store.export()


class HttpTests(StorageTests):
    # Reuse only setup/helpers; prevent inherited storage test duplication below.
    def setUp(self):
        super().setUp()
        self.httpd = server.make_server(self.store,self.packet,0)
        thread = threading.Thread(target=self.httpd.serve_forever,daemon=True); thread.start()
        def stop():
            self.httpd.shutdown(); self.httpd.server_close(); thread.join(2)
        self.addCleanup(stop)

    def request(self,path,body=None,headers=None):
        conn = http.client.HTTPConnection('127.0.0.1',self.httpd.server_port,timeout=3)
        base = {'X-Review-Token':self.store.token,'Content-Type':'application/json'}
        base.update(headers or {})
        conn.request('GET' if body is None else 'POST',path,body,base)
        response = conn.getresponse(); result = (response.status,dict(response.getheaders()),response.read()); conn.close()
        return result

    def test_http_load_save_and_conflict(self):
        status,headers,body = self.request('/api/bootstrap')
        self.assertEqual(status,200)
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        data = json.loads(body)['state']['data']; data['labels']['reviewer']='synthetic QA'
        payload = {'data':data,'base_revision':0,'mutation':str(uuid.uuid4())}
        self.assertEqual(self.request('/api/save',json.dumps(payload))[0],200)
        payload['mutation'] = str(uuid.uuid4())
        self.assertEqual(self.request('/api/save',json.dumps(payload))[0],409)

    def test_http_auth_origin_host_and_paths(self):
        for headers in ({'X-Review-Token':'bad'},{'Origin':'https://evil.invalid'},{'Host':'evil.invalid'},{'Sec-Fetch-Site':'cross-site'}):
            with self.subTest(headers=headers): self.assertEqual(self.request('/api/state',headers=headers)[0],403)
        for path in ('/api/state?token=x','/api/export/../../token','/.env'):
            self.assertIn(self.request(path)[0],(403,404))

    def test_http_strict_json_and_no_partial_write(self):
        for body in ('{"x":1,"x":2}','{"value":NaN}','[]','null','{"data":"bad"}'):
            with self.subTest(body=body): self.assertEqual(self.request('/api/save',body)[0],400)
        self.assertEqual(self.store.read()['revision'],0)


# Storage behavior is already covered once; HTTP cases only add transport checks.
for _name in tuple(dir(StorageTests)):
    if _name.startswith('test_'):
        setattr(HttpTests,_name,None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
