"""Offline v2 preparation plus quote-component replay; not a repaired v1 result."""
from __future__ import annotations

from collections import Counter
import io
import json
from pathlib import Path
import sys
import unittest

import quote_adapter as q
from test_quote_adapter import chain

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'evidence/semantic-live-20260913-v1'))
import run as prior
sys.addaudithook(prior.audit)  # no provider capability, secret or holdout reads

OUT = ROOT / 'processed/eval/preflight-20260913/quote-adapter-v2/preparation-v1'
RUNTIME_SHA = 'cbdb711a3d7045e56a3dc709d3bd145208eb3292e475551171b957af06637987'
LIVE_SHA = '94d766d6d67eb206a3c498db800e7758554db6e56539bc4f862526579a110933'


def quote_replay(request, raw):
    data = q.v1.check_request(request, 'extract')
    sources = q.v1.source_map(data)
    raw = q.v1.strict_json(raw)
    rows = []
    def check(category, path, content, quote):
        try:
            bound = q.unique_span(content, quote)
            rows.append({'category': category, 'path': path, 'status': 'unique_bound', 'host_span': bound})
        except q.QuoteBindingError as error:
            rows.append({'category': category, 'path': path, 'status': 'held', 'reason': str(error), 'quote': quote})
    for i, entry in enumerate(raw['query_scope']):
        check('query', 'query_scope[%d]' % i, data['query'], entry['quote'])
    unit_text = {unit['unit_id']: unit['text'] for unit in data['draft_units']}
    for i, unit in enumerate(raw['units']):
        for j, atom in enumerate(unit['atoms']):
            check('claim', 'units[%d].atoms[%d]' % (i,j), unit_text[unit['unit_id']], atom['claim_quote'])
    def walk(value, path):
        if isinstance(value, dict):
            if {'source_id', 'field', 'start', 'end', 'quote'} <= set(value):
                check('source', path, getattr(sources[value['source_id']], value['field']), value['quote'])
            for key, item in value.items(): walk(item, path + '.' + key)
        elif isinstance(value, list):
            for i, item in enumerate(value): walk(item, path + '[%d]' % i)
    walk(raw, 'response')
    try:
        q.v1.parse_extraction(request, q.v1.encode(raw))
        original_status = 'VALID'
    except ValueError as error:
        original_status = str(error)
    return {'component_replay_only': True, 'v1_original_parse': original_status,
            'rows': rows, 'counts': {category: dict(Counter(row['status'] for row in rows if row['category'] == category))
                                     for category in ('query', 'claim', 'source')},
            'repaired_response_created': False, 'v2_model_response': False,
            'candidate_gfc': None, 'eligible_for_service': False}


