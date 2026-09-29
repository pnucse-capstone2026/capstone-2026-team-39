"""Two pinned diagnostic contrasts, not an extraction or quality evaluation.

A: original content + minimal schema. B: synthetic content + full schema.
Each changes one factor versus the saved full/full failure, not versus each other.
One key, zero retries, durable cap2, first error stops, exclusive output paths.
"""
from __future__ import annotations

import argparse
import copy
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
PREVIOUS = ROOT / 'evidence/projected-pilot-20260913-v1/run_projected.py'
PREVIOUS_SHA = '43e24b2b0239bbbd01b7f85e0cb7999fc32f6963c7e14223de433719e5dfbfcb'
if hashlib.sha256(PREVIOUS.read_bytes()).hexdigest() != PREVIOUS_SHA:
    raise ValueError('frozen_previous_runner_changed')
spec = importlib.util.spec_from_file_location('isolation_frozen_projected', PREVIOUS)
previous = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = previous
spec.loader.exec_module(previous)
prior, p = previous.prior, previous.p
BASE = previous.BASE
RUNTIME, LIVE = BASE / 'isolation-preparation-v1', BASE / 'isolation-live-v1'
AUTH = 'I_APPROVE_TWO_SCHEMA_ISOLATION_ATTEMPTS'
SLOTS = ('a-original-content-minimal-schema', 'b-synthetic-content-full-schema')
MINIMAL_SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                  'required': ['ok'], 'additionalProperties': False}


def contrasts(full_body):
    first, second = copy.deepcopy(full_body), copy.deepcopy(full_body)
    first['generationConfig']['responseJsonSchema'] = copy.deepcopy(MINIMAL_SCHEMA)
    synthetic = p.q.build_extraction_request('제7회 ALPHA 공모전에서 취소자 참가비 처리는?',
        '취소자는 참가비 환수 대상입니다.',
        [{'chunk_id': 's1', 'source_title': '제7회 ALPHA 공모전', 'text': '취소자는 참가비 환수 대상입니다.'}])
    second['contents'] = p.envelope(synthetic)['provider_body']['contents']
    return [{'slot': SLOTS[0], 'body': first, 'changed_factor': 'responseJsonSchema_only',
             'contains_development_content': True},
            {'slot': SLOTS[1], 'body': second, 'changed_factor': 'contents_only',
             'contains_development_content': False}]


def inputs():
    pins, prepared = previous.inputs()
    inventory_path = previous.LIVE / 'output-sha256.json'
    expected = '2cc3cf16e4d18551a8043bb3066f4d048b203582d14a21a6c5b57bda96bddf21'
    if prior.sha(inventory_path) != expected:
        raise ValueError('previous_failure_inventory_changed')
    inventory = prior.read(inventory_path)
    if set(inventory) | {'output-sha256.json'} != {path.name for path in previous.LIVE.iterdir()}:
        raise ValueError('previous_failure_file_set_changed')
    pins.update({str(previous.LIVE / name): value for name, value in inventory.items()})
    pins[str(inventory_path)] = expected
    pins.update({str(path): prior.sha(path) for path in HERE.glob('*.py')})
    if any(prior.sha(path) != value for path, value in pins.items()):
        raise ValueError('input_pin_changed')
    saved = prior.read(previous.LIVE / (previous.CASE + '--extract.request.json'))
    if saved['envelope'] != prepared:
        raise ValueError('previous_body_changed')
    return pins, contrasts(saved['envelope']['provider_body'])


def prepare():
    pins, requests = inputs()
    manifest = {'experiment': 'pnu.schema-isolation.v1', 'created_at': prior.now(),
                'approval': {'approved': True, 'user_message': '계속ㅐ줘',
                             'scope': 'At most two fixed diagnostic contrasts, A then B only after A succeeds; no retries; first error stops.',
                             'destination': prior.URLS['extract'],
                             'data': 'A original known DEV query/draft/sources; B synthetic ALPHA fixture. No holdout, gold or Judge.'},
                'source_pins': pins, 'requests': requests,
                'request_body_sha256': {item['slot']: prior.a.digest(item['body']) for item in requests},
                'model': prior.MODELS['extract'], 'max_attempts': 2, 'retries': 0, 'single_key': True,
                'inter_call_seconds': 15, 'timeout_seconds': 45, 'automatic_resume': False,
                'measurement': 'HTTP acceptance, finish reason and JSON syntax only; never valid extraction or GFC.',
                'known_previous_project_attempts': 168, 'account_daily_usage_verified': False,
                'candidate_gfc': None, 'eligible_for_service': False}
    previous.new_directory(RUNTIME)
    prior.write_new(RUNTIME / 'manifest.json', manifest)
    print(json.dumps({'status': 'PREPARED', 'calls': 0, 'manifest_sha256': prior.sha(RUNTIME / 'manifest.json')}), flush=True)


class Budget(prior.Budget):
    def reserve(self, slot, stage, body):
        rows = self.rows()
        if (stage != 'extract' or len(rows) >= 2 or slot != SLOTS[len(rows)]
                or (rows and rows[0]['state'] != 'diagnostic_complete')):
            raise ValueError('fixed_order_cap_or_prior_failure')
        super().reserve(slot, stage, body)


