# 내용·출력 schema 분리 진단 — 2026-09-13

## 결론

**이번 관측에서는 입력 문서 내용보다 전체 responseJsonSchema 쪽으로 오류 범위가
좁혀졌다.** 실제 내용은 최소 schema에서 수용됐고, 전체 schema는 짧은 합성
내용에서도 거부됐다. 정확히 어떤 schema 기능·중첩·조합이 문제인지는 아직
확정하지 못했다. 근본 수정 완료나 생성 성능 향상으로 보고하지 않는다.

| 요청 | 이전 전체 요청에서 바꾼 요소 | 전송 body 크기 | 결과 |
|---|---|---:|---|
| 이전 실제 내용 + 전체 schema | 없음 | 21,798 bytes | HTTP400 |
| A: 실제 내용 + 최소 schema | responseJsonSchema만 교체 | 15,477 bytes | HTTP200, STOP, `{"ok":true}` |
| B: 합성 내용 + 전체 schema | contents만 교체 | 9,376 bytes | HTTP400/INVALID_ARGUMENT |

A와 B는 서로 단일 변수 비교가 아니라, 각각 이전 전체/전체 요청에 대한 단일
요소 비교다. 두 요청의 systemInstruction·모델·온도·토큰 상한·MIME type은 그대로다.
B의 가상 ALPHA 질문·초안·출처만 교체했으며 실제 대학 문서를 포함하지 않는다.
이 변경 범위는 실제 저장한 요청들을 복원 비교해 검증했다.

작은 B 요청이 실패하고 더 큰 A 요청은 성공했으므로 단순한 전체 body 크기 상한만으로
이번 결과를 설명하기는 어렵다. schema의 복잡도 한도인지 특정 기능 조합인지는
구분되지 않았다. 각각1회 관측이며 provider 내부 검증 원인까지 단정하지 않는다.

## 실행 조건과 결과 해석

- 승인: 사용자 “계속ㅐ줘”, 고정 진단 최대2회·한 키·재시도0·첫 오류 중단.
- 모델: 둘 다 `gemini-3.5-flash-lite`. temperature0, maxOutputTokens8192,
  responseMimeType application/json, timeout45초, 호출 간15초.
- 실행: 2026-09-13 23:58:27–23:58:44 KST. A 완료 후 B를 실행했고 B의400에서 중단.
- 실제 호출2회, HTTP200 1회, HTTP400 1회, 의미검증0회, 재시도0회.
- A latency1491.468ms, 반환 usage 입력4475/출력5/합계4480 tokens.
- B는 구체적인 문제 필드 없는 INVALID_ARGUMENT이며 usage receipt 없음.

실행 상태는 STOPPED_INCOMPLETE다. B가 실패했지만 원인 분리 관측값은 보존했다.
A의 ok는 연결 진단용 boolean일 뿐 실제 문항의 추출·답변·정답 판정이 아니다.
이 진단에는 고정 host parser를 적용해 정상 추출이라고 인정하는 단계가 없으며,
valid_extraction=false, eligible_for_service=false, candidate_gfc=null을 유지했다.
최종보고서의 성능 결과와 합산하지 않는다.

관측된 프로젝트 누적 시도는168+2=170회다. 거부 요청을 생성 성공·과금 건수로
동일시하지 않는다. 계정 전체 일일 사용량/무료 tier 잔여량은 미확인이다.
인증 키는 승인된 실행에서만 메모리로 사용했고 로그/URL/산출물에 남기지 않았다.

## 구현·검증

새 진단 실행기와 시험은 `evidence/schema-isolation-20260913-v1/`의2개 파일뿐이다.
이전 고정 실행기와 projection을 SHA로 확인해 사용했다. 시도 예약을 SQLite에
확정한 후 요청을 보내며, A의 오류·미완료 출력·JSON 오류가 있으면 B도 금지한다.
HTTP200 뒤 MAX_TOKENS가 나더라도 receipt를 보존하고 오류로 중단한다.

새 시험8개는 처음0.171s, 실행 후0.093s 모두 OK였다(failures0/errors0/skips0).
입력621개 pin, live 출력10개 SHA 및 목록 포함11개 파일 집합, 두 요청의 변경
범위를 다시 확인했다. git diff --check 통과. 전체 root unittest/lint/build는
이번에 재실행하지 않았으며 이전 시험 결과를 새 결과로 인용하지 않는다.

investigate 스킬의 증거 수집→한 요소씩 비교하는 가설 검사 절차를 적용했다.
운영 코드 수정·전역 스킬 설정·telemetry·동기화·자동 commit은 하지 않았다.
실제 holdout 열람0, 기존 답변/판정/summary/README 수정0, git 조작0이다.

## 다음 단계

후속 작업은 전체 schema에서 거부를 만드는 최소 구조를 좁히는 진단이다. 구조를
축소한 진단 출력은 정상 추출로 사용하지 않으며 기존 host 검증을 완화하지 않는다.
무작정 동일 요청을 재시도하거나 DEV 실패 문항용 규칙을 추가하는 작업이 아니다.
구조 후보·호출 수·중단 조건을 먼저 고정하고 새 승인 후에만 호출해야 한다.
이번2회 승인은 종료했으며 추가 구조 수정·요청 생성·호출은 수행하지 않았다.

원본은 `processed/eval/preflight-20260913/provider-schema-v1/isolation-live-v1/`에
보존했다. `completion.json`, A의 `*.receipt.json`, B의 `*.http-error.txt`와
`output-sha256.json`을 함께 확인한다. 수행 명령과 전체 SHA는 progress-log의
“내용/schema 분리 진단” 절에 기록한다.
