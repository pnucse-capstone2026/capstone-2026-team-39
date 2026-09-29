"""Run local unit tests with external sockets/protected data disabled."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import unittest

parser = argparse.ArgumentParser()
parser.add_argument("root", type=Path)
parser.add_argument("out", type=Path)
parser.add_argument("--pattern", default="test_*.py")
args = parser.parse_args()
root = args.root.resolve()
out = args.out.resolve()
out.parent.mkdir(parents=True, exist_ok=True)
if out.with_suffix(".json").exists() or out.with_suffix(".log").exists():
    raise SystemExit("refusing to overwrite test artifacts")
work = Path('/Users/leehyunwoo/project/pnu-docs-chatbot')


def guard(event, values):
    if event in {"socket.connect", "socket.bind"}:
        address = values[1]
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise RuntimeError("external socket disabled")
    if event == "socket.getaddrinfo" and values[0] not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("external DNS disabled")
    if event == "open" and isinstance(values[0], (str, bytes)):
        raw = os.fsdecode(values[0])
        if raw.startswith(str(work)) and ("holdout" in raw.lower() or raw.rsplit('/', 1)[-1] == '.env'):
            raise RuntimeError("protected input disabled")


sys.addaudithook(guard)
for key in list(os.environ):
    if any(part in key.upper() for part in ('API_KEY', 'ACCESS_TOKEN', 'AUTH_TOKEN', 'PASSWORD')):
        os.environ.pop(key)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.chdir(root)
sys.path[:0] = [str(root), str(root / 'scripts'), str(root / 'tests')]
capture = io.StringIO()
with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
    suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern=args.pattern)
    result = unittest.TextTestRunner(stream=capture, verbosity=1).run(suite)
summary = {
    'root': str(root), 'pattern': args.pattern, 'tests': result.testsRun,
    'skipped': len(result.skipped), 'failures': len(result.failures), 'errors': len(result.errors),
    'failure_ids': [str(test) for test, _ in result.failures],
    'error_ids': [str(test) for test, _ in result.errors],
    'skip_reasons': [(str(test), reason) for test, reason in result.skipped],
    'successful': result.wasSuccessful(), 'external_llm_calls': 0,
}
with out.with_suffix('.log').open('x') as stream:
    stream.write(capture.getvalue())
with out.with_suffix('.json').open('x') as stream:
    json.dump(summary, stream, indent=2, ensure_ascii=False)
    stream.write('\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))
sys.exit(0 if result.wasSuccessful() else 1)
