import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

SPEC = importlib.util.spec_from_file_location('single_review_server', Path(__file__).with_name('server.py'))
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / 'store'
        self.cases = mod.fixture_cases()
        self.pin = mod.sha(mod.canonical(self.cases))
        self.store = mod.Store(self.root, self.cases, self.pin, create=True)

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, data, revision=0, mutation='test-mutation-000001', finalize=False):
        return self.store.save(data, revision, mutation, finalize=finalize)

    def complete(self):
        data = self.store.read()['data']
        data['reviewer_id'] = 'synthetic-human'
        for row in data['reviews']:
            row['checks'] = {key: True for key in mod.CHECKS}
            row['decision'] = 'PASS'
        return data

    def test_initial_labels_blank(self):
        r = self.store.read()
        self.assertEqual(r['revision'], 0)
        self.assertFalse(r['locked'])
        self.assertEqual(r['data']['reviewer_id'], '')
        self.assertTrue(all(row['decision'] == 'PENDING' for row in r['data']['reviews']))

    def test_note_survives_restart_with_matching_backup(self):
        data = self.store.read()['data']
        data['reviews'][0]['notes'] = '합성 복원 검사 한글 메모'
        saved = self.save(data)
        reopened = mod.Store(self.root, self.cases, self.pin).read()
        self.assertEqual(saved, reopened)
        self.assertEqual(mod.sha(mod.canonical(reopened['data'])), reopened['sha256'])
        self.assertTrue(Path(saved['backup_path']).is_file())

    def test_publish_does_not_replace(self):
        target = self.root / 'test-backup'
        mod.publish(target, b'original')
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            mod.publish(target, b'replacement')
        self.assertEqual(target.read_bytes(), b'original')

    def test_conflict_preserves_first_writer(self):
        first = self.store.read()['data']
        first['reviews'][0]['notes'] = 'first'
        self.save(first)
        second = copy.deepcopy(first)
        second['reviews'][0]['notes'] = 'second'
        with self.assertRaises(mod.Conflict):
            self.save(second, mutation='test-mutation-000002')
        self.assertEqual(self.store.read()['data']['reviews'][0]['notes'], 'first')

    def test_idempotent_retry(self):
        data = self.store.read()['data']
        self.assertEqual(self.save(data), self.save(data))
        data['reviews'][0]['notes'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'mutation_reused'):
            self.save(data)

    def test_pass_requires_checks_and_human(self):
        data = self.store.read()['data']
        data['reviews'][0]['decision'] = 'PASS'
        with self.assertRaises(ValueError):
            self.save(data)
        data['reviews'][0]['checks'] = dict.fromkeys(mod.CHECKS, True)
        with self.assertRaises(ValueError):
            self.save(data)
        self.assertEqual(self.store.read()['revision'], 0)

    def test_issue_requires_notes(self):
        data = self.store.read()['data']
        data['reviews'][0]['decision'] = 'REVISE'
        with self.assertRaises(ValueError):
            self.save(data)
        data['reviews'][0]['notes'] = 'synthetic issue'
        self.assertEqual(self.save(data)['revision'], 1)

    def test_binding_and_schema_rejected(self):
        for field, value in [('case_id', 'other'), ('checks', {k: 1 for k in mod.CHECKS})]:
            data = self.store.read()['data']
            data['reviews'][0][field] = value
            with self.assertRaises(ValueError):
                self.save(data)
        data = self.store.read()['data']
        data['cases_sha256'] = 'wrong'
        with self.assertRaises(ValueError):
            self.save(data)

    def test_input_pin_change_rejected_on_restart(self):
        with self.assertRaisesRegex(ValueError, 'binding'):
            mod.Store(self.root, self.cases, 'wrong')

    def test_finalize_requires_all_and_seals(self):
        with self.assertRaises(ValueError):
            self.save(self.store.read()['data'], finalize=True)
        data = self.complete()
        receipt = self.save(data, finalize=True)
        self.assertTrue(receipt['locked'])
        with self.assertRaises(ValueError):
            self.save(data, 1, 'test-mutation-000002')
        self.assertTrue(mod.Store(self.root, self.cases, self.pin).read()['locked'])

    def test_backup_failure_recovers_db_commit(self):
        data = self.store.read()['data']
        data['reviews'][0]['notes'] = 'survives backup interruption'
        with patch.object(self.store, 'backup', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                self.save(data)
        recovered = mod.Store(self.root, self.cases, self.pin).read()
        self.assertEqual(recovered['revision'], 1)
        self.assertEqual(recovered['data'], data)

    def test_corruption_rejected(self):
        with sqlite3.connect(self.root / 'reviews.sqlite') as db:
            db.execute("UPDATE revisions SET data='{}' WHERE revision=0")
        with self.assertRaisesRegex(ValueError, 'hash'):
            mod.Store(self.root, self.cases, self.pin)

    def test_symlink_rejected(self):
        link = Path(self.tmp.name).resolve() / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            mod.Store(link, self.cases, self.pin)

    def test_strict_json(self):
        for raw in ['{"a":1,"a":2}', '{"n":NaN}']:
            with self.assertRaises(ValueError):
                mod.strict_json(raw)

    def test_source_allowlist_no_env_or_arbitrary_code(self):
        self.assertEqual(mod.source_map([{'source_path': str(mod.ROOT / '.env')}, {'source_path': str(mod.HERE / 'server.py')}]), {})

    def test_project_downloads_pdf_and_spreadsheet_allowed(self):
        root = Path(self.tmp.name).resolve()
        folder = root / 'downloads'
        folder.mkdir()
        cases = []
        for suffix in ('.pdf', '.xlsx', '.html', '.hwp'):
            file = folder / ('synthetic' + suffix)
            file.write_bytes(b'synthetic test only')
            cases.append({'source_path': str(file.relative_to(root)), 'source_sha256': mod.sha(file.read_bytes())})
        with patch.object(mod, 'ROOT', root):
            self.assertEqual(len(mod.source_map(cases)), 4)

    def test_open_existing_uses_exact_local_path_without_copy(self):
        path = self.root / 'existing.pdf'
        path.write_bytes(b'%PDF-1.4\nsynthetic fixture\n')
        original = path.read_bytes()
        sources = {'f' * 64: (path, mod.sha(original))}
        with patch.object(mod.subprocess, 'run') as run:
            run.return_value.returncode = 0
            result = mod.open_source(sources, 'f' * 64)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/open', '-a', '/System/Applications/Preview.app', str(path)])
        self.assertFalse(result['copy_created'])
        self.assertFalse(result['content_transformed'])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.store.read()['revision'], 0)

    def test_hwp_never_silently_sent_to_external_viewer(self):
        path = self.root / 'synthetic.hwp'
        path.write_bytes(b'synthetic fixture only')
        sources = {'e' * 64: (path, mod.sha(path.read_bytes()))}
        with patch.object(mod.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, '로컬 원문 뷰어'):
                mod.open_source(sources, 'e' * 64)
            run.assert_not_called()

    def test_changed_original_and_unknown_source_cannot_open(self):
        path = self.root / 'synthetic.pdf'
        path.write_bytes(b'%PDF-synthetic')
        with patch.object(mod.subprocess, 'run') as run:
            for sources, source_id in [({}, 'a' * 64), ({'a' * 64: (path, 'wrong')}, 'a' * 64)]:
                with self.assertRaises(ValueError):
                    mod.open_source(sources, source_id)
            run.assert_not_called()

    def test_reveal_uses_finder_without_implicit_viewer(self):
        path = self.root / 'synthetic.hwp'
        path.write_bytes(b'synthetic fixture only')
        with patch.object(mod.subprocess, 'run') as run:
            run.return_value.returncode = 0
            result = mod.open_source({'d' * 64: (path, None)}, 'd' * 64, reveal=True)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/open', '-R', str(path)])
            self.assertEqual(result['status'], 'reveal_requested')


