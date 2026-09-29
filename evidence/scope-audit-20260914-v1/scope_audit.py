"""Offline scope readiness audit, not a service patch or model prompt.

The optional scope declaration requires an explicit decision for all seven
dimensions. Exact quotes establish provenance, NOT the truth of those decisions.
No scope is inferred, filled, normalized or removed from extraction inputs.
"""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT / 'evidence/parent-bound-20260914-v1/parent_bound.py'
PARENT_SHA = '27b68873265bbe0641c554b86f3a630a4329c1e9234f95ff2f01e6b0a1f0c822'
if hashlib.sha256(PARENT.read_bytes()).hexdigest() != PARENT_SHA:
    raise ValueError('frozen_parent_contract_changed')
spec = importlib.util.spec_from_file_location('scope_audit_frozen_parent', PARENT)
p = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)
q, v1 = p.q, p.v1
VERSION = 'pnu.scope-readiness-audit.v1'
DECLARATION_VERSION = 'pnu.scope-declaration.proposal.v1'
DIMENSIONS = tuple(sorted(v1.SCOPE_KEYS))
DECISION = q.obj({'status': q.S(enum=('specified', 'not_specified', 'unresolved')),
                  'quote': q.nullable(q.S(maximum=2000)), 'reason': q.S(maximum=2000)})
DECLARATION_SCHEMA = q.obj({
    'version': q.S(enum=(DECLARATION_VERSION,)), 'request_id': q.S(),
    'dimensions': q.obj({name: copy.deepcopy(DECISION) for name in DIMENSIONS}),
})


def declaration_contract(request):
    """Pure offline specification. There is no provider body or new LLM prompt."""
    legacy = q.legacy_request(request, 'extract')
    return {'version': DECLARATION_VERSION, 'request_id': request['request_id'],
            'query_sha256': hashlib.sha256(legacy['payload']['data']['query'].encode()).hexdigest(),
            'response_schema': copy.deepcopy(DECLARATION_SCHEMA),
            'provider_compatibility_tested': False, 'external_calls': 0}


def parse_declaration(request, text):
    """Validate seven decisions without treating declared absence as proven absence."""
    contract = declaration_contract(request)
    raw = v1.strict_json(text)
    q.validate_schema(raw, contract['response_schema'])
    if raw['request_id'] != request['request_id']:
        raise ValueError('scope_declaration_request_mismatch')
    query = request['payload']['data']['query']
    specified, unresolved, spans = {}, [], {}
    for dimension, item in raw['dimensions'].items():
        if item['status'] == 'specified':
            if item['quote'] is None:
                raise ValueError('specified_scope_requires_quote:' + dimension)
            spans[dimension] = q.unique_span(query, item['quote'])
            specified[dimension] = item['quote']
        elif item['quote'] is not None:
            raise ValueError('non_specified_scope_has_quote:' + dimension)
        if item['status'] == 'unresolved':
            unresolved.append(dimension)
    return {'request_id': request['request_id'], 'query_scope': specified,
            'exact_query_spans': spans, 'unresolved_dimensions': sorted(unresolved),
            'decision_status_counts': dict(Counter(x['status'] for x in raw['dimensions'].values())),
            'structurally_complete': True, 'scope_semantics_verified': False,
            'absence_assertions_verified': False,
            'declaration_sha256': hashlib.sha256(text.encode()).hexdigest()}


def scope_difference(expected, actual):
    """Compare already supplied dimensions; never guess a missing dimension."""
    return {'missing_dimensions': sorted(expected.keys() - actual.keys()),
            'extra_dimensions': sorted(actual.keys() - expected.keys()),
            'conflicting_dimensions': sorted(k for k in expected.keys() & actual.keys()
                                             if expected[k] != actual[k])}


