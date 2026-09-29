"""Read-only response diagnosis. No model calls, response repair, or deployment."""
from __future__ import annotations

from collections import Counter
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'evidence/semantic-live-20260913-v1'))
import run as r

LIVE_INVENTORY_SHA = '94d766d6d67eb206a3c498db800e7758554db6e56539bc4f862526579a110933'
OUT = r.BASE / 'diagnosis-v1'


def span_check(text, start, end, quote):
    integer_offsets = type(start) is int and type(end) is int
    valid = bool(integer_offsets and type(quote) is str and quote and 0 <= start < end <= len(text) and text[start:end] == quote)
    matches = [i for i in range(len(text)) if type(quote) is str and quote and text.startswith(quote, i)]
    return {'supplied_start': start, 'supplied_end': end, 'quote': quote, 'valid': valid,
            'supplied_slice': text[start:end] if integer_offsets else None,
            'exact_matches': [{'start': i, 'end': i + len(quote)} for i in matches],
            'quote_occurrences': len(matches), 'unique_exact_quote': len(matches) == 1}


def inspect(request, raw):
    data = r.a.check_request(request, 'extract')
    response = r.a.strict_json(raw)
    sources = {s['source_id']: s for s in data['sources']}
    unit_text = {u['unit_id']: u['text'] for u in data['draft_units']}
    spans, shapes = [], []
    for i, scope in enumerate(response.get('query_scope', [])):
        spans.append({'path': 'query_scope[%d]' % i, 'category': 'query',
                      **span_check(data['query'], scope['start'], scope['end'], scope['quote'])})
    for i, unit in enumerate(response.get('units', [])):
        for j, atom in enumerate(unit.get('atoms', [])):
            stem = 'units[%d].atoms[%d]' % (i, j)
            spans.append({'path': stem, 'category': 'claim',
                          **span_check(unit_text[unit['unit_id']], atom['claim_start'], atom['claim_end'], atom['claim_quote'])})
            assertions = [(stem + '.claim', atom['claim'])]
            for k, fact in enumerate(atom.get('evidence', [])):
                path = stem + '.evidence[%d]' % k
                assertions.append((path + '.assertion', fact['assertion']))
                if type(fact.get('unit')) is not dict:
                    shapes.append({'path': path + '.unit', 'type': 'evidence_unit_not_span',
                                   'expected': 'SourceSpan object', 'actual': type(fact.get('unit')).__name__})
                for field in ('condition_spans', 'body_scope', 'title_scope'):
                    for n, item in enumerate(fact.get(field, [])):
                        if type(item) is not dict or set(item) != {'dimension', 'value', 'span'}:
                            shapes.append({'path': path + '.' + field + '[%d]' % n, 'type': 'scoped_span_shape',
                                           'expected': 'dimension/value/span object', 'actual_keys': sorted(item) if isinstance(item, dict) else None})
                if fact['assertion'].get('scope') and not fact.get('body_scope') and not fact.get('title_scope'):
                    shapes.append({'path': path + '.assertion.scope', 'type': 'scope_without_binding',
                                   'expected': 'source spans for nonempty scope', 'actual': 'empty body_scope and title_scope'})
            for path, assertion in assertions:
                if type(assertion.get('value', {}).get('unit')) is not str:
                    shapes.append({'path': path + '.value.unit', 'type': 'value_unit_not_string',
                                   'expected': 'string, empty string allowed', 'actual': type(assertion.get('value', {}).get('unit')).__name__})

    def walk(value, path):
        if isinstance(value, dict):
            if {'source_id', 'field', 'start', 'end', 'quote'} <= set(value):
                sid, field = value['source_id'], value['field']
                if sid in sources and field in ('title', 'text'):
                    spans.append({'path': path, 'category': 'source', 'source_id': sid,
                                  **span_check(sources[sid][field], value['start'], value['end'], value['quote'])})
            for key, item in value.items():
                walk(item, path + '.' + key)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, path + '[%d]' % i)
    walk(response, 'response')

    def replay(value):
        try:
            r.a.parse_extraction(request, r.a.encode(value))
            return 'VALID'
        except (ValueError, TypeError, KeyError) as error:
            return str(error)
    # Diagnostic counterfactual only. Do not save modified responses or use these
    # copies as candidates. This shows the first error masks further schema errors.
    original_error = replay(response)
    copy1 = copy.deepcopy(response)
    for scope in copy1['query_scope']:
        check = span_check(data['query'], scope['start'], scope['end'], scope['quote'])
        if check['unique_exact_quote']:
            scope.update(check['exact_matches'][0])
    query_only_error = replay(copy1)
    copy2 = copy.deepcopy(copy1)
    null_slots = 0
    for unit in copy2['units']:
        for atom in unit['atoms']:
            for assertion in [atom['claim']] + [f['assertion'] for f in atom['evidence']]:
                if assertion['value']['unit'] is None:
                    assertion['value']['unit'] = ''
                    null_slots += 1
    null_unit_error = replay(copy2)
    return {'spans': spans, 'shape_issues': shapes,
            'counts': {category: {'total': sum(s['category'] == category for s in spans),
                                 'valid_offsets': sum(s['category'] == category and s['valid'] for s in spans),
                                 'unique_exact_quotes': sum(s['category'] == category and s['unique_exact_quote'] for s in spans),
                                 'missing_quotes': sum(s['category'] == category and s['quote_occurrences'] == 0 for s in spans)}
                       for category in ('query', 'claim', 'source')},
            'shape_issue_counts': dict(Counter(s['type'] for s in shapes)),
            'replays': {'original': original_error, 'query_offsets_only_in_memory': query_only_error,
                        'then_null_units_only_in_memory': null_unit_error, 'null_unit_fields_in_memory': null_slots,
                        'modified_response_saved': False, 'candidate_created': False},
            'external_calls': 0, 'eligible_for_service': False, 'candidate_gfc': None,
            'limitation': 'Only the first attempted development response; no inference about 42-case accuracy or semantic correctness.'}


