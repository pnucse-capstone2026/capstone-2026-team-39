# 배열 제약 분리 실측 결과 — 2026-09-14

## 결과

**이번 합성·DEV 두 요청은 모두 HTTP200으로 수용됐다.** 이전 HTTP400 요청에서
API schema의 maxItems10개만 제거했다. 질문·프롬프트·모델·나머지 출력 구조는
동일하다. 이 두 재현 조건에서는 배열 제약 분리로 전송 오류가 해소됐다.
제공자 내부의 정확한 한도나 모든 요청에서의 호환성을 확정한 것은 아니다.

그러나 DEV 출력은 기존 인용 검증에서 `QuoteBindingError: quote_ambiguous`로
거부됐다. **유효 추출0 / 의미검증 호출0 / 성능 개선 미확인**이다.

| 조건 | 이전 요청 | 이번 요청 | 이번 후속 검사 |
|---|---|---|---|
| 동일 합성 ALPHA 내용 | HTTP400, 전체 projection | HTTP200 / STOP, maxItems만 제거 | 전체 JSON schema·요청 결속 통과, 근거 정확도는 미평가 |
| 동일 DEV shadow_emp_01 내용 | HTTP400, 전체 projection | HTTP200 / STOP, maxItems만 제거 | 전체 JSON schema 통과, 인용 위치 모호성으로 거부 |

이전 관측은 각각1회이고 이번도 각각1회다. 시간 차이가 있는 관측이며 provider
내부 동작을 직접 확인한 실험은 아니다. 원래 schema의 깊이11·노드113·타입·
필수 필드·enum은 유지했다. 전체 body는 합성9,376→9,236 bytes,
DEV21,798→21,658 bytes다. 전체 크기가 단140 bytes 줄었다는 사실 자체를
품질이나 지연 개선으로 해석하지 않는다.

## 실행 및 승인

직전 보안 검토 거부 후, 목적지 Google Gemini(`generativelanguage.googleapis.com`),
합성 입력 및 DEV 질문1개·원래 초안·근거8개 전송, 최대3회/기존 키1개/재시도0/
첫 오류 중단을 사용자에게 구체적으로 안내했다. 사용자 **“응 승인할게”** 이후에만
require_escalated 실행 요청이 허용되어 실제 프로세스가 시작됐다.

```sh
python3 -B evidence/array-schema-20260914-v1/run_array.py live --manifest-sha256 c53c5424646605b75bb432660936eb87a1f6d2709b232206397b8963af6dfa02 --authorize I_APPROVE_ARRAY_SCHEMA_THREE_ATTEMPTS --approval-message '응 승인할게'
```

이 명령은 실행 기록이며 재실행 지시가 아니다. 기존 경로 재사용/resume는 금지된다.
실행 session87403은 첫 host 오류에서 exit2로 종료했다. 백그라운드 작업은 남기지
않았다. 준비 manifest의 과거 승인 대기 상태를 덮어쓰지 않고 run.json에 이번
실제 승인 문구를 추가 보존했다.

| 단계 | 시작(KST, 2026-09-14) | HTTP | 지연(ms) | 입력/출력/합계 tokens |
|---|---|---:|---:|---|
| 합성 추출 | 02:49:40.537 | 200 | 3,010.173 | 626 / 708 / 1,334 |
| DEV 추출 | 02:49:58.573 | 200 | 10,779.670 | 4,475 / 4,427 / 8,902 |
| 독립 의미검증 | 실행 안 함 | — | — | — |

종료02:50:09.376 KST. 추출 모델은 두 요청 모두 반환값 기준
gemini-3.5-flash-lite다. temperature0/maxOutputTokens8192/JSON MIME/
timeout45초/호출 간격15초를 유지했다. 이번2시도·HTTP200 2회·재시도0·
의미검증0이며, 3회 상한에1회 여유가 있어도 첫 오류 중단 조건에 따라 사용하지
않았다. 남은1회를 다른 진단이나 재시도에 전용하지 않았다.

누적 프로젝트 관측 시도170+2=172회. 이는 계정 전체 일일 총량/무료 잔여량이
아니다. usage의 serviceTier=standard를 과금 여부 증거로 사용하지 않는다.

## DEV 오류 원인: 짧은 인용문의 전역 중복

DEV 질문은 금정 청년 구직응원 패키지의 중복 제외 사업·선정 통보/지급 시점·
자격증 응시료 연간 한도다. 모델은 초안4개 단위 모두를 complete로 제안하고
총7개 atom을 반환했으나, 이것은 모델의 자기 보고이며 검증 통과를 뜻하지 않는다.

원본 출력에 변경 없이 전체 schema와 request binding을 검사하면 통과한다.
원본을 기존 parse_extraction에 그대로 넣으면 quote_ambiguous가 재현된다.
query/claim/source 참조30개를 각각 지정된 원문에서 중복 포함 검색하면 유일28,
중복2, 미존재0이다. 중복2개는 모두 u003의 첫 atom/첫 evidence에 있다.

