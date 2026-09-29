"""New, exclusive-path offline artifacts; no live mode or API authorization."""
from __future__ import annotations

import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import provider_schema as p
from test_provider_schema import ProviderSchemaTests, restore
from test_quote_adapter import chain

ROOT = p.ROOT
HERE = Path(__file__).resolve().parent
OUT = ROOT / 'processed/eval/preflight-20260913/provider-schema-v1/offline-v1'
PILOT = ROOT / 'evidence/quote-pilot-20260913-v2/pilot.py'
PILOT_SHA = '492c5bc341ee498f6c5d02619b539503d7229c036402a45753508ad21cd73167'
if p.hashlib.sha256(PILOT.read_bytes()).hexdigest() != PILOT_SHA:
    raise ValueError('frozen_pilot_changed')
spec = importlib.util.spec_from_file_location('provider_schema_frozen_pilot', PILOT)
pilot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pilot
spec.loader.exec_module(pilot)
prior = pilot.prior
sys.addaudithook(prior.audit)  # no key/network capability is enabled


def main():
    if OUT.exists() or any(path.is_symlink() for path in (OUT, *OUT.parents)):
        raise ValueError('new_output_path_required')
    assert prior.CAPABILITY == {'key_read': False, 'url': None}
    pins, first_request = pilot.prepared()
    runtime = pilot.RUNTIME / 'manifest.json'
    live_inventory = pilot.LIVE / 'output-sha256.json'
    if prior.sha(runtime) != 'cb6438ca3f09ad7ce7ca3c510164b0f256f8a456d0ff8abaaf4baf7eaeeff0c1':
        raise ValueError('frozen_runtime_changed')
    if prior.sha(live_inventory) != '92b43d0c2fbf0085de74fb896ae4b6533fe897f31a846674b4d9c92d20e41e13':
        raise ValueError('frozen_live_inventory_changed')
    inventory = prior.read(live_inventory)
    if set(inventory) | {'output-sha256.json'} != {path.name for path in pilot.LIVE.iterdir()}:
        raise ValueError('frozen_live_file_set_changed')
    pins.update({str(pilot.LIVE / name): value for name, value in inventory.items()})
    pins.update({str(runtime): prior.sha(runtime), str(live_inventory): prior.sha(live_inventory)})
    pins.update({str(path): prior.sha(path) for path in HERE.glob('*.py')})
    if any(prior.sha(path) != value for path, value in pins.items()):
        raise ValueError('frozen_input_changed')
    failed = prior.read(pilot.LIVE / (pilot.CASE + '--extract.request.json'))
    assert failed['body'] == pilot.body(first_request)
    first = p.envelope(first_request)
    projections = {}
    for stage, schema in [('extract', p.q.EXTRACT_SCHEMA), ('semantic_review', p.q.REVIEW_SCHEMA)]:
        projected, removed = p.project(schema)
        assert restore(projected, removed) == schema
        projections[stage] = {'host_schema': schema, 'provider_schema': projected, 'removed': removed,
                              'before': p.schema_metrics(schema), 'after': p.schema_metrics(projected),
                              'restored_schema_matches': True}
    rows = []
    for slot in prior.read(pilot.PREP / 'manifest.json')['slots']:
        request = prior.read(pilot.PREP / slot['request_file'])['request']
        wrapped = p.envelope(request)
        original_body = pilot.body(request)
        restored_body = copy.deepcopy(wrapped['provider_body'])
        restored_body['generationConfig']['responseJsonSchema'] = restore(
            restored_body['generationConfig']['responseJsonSchema'], wrapped['removed_constraints'])
        assert restored_body == original_body
        assert request == wrapped['host_request']
        rows.append({'case_id': slot['case_id'], 'host_request_id': request['request_id'],
                     'draft_units': len(request['payload']['data']['draft_units']),
                     'messages_and_other_generation_settings_unchanged': True,
                     'original_body_sha256': p.q.v1.digest(original_body),
                     'provider_body_sha256': wrapped['provider_body_sha256'],
                     'removed_constraints': len(wrapped['removed_constraints'])})
    assert len(rows) == len({row['case_id'] for row in rows}) == 42

    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProviderSchemaTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise ValueError('offline_tests_failed')
    # Memory-only mutation: reproduces the pre-change forwarding and proves that
    # the regression detects restoring it. Frozen source files remain unchanged.
    mutant_stream = io.StringIO()
    with patch.object(p, 'project', side_effect=lambda schema: (copy.deepcopy(schema), [])):
        mutant = unittest.TextTestRunner(stream=mutant_stream, verbosity=1).run(
            ProviderSchemaTests('test_only_length_constraints_removed_and_exactly_restorable'))
    assert len(mutant.failures) == 1 and not mutant.errors

    synthetic = []
    for name, options in [('normal', {}), ('disagreement', {'verdict': 'contradicted'}),
                          ('known_semantic_false_positive', {'wrong': True})]:
        req, ex, sem, rev = chain(**options)
        extracted, reviewed = p.q.v1.encode(ex), p.q.v1.encode(rev)
        synthetic.append({'name': name, 'synthetic_only': True,
                          'extract': p.validate_response(p.envelope(req), extracted),
                          'semantic': p.validate_response(p.envelope(sem), reviewed),
                          'combined': p.q.combine(req, extracted, reviewed)})
    control = {'record_type': 'synthetic_control_prepared_not_executed', 'api_approved': False,
               'model': prior.MODELS['extract'], 'expected_synthetic_output': {'ok': True},
               'body': {'contents': [{'role': 'user', 'parts': [{'text': 'Return exactly {"ok":true}.'}]}],
                        'generationConfig': {'temperature': 0.0, 'maxOutputTokens': 8192,
                                             'responseMimeType': 'application/json',
                                             'responseJsonSchema': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                                                                    'required': ['ok'], 'additionalProperties': False}}}}
    summary = {'status': 'PREPARED_OFFLINE_WITH_CONCERNS', 'transport_version': p.VERSION,
               'development_requests_audited': len(rows), 'draft_units': sum(row['draft_units'] for row in rows),
               'first_case': pilot.CASE, 'source_pins': len(pins),
               'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
               'skips': len(result.skipped), 'expected_mutation_failures': len(mutant.failures),
               'original_body_bytes': len(p.q.v1.encode(failed['body']).encode()),
               'projected_body_bytes': len(p.q.v1.encode(first['provider_body']).encode()),
               'schema_metrics': {stage: {key: value for key, value in item.items() if key in ('before', 'after')}
                                  for stage, item in projections.items()},
               'removed_constraints_per_stage': {stage: len(item['removed']) for stage, item in projections.items()},
               'new_api_calls': 0, 'new_model_responses': 0, 'http400_root_cause_confirmed': False,
               'provider_acceptance_verified': False, 'host_validator_changed': False,
               'eligible_for_service': False, 'candidate_gfc': None}
    manifest = {'transport_version': p.VERSION, 'created_at': prior.now(), 'user_approved_scope': 'offline_only',
                'api_approved': False, 'documentation': p.DOCUMENTATION, 'documentation_reviewed_on': p.REVIEWED_ON,
                'models_unchanged': prior.MODELS, 'first_case': pilot.CASE,
                'first_envelope_sha256': first['envelope_sha256'],
                'first_provider_body_sha256': first['provider_body_sha256'],
                'host_request_id_unchanged': first_request['request_id'],
                'network_capability': None, 'key_read': False, 'live_runner_implemented': False,
                'next_step_unexecuted': 'Separately authorize minimal synthetic schema control before this projected DEV request; first error stops. No automatic retry or model/key switch.'}
    report = '''# API 전송 schema 분리 오프라인 검사 — 2026-09-13

상태: DONE_WITH_CONCERNS. 별도 transport 버전 구현·오프라인 검증 완료.
외부 모델 호출0, 신규 모델 응답0, HTTP400 원인 확정 아님, 성능/GFC 미측정이다.

고정 v2 host schema의 minLength/maxLength만 provider 전송본에서 제외했다.
제외 항목별 JSON pointer와 원래 값을 보존했고, 복원하면 원래 schema와 정확히
일치한다. 같은 이름의 property나 enum 문자열은 지우지 않는다. 낯선 schema
키는 임의로 삭제하지 않고 중단한다. 범용 JSON Schema 변환기가 아니다.

host 요청·prompt·원문·조건·출처 검증·모델·온도·토큰 상한은 그대로다. 응답은
항상 고정 v2의 전체 schema 및 검증기를 통과해야 한다. 전송본을 host validator로
사용하지 않는다. envelope 변조, 빈 값/초과 길이/null, 누락/추가 필드, 잘못된
출처/인용, 필수 operator/scope 누락은 계속 거부한다. 새 live 실행기는 만들지 않았다.

investigate 절차로 저장된 실패 요청에서 길이 제한144개가 그대로 전송됐음을
재현했다. 이는 로컬 호환성 점검의 근거이며 실제 API400 원인을 재현한 것은 아니다.
이전 전달 방식으로 되돌리는 메모리 mutation은 회귀시험1개를 예상대로 실패시켰다.
스킬의 전역 설정/telemetry/동기화/자동 commit은 범위 밖이므로 실행하지 않았다.

Google 문서는 JSON Schema 기능을 일부 지원한다고 설명한다. 확인한 지원 목록에는
minLength/maxLength가 없지만 목록 외 항목이 반드시400을 만든다는 뜻은 아니다.
[공식 GenerationConfig](https://ai.google.dev/api/generate-content#v1beta.GenerationConfig)
schema 중첩 깊이는 변하지 않았고 모델/API 조합 수용 여부도 미확인이다.

개발42개 요청은 형식 차이만 확인했고 정답지/기존 Judge를 새 모델 입력에 넣지 않았다.
원래 실패 문항도 포함하므로 향후 이 입력을 호출해도 독립 성능 평가가 아니다.
synthetic-chain.json의3개 결과는 합성 fixture다. 추출과 의미검증이 함께 오판하면
후보가 남을 수 있는 한계도 그대로 보존하며 서비스 적용은 모두 false다.

다음 단계 제안은 최소 합성 schema 요청부터 API 수용 여부를 확인한 다음, 통과하면
고정 DEV 문항의 새 전송본을 확인하는 것이다. 원인 분리를 위해 한 번에 한 변수만
바꾼다. 합성 control은 endpoint/schema 기본 수용만 확인하며 큰 schema의 수용을
보장하지 않는다. 호출은 새 승인 전 미실행이고 이전 승인을 이월하지 않는다.
전송 제약의 변화는 출력 분포를 바꿀 수 있어 기존 실험과 동일 조건으로 합산하지 않는다.

재현 명령:

```sh
python3 -B -m unittest discover -s evidence/provider-schema-20260913-v1 -p 'test_*.py'
python3 -B evidence/provider-schema-20260913-v1/prepare.py
```

prepare는 출력 경로가 존재하면 중단한다. 원본을 지워 재실행하지 않는다.
입력 pin은 input-sha256.json, 출력 pin은 output-sha256.json에 기록한다.
기존 동결 서비스·보안·Judge·원본 산출물 미수정, 실제 holdout/.env 열람0, git 조작0.

## 기계 집계

'''
    outputs = {'manifest.json': manifest, 'summary.json': summary, 'input-sha256.json': pins,
               'schema-projections.json': projections, 'dev42-audit.json': rows,
               'dev-first.envelope.json': first, 'synthetic-control.request.json': control,
               'synthetic-chain.json': synthetic, 'test-output.txt': stream.getvalue(),
               'mutation-test-output.txt': mutant_stream.getvalue(),
               'report.md': report + '```json\n' + json.dumps(summary, ensure_ascii=False, indent=2) + '\n```\n'}
    if any(prior.sha(path) != value for path, value in pins.items()):
        raise ValueError('input_changed_during_preparation')
    assert prior.CAPABILITY == {'key_read': False, 'url': None}
    OUT.mkdir(parents=True, exist_ok=False)
    for name, value in outputs.items():
        prior.write_new(OUT / name, value)
    prior.write_new(OUT / 'output-sha256.json', {name: prior.sha(OUT / name) for name in outputs})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
