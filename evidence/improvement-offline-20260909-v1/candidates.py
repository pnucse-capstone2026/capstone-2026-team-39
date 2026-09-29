"""Unintegrated A/B component candidates. No service patches, no model calls.

A: the existing lossless date splitter, independently replayed through C1.
B: quote-intersected explicit key/value fields, otherwise original row units.
Neither is a semantic entailment oracle. Known and new counterexamples must
remain in the evaluation, including false rejections and ambiguous relations.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from rag import evidence_binding_experiment as v1
from rag import grounded_claims_v2 as baseline

VERSION = 'pnu.evidence-binding-structured-fields.v2-dev'
split_preserving_dates = v1.split_preserving_dates

# Syntax-level labels, not DEV IDs, institution names, or specific query phrases.
_KEY = re.compile(r'(?<![0-9A-Za-z가-힣])(?P<key>[A-Za-z가-힣][A-Za-z가-힣 \t·()]{0,79})[:：]')
_VALUE_ONLY = re.compile(r'\s*\d+(?:,\d{3})*\s*(?:명|원|만원|천원|회|호|점|학기|시간|주|%)\s*[.]?\s*$')
_POLARITY = (
    ('미통과','통과'),('불합격','합격'),('미이수','이수'),('미달성','달성'),
    ('불허','허가'),('취소','확정'),
)


def explicit_fields(source):
    """Only contiguous spans. A dangling colon may bind ONE numeric value row.

    Does not reconstruct tables, interpret merged cells, or invent a subject
    inherited from an unrelated heading. Raw and normalized offsets are emitted.
    """
    matches = list(_KEY.finditer(source))
    result = []
    for i,match in enumerate(matches):
        next_key = matches[i+1].start() if i+1 < len(matches) else len(source)
        newline = source.find('\n',match.end())
        end = min(next_key,newline if newline >= 0 else len(source))
        rule = 'explicit_inline_key_value'
        if not source[match.end():end].strip() and end == newline:
            following = source.find('\n',newline+1)
            following = following if following >= 0 else len(source)
            if following <= next_key and _VALUE_ONLY.fullmatch(source[newline+1:following]):
                end,rule = following,'explicit_dangling_key_numeric_row'
        value = source[match.end():end].strip()
        if not value:
            continue
        raw = source[match.start():end].strip()
        result.append({'key':match['key'].strip(),'text':raw,'raw_start':match.start(),
                       'raw_end':match.start()+len(raw),'rule':rule})
    return result


def polarity_conflicts(claim,source):
    def signs(text,pair):
        negative,positive = pair
        negative_present = negative in text
        # Remove the longer negative marker before looking for its positive substring.
        return (negative_present,positive in text.replace(negative,''))
    return ['polarity_scope:'+negative+'/'+positive for negative,positive in _POLARITY
            if signs(claim,(negative,positive)) != signs(source,(negative,positive))
            and any(signs(claim,(negative,positive)))]


def units(source,quote):
    norm,quoted = baseline.normalize_text(source),baseline.normalize_text(quote)
    fields = explicit_fields(source)
    output = []
    for unit in v1._units(source,quote):
        # Never keep a broad row as a fallback when an explicit field lies in it.
        if any(baseline.normalize_text(field['key'])+':' in unit['text'].replace('：',':') for field in fields):
            continue
        output.append({**unit,'key':unit['text'],'rule':'original_row_or_sentence'})
    q_starts = [m.start() for m in re.finditer(re.escape(quoted),norm)]
    for field in fields:
        text = baseline.normalize_text(field['text'])
        # Prefix normalization supplies an unambiguous coordinate for repeated fields.
        start = len(baseline.normalize_text(source[:field['raw_start']]))
        start = norm.find(text,start)
        if start < 0:
            raise ValueError('field_coordinate_not_found')
        end = start+len(text)
        if any(q <= start and end <= q+len(quoted) for q in q_starts):
            output.append({**field,'text':text,'normalized_start':start,'normalized_end':end,
                           'sha256':baseline.sha256_text(text)})
    return output


def verify_claim(claim,contexts):
    parsed = baseline.parse_response({'claims':[claim],'unanswered':[]})['claims'][0]
    original = baseline._verify_claim(parsed,contexts)
    result = {'version':VERSION,'text':parsed['text'],'baseline_supported':original['supported'],
              'scope_gate_passed':False,'units':[],'reason':'baseline_rejected'}
    if not original['supported']:
        return result
    anchors = v1._anchors(parsed['text'])
    candidates = []
    for evidence in parsed['evidence']:
        number = evidence['source_number']
        context = contexts[number-1]
        for unit in units(baseline._context_text(context),evidence['quote']):
            # Key-only selection for structured fields prevents values attracting
            # a claim toward the other entity/attribute that owns the wrong value.
            count = len(baseline._anchor_overlap(anchors,v1._anchors(unit['key'])))
            candidates.append({**unit,'source_number':number,'anchor_count':count})
    best = max((c['anchor_count'] for c in candidates),default=0)
    for unit in candidates:
        unit.update(selected=best>0 and unit['anchor_count']==best,passes=False)
        if not unit['selected']:
            continue
        context = contexts[unit['source_number']-1]
        narrowed = {'text':unit['text'],'source_title':v1._safe_metadata(parsed['text'],context)}
        local = baseline._verify_claim({'text':parsed['text'],'evidence':[{'source_number':1,'quote':unit['text']}]},[narrowed])
        conflicts = v1._typed_conflicts(parsed['text'],unit['text'])+polarity_conflicts(parsed['text'],unit['text'])
        unit.update(local_reason=local['validation_reason'],conflicts=conflicts,passes=local['supported'] and not conflicts)
    result['units'] = candidates
    selected = [u for u in candidates if u['selected']]
    # Tied fields with contradictory values are ambiguous: no optimistic any().
    result['scope_gate_passed'] = bool(selected) and all(u['passes'] for u in selected)
    result['reason'] = 'field_scope_gate_passed' if result['scope_gate_passed'] else 'ambiguous_or_unsupported_field'
    return result
