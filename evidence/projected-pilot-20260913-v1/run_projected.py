"""One pinned DEV case using the prepared provider projection; at most two calls.

Reuses the frozen two-stage pipeline, budget, and full host validation. No retry,
response repair, key/model switch, service changes, or automatic resume.
"""
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
OFFLINE = BASE / 'offline-v1'
RUNTIME, LIVE = BASE / 'projected-pilot-preparation-v1', BASE / 'projected-pilot-live-v1'
AUTH = 'I_APPROVE_ONE_PROJECTED_DEV_CASE_TWO_ATTEMPTS'


def frozen_import(name, relative, expected):
    path = ROOT / relative
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('frozen_import_changed')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


p = frozen_import('projected_pilot_schema', 'evidence/provider-schema-20260913-v1/provider_schema.py',
                  '840484c628b2521c81f2f6c68096ae3839a74f8c4cd60a85fee75b3ca6f58369')
pilot = frozen_import('projected_pilot_frozen_pipeline', 'evidence/quote-pilot-20260913-v2/pilot.py',
                      '492c5bc341ee498f6c5d02619b539503d7229c036402a45753508ad21cd73167')
prior, CASE, Budget = pilot.prior, pilot.CASE, pilot.Budget


def inputs():
    inventory_path = OFFLINE / 'output-sha256.json'
    expected = 'f4b2376bfd4eff7001bccc0027ef214e781ac358df0744516fc2ebe498760bde'
    if prior.sha(inventory_path) != expected:
        raise ValueError('offline_inventory_changed')
    inventory = prior.read(inventory_path)
    if set(inventory) | {'output-sha256.json'} != {path.name for path in OFFLINE.iterdir()}:
        raise ValueError('offline_file_set_changed')
    # Verify the inventory before trusting its input pin list.
    if any(prior.sha(OFFLINE / name) != value for name, value in inventory.items()):
        raise ValueError('offline_output_changed')
    pins = prior.read(OFFLINE / 'input-sha256.json')
    pins.update({str(OFFLINE / name): value for name, value in inventory.items()})
    pins[str(inventory_path)] = expected
    control = BASE / 'control-live-v1/completion.json'
    pins[str(control)] = '7d4bb1d2d8e439573e80850e85d92dd73b5de5439fe1287f2fe3b5cd70850d1e'
    pins.update({str(path): prior.sha(path) for path in HERE.glob('*.py')})
    if any(prior.sha(path) != value for path, value in pins.items()):
        raise ValueError('input_pin_changed')
    if prior.read(control)['status'] != 'SYNTHETIC_CONTROL_PASS':
        raise ValueError('synthetic_control_must_pass_first')
    prepared = prior.read(OFFLINE / 'dev-first.envelope.json')
    request = prepared['host_request']
    if prepared != p.envelope(request) or prior.read(OFFLINE / 'manifest.json')['first_case'] != CASE:
        raise ValueError('fixed_envelope_changed')
    return pins, prepared


def new_directory(path):
    if path.exists() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('new_output_path_required_no_resume')
    path.mkdir(parents=True, exist_ok=False)


def prepare():
    pins, prepared = inputs()
    manifest = {'experiment': 'pnu.projected-pilot.v1', 'created_at': prior.now(), 'case_id': CASE,
                'approval': {'approved': True, 'user_message': 'ㄱ',
                             'scope': 'One fixed DEV case; extraction1, then semantic1 only if extraction validates; at most2 attempts.',
                             'data': 'Prepared development question, original draft and retrieved university sources; no gold or Judge results.',
                             'destination': 'Google Gemini generateContent'},
                'source_pins': pins, 'first_envelope_sha256': prepared['envelope_sha256'],
                'first_body_sha256': prepared['provider_body_sha256'], 'models': prior.MODELS,
                'max_attempts': 2, 'retries': 0, 'single_key': True, 'inter_call_seconds': 15,
                'timeout_seconds': 45, 'first_error_stops': True, 'automatic_resume': False,
                'known_previous_project_attempts': 167, 'account_daily_usage_verified': False,
                'candidate_gfc': None, 'eligible_for_service': False}
    new_directory(RUNTIME)
    prior.write_new(RUNTIME / 'manifest.json', manifest)
    print(json.dumps({'status': 'PREPARED', 'manifest_sha256': prior.sha(RUNTIME / 'manifest.json'), 'calls': 0}), flush=True)


