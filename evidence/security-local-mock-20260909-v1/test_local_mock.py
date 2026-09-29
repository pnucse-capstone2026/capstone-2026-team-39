"""Local launcher regression tests; no sockets, real env files or holdout reads."""
from pathlib import Path
import io
import sys
import tempfile
import unittest

import local_mock_bootstrap_v2 as bootstrap
import run_local_mock as runner


class LocalMockTests(unittest.TestCase):
    def test_service_argv_explicit_absent_env_file(self):
        with tempfile.TemporaryDirectory(prefix='pnu-local-mock-test-') as directory:
            argv = bootstrap.service_argv('/synthetic/search_api.py', '/synthetic/index.sqlite', directory)
            self.assertIn('--env-file', argv)
            env_path = Path(argv[argv.index('--env-file') + 1])
            self.assertEqual(env_path, Path(directory) / 'intentionally-absent-environment-file')
            self.assertFalse(env_path.exists())

    def test_existing_env_path_refused(self):
        with tempfile.TemporaryDirectory(prefix='pnu-local-mock-test-') as directory:
            (Path(directory) / 'intentionally-absent-environment-file').mkdir()
            with self.assertRaises(bootstrap.bridge.GuardError):
                bootstrap.service_argv('/synthetic/search_api.py', '/synthetic/index.sqlite', directory)

    def test_secret_and_holdout_read_still_denied(self):
        for path in ('.env', '.env.local', '/synthetic/holdout-case.jsonl'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(RuntimeError, 'protected_data_disabled'):
                    bootstrap.offline_provider_audit('open', (path, 'r', 0))

    def test_outbound_provider_still_denied(self):
        with self.assertRaisesRegex(RuntimeError, 'outbound_network_disabled'):
            bootstrap.offline_provider_audit('socket.connect', (None, ('203.0.113.1', 443)))

    def test_parent_only_owned_port_and_routes(self):
        runner.ALLOWED_PORTS.add(23456)
        try:
            runner.parent_audit('socket.connect', (None, ('127.0.0.1', 23456)))
            runner.parent_audit('urllib.Request', ('http://127.0.0.1:23456/health',))
            for url in ('http://127.0.0.1:23456/other', 'http://127.0.0.1:23457/chat',
                        'https://example.invalid/chat', 'http://127.0.0.1:23456/chat?x=1'):
                with self.subTest(url=url):
                    with self.assertRaises(bootstrap.bridge.GuardError):
                        runner.parent_audit('urllib.Request', (url,))
        finally:
            runner.ALLOWED_PORTS.discard(23456)


if __name__ == '__main__':
    if '--output' in sys.argv:
        output = Path(sys.argv[sys.argv.index('--output') + 1])
        output.mkdir(parents=True, exist_ok=False)
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(LocalMockTests))
        bootstrap.bridge.write_new(output / 'unit-tests.log', stream.getvalue().encode())
        counts = {'total': result.testsRun, 'skip': len(result.skipped),
                  'failures': len(result.failures), 'errors': len(result.errors)}
        bootstrap.bridge.write_new(output / 'test-counts.json', bootstrap.bridge.canonical(counts) + b'\n')
        print(stream.getvalue(), end='')
        raise SystemExit(0 if result.wasSuccessful() else 1)
    unittest.main(verbosity=2)
