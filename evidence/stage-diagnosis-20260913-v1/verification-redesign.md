# 생성·검증 분리 진단과 다음 실험 설계 — 2026-09-13

상태: **진단 완료 / 재설계 제안 / 서비스 미적용**. 외부 호출 0회.
`investigate`의 원인 분리 원칙에 따라 거부 조건을 더 완화하지 않고, 같은 저장 초안의
단계별 재현 및 원문 대조로 범위를 바꿨다. 사람 검수·독립 holdout 검증·성능 향상 결과가 아니다.

## 1. 지금 확정할 수 있는 결론

오늘 생성한 C1 core42의 최종 GFC는 기존 Judge v11 **13/42(31.0%)** 그대로다.
생성 초안에 서버 인용이 없으므로 초안 GFC를 계산하거나 최종 GFC와 비교하지 않았다.
이 진단은 이미 관찰한 개발 문항의 실패 원인을 찾는 작업이며 과거 DEV45 n=3이나
이전 Shadow60 결과를 새 결과와 섞지 않는다.[^packet]

| 단계 | 재현 결과 | 해석 한계 |
|---|---:|---|
| 원본 답변·인용·claim·후처리·보안 요약 재현 | 42/42 일치 | 새 생성 결과가 아님 |
| 초안에서 분리되어 검증된 문장 | 153 | 문장이 원자적 사실 1개라는 뜻은 아님 |
| 기존 검증기 허용 / 거부 | 105 / 48 | 허용=참, 거부=거짓이 아님 |
| 거부 문장이 있는 질문 / 전부 거부된 질문 | 29 / 6 | 질문별 복수 원인이 겹침 |
| 비GFC 중 거부가 있는 질문 / 없는 질문 | 27 / 2 | 거부를 비GFC의 단일 원인으로 볼 수 없음 |
| 문장 수 제한으로 잘린 문장 | 0 | 이번 실행의 병목은 claim limit이 아님 |
| 초안 인용 정제 변경 / 추출식 fallback | 0 / 0 | 다른 실행까지 일반화하지 않음 |
| 보안 output gate가 변경한 답변 | 0 | 보안 게이트의 의미 정확성 보장을 뜻하지 않음 |

48개 거부 문장을 저장된 검색 원문과 **AI가 모두 대조**했다. 인용 문자열은
실제 chunk의 연속 부분 문자열이어야 하며 오프셋과 SHA-256을 검증했다.
문자열 일치 검증은 아래 의미 판정의 정답성을 자동으로 보장하지 않는다.[^notes]

| AI 대조 분류 | 문장 수 |
|---|---:|
| 원문이 지지하는 사실 | 30 |
| 일부만 지지하거나 질문의 프로그램·전형·대상 범위를 벗어남 | 5 |
| 초안부터 확인 불가·충돌 확인 필요라고 쓴 회피 문장 | 13 |
| 합계 | 48 |

30개 중 질문에서 요구한 속성과 직접 관련된 것은 **15문항의 29문장**이다.
나머지 1개는 등록금 고지서 출력 시각이라는 부가 정보다. 여기서 'required'는
질문 속성과의 관련성 분류다. gold 최소 claim 전체 충족 여부와 동일하지 않다.
예를 들어 GSAT 세부 일정 3문장은 질문의 운영일정에 관련되지만 gold 최소 요구보다 상세하다.
**15문항이 추가 정답이 된다는 뜻도, GFC 개선 상한도 아니다.** 같은 답변의 다른 오류,
누락, 인용 문제 및 사람 판정 불확실성이 남아 있다.

## 2. 실제 초안과 최종답변에서 확인한 서로 다른 문제

### A. 맞는 사실의 삭제: KB굿잡 일시·장소