def main():
    sys.addaudithook(r.audit)
    if OUT.exists() or any(p.is_symlink() for p in (OUT, *OUT.parents)):
        raise ValueError('new_diagnosis_path_required')
    if r.sha(r.LIVE / 'output-sha256.json') != LIVE_INVENTORY_SHA:
        raise ValueError('live_inventory_changed')
    inventory = r.read(r.LIVE / 'output-sha256.json')
    pins = {str(r.LIVE / name): value for name, value in inventory.items()}
    pins[str(r.LIVE / 'output-sha256.json')] = LIVE_INVENTORY_SHA
    pins.update(r.read(r.RUNTIME / 'manifest.json')['source_pins'])
    pins.update({str(p): r.sha(p) for p in HERE.glob('*.py')})
    if any(r.sha(p) != value for p, value in pins.items()):
        raise ValueError('diagnostic_input_changed')
    req = r.read(r.LIVE / 'shadow_emp_01--extract.request.json')['request']
    raw = (r.LIVE / 'shadow_emp_01--extract.response.txt').read_text()
    diagnosis = inspect(req, raw)
    receipt = r.read(r.LIVE / 'shadow_emp_01--extract.receipt.json')
    report = '''# 추출 실호출 중단 원인

상태: STOPPED_INCOMPLETE. 승인 한도84회 중 실제 추출1회(HTTP200)만 사용했고,
의미검증0회, 재시도0회다. 형식 검사 첫 오류에서 계획대로 중단했다.
사용한 모델은 gemini-3.5-flash-lite이고 다른 모델·키로 전환하지 않았다.
42개 전체를 시험한 결과나 서비스 GFC=0 결과가 아니다.

## 원인

질문의 `2026 금정 청년 구직응원 패키지`는 19글자지만 모델은 [0,15)를 반환했다.
저장 응답을 다시 읽어도 adapter.py의 query_scope 위치 검사에서 동일하게 실패한다.
원래 네 줄의 답변 인용은 위치가 맞는다. 질문/검색 본문의 위치와 JSON 자료형이
문제이며, API 인증·한도·출력 잘림 때문은 아니다(STOP, HTTP200).

그 밖에 EvidenceFact.unit에 구간 객체 대신 source ID 문자열을 반환했고,
Value.unit에 null을 넣었으며 condition_spans도 규정된 감싸는 객체를 생략했다.
scope 태그가 있는데 근거 구간은 비어 있다. 입력 지시만으로 복잡한 계약을
충족하도록 요구한 인터페이스의 사용 가능성이 첫 실응답에서 확보되지 않았다.
이를 서비스 답변의 의미 정확도 저하나 모든 문항의 실패로 일반화하지 않는다.

진단용 메모리 사본에서 query 위치만 바로잡아도 다음 자료형 오류가 남는다.
수정 응답을 파일에 저장하거나 후보로 채택하지 않았고 검사 기준도 완화하지 않았다.
investigate 절차에 따라 저장 응답 재현→독립 오류 검사→원인 분리를 수행했다.
이번 단계에는 서비스/실험 adapter 수정이 없다. 전역 설정·telemetry·git 작업도 없다.

## 다음 설계 제안, 아직 실행/적용하지 않음

모델에게 글자 위치를 계산시키지 말고 원문 인용과 출처만 받는 새 실험 계약을
검토한다. Host가 해당 출처에서 완전 일치하는 유일한 구간만 연결하고, 없거나
여러 곳에 있으면 보류해야 한다. 유사문자열/정규화로 위치를 추측하지 않는다.
중첩 자료형은 명시적 JSON schema로 전달하고 단위 빈값 규약도 고정한다.
이 변경은 v1의 결과를 고치는 것이 아니라 별도 버전의 실험이어야 한다.
이렇게 해도 의미 태그 정확성은 보장되지 않으므로 별도 의미검증은 계속 필요하다.
추가 모델 호출은 하지 않았고 기존 실패 문항을 성공 결과로 덮어쓰지 않는다.

## 기계 집계

'''
    report += '```json\n' + json.dumps({'counts': diagnosis['counts'], 'shape_issue_counts': diagnosis['shape_issue_counts'],
                    'replays': diagnosis['replays'], 'receipt': receipt}, ensure_ascii=False, indent=2) + '\n```\n'
    OUT.mkdir(parents=True, exist_ok=False)
    r.write_new(OUT / 'diagnosis.json', diagnosis)
    r.write_new(OUT / 'input-sha256.json', pins)
    r.write_new(OUT / 'report.md', report)
    r.write_new(OUT / 'output-sha256.json', {p.name: r.sha(p) for p in OUT.iterdir()})
    print(json.dumps({k: diagnosis[k] for k in ('counts', 'shape_issue_counts', 'replays')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
