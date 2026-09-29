"""OFFLINE counterfactual binding contract; never changes old run classifications.

Accepts a frozen v2 record as replay input, not as a fresh v3 model response.
For non-table facts, child body references must be unique within the globally
unique evidence_quote. Query/claim/title/table/review resolution stays global.
Exact text, source identity, containment, schema and typed verifier stay strict.
No network, repair, first-occurrence fallback, service writes, or score claims.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
FROZEN = {
    'evidence/array-schema-20260914-v1/array_schema.py': '6022287a2713bca89f8977877a579b22136fd0d30d647aedb4df90378be9ea30',
    'evidence/array-schema-20260914-v1/run_array.py': 'bbb385a759e1b48f4219c6a2f16f58515b7b60f7dcd8e687ed4d7446733a9c3f',
}
for relative, expected in FROZEN.items():
    if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
        raise ValueError('frozen_array_source_changed')
path = ROOT / 'evidence/array-schema-20260914-v1/run_array.py'
sys.path.insert(0, str(path.parent))
spec = importlib.util.spec_from_file_location('parent_bound_frozen_array_run', path)
r = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = r
spec.loader.exec_module(r)
q, v1 = r.q, r.q.v1
VERSION = 'pnu.parent-bound-analysis.v1'


def resolve_child(reference, parent_reference, sources):
    """Resolve only after validating the parent against the entire source field.

    Neither caller-provided offsets nor a prevalidated flag are accepted.
    The original quote is unchanged; only an exact host-computed offset is added.
    """
    q.validate_schema(reference, q.QUOTE)
    parent = q.resolve_reference(parent_reference, sources)
    if (parent['field'] != 'text' or reference['field'] != 'text'
            or reference['source_id'] != parent['source_id']):
        raise q.QuoteBindingError('parent_child_source_or_field_mismatch')
    local = q.unique_span(parent['quote'], reference['quote'])
    result = {**copy.deepcopy(reference), 'start': parent['start'] + local['start'],
              'end': parent['start'] + local['end']}
    # Recheck the absolute span with the unchanged source/hash/offset validator.
    parsed = v1.parse_span(result, sources)
    outer = v1.parse_span(parent, sources)
    if not v1.c.contains(outer, parsed):
        raise q.QuoteBindingError('resolved_child_outside_parent')
    return result


def extraction_bridge(request, text):
    """Replay the original immutable bytes under an explicitly different contract."""
    legacy = q.legacy_request(request, 'extract')
    raw = q.response(request, text, 'extract')
    data = legacy['payload']['data']
    sources = v1.source_map(data)
    units = {u['unit_id']: u['text'] for u in data['draft_units']}
    converted = {'version': v1.VERSION, 'request_id': legacy['request_id'], 'query_scope': [], 'units': []}
    audit = []
    for scope in raw['query_scope']:
        converted['query_scope'].append({'dimension': scope['dimension'], 'value': scope['quote'],
                                        **q.unique_span(data['query'], scope['quote'])})
    for ui, unit in enumerate(raw['units']):
        if unit['unit_id'] not in units:
            raise ValueError('unknown_unit')
        converted_unit = {**copy.deepcopy(unit), 'atoms': []}
        for ai, atom in enumerate(unit['atoms']):
            claim_span = q.unique_span(units[unit['unit_id']], atom['claim_quote'])
            target = {**copy.deepcopy(atom), 'claim_start': claim_span['start'], 'claim_end': claim_span['end'],
                      'claim': q.assertion_wire(atom['claim']), 'evidence': []}
            for fi, fact in enumerate(atom['evidence']):
                base_path = '$.units[%d].atoms[%d].evidence[%d]' % (ui, ai, fi)
                parent = fact['evidence_quote']
                resolved_parent = q.resolve_reference(parent, sources)
                target_fact = {**copy.deepcopy(fact), 'assertion': q.assertion_wire(fact['assertion'])}
                target_fact.pop('evidence_quote')
                target_fact['unit'] = resolved_parent

                def resolve(reference, suffix, body_child):
                    # Table header/row/cell semantics are deliberately unchanged.
                    bounded = body_child and fact['table'] is None
                    result = resolve_child(reference, parent, sources) if bounded else q.resolve_reference(reference, sources)
                    if bounded:
                        content = getattr(sources[reference['source_id']], reference['field'])
                        quote = reference['quote']
                        occurrences, position = 0, content.find(quote)
                        while position >= 0:
                            occurrences += 1
                            position = content.find(quote, position + 1)
                        audit.append({'path': base_path + suffix, 'reference': copy.deepcopy(reference),
                                      'parent': copy.deepcopy(resolved_parent), 'resolved': copy.deepcopy(result),
                                      'global_occurrences': occurrences, 'parent_occurrences': 1,
                                      'binding_rule': 'globally_unique_parent_then_locally_unique_child'})
                    return result

                for key in ('relation_span', 'value_span', 'operator_span'):
                    target_fact[key] = resolve(fact[key], '.' + key, True) if fact[key] is not None else None
                for key in ('body_scope', 'title_scope', 'condition_spans'):
                    target_fact[key] = [{'dimension': item['dimension'], 'value': item['reference']['quote'],
                                         'span': resolve(item['reference'], '.%s[%d].reference' % (key, i), key != 'title_scope')}
                                        for i, item in enumerate(fact[key])]
                if fact['table'] is not None:
                    target_fact['table'] = {key: q.resolve_reference(ref, sources) for key, ref in fact['table'].items()}
                target['evidence'].append(target_fact)
            converted_unit['atoms'].append(target)
        converted['units'].append(converted_unit)
    converted_text = v1.encode(converted)
    # All existing typed binding checks, including source revision and conflicts.
    parsed = v1.parse_extraction(legacy, converted_text)
    return legacy, converted_text, parsed, audit


def analyze(request, text):
    _, _, parsed, audit = extraction_bridge(request, text)
    raw_hash = hashlib.sha256(text.encode()).hexdigest()
    return {'analysis_contract': VERSION, 'record_type': 'counterfactual_replay_of_v2_output',
            'original_wire_version': q.VERSION, 'original_request_id': request['request_id'],
            'original_response_sha256': raw_hash,
            'analysis_id': v1.digest([VERSION, request['request_id'], raw_hash]),
            'structural_validation': parsed, 'binding_audit': audit,
            'original_run_reclassified': False, 'eligible_for_service': False, 'candidate_gfc': None,
            'semantic_verified': False, 'external_calls': 0}