`shadow_emp_04` 초안에는 `2026.04.27(월) 10:00~17:00`와 `서울 COEX A홀`이
있다. 저장 Source 6의 같은 2026 제1차 KB굿잡 공지에 두 사실이 직접 나온다.
그러나 두 문장이 모두 `critical_value_mismatch`로 거부되어 최종은 근거 부족
회피다. `calendar_year:2026` 같은 표현 타입의 차이와 행사 식별 정보를
명시적인 근거 관계와 분리하지 못한 사례다. 날짜 문장은 별도로 실행한 기존
의미 검사에서도 거부되어 연도 정규화 하나만 바꿔 해결된다고 주장하지 않는다.[^source]

### B. 표 관계·표현 방식: 날짜를 찾았어도 거부

`shadow_core_04`의 GSAT 운영기간과 세부 일정은 해당 표 행에 있다.
Source 2의 전체 운영기간, Source 1의 추리·수리·모의고사 행을 각각 검사하면
critical value는 통과하지만 기존 semantic relation 검사는 거부한다.
`shadow_adm_06`은 접수일과 발표일이 **같은 전기 모집 열의 서로 다른 행**에 있어,
복합 문장의 값들이 하나의 evidence unit에 모여 있지 않아 탈락한다.[^source]

### C. 실패 이유 로그가 '근거 전체의 부재'를 뜻하지 않음

`shadow_sup_04`의 전화번호 문장은 최종 trace가 `critical_value_mismatch`이며
전화번호를 missing으로 기록한다. 하지만 실제 전화번호가 있는 **Source 7만** 검사하면
critical value와 semantic 검사는 통과하고 `low_lexical_overlap`로 거부된다.
`attribute_claim()`이 lexical 점수가 가장 높은 후보의 missing 값을 대표로 남기기
때문이다. 따라서 현재 trace의 missing을 '모든 검색 근거에 전화번호가 없다'로
해석하면 잘못된 원인 분석이 된다. 서비스 trace를 고치지 않고 진단 도구에서
후보별 결과를 따로 저장했다.[^source]

### D. 초안부터 다른 프로그램을 섞음

- `shadow_emp_06`: 질문은 **SK텔레콤**인데 초안의 1·2차 일정은 Source 3의
  **해냄/HNM** 프로그램 일정이다. Source 4는 `운영기관: 해냄 주식회사 [(주)HNM]`을
  명시한다. 4주 기준 지원금도 해당 자료에서 가져왔다. 날짜가 원문에 존재한다는
  이유만으로 거부 문장을 복원해서는 안 된다.
- `shadow_intl_02`: 질문은 해외 대학 학생이 **부산대로 오는** 방문학생이다.
  Source 1은 부산대에 직접 납부한다고 설명하지만 초안은 Source 5의
  **해외파견** 표를 섞어 `본교 및 파견 대학 학비 모두 납부`라고 답한다.
  이 문장은 기존 검증기도 허용했다. 같은 '방문학생' 키워드라도 방향이 다르다.

두 사례는 모든 허용 문장의 전수 사실 검수가 아니라 확인된 사례 분석이다.
생성 오류율이나 허용 문장 precision을 이 두 건으로 계산하지 않는다.[^packet]

### E. 복원해도 생성 누락은 남음

`shadow_grad_01` 원문은 `국내 대학(원) 재학(휴학)생`인데 **raw draft부터**
`국내 대학원 재학휴학생`으로 좁혀 학부생을 빠뜨렸다. 회차 타입 문제만 고쳐도
이 누락은 해결되지 않는다. `shadow_sup_04`도 초안부터 원문의 '방문 전 예약 필수'가
없으므로 운영시간·전화번호 복원과 전체 gold 충족은 구분한다.[^packet]

### F. 생성 성능과 구별해야 할 평가·시점 문제

문장 삭제가 없던 비GFC 2건은 아래와 같다. 이를 후처리 탓으로 세지 않는다.

