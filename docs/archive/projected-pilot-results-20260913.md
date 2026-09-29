# 전송용 schema 분리 후 첫 문항 검사 — 2026-09-13

**결과: STOPPED_INCOMPLETE. 추출 요청1회가 HTTP400으로 거부됐고 즉시 중단했다.**
의미검증0회, 재시도0회, 새 모델 출력0, GFC 미측정, 서비스 미적용이다.
실제 생성 성능이 낮다는 판정이 아니라 모델 출력을 받기 전의 연결 검사 실패다.

## 이번에 확인한 것

동일 개발 문항 `shadow_emp_01`의 고정 전송본을 사용했다. 이전 v2 요청과 이번
실제 전송 body를 재귀 비교하면 minLength72개와 maxLength72개 제외만 있으며
그 외 차이는0이다. 질문·초안·출처·프롬프트·모델·온도·토큰 상한은 그대로다.
전송 schema는6,424 UTF-8 bytes,113개 schema 객체, 최대 깊이11이다.
host의 전체 검증 조건은 바꾸지 않았다.

| 검사 | API 응답 | 확인 범위 |
|---|---|---|
| 기존 v2 전체 schema | HTTP400 | 요청 거부, 모델 출력 없음 |
| 최소 합성 boolean schema | HTTP200, `{"ok":true}` | 기본 인증·엔드포인트·최소 schema 동작 |
| 길이 제한만 제외한 전체 schema(이번) | HTTP400 | 해당 제외만으로 문제 해결 안 됨 |

세 결과는 같은 설정의 추출 모델 `gemini-3.5-flash-lite`를 사용했다. 단, 최소
합성 control은 prompt와 schema가 모두 달라 전체 요청에 대한 단일 변수 대조군은
아니다. 두 전체 요청 사이에는 전송 body의 길이 제한 제외만 차이가 있지만 실행
시각이 다르고 각각1회 관측이므로 provider 내부 원인까지 확정할 수 없다.

이번 실행 시각은23:24:38–23:24:39 KST다. 반환 내용은 이전과 동일한 포괄적
`INVALID_ARGUMENT`이며 구체적인 문제 필드나 schema 경로는 없었다. token usage
receipt도 없으므로 거부된 요청을 생성 성공·과금 건수와 동일시하지 않는다.
프로젝트에서 관측한 누적 시도는167+1=168회다. 계정 전체 일일 사용량/무료 tier
잔여량은 확인하지 않았다.

## 판단과 미확정 사항

`investigate` 절차에 따라 실패 요청을 보존하고 가설을 검토했다. **“길이 제한만
빼면 해결된다”는 충분조건 가설은 이번 결과와 맞지 않는다.** 길이 제한이 원래
전혀 영향을 주지 않았다고까지 증명한 것은 아니다. 실패했는데 수정 완료로
표시하거나 다른 설정으로 자동 재시도하지 않았다.

큰/깊은 schema가 거부될 수 있다는 공식 설명은 있으나, 이번 깊이11·객체113개가
해당 모델 한도를 넘었다는 직접 증거는 없다. 복잡도, schema 기능 조합, 요청
내용/설정과 schema의 상호작용은 아직 후보 가설이다.
[Google structured outputs 문서](https://ai.google.dev/gemini-api/docs/structured-output)

스킬의 전역 설정/telemetry/동기화/자동 commit은 범위 밖이라 실행하지 않았다.
고정 코드·검증 조건은 추가로 고치지 않았다. 이번 승인 조건인 첫 오류 중단에
도달했으므로 남은 호출1회를 다른 진단에 전용하지 않았다.

## 다음 제안 — 아직 준비·호출하지 않음

다음은 새 답변 생성 실험 확대가 아니라 **요청 최소화 진단**이다. 우선 모델과
요청의 다른 설정을 고정한 채 내용과 schema를 분리한 작은 대조 실험을 설계한다.
예를 들어 전체 요청의 내용은 유지하면서 schema만 최소 형태로 바꾸는 검사와,
내용을 합성으로 바꾸되 전체 schema를 유지하는 검사를 구별한다. HTTP 수용 여부를
측정하며 여기서 나온 출력은 유효한 추출·답변으로 사용하지 않는다.

schema 쪽으로 원인이 좁혀진 뒤에만 구조를 단계적으로 축소해 거부를 만드는 최소
조합을 찾는다. 이때도 host 검증 완화나 운영 적용은 하지 않는다. 호출 수·정지
조건·출력 상한을 먼저 고정하고 새 승인을 받아야 한다. 현재 새 요청이나 실행기는
만들지 않았고 추가 API 호출은0이다.

## 검증과 원본

새 실행기 시험7개가 실행 전·후 모두 통과했다(failures0/errors0/skips0).
무결성 검사에서 입력612개 pin, live 출력6개와 목록 자체 포함7개 파일 집합,
고정 envelope와 실제 저장 요청의 일치를 확인했다. 전체 root unittest/lint/build는
이번에 재실행하지 않았다. 기존 테스트 수를 새 실측으로 재사용하지 않는다.

원본 경로:

- `processed/eval/preflight-20260913/provider-schema-v1/projected-pilot-preparation-v1/manifest.json`
- `processed/eval/preflight-20260913/provider-schema-v1/projected-pilot-live-v1/completion.json`
- `processed/eval/preflight-20260913/provider-schema-v1/projected-pilot-live-v1/shadow_emp_01--extract.request.json`
- `processed/eval/preflight-20260913/provider-schema-v1/projected-pilot-live-v1/shadow_emp_01--extract.http-error.txt`
- `processed/eval/preflight-20260913/provider-schema-v1/projected-pilot-live-v1/output-sha256.json`

실제 holdout 열람0, git 조작0. 기존 산출물은 수정·삭제하지 않았다. 이번 결과는
최종보고서의 성능 향상 수치에 포함하지 않는다. 상세 수행 명령·SHA·관련 회귀시험은
`docs/archive/progress-log-20260901.md`의 이번 projected pilot 절에 기록한다.