def post(budget, stage, request, key):
    if request['payload']['stage'] != stage:
        raise ValueError('request_stage_mismatch')
    prepared = p.envelope(request)
    body, slot = prepared['provider_body'], CASE + '--' + stage
    prior.write_new(budget.root / (slot + '.request.json'), {'model': prior.MODELS[stage], 'envelope': prepared})
    budget.reserve(slot, stage, body)
    prior.CAPABILITY['url'] = prior.URLS[stage]
    status, started = None, time.perf_counter()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), prior.NoRedirect())
        req = urllib.request.Request(prior.URLS[stage], p.q.v1.encode(body).encode(),
                                     headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
        with opener.open(req, timeout=45) as response:
            status, raw = response.status, response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024 or key.encode() in raw:
            raise ValueError('unsafe_or_oversized_response')
        prior.write_new(budget.root / (slot + '.provider.txt'), raw.decode('utf-8'))
        payload = json.loads(raw)
        budget.finish(slot, 'received', status)
        text = prior.response_text(payload)
        prior.write_new(budget.root / (slot + '.response.txt'), text)
        prior.write_new(budget.root / (slot + '.receipt.json'), {'http_status': status,
                        'latency_ms': round((time.perf_counter() - started) * 1000, 3),
                        'usage_metadata': payload.get('usageMetadata', {}), 'model_version': payload.get('modelVersion'),
                        'response_sha256': hashlib.sha256(text.encode()).hexdigest()})
        # Full unchanged host schema, never the projected provider schema.
        validation = p.validate_response(prepared, text)
        prior.write_new(budget.root / (slot + '.host-validation.json'), validation)
        return text
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


def live(manifest_sha, authorize):
    if authorize != AUTH or prior.sha(RUNTIME / 'manifest.json') != manifest_sha:
        raise ValueError('exact_approval_and_manifest_required')
    pins, prepared = inputs()
    manifest = prior.read(RUNTIME / 'manifest.json')
    if (manifest['source_pins'] != pins or manifest['first_envelope_sha256'] != prepared['envelope_sha256']
            or manifest['first_body_sha256'] != prepared['provider_body_sha256'] or manifest['models'] != prior.MODELS):
        raise ValueError('runtime_inputs_changed')
    new_directory(LIVE)
    prior.write_new(LIVE / 'run.json', {'manifest': manifest, 'manifest_sha256': manifest_sha, 'started_at': prior.now()})
    budget = Budget(LIVE)
    result = {'status': 'STOPPED_INCOMPLETE', 'failure': None, 'decision': None}
    try:
        key = prior.load_key()
        result = pilot.pipeline(prepared['host_request'], LIVE, lambda stage, req: post(budget, stage, req, key),
                                lambda stage: budget.finish(CASE + '--' + stage, 'validated', 200), time.sleep)
    except Exception as error:
        result['failure'] = {'error_type': type(error).__name__, 'reason': 'stopped_no_retry_details_suppressed'}
    finally:
        rows = budget.rows()
        budget.db.close()
        result.update(case_id=CASE, finished_at=prior.now(), attempts=rows, reserved_attempts=len(rows),
                      http200_count=sum(row['http_status'] == 200 for row in rows),
                      extraction_valid=(LIVE / 'extraction-validation.json').exists(),
                      semantic_valid=(LIVE / 'semantic-validation.json').exists(),
                      retries=0, single_key=True, eligible_for_service=False, candidate_gfc=None,
                      account_daily_usage_verified=False,
                      limitation='Known development case, interface viability only; not a quality or GFC evaluation.')
        prior.write_new(LIVE / 'completion.json', result)
        prior.write_new(LIVE / 'output-sha256.json', {path.name: prior.sha(path) for path in LIVE.iterdir() if path.is_file()})
        print(json.dumps({key: result[key] for key in ('status', 'reserved_attempts', 'http200_count', 'extraction_valid', 'semantic_valid', 'failure')}, ensure_ascii=False), flush=True)
    return 0 if result['status'] == 'PILOT_COMPLETE' else 2


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
