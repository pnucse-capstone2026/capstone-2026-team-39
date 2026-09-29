import base64
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


s = module('preview_server', 'server.py')
w = module('preview_worker', 'hwp_preview.py')


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / 'original.hwp'
        self.raw = bytes.fromhex('d0cf11e0a1b11ae1') + b'synthetic-only-not-a-real-HWP'
        self.source.write_bytes(self.raw)
        self.id = 'a' * 64
        self.sources = {self.id: (self.source, s.sha(self.raw))}

    def tearDown(self):
        self.tmp.cleanup()

    def db(self, source_path=None, count=2):
        path = self.root / 'chunks.sqlite'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE chunks (chunk_id, document_id, chunk_index, parser, source_path, crawl_storage_path, page_start, page_end, text)')
            for i in range(count):
                db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?)',
                           (str(i), 'doc', i, 'synthetic-parser', source_path or str(self.source), '', 1, 2, '<script>synthetic</script>'))
        return path

    def test_document_map_requires_explicit_id(self):
        self.assertEqual(s.source_documents([{'source_path': 'x', 'document_id': 'doc'}]), {s.sha(b'x'): {'doc'}})
        self.assertEqual(s.source_documents([{'source_path': 'x'}]), {})

    def test_chunks_preserve_order_and_text_no_search(self):
        result = s.stored_chunks(self.sources, {self.id: {'doc'}}, self.id, self.db())
        self.assertEqual([c['chunk_index'] for c in result['chunks']], [0, 1])
        self.assertEqual(result['chunks'][0]['text'], '<script>synthetic</script>')
        self.assertFalse(result['retrieval_performed'])
        self.assertFalse(result['review_state_changed'])
        self.assertFalse(result['truncated'])

    def test_chunks_reject_wrong_source_and_ambiguous_document(self):
        db = self.db('/different-file.hwp')
        for docs in ({self.id: {'doc'}}, {self.id: {'doc', 'other'}}, {}):
            with self.assertRaises(ValueError):
                s.stored_chunks(self.sources, docs, self.id, db)

    def test_chunk_display_limit_is_explicit(self):
        result = s.stored_chunks(self.sources, {self.id: {'doc'}}, self.id, self.db(count=501))
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['chunks']), 500)

    def test_hwp_rejects_wrong_magic_or_changed_original(self):
        with patch.object(s.subprocess, 'run') as run:
            self.source.write_bytes(b'not HWP')
            with self.assertRaises(ValueError):
                s.hwp_preview(self.sources, self.id, self.root / 'cache')
            with self.assertRaises(ValueError):
                s.hwp_preview({self.id: (self.source, None)}, self.id, self.root / 'cache')
            run.assert_not_called()

    def test_hwp_cached_conversion_uses_network_denied_worker(self):
        payload = {'source_sha256': s.sha(self.raw), 'html': 'synthetic', 'html_sha256': s.sha(b'synthetic')}
        def execute(command, **kwargs):
            self.assertIn('(deny network*)', command[2])
            self.assertIn('(deny file-write*)', command[2])
            self.assertNotIn('HOME', kwargs['env'])
            Path(command[-1]).write_bytes(s.canonical(payload))
            return subprocess.CompletedProcess(command, 0)
        with patch.object(s.subprocess, 'run', side_effect=execute) as run:
            cache = self.root / 'cache'
            self.assertEqual(s.hwp_preview(self.sources, self.id, cache), payload)
            self.assertEqual(s.hwp_preview(self.sources, self.id, cache), payload)
            self.assertEqual(run.call_count, 1)
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_timeout_does_not_publish_partial_conversion(self):
        with patch.object(s.subprocess, 'run', side_effect=subprocess.TimeoutExpired('synthetic', 45)):
            with self.assertRaisesRegex(ValueError, '시간 제한'):
                s.hwp_preview(self.sources, self.id, self.root / 'cache')
        self.assertEqual(list((self.root / 'cache').glob('*.json')), [])

    def test_local_asset_rejects_network_escape_and_symlink(self):
        other = self.root / 'other.png'; other.write_bytes(b'synthetic')
        folder = self.root / 'assets'; folder.mkdir()
        (folder / 'link').symlink_to(other)
        for name in ['https://example.com/image.png', '../other.png', '/etc/passwd', 'link']:
            with self.assertRaises(ValueError):
                w.local_asset(folder, name)

    def test_layout_css_blocks_external_resources(self):
        for css in ['@import "https://example.com";', 'x{background:url(https://example.com)}', 'x{width:expression(alert(1))}', r'x{a:\75rl(x)}']:
            self.assertEqual(w.safe_css(css), '')
        self.assertEqual(w.safe_css('td{border:1px solid black}'), 'td{border:1px solid black}')
        self.assertEqual(w.safe_css('/* local layout */td{border:1px solid black}'), 'td{border:1px solid black}')
        self.assertEqual(w.safe_css('@im/**/port "https://example.com";'), '')

    def test_bundle_preserves_table_and_image_but_removes_active_html(self):
        # Run this test suite with the isolated preview interpreter (lxml installed).
        folder = self.root / 'converted'; folder.mkdir()
        (folder / 'styles.css').write_text('td{border:1px solid black}', encoding='utf-8')
        (folder / 'pixel.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV5kAAAAASUVORK5CYII='))
        (folder / 'index.xhtml').write_text('<html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" href="styles.css"/></head><body><table><tr><td rowspan="2">합성 표</td><td>10</td></tr><tr><td>20</td></tr></table><img src="pixel.png"/><img src="https://example.com/tracker"/><a href="https://example.com">합성 링크 텍스트</a><script>alert(1)</script><p onclick="alert(1)">합성 본문</p></body></html>', encoding='utf-8')
        html, stats = w.bundle(folder)
        self.assertEqual(stats['table_count'], 1)
        self.assertEqual(stats['embedded_images'], 1)
        self.assertEqual(stats['omitted_images'], 1)
        self.assertIn('rowspan="2"', html)
        self.assertIn('합성 링크 텍스트', html)
        self.assertIn('data:image/png;base64,', html)
        for bad in ['onclick=', '<script', 'alert(1)', 'https://example.com']:
            self.assertNotIn(bad, html)
        self.assertIn("connect-src 'none'", html)
        self.assertIn(w.WARNING, html)


if __name__ == '__main__':
    unittest.main()