def analyze(request, text, declaration_text=None):
    """Readiness is a local precondition only, never a call or answer approval.

    Full frozen schema, exact bindings and typed verification run before this
    audit. Parent-scoped quote resolution is an explicitly separate replay
    contract, not a reclassification of a frozen v2 live run.
    """
    raw = q.response(request, text, 'extract')
    _, _, parsed, bindings = p.extraction_bridge(request, text)
    query_scope = parsed['query_scope']
    contracts = {a['atom_id']: a['contract'] for u in parsed['units'] for a in u['atoms']}
    rows, issues = [], []
    if not query_scope:
        issues.append('query_scope_empty')
    for unit in raw['units']:
        if unit['coverage'] != 'complete':
            issues.append('unit_coverage_' + unit['coverage'])
        for atom in unit['atoms']:
            claim = atom['claim']['scope']
            effective = {**query_scope, **claim}
            fact_rows = []
            for fact in atom['evidence']:
                scope = fact['assertion']['scope']
                fact_rows.append({'fact_id': fact['fact_id'], 'source_id': fact['evidence_quote']['source_id'],
                                  'scope': copy.deepcopy(scope),
                                  **scope_difference(effective, scope),
                                  'body_scope_bindings': len(fact['body_scope']),
                                  'title_scope_bindings': len(fact['title_scope'])})
            outcome = contracts[atom['atom_id']]
            if outcome['status'] != 'matched':
                issues.append('typed_contract_not_matched')
            rows.append({'unit_id': unit['unit_id'], 'atom_id': atom['atom_id'],
                         'explicit_claim_scope': copy.deepcopy(claim),
                         'empty_claim_scope_is_not_itself_an_error': True,
                         'query_inherited_dimensions': sorted(query_scope.keys() - claim.keys()),
                         'claim_query_conflicts': sorted(k for k in query_scope.keys() & claim.keys()
                                                         if query_scope[k] != claim[k]),
                         'effective_scope': effective, 'facts': fact_rows,
                         'typed_status': outcome['status'], 'typed_reason': outcome['reason']})
    if not rows:
        issues.append('no_factual_atoms')
    declaration = None
    declaration_difference = None
    if declaration_text is None:
        issues.append('explicit_scope_declaration_missing')
    else:
        declaration = parse_declaration(request, declaration_text)
        if declaration['unresolved_dimensions']:
            issues.append('scope_declaration_unresolved')
        if not declaration['query_scope']:
            issues.append('scope_declaration_has_no_specified_dimension')
        declaration_difference = scope_difference(declaration['query_scope'], query_scope)
        if any(declaration_difference.values()):
            issues.append('declaration_extraction_scope_disagreement')
    issues = sorted(set(issues))
    facts = [fact for row in rows for fact in row['facts']]
    return {'analysis_contract': VERSION, 'binding_contract': p.VERSION,
            'record_type': 'offline_readiness_audit_of_v2_output',
            'request_id': request['request_id'],
            'input_response_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'structural_parse': 'pass', 'query_scope': copy.deepcopy(query_scope),
            'units': len(raw['units']), 'atoms': len(rows), 'facts': len(facts),
            'empty_claim_scopes': sum(not r['explicit_claim_scope'] for r in rows),
            'empty_fact_scopes': sum(not f['scope'] for f in facts),
            'body_scope_bindings': sum(f['body_scope_bindings'] for f in facts),
            'title_scope_bindings': sum(f['title_scope_bindings'] for f in facts),
            'typed_status_counts': dict(Counter(r['typed_status'] for r in rows)),
            'typed_reason_counts': dict(Counter(r['typed_reason'] for r in rows)),
            'scope_declaration': declaration, 'declaration_difference': declaration_difference,
            'issues': issues, 'local_preconditions_satisfied': not issues,
            'atom_audit': rows, 'parent_bound_references': len(bindings),
            'original_run_reclassified': False, 'eligible_for_service': False,
            'candidate_gfc': None, 'semantic_verified': False, 'external_calls': 0,
            'limitation': 'Even satisfied local preconditions do not prove scope completeness, '
                          'declared absence, assertion semantics, accuracy or GFC. '
                          'No runtime or model behavior is changed.'}
