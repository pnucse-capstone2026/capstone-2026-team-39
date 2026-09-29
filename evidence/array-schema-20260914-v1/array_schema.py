"""Experimental maxItems-only transport change; the frozen host stays strict.

This is a testable compatibility candidate, not a service change or quality gain.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'evidence/schema-isolation-20260913-v1/isolate.py'
if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != '9b4d1db4dd86d437d1eeb53d1fe960b64da0ba1c445555043ca464d04d3424d9':
    raise ValueError('frozen_isolation_changed')
spec = importlib.util.spec_from_file_location('array_schema_frozen_isolation', SOURCE)
isolation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = isolation
spec.loader.exec_module(isolation)
previous, p, prior = isolation.previous, isolation.p, isolation.prior
q = p.q
VERSION = 'pnu.provider-array-schema.v1'


def without_array_bounds(schema):
    """Input is the existing validated projection; visit schema nodes, not data keys."""
    result, removed = copy.deepcopy(schema), []

    def walk(node, path=''):
        if node.get('type') == 'array':
            if type(node.get('maxItems')) is not int or node['maxItems'] < 0:
                raise ValueError('expected_pinned_array_bound:' + path)
            removed.append({'pointer': path + '/maxItems', 'keyword': 'maxItems',
                            'value': node.pop('maxItems'), 'enforced_by': 'unchanged_v2_host_validator'})
        for key, child in node.get('properties', {}).items():
            walk(child, path + '/properties/' + key.replace('~', '~0').replace('/', '~1'))
        if 'items' in node:
            walk(node['items'], path + '/items')
        for index, child in enumerate(node.get('anyOf', [])):
            walk(child, path + '/anyOf/' + str(index))

    walk(result)
    return result, removed


def envelope(request):
    prepared = p.envelope(request)  # validates the complete frozen input contract
    prepared.pop('envelope_sha256')
    config = prepared['provider_body']['generationConfig']
    config['responseJsonSchema'], removed = without_array_bounds(config['responseJsonSchema'])
    prepared.update(transport_version=VERSION, removed_array_constraints=removed,
                    provider_body_sha256=q.v1.digest(prepared['provider_body']),
                    record_type='experimental_compatibility_candidate')
    prepared['envelope_sha256'] = q.v1.digest(prepared)
    return prepared


def validate_response(prepared, text):
    request = prepared['host_request']
    if prepared != envelope(request):
        raise ValueError('transport_envelope_changed')
    parser = q.parse_extraction if request['payload']['stage'] == 'extract' else q.parse_semantic
    return {'host_validation': parser(request, text), 'eligible_for_service': False, 'candidate_gfc': None}


def synthetic_request():
    return q.build_extraction_request('제7회 ALPHA 공모전에서 취소자 참가비 처리는?',
        '취소자는 참가비 환수 대상입니다.',
        [{'chunk_id': 's1', 'source_title': '제7회 ALPHA 공모전', 'text': '취소자는 참가비 환수 대상입니다.'}])


def inputs():
    pins, contrasts = isolation.inputs()
    inventory_path = isolation.LIVE / 'output-sha256.json'
    expected = 'b0fa7471d46c93adf91e038b966e43efc4209e6699179f49911ed1f0f9d1b6f3'
    if prior.sha(inventory_path) != expected:
        raise ValueError('isolation_inventory_changed')
    inventory = prior.read(inventory_path)
    if set(inventory) | {'output-sha256.json'} != {path.name for path in isolation.LIVE.iterdir()}:
        raise ValueError('isolation_file_set_changed')
    pins.update({str(isolation.LIVE / name): value for name, value in inventory.items()})
    pins[str(inventory_path)] = expected
    pins.update({str(path): prior.sha(path) for path in Path(__file__).parent.glob('*.py')})
    if any(prior.sha(path) != value for path, value in pins.items()):
        raise ValueError('input_pin_changed')
    _, original = previous.inputs()
    synthetic = synthetic_request()
    if p.envelope(synthetic)['provider_body'] != contrasts[1]['body']:
        raise ValueError('synthetic_baseline_changed')
    return pins, synthetic, original['host_request']
