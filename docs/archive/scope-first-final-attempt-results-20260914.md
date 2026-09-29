# 2026-09-14 마지막 개선 시도 결과

**결론: 이번 후보도 채택하지 않는다. 사용자 지시에 따라 추가 튜닝/수정/재시도를
끝내고 기존 C1을 유지한다. 최종평가는 별도 선행 조건 확인 뒤 실행해야 한다.**

## 실제 실행

사용자는 기존 DEV1문항의 질문·기존 초안·검색 근거8개 및 새 범위 제안을 Google
Gemini에 전송하는 최대3회 pilot을 마지막 실험으로 승인했다. 이전에 차단됐던
실행을 동일 코드/manifest로 시작했다. 사전에 입력672개 SHA와 첫 요청 body의
일치를 확인했으며 새 코드 변경은 없었다.

| 단계 | 결과 | 응답 시간 | 호출 |
|---|---|---:|---:|
| 질문 범위 추출 | HTTP200/STOP, host 검증 통과 | 2.091초 | 1 |
| 주장·근거 추출 | HTTP200/STOP, 원문 결속 검증 실패 | 12.401초 | 1 |
| 독립 의미 검토 | 첫 실패 중단 원칙으로 미실행 | — | 0 |

실제 호출2회, 재시도0, 단일 기본 키, 모델 전환0. 두 응답 modelVersion은 모두
`gemini-3.5-flash-lite`였다. 첫 ledger 시도14:08:39.553 KST부터 종료14:09:09.216
KST까지 약30초(호출 간15초 포함). 프로세스 session56200 exit2,
`STOPPED_INCOMPLETE`로 끝났다. 남은1회는 재사용하지 않는다.

scope 입력/출력 tokens378/411, 합789; extraction4804/5053, 합9857.
이는 제공자 usageMetadata이며 무료 과금 여부를 확인한 값은 아니다.
관측된 프로젝트 기존172시도에 이번2시도를 더하면174다. 계정의 하루 총 사용량과
무료 잔여량은 여전히 미확인이다.

## 무엇이 됐고, 무엇이 실패했는가

첫 범위 추출은 `entity=금정 청년 구직응원 패키지`, `year=2026`을 질문 원문에서
유일하게 인용했고 나머지5차원을 not_specified로 출력했다. 직전 실제 응답의
빈 query_scope와 달리 범위를 출력했다는 한 문항의 관측은 있다.

그러나 두 번째 추출은 다음 계약을 만족하지 못했다.

1. `body_scope`에 본문(text)이 아니라 제목(title) 인용을 넣었다. 4개 atom의
   entity/year 참조에서 총8개가 같은 유형으로 잘못 들어갔다. 첫 항목에서
   `QuoteBindingError: parent_child_source_or_field_mismatch`가 발생했다.
2. 원문 정적 확인 결과 body/title 범위 참조16개는 개별 사업명/연도 대신 전체
   파일 제목을 인용했다. 따라서 해당 quote는 assertion.scope의 개별 값과
   그대로 일치하지 않는다. 이 문제도 존재하지만 실제 pipeline은 첫 오류에서
   이미 중단됐으므로 뒤 검증의 실행 결과처럼 보고하지 않는다.
3. 추출 결과의 entity는 `2026 금정 청년 구직응원 패키지`로, 앞 단계의
   `금정 청년 구직응원 패키지`와도 달랐다. 코드가 임의 정규화하거나 덮어쓰지 않았다.

두 번째 응답은 schema 형식만 통과했다. 4단위 모두 complete를 선언하고4개 atom을
반환했으나 원문 결속/의미 완성도를 검증한 상태가 아니다. 이전 응답7개 atom과
단순 개수 비교로 개선을 주장하지 않는다. 오류 당시 HTTP 자체는200이었으며
네트워크/할당량 오류가 아니라 모델 출력의 계약 불일치다.

**사업명·연도 출력의 부분 진전은 있지만, 사용 가능한 근거 추출이나 답변 품질
개선으로 인정할 결과는 아니다.** 새 답변 생성0, Judge GFC 호출0, 의미 검토0,
서비스 적용0, GFC 향상 미측정이다. 실패를0점으로 재채점하거나 기존 결과와 합산하지 않는다.