def policy_cases():
    cases = mod.fixture_cases()
    cases[0].update(split='holdout-core', role='pnu-student', category='registration', difficulty_type='multi_evidence')
    cases[0]['required_claims'][0]['evidence_options'][0]['source_path'] = 'downloads/synthetic.pdf'
    cases[1].update(split='holdout-challenge', role='pnu-student', category='challenge', challenge_type='unanswerable')
    return cases


class ApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve() / 'store'
        self.cases = policy_cases()
        self.pin = mod.sha(mod.canonical(self.cases))
        self.store = mod.Store(self.root, self.cases, self.pin, create=True)

    def tearDown(self):
        self.tmp.cleanup()

    def complete(self):
        data = self.store.read()['data']
        data['reviewer_id'] = 'synthetic-human'
        for row in data['reviews']:
            row['checks'] = dict.fromkeys(mod.CHECKS, True)
            row['decision'] = 'PASS'
        data['reviews'][1]['checks']['source_verified'] = False
        return data

    def test_negative_source_is_na_but_three_judgments_still_required(self):
        specs = mod.check_policy(self.cases[1])['checks']
        self.assertEqual(specs['source_verified']['status'], 'not_applicable')
        self.assertTrue(all(specs[k]['status'] == 'required' for k in mod.CHECKS[1:]))
        self.assertIn('기대', specs['gold_verified']['title'])

    def test_missing_material_is_blocked_not_na(self):
        for field in ('required_claims', 'role', 'answerable'):
            case = copy.deepcopy(self.cases[0])
            case.pop(field)
            specs = mod.check_policy(case)['checks']
            self.assertTrue(any(s['status'] == 'blocked' for s in specs.values()))
            self.assertFalse(any(s['status'] == 'not_applicable' for s in specs.values()))
        negative = copy.deepcopy(self.cases[1])
        negative.pop('challenge_oracle')
        self.assertEqual(mod.check_policy(negative)['checks']['gold_verified']['status'], 'blocked')

    def test_classification_and_guide_do_not_depend_on_question_text(self):
        for kind in ('multi_evidence', 'structure_sensitive'):
            case = copy.deepcopy(self.cases[0])
            case['difficulty_type'] = kind
            policy = mod.check_policy(case)
            case['query'] = 'entirely different synthetic wording'
            self.assertEqual(policy, mod.check_policy(case))
            self.assertTrue(all(s['status'] == 'required' for s in policy['checks'].values()))
            self.assertIn('등록', str(policy['classification']))
            self.assertTrue(policy['guide'])

    def test_na_pass_preserves_false_and_mixed_history_survives(self):
        old = self.store.read()['data']
        old['reviews'][0]['notes'] = 'earlier human note, preserved'
        legacy = self.store.save(old, 0, 'legacy-synthetic-0001')
        data = self.complete()
        out = self.store.save(data, 1, 'policy-synthetic-0002', check_policy_sha256=self.store.policy_sha256)
        self.assertFalse(out['data']['reviews'][1]['checks']['source_verified'])
        reopened = mod.Store(self.root, self.cases, self.pin)
        self.assertEqual(reopened.read(), out)
        self.assertEqual(reopened.read()['data']['reviews'][0]['notes'], old['reviews'][0]['notes'])
        self.assertEqual(json.loads(Path(legacy['backup_path']).read_text())['sha'], legacy['sha256'])

    def test_required_unchecked_and_wrong_policy_rejected(self):
        for row_number, key in ((0, 'source_verified'), (1, 'gold_verified'), (1, 'answerability_verified'), (1, 'label_verified')):
            data = self.complete()
            data['reviews'][row_number]['checks'][key] = False
            with self.assertRaises(ValueError):
                self.store.save(data, 0, 'invalid-synthetic-0001', check_policy_sha256=self.store.policy_sha256)
        with self.assertRaisesRegex(ValueError, 'policy'):
            self.store.save(self.complete(), 0, 'invalid-synthetic-0002', check_policy_sha256='wrong')
        self.assertEqual(self.store.read()['revision'], 0)

    def test_missing_metadata_cannot_pass_even_with_all_checks(self):
        cases = copy.deepcopy(self.cases)
        cases[0].pop('role')
        policy = mod.review_policy(cases, self.pin)
        data = self.complete()
        with self.assertRaises(ValueError):
            mod.validate_state(data, self.store.template, policy)

    def test_new_finalize_seals_and_retry_is_idempotent(self):
        data = self.complete()
        kwargs = dict(finalize=True, check_policy_sha256=self.store.policy_sha256)
        out = self.store.save(data, 0, 'final-policy-000001', **kwargs)
        self.assertEqual(self.store.save(data, 0, 'final-policy-000001', **kwargs), out)
        self.assertTrue(mod.Store(self.root, self.cases, self.pin).read()['locked'])
        with self.assertRaises(ValueError):
            self.store.save(data, 1, 'after-final-000001', check_policy_sha256=self.store.policy_sha256)

    def test_legacy_true_na_is_retained_but_export_does_not_count_as_verification(self):
        data = self.complete()
        data['reviews'][1]['checks']['source_verified'] = True
        self.store.save(data, 0, 'legacy-all-true-0001')
        exported = self.store.export()
        self.assertTrue(exported['data']['reviews'][1]['checks']['source_verified'])
        self.assertEqual(exported['effective_checks'][self.cases[1]['id']]['source_verified'], 'NOT_APPLICABLE')
        self.assertFalse(exported['locked'])

    def test_http_old_draft_new_policy_and_finalize(self):
        server = mod.make_server(self.store, self.cases, self.pin)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def call(path, body=None):
            request = urllib.request.Request('http://' + server.authority + path,
                None if body is None else mod.canonical(body),
                {'Content-Type': 'application/json', 'X-Review-Token': self.store.token})
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read())
        try:
            bootstrap = call('/api/bootstrap')
            self.assertEqual(bootstrap['check_policy_sha256'], self.store.policy_sha256)
            data = bootstrap['state']['data']
            data['reviews'][0]['notes'] = 'active old client note'
            old = {'data': data, 'base_revision': 0, 'mutation': 'old-http-client-0001'}
            self.assertEqual(call('/api/save', old)['data'], data)
            new = {'data': self.complete(), 'base_revision': 1, 'mutation': 'new-http-client-0001'}
            with self.assertRaises(urllib.error.HTTPError):
                call('/api/finalize', new)
            new['check_policy_sha256'] = None
            with self.assertRaises(urllib.error.HTTPError):
                call('/api/save', new)
            new['check_policy_sha256'] = self.store.policy_sha256
            saved = call('/api/save', new)
            self.assertEqual(saved, call('/api/state'))
            final = {**new, 'base_revision': 2, 'mutation': 'new-http-final-0001'}
            self.assertTrue(call('/api/finalize', final)['locked'])
            exported = call('/api/export')
            self.assertEqual(exported['export_schema'], 'pnu.single-reviewer-gold-export.v2')
            self.assertEqual(exported['effective_checks'][self.cases[1]['id']]['source_verified'], 'NOT_APPLICABLE')
            self.assertEqual(exported['data']['reviews'][0]['notes'], 'active old client note')
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_synthetic_ui_disabled_checks_capture_and_guides(self):
        # Isolated DOM stub, no browser, no network, no actual review packet or storage.
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = {};
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.value = ''; this.checked = false; this.disabled = false; this.style = {}; this.classList = {add(){}}; }
  set id(v) { this._id = v; nodes[v] = this; } get id() { return this._id; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(k,v) { this[k] = v; }
  querySelector() { return nodes.passOption; }
}
for (const id of ['reviewer','notes','decision','saved','progressText','progressBar','finalize','nav','question','checks','caseGuide','next','save','draft','export','historyButton','history','loading','content','mode','storage','passOption']) { const e = new Element('div'); e.id = id; }
const ctx = vm.createContext({fixture, nodes, console, structuredClone,
  document: {getElementById:id => nodes[id], createElement:tag => new Element(tag), body:new Element('body')},
  location:{hash:'#synthetic-token',pathname:'/'}, sessionStorage:{getItem(){return '';},setItem(){}}, history:{replaceState(){}},
  fetch:() => new Promise(()=>{}), addEventListener(){}, setTimeout(){return 1;},clearTimeout(){},
});
vm.runInContext(fs.readFileSync(fixture.app, 'utf8'), ctx);
vm.runInContext(`
  packet = {cases:fixture.cases,check_policy:fixture.policy}; receipt = {locked:false}; draft = structuredClone(fixture.data);
  $('reviewer').value = draft.reviewer_id; index = 1; render();
`, ctx);
assert.equal(nodes['check-source_verified'].disabled, true);
assert.equal(nodes['check-source_verified'].checked, false);
assert.equal(nodes['check-gold_verified'].disabled, false);
assert.ok(JSON.stringify(nodes.caseGuide.children).includes('직접 확인 3개'));
assert.ok(JSON.stringify(nodes.question.children).includes('답변 불가'));
assert.equal(nodes.passOption.disabled, false);
vm.runInContext('capture();', ctx);
assert.equal(vm.runInContext('draft.reviews[1].checks.source_verified', ctx), true); // legacy raw value preserved
vm.runInContext(`$('check-gold_verified').checked = false; $('check-gold_verified').onchange();`, ctx);
assert.equal(vm.runInContext('draft.reviews[1].decision', ctx), 'PENDING');
assert.equal(nodes.passOption.disabled, true);
vm.runInContext('index = 0; render();', ctx);
assert.equal(nodes['check-source_verified'].disabled, false);
assert.ok(JSON.stringify(nodes.question.children).includes('등록'));
assert.ok(JSON.stringify(nodes.caseGuide.children).includes('직접 확인 4개'));
vm.runInContext(`packet.check_policy.cases[draft.reviews[0].case_id].checks.source_verified.status = 'blocked'; render();`,ctx);
assert.equal(nodes['check-source_verified'].disabled, true);
assert.equal(nodes['check-source_verified'].checked, false);
assert.equal(nodes.passOption.disabled, true);
assert.ok(JSON.stringify(nodes.caseGuide.children).includes('PASS 불가'));
'''
        data = self.complete()
        data['reviews'][1]['checks']['source_verified'] = True
        result = subprocess.run(['node', '-e', script], input=mod.canonical({
            'app': str(mod.HERE / 'app.js'), 'cases': self.cases, 'policy': self.store.policy, 'data': data}),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())


class HttpTests(unittest.TestCase):
    setUp = ReviewTests.setUp
    tearDown = ReviewTests.tearDown

    def test_chunk_and_hwp_routes_are_authenticated_and_do_not_save(self):
        server = mod.make_server(self.store, self.cases, self.pin)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://' + server.authority
        try:
            with patch.object(mod, 'stored_chunks', return_value={'chunks': [], 'review_state_changed': False}) as chunks, patch.object(mod, 'hwp_preview', return_value={'html': 'synthetic'}) as hwp:
                for endpoint, body in [('/api/source-chunks/' + 'a' * 64, None), ('/api/source-hwp-preview/' + 'a' * 64, b'{}')]:
                    req = urllib.request.Request(base + endpoint, body, {'Content-Type': 'application/json'})
                    with self.assertRaises(urllib.error.HTTPError):
                        urllib.request.urlopen(req, timeout=5)
                    self.assertFalse(chunks.called)
                    self.assertFalse(hwp.called)
                for endpoint, body in [('/api/source-chunks/' + 'a' * 64, None), ('/api/source-hwp-preview/' + 'a' * 64, b'{}')]:
                    req = urllib.request.Request(base + endpoint, body, {'Content-Type': 'application/json', 'X-Review-Token': self.store.token})
                    with urllib.request.urlopen(req, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                chunks.assert_called_once()
                hwp.assert_called_once()
                self.assertEqual(self.store.read()['revision'], 0)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_pdf_preview_exact_bytes_and_native_action_auth(self):
        path = self.root / 'original.pdf'
        raw = b'%PDF-1.4\nsynthetic transport fixture, not a visual test\n'
        path.write_bytes(raw)
        source_id = 'a' * 64
        server = mod.make_server(self.store, self.cases, self.pin)
        server.sources = {source_id: (path, mod.sha(raw))}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = 'http://' + server.authority
        try:
            request = urllib.request.Request(url + '/api/source-preview/' + source_id,
                headers={'X-Review-Token': self.store.token})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.headers['Content-Type'], 'application/pdf')
                self.assertIsNone(response.headers.get('Content-Disposition'))
                self.assertEqual(response.read(), raw)
            with patch.object(mod.subprocess, 'run') as run:
                run.return_value.returncode = 0
                for token, origin in [(False, None), (True, 'https://example.com')]:
                    headers = {'Content-Type': 'application/json'}
                    if token:
                        headers['X-Review-Token'] = self.store.token
                    if origin:
                        headers['Origin'] = origin
                    bad = urllib.request.Request(url + '/api/source-open/' + source_id, b'{}', headers)
                    with self.assertRaises(urllib.error.HTTPError):
                        urllib.request.urlopen(bad, timeout=5)
                run.assert_not_called()
                good = urllib.request.Request(url + '/api/source-open/' + source_id, b'{}',
                    {'Content-Type': 'application/json', 'X-Review-Token': self.store.token})
                with urllib.request.urlopen(good, timeout=5) as response:
                    self.assertEqual(json.loads(response.read())['status'], 'open_requested')
                run.assert_called_once()
            self.assertEqual(self.store.read()['revision'], 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_http_auth_origin_save_readback_export(self):
        server = mod.make_server(self.store, self.cases, self.pin)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = 'http://' + server.authority
        def call(path, body=None, token=True, origin=None):
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['X-Review-Token'] = self.store.token
            if origin:
                headers['Origin'] = origin
            request = urllib.request.Request(url + path, None if body is None else mod.canonical(body), headers)
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read())
        try:
            self.assertEqual(call('/health', token=False)['case_count'], 2)
            with self.assertRaises(urllib.error.HTTPError) as exc:
                call('/api/bootstrap', token=False)
            self.assertEqual(exc.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError):
                call('/api/state', origin='https://example.com')
            data = call('/api/bootstrap')['state']['data']
            data['reviews'][0]['notes'] = 'HTTP synthetic'
            out = call('/api/save', {'data': data, 'base_revision': 0, 'mutation': 'http-synthetic-0001'})
            self.assertEqual(out, call('/api/state'))
            exported = call('/api/export')
            self.assertEqual(exported['reviewer_count'], 1)
            self.assertEqual(exported['original_two_reviewer_gate'], 'NOT_APPLICABLE')
            self.assertFalse(exported['locked'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
