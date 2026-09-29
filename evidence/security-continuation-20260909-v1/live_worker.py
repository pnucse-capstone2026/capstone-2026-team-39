"""Reuse the exact original owned-worker implementation with continuation controls."""
import importlib.util
import sys

import live_runner as continuation

continuation.bridge.guard.verify_file(continuation.ORIGINAL_WORKER, continuation.WORKER_SHA)
spec = importlib.util.spec_from_file_location('_pnu_pinned_live_worker', continuation.ORIGINAL_WORKER)
worker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = worker
spec.loader.exec_module(worker)

if __name__ == '__main__':
    worker.main()