| JSON 위치 | 인용 | 원문 전체의 시작 위치 | 유일한 evidence_quote 내부 시작 위치 |
|---|---|---|---|
| `$.units[3].atoms[0].evidence[0].relation_span` | 자격증 응시료 | 10, 181 | 10 |
| `$.units[3].atoms[0].evidence[0].operator_span` | 한도 | 31, 65, 175 | 31 |

출처는 `doc_cb574d4321358d2eb447a2af:cascade#0002`, field=text다. 위치는
Python Unicode 문자 인덱스이며 byte offset이 아니다. 부모 evidence_quote는
원문 전체에서 유일한 `[8,39)` 범위:

> ▷ 자격증 응시료 : 1인 연간 10만원 한도 실비 지원

기존 quote_adapter.py:135는 지정된 source field 전체에서 인용이 반복되면
거부한다. extraction_bridge:222의 relation_span 처리에서 먼저 중단된다.
모델이 충분히 긴 인용을 복사하라는 기존 지시를 지키지 못한 결과이며,
검증기의 동작 자체는 고정 계약과 일치한다. API400이나 JSON 구문 오류가 아니다.
다른 host 검사는 이 첫 오류 이후까지 모두 실행되지 않았으므로 추가 결함이
없다고 주장하지 않는다. 28개 유일 참조 역시 의미적 정답을 입증하지 않는다.

## 후속 후보, 이번에는 미적용

이미 원문 전체에서 유일하게 확인한 evidence_quote 내부에서 relation/operator
위치를 검증하는 **부모 근거 범위 결속 방식**을 일반적인 차기 계약 후보로 검토할
수 있다. 이번2개는 그 범위 안에서 각각 유일했다. 다만 현재 계약은 source field
전체 유일성을 요구하므로 이를 몰래 완화하지 않는다. 새 계약으로 다루려면
다른 출처/범위 이탈/동일 범위 중복/상충하는 부모 근거에 대한 부정 테스트와
독립 의미검증이 필요하다. 이번 실행의 실패를 나중에 성공으로 재분류하지 않는다.

이번에는 모델 출력 수선·첫 번째 위치 선택·호스트 검사 완화·prompt 수정·추가
호출 없이 원인 조사까지만 수행했다. 서비스 규칙이나 DEV 표적 규칙을 추가하지
않았다. 추가 실행/계약 변경은 별도 결정 사항이다.

## 검증 및 보존

- 실행 전 unittest14 tests in0.478s OK, 실행 후14 tests in0.472s OK.
  반복 실행을 합산하지 않으며 failures0/errors0/skips0이다.
- input635개 SHA와 준비 manifest, 실제 요청 envelope의 일치 확인.
- 저장된 두 body에서 제거한 maxItems10개를 복원하면 각각 이전 body와 정확히 일치.
- live 출력12개 SHA 및 자기 목록 포함13개 파일 집합 전부 일치.
- git diff --check 통과. 이번 root 전체시험/lint/build는 재실행하지 않았다.
- investigate 스킬에 따라 실제 전송 문제와 후속 근거 검사 실패를 분리했다.
  전역 스킬 설정/telemetry/동기화/commit은 작업 범위 밖이라 실행하지 않았다.
- 서비스/보안/Judge/검증기/원래 prompt/기존 산출물/README/준비 manifest 수정0,
  실제 holdout 열람0, git 쓰기0. 검사 때 key_read=false/url=null이었다.

원본 디렉터리:
`processed/eval/preflight-20260914/array-schema-v1/live-v1/`.

| 주요 산출물 | SHA-256 |
|---|---|
| `completion.json` | `8f650092017240e42d6fb7b250c9b5a54b91b42e2a460852e316bbdd62e76157` |
| `output-sha256.json` | `5cbc5c120a84bedf2a00e925d62481bfeeb69dd8f5444569dadcd60f755fa296` |
| `provider-attempts.sqlite` | `5370aa99f1675fe0f848f1efd1634fd784d2802872abfc58e49291a6cdea78da` |
| `run.json` | `705ca87f04f7c9cfafd70fe6ceab19e15a345939276d9a8a4178f595d1581ad7` |
| `shadow_emp_01--extract.response.txt` | `f0cc846bc5d1652073ef0a91061bfaf6e3ac6361f802bc140853f782a5cdcf2d` |
| `shadow_emp_01--extract.receipt.json` | `94ed24e4b644d5aa54c22fd435557805476ead3bb111af6971651fcd5f369c97` |
| `synthetic--extract.receipt.json` | `674c72fef6dd1d34a8f49e4e3a775539a82eaa7a4e7130d3e2258b081544e3c8` |

최종 상태: **전송 호환성 수정 효과 관측 / 전체 실행 STOPPED_INCOMPLETE**.
추출·의미검증은 미완료이며 eligible_for_service=false, candidate_gfc=null이다.
최종보고서의 GFC·생성 성능 수치에 합산하지 않는다.
