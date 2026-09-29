"""Run only collector/artifact unit tests with the probe's strict offline guard."""
import argparse
import contextlib
import io
import json
import unittest

from probe_collector import ROOT

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', required=True)
args = parser.parse_args()
from pathlib import Path

out = Path(args.out)
log_path = out.with_suffix('.log')
if out.exists() or log_path.exists():
    raise SystemExit('refusing to overwrite outputs')
capture = io.StringIO()
patterns = ['test_evaluate_service_answers.py', 'test_service_eval_artifacts.py']
with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
    suite = unittest.TestSuite()
    for pattern in patterns:
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern=pattern))
    result = unittest.TextTestRunner(stream=capture, verbosity=1).run(suite)
summary = {
    'patterns': patterns, 'tests': result.testsRun, 'skipped': len(result.skipped),
    'failures': len(result.failures), 'errors': len(result.errors),
    'external_llm_calls': 0, 'successful': result.wasSuccessful(),
}
out.parent.mkdir(parents=True, exist_ok=True)
with out.open('x') as stream:
    stream.write(json.dumps(summary, indent=2) + '\n')
with log_path.open('x') as stream:
    stream.write(capture.getvalue())
print(json.dumps(summary, indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)
