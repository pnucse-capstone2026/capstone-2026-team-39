"""One fixed DEV case, at most three requests, no retries/resume/key rotation."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

import scope_first as c

prior, q = c.p.r.prior, c.q
BASE = c.ROOT / 'processed/eval/preflight-20260914/scope-first-v1'
PREP, LIVE = BASE / 'preparation-v1', BASE / 'live-v1'
SLOTS = ('scope', 'extract', 'semantic_review')
STAGES = ('extract', 'extract', 'semantic_review')
AUTH = 'I_APPROVE_SCOPE_FIRST_THREE_ATTEMPTS'
SCOPE_RUNNER_SHA = 'f9606e23f2063e92adb6aa7f0387fd7ce141b428c37e3bd02e6cf8ab002834b4'
SCOPE_INVENTORY_SHA = '815e135c790e534601b20fb61af565d1e5c56685392b88697d482ca589ecb3f1'


def inputs():
    path = c.ROOT / 'evidence/scope-audit-20260914-v1/run_offline.py'
    if prior.sha(path) != SCOPE_RUNNER_SHA:
        raise ValueError('frozen_scope_runner_changed')
    spec = importlib.util.spec_from_file_location('scope_first_previous_run', path)
    previous = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = previous
    spec.loader.exec_module(previous)
    pins, request, _ = previous.input_set()
    inventory_path = previous.OUT / 'output-sha256.json'
    if prior.sha(inventory_path) != SCOPE_INVENTORY_SHA:
        raise ValueError('scope_output_inventory_changed')
    inventory = prior.read(inventory_path)
    if set(inventory) | {'output-sha256.json'} != {p.name for p in previous.OUT.iterdir()}:
        raise ValueError('scope_output_file_set_changed')
    pins.update({str(previous.OUT / name): sha for name, sha in inventory.items()})
    pins[str(inventory_path)] = SCOPE_INVENTORY_SHA
    pins.update({str(p): prior.sha(p) for p in Path(__file__).parent.glob('*.py')})
    verify_pins(pins)
    return pins, request


def verify_pins(pins):
    if any(prior.sha(path) != sha for path, sha in pins.items()):
        raise ValueError('source_pin_changed')


def settings():
    return {'slots': list(SLOTS), 'models': prior.MODELS, 'max_attempts': 3,
            'retries': 0, 'timeout_seconds': 45, 'inter_call_seconds': 15,
            'single_key': True, 'first_error_stops': True, 'automatic_resume': False,
            'eligible_for_service': False, 'candidate_gfc': None}


def prepare():
    pins, base = inputs()
    req = c.scope_request(base)
    manifest = {'experiment': c.VERSION, 'created_at': prior.now(), 'source_pins': pins,
                'base_request_id': base['request_id'], 'scope_request': c.envelope(req, c.scope_request(base)),
                'scope': 'Selected known DEV one-case staged extraction pilot, NOT new generation or GFC evaluation.',
                'transmitted_data': {'scope': 'Original DEV query only.',
                    'extract': 'Same original DEV query/raw draft/eight retrieved sources plus fresh exact-query scope proposals.',
                    'semantic_review': 'Original query/raw draft/eight sources and proposed source IDs; no extractor tags or reasons.'},
                'destination': 'Google Gemini generateContent',
                'approval_status': 'REQUIRES_EXPLICIT_EXECUTION_APPROVAL',
                'known_previous_project_attempts': 172, 'account_daily_usage_verified': False, **settings()}
    c.p.r.s.previous.new_directory(PREP)
    prior.write_new(PREP / 'manifest.json', manifest)
    print(json.dumps({'status': 'PREPARED', 'external_calls': 0, 'pinned_inputs': len(pins),
                      'manifest_sha256': prior.sha(PREP / 'manifest.json')}), flush=True)


class Budget(prior.Budget):
    def reserve(self, slot, stage, body):
        rows = self.rows()
        count = len(rows)
        if (count >= 3 or (slot, stage) != (SLOTS[count], STAGES[count])
                or (rows and rows[-1]['state'] != 'validated')):
            raise ValueError('three_slot_order_or_previous_not_validated')
        super().reserve(slot, stage, body)


class ReadinessHeld(ValueError):
    pass


def expected_request(slot, base, history):
    if slot == 'scope': return c.scope_request(base)
    if slot == 'extract': return c.extraction_request(base, history['scope'])
    if slot == 'semantic_review': return c.review_request(base, history['scope'], history['extract'])
    raise ValueError('unknown_slot')


def validate(slot, base, history, text):
    if slot == 'scope':
        _, result = c.scope_bridge(base, text)
        ready = bool(result['query_scope']) and not result['unresolved_dimensions']
    elif slot == 'extract':
        result = c.extraction_bridge(base, history['scope'], text)
        ready = result['scope_audit']['local_preconditions_satisfied']
    else:
        result = q.parse_semantic(expected_request(slot, base, history), text)
        ready = True  # A valid negative review is retained, never promoted to entailed.
    return {'host_validation': result, 'next_stage_locally_ready': ready,
            'eligible_for_service': False, 'candidate_gfc': None}


def pipeline(base, call, pause):
    history, slot = {}, SLOTS[0]
    try:
        for slot in SLOTS:
            if history: pause(15)
            request = expected_request(slot, base, history)
            text = call(slot, request, history)
            checked = validate(slot, base, history, text)
            history[slot] = text
            if not checked['next_stage_locally_ready']:
                raise ReadinessHeld('stage_held_no_next_call:' + slot)
        result = c.combine(base, history['scope'], history['extract'], history['semantic_review'])
        if result['decision']['status'] != 'offline_review_complete':
            raise ValueError('combined_decision_invalid')
        return {'status': 'PILOT_COMPLETE', 'failure': None, 'result': result}
    except Exception as error:
        return {'status': 'STOPPED_HELD' if isinstance(error, ReadinessHeld) else 'STOPPED_INCOMPLETE',
                'result': None, 'failure': {'slot': slot, 'error_type': type(error).__name__,
                    'http_status': error.code if isinstance(error, urllib.error.HTTPError) else None,
                    'reason': str(error)[:300] if isinstance(error, ValueError) else 'first_error_no_retry_details_suppressed'}}


def post(budget, slot, request, base, history, key, pins):
    verify_pins(pins)
    prepared = c.envelope(request, expected_request(slot, base, history))
    body, stage = prepared['provider_body'], STAGES[SLOTS.index(slot)]
    budget.reserve(slot, stage, body)
    prior.write_new(budget.root / (slot + '.request.json'), {'envelope': prepared, 'model': prior.MODELS[stage]})
    status, started = None, time.perf_counter()
    prior.CAPABILITY['url'] = prior.URLS[stage]
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), prior.NoRedirect())
        sent = urllib.request.Request(prior.URLS[stage], c.v1.encode(body).encode(),
                                      headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
        with opener.open(sent, timeout=45) as received:
            status, raw = received.status, received.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024 or key.encode() in raw:
            raise ValueError('unsafe_or_oversized_provider_response')
        prior.write_new(budget.root / (slot + '.provider.txt'), raw.decode('utf-8'))
        payload = json.loads(raw)
        budget.finish(slot, 'received', status)
        prior.write_new(budget.root / (slot + '.receipt.json'), {
            'http_status': status, 'latency_ms': round((time.perf_counter() - started) * 1000, 3),
            'usage_metadata': payload.get('usageMetadata', {}), 'model_version': payload.get('modelVersion'),
            'finish_reasons': [x.get('finishReason') for x in payload.get('candidates', [])]})
        text = prior.response_text(payload)
        prior.write_new(budget.root / (slot + '.response.txt'), text)
        checked = validate(slot, base, history, text)
        prior.write_new(budget.root / (slot + '.host-validation.json'), checked)
        if not checked['next_stage_locally_ready']:
            budget.finish(slot, 'held', status)
            raise ReadinessHeld('stage_held_no_next_call:' + slot)
        budget.finish(slot, 'validated', status)
        print(json.dumps({'slot': slot, 'status': 'HOST_VALIDATED', 'http_status': status}), flush=True)
        return text
    except ReadinessHeld:
        raise
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


def live(manifest_sha, authorization, approval_message):
    if authorization != AUTH or not approval_message or prior.sha(PREP / 'manifest.json') != manifest_sha:
        raise ValueError('exact_manifest_and_explicit_approval_required')
    pins, base = inputs()
    manifest = prior.read(PREP / 'manifest.json')
    req = c.scope_request(base)
    if (manifest['source_pins'] != pins or manifest['base_request_id'] != base['request_id']
            or manifest['scope_request'] != c.envelope(req, req)
            or any(manifest[k] != value for k, value in settings().items())):
        raise ValueError('prepared_run_changed')
    c.p.r.s.previous.new_directory(LIVE)
    prior.write_new(LIVE / 'run.json', {'manifest': manifest, 'manifest_sha256': manifest_sha,
                                      'explicit_approval_message': approval_message, 'started_at': prior.now()})
    budget = Budget(LIVE)
    result = {'status': 'STOPPED_INCOMPLETE', 'failure': None, 'result': None}
    try:
        key = prior.load_key()
        result = pipeline(base, lambda slot, request, history: post(budget, slot, request, base, history, key, pins), time.sleep)
    except Exception as error:
        result['failure'] = {'error_type': type(error).__name__, 'reason': 'no_retry_details_suppressed'}
    finally:
        rows = budget.rows()
        budget.db.close()
        if result['result'] is not None:
            prior.write_new(LIVE / 'decision.json', result.pop('result'))
        else:
            result.pop('result')
        result.update(attempts=rows, reserved_attempts=len(rows),
                      http200_count=sum(x['http_status'] == 200 for x in rows),
                      finished_at=prior.now(), retries=0, single_key=True,
                      original_run_reclassified=False, eligible_for_service=False, candidate_gfc=None,
                      new_answer_generation_calls=0, new_judge_gfc_calls=0,
                      account_daily_usage_verified=False)
        prior.write_new(LIVE / 'completion.json', result)
        prior.write_new(LIVE / 'output-sha256.json', {p.name: prior.sha(p) for p in LIVE.iterdir() if p.is_file()})
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result['status'] == 'PILOT_COMPLETE' else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'live'))
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--authorize')
    parser.add_argument('--approval-message')
    args = parser.parse_args()
    sys.addaudithook(prior.audit)
    if args.mode == 'prepare': prepare()
    else: raise SystemExit(live(args.manifest_sha256, args.authorize, args.approval_message))
