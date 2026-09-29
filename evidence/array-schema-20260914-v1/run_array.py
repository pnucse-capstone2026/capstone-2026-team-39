"""Three-slot validation: synthetic schema, one DEV extraction, independent review.

Approval required; primary key only; no retries, resume or fallback; first error stops.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

import array_schema as s

prior, q = s.prior, s.q
BASE = s.ROOT / 'processed/eval/preflight-20260914/array-schema-v1'
PREP, LIVE = BASE / 'preparation-v1', BASE / 'live-v1'
CASE = s.previous.CASE
SLOTS = ('synthetic--extract', CASE + '--extract', CASE + '--semantic_review')
STAGES = ('extract', 'extract', 'semantic_review')
AUTH = 'I_APPROVE_ARRAY_SCHEMA_THREE_ATTEMPTS'


class Budget(prior.Budget):
    def reserve(self, slot, stage, body):
        rows = self.rows()
        count = len(rows)
        if (count >= 3 or (slot, stage) != (SLOTS[count], STAGES[count])
                or (rows and rows[-1]['state'] != 'validated')):
            raise ValueError('fixed_three_slot_order_or_previous_not_validated')
        super().reserve(slot, stage, body)


def prepare():
    pins, synthetic, request = s.inputs()
    manifest = {'experiment': s.VERSION, 'created_at': prior.now(), 'source_pins': pins,
                'approval': {'status': 'REQUIRES_EXPLICIT_THREE_ATTEMPT_APPROVAL',
                             'scope': 'Synthetic schema1; then fixed DEV extraction1; then semantic1 only after full host validation. First error stops.',
                             'data': 'Synthetic ALPHA then prior DEV query/draft/sources. No holdout, gold or Judge.',
                             'destination': 'Google Gemini generateContent'},
                'requests': {'synthetic': s.envelope(synthetic), 'dev': s.envelope(request)},
                'models': prior.MODELS, 'slots': list(SLOTS), 'max_attempts': 3, 'retries': 0,
                'timeout_seconds': 45, 'inter_call_seconds': 15, 'single_key': True,
                'first_error_stops': True, 'automatic_resume': False,
                'known_previous_project_attempts': 170, 'account_daily_usage_verified': False,
                'eligible_for_service': False, 'candidate_gfc': None}
    s.previous.new_directory(PREP)
    prior.write_new(PREP / 'manifest.json', manifest)
    print(json.dumps({'status': 'PREPARED', 'calls': 0, 'input_pins': len(pins),
                      'manifest_sha256': prior.sha(PREP / 'manifest.json')}), flush=True)


def post(budget, slot, stage, request, key):
    if request['payload']['stage'] != stage:
        raise ValueError('stage_mismatch')
    prepared = s.envelope(request)
    body = prepared['provider_body']
    budget.reserve(slot, stage, body)
    prior.write_new(budget.root / (slot + '.request.json'), {'envelope': prepared, 'model': prior.MODELS[stage]})
    status, started = None, time.perf_counter()
    prior.CAPABILITY['url'] = prior.URLS[stage]
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), prior.NoRedirect())
        sent = urllib.request.Request(prior.URLS[stage], q.v1.encode(body).encode(),
                                      headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
        with opener.open(sent, timeout=45) as response:
            status, raw = response.status, response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024 or key.encode() in raw:
            raise ValueError('unsafe_or_oversized_response')
        prior.write_new(budget.root / (slot + '.provider.txt'), raw.decode('utf-8'))
        payload = json.loads(raw)
        budget.finish(slot, 'received', status)
        prior.write_new(budget.root / (slot + '.receipt.json'), {
            'http_status': status, 'latency_ms': round((time.perf_counter() - started) * 1000, 3),
            'usage_metadata': payload.get('usageMetadata', {}), 'model_version': payload.get('modelVersion'),
            'finish_reasons': [c.get('finishReason') for c in payload.get('candidates', [])]})
        text = prior.response_text(payload)
        prior.write_new(budget.root / (slot + '.response.txt'), text)
        if slot == SLOTS[0]:
            # Diagnostic checks schema + request binding, NOT evidence/semantic truth.
            q.response(request, text, 'extract')
            validation = {'schema_valid': True, 'valid_extraction': False,
                          'eligible_for_service': False, 'candidate_gfc': None}
        else:
            validation = s.validate_response(prepared, text)
        prior.write_new(budget.root / (slot + '.host-validation.json'), validation)
        budget.finish(slot, 'validated', status)
        print(json.dumps({'status': 'SLOT_VALIDATED', 'slot': slot, 'http_status': status}), flush=True)
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


def pipeline(synthetic, request, root, call, pause):
    slot = SLOTS[0]
    try:
        call(slot, 'extract', synthetic)
        pause(15)
        slot = SLOTS[1]
        raw = call(slot, 'extract', request)
        semantic = q.build_semantic_request(request, raw)
        pause(15)
        slot = SLOTS[2]
        reviewed = call(slot, 'semantic_review', semantic)
        decision = q.combine(request, raw, reviewed)
        if decision['status'] != 'offline_review_complete':
            raise ValueError('combined_result_blocked')
        prior.write_new(root / 'decision.json', decision)
        return {'status': 'PILOT_COMPLETE', 'failure': None, 'decision': decision}
    except Exception as error:
        return {'status': 'STOPPED_INCOMPLETE', 'decision': None,
                'failure': {'slot': slot, 'error_type': type(error).__name__,
                            'http_status': error.code if isinstance(error, urllib.error.HTTPError) else None,
                            'reason': str(error)[:300] if isinstance(error, ValueError) else 'first_error_no_retry_details_suppressed'}}


def live(manifest_sha, authorize, approval_message):
    if authorize != AUTH or not approval_message or prior.sha(PREP / 'manifest.json') != manifest_sha:
        raise ValueError('exact_approval_and_manifest_required')
    pins, synthetic, request = s.inputs()
    manifest = prior.read(PREP / 'manifest.json')
    if (manifest['source_pins'] != pins or manifest['models'] != prior.MODELS
            or manifest['requests'] != {'synthetic': s.envelope(synthetic), 'dev': s.envelope(request)}
            or manifest['slots'] != list(SLOTS) or manifest['max_attempts'] != 3):
        raise ValueError('runtime_inputs_changed')
    s.previous.new_directory(LIVE)
    prior.write_new(LIVE / 'run.json', {'manifest': manifest, 'manifest_sha256': manifest_sha,
                                      'explicit_approval_message': approval_message, 'started_at': prior.now()})
    budget = Budget(LIVE)
    result = {'status': 'STOPPED_INCOMPLETE', 'decision': None, 'failure': None}
    try:
        key = prior.load_key()
        result = pipeline(synthetic, request, LIVE, lambda slot, stage, req: post(budget, slot, stage, req, key), time.sleep)
    except Exception as error:
        result['failure'] = {'error_type': type(error).__name__, 'reason': 'stopped_no_retry_details_suppressed'}
    finally:
        rows = budget.rows()
        budget.db.close()
        result.update(attempts=rows, reserved_attempts=len(rows), finished_at=prior.now(),
                      http200_count=sum(r['http_status'] == 200 for r in rows), retries=0, single_key=True,
                      extraction_valid=(LIVE / (SLOTS[1] + '.host-validation.json')).exists(),
                      semantic_valid=(LIVE / (SLOTS[2] + '.host-validation.json')).exists(),
                      eligible_for_service=False, candidate_gfc=None, account_daily_usage_verified=False)
        prior.write_new(LIVE / 'completion.json', result)
        prior.write_new(LIVE / 'output-sha256.json', {path.name: prior.sha(path) for path in LIVE.iterdir() if path.is_file()})
        print(json.dumps({k: v for k, v in result.items() if k != 'decision'}, ensure_ascii=False, indent=2), flush=True)
    return 0 if result['status'] == 'PILOT_COMPLETE' else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'live'))
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--authorize')
    parser.add_argument('--approval-message')
    args = parser.parse_args()
    sys.addaudithook(prior.audit)
    if args.mode == 'prepare':
        prepare()
    else:
        raise SystemExit(live(args.manifest_sha256, args.authorize, args.approval_message))