def main():
    if OUT.exists() or any(p.is_symlink() for p in (OUT, *OUT.parents)):
        raise ValueError('new_output_path_required')
    runtime_path = prior.RUNTIME / 'manifest.json'
    live_path = prior.LIVE / 'output-sha256.json'
    if prior.sha(runtime_path) != RUNTIME_SHA or prior.sha(live_path) != LIVE_SHA:
        raise ValueError('prior_manifest_changed')
    pins = prior.read(runtime_path)['source_pins']
    pins.update({str(runtime_path): RUNTIME_SHA, str(live_path): LIVE_SHA})
    pins.update({str(prior.LIVE / name): value for name, value in prior.read(live_path).items()})
    pins.update({str(p): prior.sha(p) for p in HERE.glob('*.py')})
    if any(prior.sha(path) != value for path, value in pins.items()):
        raise ValueError('frozen_input_changed')
    outputs, slots = {}, []
    old_slots = prior.read(prior.PREP / 'manifest.json')['slots']
    for slot in old_slots:
        original = prior.read(prior.PREP / slot['request_file'])['request']
        data = q.v1.check_request(original, 'extract')
        request = q.make_request('extract', data)
        bundle = q.model_bundle(request)
        filename = 'extract--' + slot['case_id'] + '.request.json'
        outputs[filename] = {'record_type': 'v2_prepared_not_executed', 'request': request, 'model_bundle': bundle}
        slots.append({'case_id': slot['case_id'], 'request_file': filename, 'request_id': request['request_id'],
                      'raw_input_matches_v1': request['payload']['data'] == original['payload']['data'],
                      'model_bundle_utf8_bytes': len(q.v1.encode(bundle).encode())})
    if len(slots) != 42 or len({s['case_id'] for s in slots}) != 42 or not all(s['raw_input_matches_v1'] for s in slots):
        raise ValueError('development_input_mismatch')
    saved_request = prior.read(prior.LIVE / 'shadow_emp_01--extract.request.json')['request']
    raw = (prior.LIVE / 'shadow_emp_01--extract.response.txt').read_text()
    replay = quote_replay(saved_request, raw)
    # A v1 response is NEVER reclassified as a real v2 response.
    new_request = outputs[slots[0]['request_file']]['request']
    try:
        q.parse_extraction(new_request, raw)
        raise AssertionError('v1_response_must_not_be_accepted')
    except ValueError as error:
        replay['unmodified_v1_response_rejected_by_v2'] = str(error)
    for label, wrong, verdict in [('positive', False, 'entailed'), ('contradiction', True, 'contradicted'),
                                   ('semantic-false-positive', True, 'entailed')]:
        req, ex, sem, rev = chain(wrong=wrong, verdict=verdict)
        outputs['mock-' + label + '.json'] = {'synthetic_only': True, 'real_model_calls': 0,
                'extraction_request': req, 'mock_extraction': ex, 'semantic_request': sem, 'mock_semantic': rev,
                'result': q.combine(req, q.v1.encode(ex), q.v1.encode(rev))}
    stream = io.StringIO()
    tests = unittest.TextTestRunner(stream=stream).run(unittest.defaultTestLoader.discover(str(HERE), pattern='test_*.py'))
    if not tests.wasSuccessful():
        raise ValueError('tests_failed:' + stream.getvalue())
    summary = {'status': 'PREPARED_OFFLINE', 'version': q.VERSION, 'cases': 42,
               'raw_line_units': sum(len(outputs[s['request_file']]['request']['payload']['data']['draft_units']) for s in slots),
               'extraction_requests_ready': 42, 'semantic_requests_ready': 0,
               'external_calls_this_stage': 0, 'real_v2_responses': 0,
               'quote_component_replay': replay['counts'], 'unit_tests': {'total': tests.testsRun,
               'failures': len(tests.failures), 'errors': len(tests.errors), 'skipped': len(tests.skipped)},
               'input_pin_count': len(pins), 'provider_schema_compatibility_tested': False,
               'human_review': False, 'candidate_gfc': None, 'eligible_for_service': False}
    manifest = {'version': q.VERSION, 'slots': slots, 'v1_results_immutable': True,
                'next_step_proposal': {'approved': False, 'scope': 'First-case contract viability pilot only, not full42 or GFC evaluation.',
                    'first_case': slots[0]['case_id'], 'max_extraction_calls': 1, 'max_semantic_calls': 1,
                    'total_attempt_cap_proposal': 2, 'retries': 0, 'one_key': True, 'inter_call_seconds': 15,
                    'first_error_stops': True, 'provider_transport_ready': False,
                    'model_proposals_from_previous_execution': prior.MODELS,
                    'account_usage_or_free_tier_verified': False,
                    'prior_v1_approval_not_transferred': True}}
    report = '''# 원문 인용 기반 연결 v2: 오프라인 구현 결과

상태: DONE_WITH_CONCERNS. 새 실험 연결부 구현·합성 회귀시험·실제 입력42개 준비 완료.
이번 API0, 실제 v2 응답0. 서비스 성능 향상이나 전체 모델 출력 오류 해결을 입증하지 않았다.

## 바꾼 부분

모델은 위치 숫자 대신 출처 ID/필드/원문 인용을 반환한다. Host가 그 출처의 해당
필드 전체에서 유일하게 완전 일치하는 위치만 확정한다. 없거나 여러 곳에 나오면
보류한다. 공백/Unicode/문장부호 정규화, 유사 검색, 첫 일치 선택은 하지 않는다.
긴 연속 인용으로 식별할 수 있지만 짧은 중복 표현에 맞춰 근거를 추측하지 않는다.
질문 scope와 초안 claim, 별도 의미검증의 인용에도 같은 원칙을 적용했다.

측정 단위는 measurement_unit 문자열(없으면 빈 문자열), 근거 범위는
evidence_quote 객체로 나눴다. 조건/근거 배열까지 명시한 JSON schema를 제공한다.
null/string 등 잘못된 자료형을 자동 보정하지 않는다. 모델 입력에서는 host 위치와
source hash도 빼고 원래 질문/초안/검색 본문만 유지했다. 정답지/기존 Judge는 없다.

새 형식을 host가 기존 v1 형식으로 변환한 뒤 고정된 검증기를 그대로 호출한다.
출처/질문 범위, 조건, 필수 operator 근거, TSV 행/열, 추가 인용 검사는 완화하지
않았다. 같은 계열 모델의 의미 오판 가능성도 그대로 남는다. 두 단계가 통과해도
eligible_for_service=false, candidate_gfc=null이고 답변을 배포하거나 교체하지 않는다.

## v1 저장 인용으로 확인한 범위

실패한 v1 응답은 원본 그대로 보존했고 v2 응답으로도 인정하지 않았다.
인용 문자열만 연결 함수에 넣은 별도 부품 재생이다. 질문1+초안4+본문8은 유일한
정확 위치로 연결됐고, 중복 본문 인용5개는 보류됐다. v1의 자료형/의미 오류를
수정한 전체 응답이나 새로운 모델 결과를 만든 것이 아니다.

inspect/reproduce/regression 순서의 investigate 절차로 위치 계산 책임을 host에
옮겼다. 첫 일치만 고르는 결함 구현을 메모리에서 대입하면 중복 인용 시험2개가
실패함도 확인했다. 실제 source나 검증 코드를 바꾼 mutation은 아니다.
전역 스킬 설정/telemetry/동기화/git 조작은 범위 밖이므로 수행하지 않았다.

## 남은 확인과 다음 단계 제안

JSON schema의 provider 수용 여부와 모델의 실제 형식 준수/의미 정확도는 미검증이다.
현재 로컬 schema 검사는 이 모듈이 내보내는 제한된 어휘만 지원하며 범용 validator가 아니다.
별도 실험 prompt만 변경했으며 운영 prompt·서비스·보안·Judge·holdout은 변경하지 않았다.
기존 v1 원본을 완전한 성공 응답으로 고친 것이 아니므로 v1 0/1 유효 결과도 유지한다.
새 버전은 승인 후 첫 문항 추출1+유효 시 의미검증1, 최대2회로 연결 가능성부터
확인하는 것을 제안한다. 현재는 실행기 미연결이며 실제 호출 승인도 이전 v1에서
이월하지 않았다. 모델/키 전환이나 유료 fallback은 계획하지 않는다.

## 재현

`python3 -B -m unittest discover -s evidence/quote-adapter-20260913-v2 -p 'test_*.py'`

`python3 -B evidence/quote-adapter-20260913-v2/prepare.py`

출력 경로가 존재하면 거부한다. 원본 산출물을 지우고 재실행하지 않는다.

## 기계 집계

'''
    outputs.update({'manifest.json': manifest, 'summary.json': summary, 'input-sha256.json': pins,
                    'test-output.txt': stream.getvalue(), 'quote-component-replay.json': replay,
                    'extract-response-schema.json': q.EXTRACT_SCHEMA, 'semantic-response-schema.json': q.REVIEW_SCHEMA,
                    'report.md': report + '```json\n' + json.dumps(summary, ensure_ascii=False, indent=2) + '\n```\n'})
    if any(prior.sha(path) != value for path, value in pins.items()):
        raise ValueError('inputs_changed_during_preparation')
    OUT.mkdir(parents=True, exist_ok=False)
    for name, value in outputs.items(): prior.write_new(OUT / name, value)
    prior.write_new(OUT / 'output-sha256.json', {name: prior.sha(OUT / name) for name in outputs})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
