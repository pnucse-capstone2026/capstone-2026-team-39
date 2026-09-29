"""One immutable synthetic schema-control request. No DEV/semantic call or retry."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = ROOT / 'processed/eval/preflight-20260913/provider-schema-v1'
SOURCE = BASE / 'offline-v1/synthetic-control.request.json'
SOURCE_SHA = '9e5a95e36f1d196d26d50d65c6dc40f284e924bea112ef2581b48034aca588eb'
RUNTIME, LIVE = BASE / 'control-preparation-v1', BASE / 'control-live-v1'
TRANSPORT = ROOT / 'evidence/semantic-live-20260913-v1/run.py'
TRANSPORT_SHA = '585858ceeafcba12d18ae3629abb6efa80f3b09f45efb95ba8ed48a97d7dd1bc'
AUTH = 'I_APPROVE_ONE_SYNTHETIC_SCHEMA_CONTROL'
SLOT = 'synthetic-control--extract'
if hashlib.sha256(TRANSPORT.read_bytes()).hexdigest() != TRANSPORT_SHA:
    raise ValueError('frozen_transport_changed')
spec = importlib.util.spec_from_file_location('schema_control_frozen_transport', TRANSPORT)
prior = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = prior
spec.loader.exec_module(prior)


def inputs():
    if prior.sha(SOURCE) != SOURCE_SHA:
        raise ValueError('frozen_control_changed')
    item = prior.read(SOURCE)
    if item['model'] != prior.MODELS['extract'] or item['record_type'] != 'synthetic_control_prepared_not_executed':
        raise ValueError('control_identity_changed')
    pins = {str(SOURCE): SOURCE_SHA, str(TRANSPORT): TRANSPORT_SHA,
            str(prior.ADAPTER / 'adapter.py'): prior.ADAPTER_SHA,
            str(prior.a.CONTRACT): prior.a.CONTRACT_SHA}
    pins.update({str(path): prior.sha(path) for path in HERE.glob('*.py')})
    if any(prior.sha(path) != value for path, value in pins.items()):
        raise ValueError('input_changed')
    return pins, item


def new_directory(path):
    if path.exists() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('new_output_path_required_no_resume')
    path.mkdir(parents=True, exist_ok=False)


def prepare():
    pins, item = inputs()
    manifest = {'experiment': 'pnu.schema-control.v1', 'created_at': prior.now(),
                'approval': {'approved': True, 'user_message': '응 진ㅇ해줘',
                             'scope': 'Exactly one prepared synthetic schema control; no DEV or semantic requests.',
                             'data': 'Literal Return exactly {"ok":true}. and a minimal boolean schema.',
                             'destination': prior.URLS['extract']},
                'source_pins': pins, 'body_sha256': prior.a.digest(item['body']),
                'model': item['model'], 'max_attempts': 1, 'retries': 0, 'single_key': True,
                'automatic_resume': False, 'timeout_seconds': 45, 'known_previous_project_attempts': 166,
                'account_daily_usage_verified': False, 'eligible_for_service': False, 'candidate_gfc': None}
    new_directory(RUNTIME)
    prior.write_new(RUNTIME / 'manifest.json', manifest)
    print(json.dumps({'status': 'PREPARED', 'manifest_sha256': prior.sha(RUNTIME / 'manifest.json'), 'calls': 0}), flush=True)


class Budget(prior.Budget):
    def reserve(self, slot, stage, body):
        if slot != SLOT or stage != 'extract' or self.rows():
            raise ValueError('only_one_synthetic_attempt_allowed')
        super().reserve(slot, stage, body)


def validate(raw):
    value = prior.a.strict_json(raw)
    if type(value) is not dict or set(value) != {'ok'} or value['ok'] is not True:
        raise ValueError('synthetic_control_output_mismatch')
    return value


def post(budget, item, key):
    if item != inputs()[1]:
        raise ValueError('only_frozen_control_request_allowed')
    prior.write_new(budget.root / 'request.json', item)
    budget.reserve(SLOT, 'extract', item['body'])
    prior.CAPABILITY['url'] = prior.URLS['extract']
    status, started = None, time.perf_counter()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), prior.NoRedirect())
        request = urllib.request.Request(prior.URLS['extract'], prior.a.encode(item['body']).encode(),
                                         headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
        with opener.open(request, timeout=45) as response:
            status, raw = response.status, response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024 or key.encode() in raw:
            raise ValueError('unsafe_or_oversized_response')
        prior.write_new(budget.root / 'provider.txt', raw.decode('utf-8'))
        payload = json.loads(raw)
        budget.finish(SLOT, 'received', status)
        text = prior.response_text(payload)
        prior.write_new(budget.root / 'response.txt', text)
        prior.write_new(budget.root / 'receipt.json', {'http_status': status,
                        'latency_ms': round((time.perf_counter() - started) * 1000, 3),
                        'usage_metadata': payload.get('usageMetadata', {}), 'model_version': payload.get('modelVersion'),
                        'response_sha256': hashlib.sha256(text.encode()).hexdigest()})
        validated = validate(text)
        budget.finish(SLOT, 'validated', status)
        return validated
    except urllib.error.HTTPError as error:
        budget.finish(SLOT, 'http_error', error.code)
        try:
            detail = error.read(65536).decode('utf-8', errors='replace').replace(key, '[REDACTED]')
            prior.write_new(budget.root / 'http-error.txt', detail)
        except Exception:
            pass
        raise
    except Exception:
        budget.finish(SLOT, 'failed', status)
        raise
    finally:
        prior.CAPABILITY['url'] = None


def live(manifest_sha, authorize):
    if authorize != AUTH or prior.sha(RUNTIME / 'manifest.json') != manifest_sha:
        raise ValueError('exact_approval_and_manifest_required')
    pins, item = inputs()
    manifest = prior.read(RUNTIME / 'manifest.json')
    if manifest['source_pins'] != pins or manifest['body_sha256'] != prior.a.digest(item['body']):
        raise ValueError('runtime_input_changed')
    new_directory(LIVE)
    prior.write_new(LIVE / 'run.json', {'manifest': manifest, 'manifest_sha256': manifest_sha, 'started_at': prior.now()})
    budget = Budget(LIVE)
    result = {'status': 'STOPPED_INCOMPLETE', 'failure': None, 'validated_output': None}
    try:
        result['validated_output'] = post(budget, item, prior.load_key())
        result['status'] = 'SYNTHETIC_CONTROL_PASS'
    except Exception as error:
        result['failure'] = {'error_type': type(error).__name__,
                             'http_status': error.code if isinstance(error, urllib.error.HTTPError) else None,
                             'reason': 'stopped_no_retry_details_suppressed'}
    finally:
        rows = budget.rows()
        budget.db.close()
        result.update(attempts=rows, reserved_attempts=len(rows), http200_count=sum(row['http_status'] == 200 for row in rows),
                      finished_at=prior.now(), retries=0, single_key=True, dev_calls=0, semantic_calls=0,
                      eligible_for_service=False, candidate_gfc=None, account_daily_usage_verified=False,
                      limitation='Minimal synthetic schema only. Does not prove full projected schema acceptance or service performance.')
        prior.write_new(LIVE / 'completion.json', result)
        prior.write_new(LIVE / 'output-sha256.json', {path.name: prior.sha(path) for path in LIVE.iterdir() if path.is_file()})
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result['status'] == 'SYNTHETIC_CONTROL_PASS' else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'live'))
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--authorize')
    args = parser.parse_args()
    sys.addaudithook(prior.audit)
    if args.mode == 'prepare':
        prepare()
    else:
        raise SystemExit(live(args.manifest_sha256, args.authorize))