1. `shadow_adm_05`: 초안·최종에 `운전먼허증`이라는 오타가 있고, Judge가
   제시한 answer_quote에는 `운전면허증`으로 정상화되어 있다. v11의 연속 인용
   검증에서 실패하여 supported→missing, GFC true→false, score 2→1로 내려갔다.
   실제 saved guard에 이 경로가 남아 있다. 이는 인용 검증 규약의 민감성 사례이며
   이 관찰을 이유로 기존 guard나 점수를 바꾸지 않는다. 원문 수험표의 누락도 있지만
   질문이 수험표를 명시적으로 묻지 않으므로 **이번 비GFC의 확인된 원인을 수험표
   누락으로 바꿔 쓰지 않는다**.
2. `shadow_sup_05`: gold와 Judge는 Source 1의 **2020-04-20 상담**에 따라
   무보수여도 신고해야 한다고 판단한다. 그런데 검색 Source 4의 **2021-04-28 상담**에는
   `사례금을 받는 외부강의등만 신고 의무 있으므로 사례금 없는 경우는 신고대상이 아님('20.5.27.개정)`이
   있다. 최종 답변은 이 자료와 일치한다. 따라서 단순 부정어 반전 환각으로
   단정할 수 없다. **저장 코퍼스와 gold의 시점 불일치 의심**으로 별도 검토한다.
   현행 법률의 정확성을 판정한 것이 아니며 법률 안내로 사용하지 않는다.

추가로 `shadow_intl_06`의 예금증명 유효기간은 일반 전형과 재외한국교육원장
추천 트랙의 일정 범위가 섞일 여지가 있다. 이 답변의 5/8 날짜는 후자 자료에
있으므로 무조건 '맞는 문장 삭제'로 합산하지 않았다. gold/질문 시점·전형의
모호성은 별도 이슈이며 이번 실험의 gold와 정본 수치는 그대로 유지한다.[^packet]

## 3. 수정 위치와 설계 원칙

현재 경로는 동결 snapshot `scripts/search_api.py`의 `_critical_value_support`
(6770행), `attribute_claim`(6926행), `split_draft_claims`(7084행),
`build_rag_response`(7100행)이다. `_semantic_relation_mismatch`(6527행)는
특정 관계와 예외를 추출해 검사하지만 질문 자체는 `attribute_claim()`의 인자가 아니다.
따라서 질문의 주체가 생략된 claim을 질문 범위와 직접 묶는 일반 계약이 약하다.
이 행 번호는 루트 파일이 아니라 **C1 보안 snapshot** 기준이다.[^snapshot]

다음 구현은 별도 실험 모듈에서 **claim–source 관계를 명시적으로 표현하는 구조**로
시작한다. 이름별 허용목록, 날짜별 정규식, 검증 threshold 완화를 추가하지 않는다.

| 단계 | 명시적으로 남길 데이터 | 실패 시 처리 |
|---|---|---|
| 질문 범위 | 기관·프로그램·전형·방향·연도/회차·묻는 속성 | 모호하면 범위를 밝혀 질문/보류 |
| 사실 후보 분리 | 주체, 속성, 값/단위, 부정·의무, 조건, 시점 | 다른 속성을 한 문장에 뭉개지 않음 |
| 근거 결합 | chunk ID, 본문 span, 행·열 header span, scope span | 제목/값 존재만으로 통과 금지 |
| 관계 검증 | supported / contradicted / insufficient / uncertain 및 각 근거 | 불확실성을 거짓과 구분, 자동 복원 금지 |
| 답변 조립 | 지지된 사실과 대응 인용, 미확인 질문 속성 목록 | 근거 없는 보충 생성 금지 |
| 기존 보안 게이트 | 기존 context/output 정책·로그 | 우회·완화 없음 |

구체 계약은 다음과 같다.

- 제목의 제7회는 `event.edition`이다. `연 3회`는 수량/빈도다. 제목에서 상속한
  값은 본문 fact span과 다른 종류로 남긴다. 제목 7회·본문 8회이면 자동 상속하지 않는다.
- 표는 셀 값만 떼지 않고 **행 속성 + 열 대상 + 값**을 묶는다. PDF/HWP 표 구조가
  불명확하거나 병합 셀 범위를 복구할 수 없으면 uncertain으로 두고, 이 단계에서
  파서를 몰래 바꾸지 않는다. 동일 표의 두 행 요약도 두 fact를 각각 검증한 뒤 조립한다.
