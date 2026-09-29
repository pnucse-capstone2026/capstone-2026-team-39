"""Offline-only provider projection over the unchanged, pinned v2 host verifier.

No HTTP client, API key access, fallback, response repair, or service deployment.
This tests a schema-compatibility hypothesis, not a confirmed HTTP 400 fix.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = ROOT / 'evidence/quote-adapter-20260913-v2/quote_adapter.py'
ADAPTER_SHA = '458f3dcba124d99dbadebd0323daf512746d2e5b6a9716cced7b32e0bb6a5461'
if hashlib.sha256(ADAPTER.read_bytes()).hexdigest() != ADAPTER_SHA:
    raise ValueError('frozen_quote_adapter_changed')
sys.path.insert(0, str(ADAPTER.parent))
import quote_adapter as q

VERSION = 'pnu.provider-schema.v1'
DOCUMENTATION = 'https://ai.google.dev/api/generate-content#v1beta.GenerationConfig'
REVIEWED_ON = '2026-09-13'
HOST_ONLY = frozenset({'minLength', 'maxLength'})


def project(schema):
    """Only this pinned contract's finite vocabulary; unknown features fail closed.

    Remove exactly two string constraints from provider schema, keeping a complete
    JSON-pointer audit trail. Property names and enum strings are data, not keywords.
    Host validation always uses the original full schema, never this projection.
    """
    removed = []

    def pointer(path, key):
        return path + '/' + key.replace('~', '~0').replace('/', '~1')

    def walk(node, path='', depth=1):
        if type(node) is not dict or depth > 64:
            raise ValueError('unsupported_schema_shape:' + path)
        if 'anyOf' in node:
            if set(node) != {'anyOf'} or type(node['anyOf']) is not list or not node['anyOf']:
                raise ValueError('unsupported_schema_union:' + path)
            return {'anyOf': [walk(child, path + '/anyOf/' + str(i), depth + 1)
                              for i, child in enumerate(node['anyOf'])]}
        kind = node.get('type')
        fields = {'object': {'type', 'properties', 'required', 'additionalProperties'},
                  'array': {'type', 'items', 'maxItems'},
                  'string': {'type', 'minLength', 'maxLength'},
                  'boolean': {'type'}, 'null': {'type'}}
        if type(kind) is not str or kind not in fields:
            raise ValueError('unsupported_schema_type:' + path)
        expected = fields[kind] | ({'enum'} if kind == 'string' and 'enum' in node else set())
        if set(node) != expected:
            raise ValueError('unsupported_schema_keys:' + path)
        result = copy.deepcopy(node)
        if kind == 'object':
            if (type(node['properties']) is not dict or type(node['required']) is not list
                    or not all(type(k) is str for k in node['required'])
                    or len(set(node['required'])) != len(node['required'])
                    or not set(node['required']) <= set(node['properties'])
                    or node['additionalProperties'] is not False):
                raise ValueError('unsupported_schema_object:' + path)
            result['properties'] = {key: walk(child, pointer(path + '/properties', key), depth + 1)
                                    for key, child in node['properties'].items()}
        elif kind == 'array':
            if type(node['maxItems']) is not int or node['maxItems'] < 0:
                raise ValueError('unsupported_schema_array:' + path)
            result['items'] = walk(node['items'], path + '/items', depth + 1)
        elif kind == 'string':
            if (any(type(node[k]) is not int for k in HOST_ONLY)
                    or not 0 <= node['minLength'] <= node['maxLength']
                    or ('enum' in node and (type(node['enum']) is not list or not node['enum']
                                           or not all(type(v) is str for v in node['enum'])))):
                raise ValueError('unsupported_schema_string:' + path)
            for key in sorted(HOST_ONLY):
                removed.append({'pointer': pointer(path, key), 'keyword': key, 'value': node[key],
                                'enforced_by': 'unchanged_v2_host_validator'})
                del result[key]
        return result

    return walk(schema), removed


def envelope(request):
    """Separate versioned transport body; original host request and messages unchanged."""
    bundle = q.model_bundle(request)  # checks canonical full host schema and prompt
    projected, removed = project(bundle['response_json_schema'])
    body = {'systemInstruction': {'parts': [{'text': bundle['messages'][0]['content']}]},
            'contents': [{'role': 'user', 'parts': [{'text': bundle['messages'][1]['content']}]}],
            'generationConfig': {'temperature': 0.0, 'maxOutputTokens': 8192,
                                 'responseMimeType': 'application/json', 'responseJsonSchema': projected}}
    result = {'transport_version': VERSION, 'host_request': copy.deepcopy(request),
              'host_schema_sha256': q.v1.digest(bundle['response_json_schema']),
              'provider_body': body, 'provider_body_sha256': q.v1.digest(body),
              'removed_constraints': removed, 'eligible_for_service': False,
              'candidate_gfc': None, 'record_type': 'offline_prepared_not_executed'}
    result['envelope_sha256'] = q.v1.digest(result)
    return result


def validate_response(prepared, raw_text):
    """Never validate a response with the projected schema or repair rejected values."""
    request = prepared['host_request']
    if prepared != envelope(request):
        raise ValueError('transport_envelope_changed')
    stage = request['payload']['stage']
    parser = q.parse_extraction if stage == 'extract' else q.parse_semantic
    return {'transport_version': VERSION, 'provider_body_sha256': prepared['provider_body_sha256'],
            'host_validation': parser(request, raw_text),
            'eligible_for_service': False, 'candidate_gfc': None}


def schema_metrics(schema):
    depths = []
    def walk(node, depth=1):
        depths.append(depth)
        for child in node.get('properties', {}).values():
            walk(child, depth + 1)
        if 'items' in node:
            walk(node['items'], depth + 1)
        for child in node.get('anyOf', []):
            walk(child, depth + 1)
    walk(schema)
    return {'utf8_bytes': len(q.v1.encode(schema).encode()), 'schema_nodes': len(depths),
            'max_schema_depth': max(depths)}
