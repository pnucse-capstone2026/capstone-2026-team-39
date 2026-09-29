"""One fixed DEV case, at most two provider attempts, first error stops.

Uses immutable v2 model inputs/schema and the prior transport's safety primitives.
No automatic resume, retry, model/key switch, gold, or service changes.
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
BASE = ROOT / 'processed/eval/preflight-20260913/quote-adapter-v2'
PREP, RUNTIME, LIVE = (BASE / name for name in ('preparation-v1', 'pilot-preparation-v1', 'pilot-live-v1'))
CASE = 'shadow_emp_01'
AUTH = 'I_APPROVE_QUOTE_V2_ONE_CASE_TWO_ATTEMPTS'
PREP_SHA = '893af0f6fa0a59618297da4506b04a2ab242cf9a6009f9901cee7249f7ce5485'
INVENTORY_SHA = 'a0eaa91d3e374ab8af0b8ea35f79521dc9aeb2bfb099d3952de4841c60e8b555'
FROZEN_IMPORTS = {
    ROOT / 'evidence/quote-adapter-20260913-v2/quote_adapter.py': '458f3dcba124d99dbadebd0323daf512746d2e5b6a9716cced7b32e0bb6a5461',
    ROOT / 'evidence/semantic-live-20260913-v1/run.py': '585858ceeafcba12d18ae3629abb6efa80f3b09f45efb95ba8ed48a97d7dd1bc'}
for path, expected in FROZEN_IMPORTS.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('frozen_import_changed')
    sys.path.insert(0, str(path.parent))
import quote_adapter as q
# q imports its frozen dependency tree, which also contains a run.py. Resolve
# the transport by its pinned absolute path, never by that ambiguous module name.
_spec = importlib.util.spec_from_file_location('quote_pilot_frozen_transport', ROOT / 'evidence/semantic-live-20260913-v1/run.py')
prior = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = prior
_spec.loader.exec_module(prior)


def prepared():
    if prior.sha(PREP / 'manifest.json') != PREP_SHA or prior.sha(PREP / 'output-sha256.json') != INVENTORY_SHA:
        raise ValueError('v2_preparation_changed')
    inventory = prior.read(PREP / 'output-sha256.json')
    if set(inventory) | {'output-sha256.json'} != {p.name for p in PREP.iterdir()}:
        raise ValueError('v2_preparation_file_set_changed')
    pins = prior.read(PREP / 'input-sha256.json')
    pins.update({str(PREP / name): value for name, value in inventory.items()})
    pins[str(PREP / 'output-sha256.json')] = INVENTORY_SHA
    pins.update({str(p): prior.sha(p) for p in HERE.glob('*.py')})
    if any(prior.sha(path) != value for path, value in pins.items()):
        raise ValueError('pinned_input_changed')
    manifest = prior.read(PREP / 'manifest.json')
    if manifest['next_step_proposal']['first_case'] != CASE or manifest['slots'][0]['case_id'] != CASE:
        raise ValueError('fixed_pilot_case_changed')
    item = prior.read(PREP / ('extract--' + CASE + '.request.json'))
    if item['record_type'] != 'v2_prepared_not_executed' or item['model_bundle'] != q.model_bundle(item['request']):
        raise ValueError('prepared_bundle_changed')
    return pins, item['request']


def body(request):
    bundle = q.model_bundle(request)
    return {'systemInstruction': {'parts': [{'text': bundle['messages'][0]['content']}]},
            'contents': [{'role': 'user', 'parts': [{'text': bundle['messages'][1]['content']}]}],
            'generationConfig': {'temperature': 0.0, 'maxOutputTokens': 8192,
                                 'responseMimeType': 'application/json',
                                 'responseJsonSchema': bundle['response_json_schema']}}


class Budget(prior.Budget):
    def reserve(self, slot, stage, request_body):
        if stage not in prior.MODELS or slot != CASE + '--' + stage:
            raise ValueError('only_fixed_case_stage_allowed')
        rows = self.rows()
        if len(rows) >= 2 or any(r['stage'] == stage for r in rows):
            raise ValueError('pilot_attempt_cap_or_duplicate')
        if stage == 'semantic_review' and not any(r['stage'] == 'extract' and r['state'] == 'validated' for r in rows):
            raise ValueError('semantic_requires_valid_extraction')
        super().reserve(slot, stage, request_body)


def prepare_runtime():
    pins, request = prepared()
    if RUNTIME.exists() or any(p.is_symlink() for p in (RUNTIME, *RUNTIME.parents)):
        raise ValueError('new_runtime_path_required')
    manifest = {'version': q.VERSION, 'created_at': prior.now(), 'case_id': CASE,
                'approval': {'approved': True, 'user_message': '응',
                             'scope': 'One fixed development case; extraction1 then semantic review1 only if extraction validates; at most2 attempts.',
                             'destination': 'Google Gemini generateContent',
                             'data': 'Prior prepared query/raw draft/retrieved university sources and v2 schema, no gold or prior judgments.'},
                'source_pins': pins, 'models': prior.MODELS, 'request_id': request['request_id'],
                'extraction_body_sha256': q.v1.digest(body(request)), 'attempt_cap': 2, 'retries': 0,
                'single_key': True, 'inter_call_seconds': 15, 'first_error_stops': True, 'automatic_resume': False,
                'new_generation_calls': 0, 'final_judge_calls': 0, 'account_daily_usage_verified': False,
                'known_previous_project_calls': 165, 'live_root': str(LIVE),
                'candidate_gfc': None, 'eligible_for_service': False}
    RUNTIME.mkdir(parents=True, exist_ok=False)
    prior.write_new(RUNTIME / 'manifest.json', manifest)
    print(json.dumps({'status': 'PREPARED', 'manifest_sha256': prior.sha(RUNTIME / 'manifest.json'), 'calls': 0}), flush=True)


def post(budget, stage, request, key):
    if request['payload']['stage'] != stage:
        raise ValueError('request_stage_mismatch')
    payload = body(request)
    slot = CASE + '--' + stage
    prior.write_new(budget.root / (slot + '.request.json'), {'request': request, 'model': prior.MODELS[stage], 'body': payload})
    budget.reserve(slot, stage, payload)
    prior.CAPABILITY['url'] = prior.URLS[stage]
    status, started = None, time.perf_counter()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), prior.NoRedirect())
        req = urllib.request.Request(prior.URLS[stage], q.v1.encode(payload).encode(),
                                     headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
        with opener.open(req, timeout=90) as resp:
            status = resp.status
            raw = resp.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024 or key.encode() in raw:
            raise ValueError('unsafe_or_oversized_response')
        prior.write_new(budget.root / (slot + '.provider.txt'), raw.decode('utf-8'))
        provider = json.loads(raw)
        budget.finish(slot, 'received', status)
        text = prior.response_text(provider)
        prior.write_new(budget.root / (slot + '.response.txt'), text)
        prior.write_new(budget.root / (slot + '.receipt.json'), {'http_status': status,
                        'latency_ms': round((time.perf_counter() - started) * 1000, 3),
                        'usage_metadata': provider.get('usageMetadata', {}), 'model_version': provider.get('modelVersion'),
                        'response_sha256': hashlib.sha256(text.encode()).hexdigest()})
        return text
    except urllib.error.HTTPError as error:
        budget.finish(slot, 'http_error', error.code)
        # Preserve provider schema/quota rejection evidence without headers/key.
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


def pipeline(request, out, call, validated, pause):
    stage = 'extract'
    try:
        raw = call(stage, request)
        parsed = q.parse_extraction(request, raw)
        prior.write_new(out / 'extraction-validation.json', parsed)
        validated(stage)
        stage = 'semantic_review'
        semantic = q.build_semantic_request(request, raw)
        pause(15)
        reviewed = call(stage, semantic)
        parsed_review = q.parse_semantic(semantic, reviewed)
        prior.write_new(out / 'semantic-validation.json', parsed_review)
        validated(stage)
        decision = q.combine(request, raw, reviewed)
        if decision['status'] != 'offline_review_complete':
            raise ValueError('combined_result_blocked')
        prior.write_new(out / 'decision.json', decision)
        return {'status': 'PILOT_COMPLETE', 'decision': decision, 'failure': None}
    except Exception as error:
        failure = {'stage': stage, 'error_type': type(error).__name__,
                   'reason': str(error)[:300] if isinstance(error, ValueError) else 'runtime_or_transport_error_details_suppressed',
                   'http_status': error.code if isinstance(error, urllib.error.HTTPError) else None}
        prior.write_new(out / 'error.json', failure)
        print(json.dumps({'status': 'STOPPED_ON_FIRST_ERROR', 'failure': failure}), flush=True)
        return {'status': 'STOPPED_INCOMPLETE', 'decision': None, 'failure': failure}


def live(manifest_sha, authorize):
    if authorize != AUTH or prior.sha(RUNTIME / 'manifest.json') != manifest_sha:
        raise ValueError('exact_approval_and_manifest_required')
    manifest = prior.read(RUNTIME / 'manifest.json')
    pins, request = prepared()
    if manifest['source_pins'] != pins or manifest['extraction_body_sha256'] != q.v1.digest(body(request)):
        raise ValueError('runtime_inputs_changed')
    if LIVE.exists() or any(p.is_symlink() for p in (LIVE, *LIVE.parents)):
        raise ValueError('existing_live_path_no_automatic_resume')
    LIVE.mkdir(parents=True, exist_ok=False)
    prior.write_new(LIVE / 'run.json', {'manifest_sha256': manifest_sha, 'manifest': manifest, 'started_at': prior.now()})
    budget = Budget(LIVE)
    result = {'status': 'STOPPED_INCOMPLETE', 'failure': None, 'decision': None}
    try:
        key = prior.load_key()
        result = pipeline(request, LIVE, lambda stage, req: post(budget, stage, req, key),
                          lambda stage: budget.finish(CASE + '--' + stage, 'validated', 200), time.sleep)
    except Exception as error:
        result['failure'] = {'error_type': type(error).__name__, 'reason': 'execution_stopped_no_retry'}
    finally:
        rows = budget.rows()
        budget.db.close()
        result.update(case_id=CASE, finished_at=prior.now(), attempts=rows, reserved_attempts=len(rows),
                      http200_count=sum(r['http_status'] == 200 for r in rows),
                      extraction_valid=(LIVE / 'extraction-validation.json').exists(),
                      semantic_valid=(LIVE / 'semantic-validation.json').exists(),
                      eligible_for_service=False, candidate_gfc=None, human_review=False,
                      retries=0, single_key=True, account_daily_usage_verified=False,
                      limitation='One known development case, interface viability only; not GFC or model accuracy evaluation.')
        prior.write_new(LIVE / 'completion.json', result)
        prior.write_new(LIVE / 'output-sha256.json', {p.name: prior.sha(p) for p in LIVE.iterdir() if p.is_file()})
        print(json.dumps({k: result[k] for k in ('status', 'reserved_attempts', 'http200_count', 'extraction_valid', 'semantic_valid')}), flush=True)
    return 0 if result['status'] == 'PILOT_COMPLETE' else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'live'))
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--authorize')
    args = parser.parse_args()
    sys.addaudithook(prior.audit)
    if args.mode == 'prepare':
        prepare_runtime()
    else:
        raise SystemExit(live(args.manifest_sha256, args.authorize))