- 같은 값이라도 `접수 마감`과 `결과 발표`, `환수`와 `지급`, `필수`와 `선택`,
  `국내 대학(원)`과 `대학원`, `inbound`와 `outbound`는 다른 관계다.
- 출처의 최신성은 게시일 숫자 하나로 결정하지 않는다. 적용 시점·변경 조항·대상
  프로그램이 확인되어야 하며, 비교 불가능한 충돌은 양쪽 근거와 함께 보류한다.
- 검증은 **인용에 붙이는 모든 source**에 대해 수행한다. Source 1이 이메일을
  지지한다고 같은 문서의 개인정보 양식 Source 2도 덧붙이지 않는다.
- URL·해시·인용 offset의 무결성과 의미 지지는 별도 결과다. 기존 보안 게이트는
  의미 정확성 판정기로 간주하지 않는다.
- facet 목록은 운영 질문에서 유도한다. 평가 gold의 required_claims는 평가에만
  사용하며 서비스 생성 입력이나 답 복원 규칙에 공급하지 않는다.

## 4. 다음 실험의 순서와 채택 기준

**1차: API 0회 계약·반례 시험.** 신규 실험 모듈과 합성 fact/span fixtures로
자료형, 동일 source 결합, 표 header 연결, 충돌 보류, 인용 최소화를 먼저 검증한다.
기존 회차 v2의 29개 의미 probe(오허용 5, 오거부 1)를 개발 회귀시험으로 재사용한다.
이미 본 반례이므로 이를 통과해도 독립 안전성 입증이라고 쓰지 않는다. 새 합성 기대값은
실행 전에 저장하고, 같은 값의 다른 주체·회차·속성·의무·범위 반례를 추가한다.

**2차: 동일 초안 검증기만 비교.** 오늘 42개 raw draft와 동일 contexts를 그대로
고정하고 기존 검증/후보 검증만 바꿔 문장 보존·오허용·인용 연결 차이를 관찰한다.
raw에 없는 정답을 주입하지 않는다. 생성 프롬프트·모델·검색도 동시에 바꾸지 않는다.
LLM 기반 fact 추출/의미 검증이 필요해지면 요청 payload·모델·예상 호출 수를
먼저 확정하고 별도 승인 후 실행한다. 현재는 호출 계획도 실행하지 않았다.

**3차: 생성 구조 변경은 별도 비교.** 검증기 개선과 구분해 동일 contexts에서만
질문 범위/속성별 생성 구조를 비교한다. 이후 필요하면 n=3 반복 생성과 동결된
v11 Judge로 최종 GFC를 평가한다. 사람이 확인하지 않은 AI 라벨을 gold로 승격하지 않는다.

개발 단계의 필요조건(일반화 보장은 아님):

- 원본 C1 재생 42/42 일치, 입력 pin 불변.
- 고정 오답 반례의 **새 오허용 0**, 의무/금액 방향 반전 및 다른 프로그램 혼합 차단.
- 정상 반례의 회수율도 병기. 전부 거부하는 검증기를 성공으로 간주하지 않음.
- 새로 보존된 문장은 원문 span 대조; 추가되는 **각 인용**도 관계 검증.
- source-supported 문장 수와 최종 GFC를 분리. 최종 GFC는 새 판정 없으면 null.
- 평가 시점 모호성은 전체 정본 결과를 보존한 채 별도 민감도 분석 후보로 명시한다.
  실패 문항을 뒤늦게 빼거나 정답을 고쳐 headline 점수를 올리지 않는다.
- 진짜 holdout은 사람 검수·signoff 및 사전 조건 충족 이후에만 실행한다.

오늘 제출 준비에는 **확인된 실측 + 실패 원인 + 채택하지 않은 후보의 근거**를
사용한다. 구조 재설계를 구현하지 않은 채 성능이 개선됐다고 쓰지 않는다.

## 5. 보고서에 넣을 수 있는 문단