## 마지막 시도 이후 한 일

원문 결과를 읽고 위 참조/값 불일치를 확인하는 정적 진단만 했다.
body_scope를 title_scope로 옮기거나 quote를 짧게 고치는 사후 보정은 하지 않았다.
기존 parser/후보/서비스/보안/prompt/Judge 변경0, 추가 모델 호출0이다.
새 후보나 v2 재시도도 준비하지 않았다.

실행 후 동일 후보26 tests in0.449s OK(실패/오류/skip0), 입력672개 SHA 동일,
출력12개 SHA와 inventory 포함13개 파일 집합 일치, `git diff --check` 통과를 확인했다.
직전 준비 시 관련154개와 전체862개(skip6) 통과 기록은 그대로지만,
이번에 전체862개를 새로 실행한 것은 아니다. 전체 회귀는 코드 검증이지 모델 출력
성공률이 아님을 이번 실제 실패에서도 구분해야 한다.

## 최종평가로 전환할 때

현재 런북 `docs/archive/final-eval-runbook-20260914.md`는 총432회 외부 호출 계획
(생성198 + Judge234, 기술적 재시도 제외)이다. 이번 최대3회 승인을 전량 평가의
승인으로 확대하지 않았다.

런북의 필수 조건은 사람2인 holdout sign-off와 승인된 clean code freeze다.
이번에는 holdout 질문/검토 응답을 읽지 않고 파일명·Git 상태만 확인했다.
`evidence/`의 signoff JSON 검색 결과 없음, `pnu-eval-code-freeze-*` tag 없음,
working tree에 기존 사용자 변경이 남아 있었다. 따라서 독립 최종 holdout 평가를
지금 자동으로 시작할 상태는 아니다. 검수 결과를 대신 만들거나 gate를 우회하지 않는다.

다음은 개선 실험이 아니라 **기존 C1 유지 → 사람 검수 및 최종 평가 조건 확정 →
코드 동결 → 계획된 평가 실행 → 결과 보고**다. 이 조건을 완료할 수 없으면
기존 개발셋 결과를 독립 holdout 결과처럼 명명해서 제출하지 않는다.
이 문서는 기존 최종보고서를 덮어쓰지 않는 별도 결과 기록이다.

## 정본과 SHA-256

출력 기준: `processed/eval/preflight-20260914/scope-first-v1/live-v1/`.

| 산출물 | SHA-256 |
|---|---|
| `completion.json` | `df0f292e0c959e30bcf5a1b3bea41b69c1c1ca441eb1dcd8fff950561067c9df` |
| `run.json` | `31719a07dbf91602a00a4885066c1d3f59efb1cec8f0c8e29cd4882b813b6d6e` |
| `provider-attempts.sqlite` | `6db3fed7d0e78b09dbd8c6f106605bba1285e3c5b0dda45d5898c0dd06da73fe` |
| `scope.response.txt` | `c9e0deb9af4ac46d90c966525de9560078804caa2762604aa0d413923da39425` |
| `scope.host-validation.json` | `0e0c4d6d9fb51c5b8f84b4c91959838ca9ecc95dadb1be66bad0e6795215a587` |
| `scope.receipt.json` | `f0c8c8c6a97c964c6099ecb0ecf663b991e8f3b0982dde60b563de615cd64a68` |
| `extract.response.txt` | `8878cbfff15b271eccfa6554d0b7d1701bfcb3def68a2b0e31898aecb93471c2` |
| `extract.receipt.json` | `f6907db4ad93f18fec87b2585c4b6e286d9a212b568198cb08b105a8962db130` |
| `output-sha256.json` | `c3afa7a3132c854813e7a018f01deedb1965fa62f3c2ea6a43c496d097bfdb45` |

나머지 provider/request 파일 SHA는 output-sha256.json과 progress-log에 기록했다.
준비 manifest SHA `3ae2db8749cff1f448660bc5c1eaffbfa08dc7d75580e499b6f2befed6b42b72`.
완료 상태는 **DONE_WITH_CONCERNS: 마지막 제한 실험은 종료, 후보 미채택,
추가 튜닝 중단. 최종평가 실행은 별도 선행 조건/승인 필요**다.