def validate_diagnostic(slot, text):
    value = prior.a.strict_json(text)
    if slot == SLOTS[0] and (type(value) is not dict or set(value) != {'ok'} or type(value['ok']) is not bool):
        raise ValueError('minimal_schema_output_mismatch')
    if slot not in SLOTS:
        raise ValueError('unknown_diagnostic_slot')
    return {'http_accepted': True, 'json_syntax_valid': True, 'valid_extraction': False,
            'candidate_gfc': None, 'eligible_for_service': False}


def post(budget, item, key):
    slot, body = item['slot'], item['body']
    prior.write_new(budget.root / (slot + '.request.json'), {'model': prior.MODELS['extract'], **item})
    budget.reserve(slot, 'extract', body)
    prior.CAPABILITY['url'] = prior.URLS['extract']
    status, started = None, time.perf_counter()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), prior.NoRedirect())
        request = urllib.request.Request(prior.URLS['extract'], prior.a.encode(body).encode(),
                                         headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
        with opener.open(request, timeout=45) as response:
            status, raw = response.status, response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024 or key.encode() in raw:
            raise ValueError('unsafe_or_oversized_response')
        prior.write_new(budget.root / (slot + '.provider.txt'), raw.decode('utf-8'))
        payload = json.loads(raw)
        budget.finish(slot, 'received', status)
        prior.write_new(budget.root / (slot + '.receipt.json'), {'http_status': status,
                        'latency_ms': round((time.perf_counter() - started) * 1000, 3),
                        'usage_metadata': payload.get('usageMetadata', {}), 'model_version': payload.get('modelVersion'),
                        'finish_reasons': [candidate.get('finishReason') for candidate in payload.get('candidates', [])]})
        text = prior.response_text(payload)  # non-STOP is an error, even after HTTP200
        prior.write_new(budget.root / (slot + '.response.txt'), text)
        diagnostic = validate_diagnostic(slot, text)
        prior.write_new(budget.root / (slot + '.diagnostic.json'), diagnostic)
        budget.finish(slot, 'diagnostic_complete', status)
        return diagnostic
    except urllib.error.HTTPError as error:
        budget.finish(slot, 'http_error', error.code)
        try:
            detail = error.read(65536).decode('utf-8', errors='replace').replace(key, '[REDACTED]')
            prior.write_new(budget.root / (slot + '.http-error.txt'), detail)
        except Exception:
            pass
        raise
    except Exception:
        budget.finish(slot, 'failed', status)
        raise
    finally:
        prior.CAPABILITY['url'] = None


def run_requests(requests, call, pause):
    completed = []
    if tuple(item['slot'] for item in requests) != SLOTS:
        raise ValueError('fixed_two_contrasts_required')
    for item in requests:
        try:
            if completed:
                pause(15)
            diagnostic = call(item)
            completed.append({'slot': item['slot'], **diagnostic})
            print(json.dumps({'status': 'DIAGNOSTIC_COMPLETE', 'slot': item['slot']}), flush=True)
        except Exception as error:
            return {'status': 'STOPPED_INCOMPLETE', 'completed': completed,
                    'failure': {'slot': item['slot'], 'error_type': type(error).__name__,
                                'http_status': error.code if isinstance(error, urllib.error.HTTPError) else None,
                                'reason': 'first_error_no_retry_details_suppressed'}}
    return {'status': 'TWO_DIAGNOSTICS_COMPLETE', 'completed': completed, 'failure': None}


def live(manifest_sha, authorize):
    if authorize != AUTH or prior.sha(RUNTIME / 'manifest.json') != manifest_sha:
        raise ValueError('exact_approval_and_manifest_required')
    pins, requests = inputs()
    manifest = prior.read(RUNTIME / 'manifest.json')
    if (manifest['source_pins'] != pins or manifest['requests'] != requests
            or manifest['model'] != prior.MODELS['extract']
            or manifest['request_body_sha256'] != {item['slot']: prior.a.digest(item['body']) for item in requests}):
        raise ValueError('runtime_input_changed')
    previous.new_directory(LIVE)
    prior.write_new(LIVE / 'run.json', {'manifest': manifest, 'manifest_sha256': manifest_sha, 'started_at': prior.now()})
    budget = Budget(LIVE)
    result = {'status': 'STOPPED_INCOMPLETE', 'completed': [], 'failure': None}
    try:
        key = prior.load_key()
        result = run_requests(requests, lambda item: post(budget, item, key), time.sleep)
    except Exception as error:
        result['failure'] = {'error_type': type(error).__name__, 'reason': 'stopped_no_retry_details_suppressed'}
    finally:
        rows = budget.rows()
        budget.db.close()
        result.update(attempts=rows, reserved_attempts=len(rows), http200_count=sum(row['http_status'] == 200 for row in rows),
                      finished_at=prior.now(), retries=0, single_key=True, semantic_calls=0,
                      valid_extraction=False, eligible_for_service=False, candidate_gfc=None,
                      account_daily_usage_verified=False)
        prior.write_new(LIVE / 'completion.json', result)
        prior.write_new(LIVE / 'output-sha256.json', {path.name: prior.sha(path) for path in LIVE.iterdir() if path.is_file()})
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result['status'] == 'TWO_DIAGNOSTICS_COMPLETE' else 2


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