> 추가 개발 진단에서는 동일한 검색 컨텍스트에서 생성된 C1 답변 42개의 원시 초안과
> 후처리 결과를 재생하여 최종 답변 및 인용·보안 요약의 일치를 확인하였다.
> 총 153개 문장 중 기존 검증기는 105개를 허용하고 48개를 거부하였다.
> 거부 문장의 사후 AI 원문 대조에서 30개는 근거가 지지하는 사실, 5개는 부분 지지 또는
> 대상 범위 불일치, 13개는 초안 자체의 회피 진술로 분류되었다. 원문이 지지하면서
> 질문 속성과 관련된 거부 문장은 15문항의 29개였으나, 이 수치를 추가 GFC나
> 성능 향상의 추정치로 사용하지 않았다. 다른 프로그램의 정보 혼합, 초안의 대상 범위
> 누락, 평가 근거의 적용 시점 불일치도 관찰되어, 후처리 완화만으로는 해결되지 않는
> 생성·근거 결합·평가의 복합 병목이 확인되었다. 본 분류는 사람 검수나 독립 holdout
> 평가가 아닌 개발 자료의 AI 진단이며, 정식 성능 수치는 기존 Judge v11 결과를 유지하였다.

## 재현 및 파일

```sh
python3 -B evidence/stage-diagnosis-20260913-v1/diagnose.py build --out processed/eval/preflight-20260913/stage-diagnosis-v1/packet-v1
python3 -B evidence/stage-diagnosis-20260913-v1/author_observations.py
python3 -B evidence/stage-diagnosis-20260913-v1/diagnose.py analyze --packet processed/eval/preflight-20260913/stage-diagnosis-v1/packet-v1/packet.json --notes processed/eval/preflight-20260913/stage-diagnosis-v1/notes-v1/ai-observations.json --out processed/eval/preflight-20260913/stage-diagnosis-v1/analysis-v1
python3 -B evidence/stage-diagnosis-20260913-v1/inspect_sources.py
python3 -B -m unittest discover -s evidence/stage-diagnosis-20260913-v1 -p 'test_*.py'
```

생성 명령은 이미 존재하는 경로를 덮어쓰지 않는다. 재생 시 build/analyze는 새
출력 디렉터리를 사용해야 하며, 고정 packet에 결합된 AI notes를 새 packet의 독립
검수 결과로 재사용해서는 안 된다. 상세 42문항 초안·최종 및 삭제 근거 대조는
[comparison.md](../../processed/eval/preflight-20260913/stage-diagnosis-v1/analysis-v1/comparison.md)에 있다.

[^packet]: `processed/eval/preflight-20260913/stage-diagnosis-v1/packet-v1/packet.json` 및 `mechanical-summary.json`. 원본은 `processed/eval/preflight-20260913/scope-bound-c3-v1/live-v1/c1-sec-control--<case_id>.{answers,judgments}.jsonl`. 원래 answer/Judge 쌍의 binding을 개별 검증했다.
[^notes]: `processed/eval/preflight-20260913/stage-diagnosis-v1/notes-v1/ai-observations.json`; `analysis-v1/observations-with-spans.json`, `analysis-v1/summary.json`. author_type=assistant_ai, human_review=false, 원시 초안/대체 최종 GFC=null.
[^source]: `processed/eval/preflight-20260913/stage-diagnosis-v1/source-mechanics-v1/per-source.json`. 4개 질문 10개 거부 문장에 대한 14개 후보별 검사. 이 선택 표본의 비율을 전체에 일반화하지 않는다.
[^snapshot]: `processed/eval/preflight-20260909/security-live-preparation-v1/preparation-v1/snapshots/c1-sec-merged/`. snapshot SHA `ce761bc0b4a0a48b421b0a8e126425027dd776953d35eff8ec26fbce122402f6`. 산출물 SHA와 수행 결과는 `docs/progress-log-20260901.md`의 '2026-09-13 생성 초안·검증·최종답변 분리 진단'에 기록한다.
