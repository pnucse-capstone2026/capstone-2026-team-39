# 보안 평가 전용 수집기 수정 · 2026-09-08

상태: **DONE_WITH_CONCERNS — 별도 사본 구현·오프라인 검증 완료. 서비스 미반영, 실제 재측정 전.**

사용자가 승인한 범위는 “원본 유지, 별도 평가용 수집기 보완과 오프라인 검증”이다.
`investigate`의 원인 재현 → 실패 회귀 테스트 → 최소 수정 → 전체 검증 순서로 진행했다.
원본 수집기·검색·생성·보안 규칙·Judge는 수정하지 않았다. 외부 API 호출 0회,
실제 holdout 읽기 0회, 커밋·머지·푸시 0회다.

## 1. 결과

- 입력/출력 게이트의 전체 요약을 답변 artifact의 `security`에 보존한다.
- 모든 근거가 보안 게이트에서 제외됐고 실제 생성 호출도 없었다는 계약이
  일치하면, 수집 오류가 아닌 **생성 없는 서비스 답변**으로 저장하고 다음 문항으로 진행한다.
- 모델 fallback·다른 모델·통신 오류·일반 검색 무결과는 이 예외로 통과시키지 않는다.
- 차단 여부와 GFC는 별개다. 수집기는 안전성 성공이나 정답 점수를 부여하지 않는다.

실행 사본:
`/private/tmp/pnu-security-collector-20260908.SGmlEr/repo/`.

이 사본은 앞서 오프라인 검증한 수정 보안 사본을 복사한 것이며 Git 저장소가 아니다.
새 구현 파일은 `scripts/evaluate_security_service_answers.py` 하나,
새 테스트는 `tests/test_evaluate_security_service_answers.py` 하나다.
원본 collector 1,911줄을 복사하고 보안 수집 계약만 더해 새 파일은 2,060줄이다.
이 복사본의 규모를 서비스 검색/생성 코드 증가로 합산하지 않는다.
실질 변경 구간은 전달 묶음의 `collector-delta.diff`로 검토할 수 있다.

## 2. 원인과 수정 경계

| 원인 | 별도 수집기의 수정 | 유지한 제한 |
|---|---|---|
| 응답의 최상위 `security`를 저장하지 않음 | deep copy 후 기존 record SHA 계산에 포함 | 답변·claim·인용·원문을 고치지 않음 |
| `used=none`을 무조건 provider 오류로 처리 | 엄격한 전부 제외/미생성 계약에서만 예외 | 요청 provider·parser·검색 조건 검사는 유지 |
| 보안 여부가 collector config에 없음 | `expected_security_mode`, 계약 버전, 수집기 파일 SHA 추가 | 재개 시 config 변경·기록 변조 거부 |

`inspect_security_response`는 90행, 저장 기록 검증은 201행,
응답 조건 검증은 820행, 수집 config 핀은 1,493행,
보안 요약 저장은 1,884행부터다.

```text
/chat 응답
  ├─ 정상 생성 → 기존 모델·요청 조건 검사 → 답변 + 보안 요약 저장
  ├─ 모든 근거 제외 + 생성 없음
  │    ├─ 두 게이트·개수·원문/초안 trace 일치 → security_abstention 저장
  │    └─ 불일치/누락 → control_error, 수집 중단
  └─ 모델 변경·통신 실패 → 기존 오류 처리 유지
                           ↓
            같은 config로 재개 → 완료 답변은 재호출하지 않음
```

예외에 필요한 조건은 enforce 모드·정책 버전·양쪽 입력 요약 일치,
양의 evaluated와 같은 excluded, allowed/sanitized=0, 빈 results/claims/citations,
출력 abstain와 0인 출력 검사 개수, `used=none`, `model=None`, `no_results`, 빈 attempts,
null인 generation input/raw draft/sanitized draft와 빈 final contexts다.
`used`나 `model`을 요청 모델로 덮어써 생성이 있었던 것처럼 만들지 않는다.
이 조건은 서버 관측의 일관성을 검증할 뿐, 답변 내용의 안전성/정답성을 증명하지 않는다.

새 `security_evaluation` 필드의 `outcome=security_abstention`, `generator_called=false`로
미생성을 구별한다. 생성 후 출력 게이트가 회피시킨 경우에는 `outcome=generated`를
유지하고 실제 `security.output_gate.decision=abstain`을 함께 보존한다.

보안 적용 전 조건은 `--expected-security-mode absent`로 수집하며 `security=null`이다.
머지/수정 보안은 `enforce`를 사용한다. shadow/off를 enforce로 받아들이지 않는다.
두 enforce 구현을 구별하는 서버 파일 manifest와 프로세스 연결 핀은 여전히 별도다.

## 3. 검증

정본: `processed/eval/preflight-20260908/security-collector-v1/full-final.{json,log}`.

```text
Ran 857 tests in 31.926s
OK (skipped=6)
```

857개는 기존 수정 보안 사본 828개 + 신규 수집기 테스트 29개다.
skip 6개는 선택 의존성 NumPy 4개, python-docx 1개, openpyxl 1개다.

| 단계 | 실행 / skip / 실패 / 오류 | 해석 |
|---|---|---|
| 수정 전 두 회귀 검사 `before` | 2 / 0 / 1 / 1 | 출력 요약 누락과 차단 시 수집 중단 재현 |
| 수정 후 두 회귀 검사 `after-regression` | 2 / 0 / 0 / 0 | 두 원래 문제 해결 |
| 확장 검사 첫 실행 `expanded-tests` | 26 / 0 / 0 / 1 | sandbox의 localhost bind 금지, 구현 실패 아님 |
| OS 정책 적용 후 전체 `full-tests` | 854 / 6 / 0 / 0 | HTTP 통합 포함 통과 |
| 추가 오류 경계 포함 최종 `full-final` | **857 / 6 / 0 / 0** | 최종 정본 |

모든 이전 산출물을 보존했다. 최초 localhost 오류는 예외를 무시하거나 테스트를
skip해서 통과시키지 않았다. 승인된 실행 권한에서 외부 통신 금지·localhost 허용
OS sandbox를 적용해 다시 검증했다. 실제 holdout와 `.env` 읽기, 원래 프로젝트 쓰기는
새 결과 경로 외 금지했다. Python audit hook도 함께 적용했다.

신규 29개 검사는 정상/차단/생성 후 회피/보안 전 조건, metadata 누락·형식·개수 오류,
요청 모델/provider/코퍼스/trace 불일치, 오류 중단, 기록 SHA 변조, 동일 재개·config 변경,
실제 holdout 경로를 열기 전 거부 및 합성 holdout split 거부, 빈 답변, HTTP 수집 연속성,
실제 로컬 게이트 함수와의 계약 일치, Judge 입력 호환성 등을 다룬다.
HTTP 검사는 가짜 서버를 사용한다. 실제 Gemini 서비스 end-to-end 실험은 아니다.

검증 명령:

```sh
/usr/bin/sandbox-exec -f /private/tmp/pnu-security-collector-20260908.SGmlEr/offline.sb python3 -B /Users/leehyunwoo/project/pnu-docs-chatbot/evidence/security-layer-fixes-20260908-v1/run_offline_tests.py /private/tmp/pnu-security-collector-20260908.SGmlEr/repo /Users/leehyunwoo/project/pnu-docs-chatbot/processed/eval/preflight-20260908/security-collector-v1/full-final
git apply --check evidence/security-eval-collector-20260908-v1/changes.patch
git diff --check
```

재실행은 새 산출물 경로로 한다. 원본에 patch를 적용하지 않았고 읽기 전용 `--check`만
통과했다. 신규 Python 2파일 AST·trailing whitespace 검사 통과. 프런트엔드 변경이
없으므로 lint/build는 재실행하지 않았다.

## 4. 동결 후 발견: 빈 근거와 Judge v11

기존 Judge는 `final_contexts=[]`도 “trace 없음”으로 취급해 기본 CLI에서 거부한다
(`scripts/judge_service_answers.py:1482`). 새 수집기의 명시적 차단 답변은 빈 근거가
정상적인 관측이므로, 기존 `--allow-missing-trace` 옵션을 사용한 **합성 validate-only**가
통과함을 확인했다. 요청이나 판정 출력 생성은 없었고 Judge 코드·rubric은 그대로다.

향후 이 옵션은 검증된 보안 진단 artifact에만 사용해야 한다. 먼저 전체 record SHA와
수집 계약을 검증하고, 빈 근거가 검증된 `security_abstention`인지 확인해야 한다.
일반 생성에서 trace 누락·개수 불일치는 새 수집기가 거부한다. 다른 기존 artifact나
final holdout 전체에 이 옵션을 무조건 적용해서는 안 된다.
현재 검사는 Judge 입력 호환성 검사이며 **채점 결과나 GFC 향상 결과가 아니다**.

## 5. 전달물과 남은 단계

`evidence/security-eval-collector-20260908-v1/`:

- `changes.patch`: 별도 collector·테스트 **신규 2파일**만 추가하는 전달 패치.
- `collector-delta.diff`: 원본 수집기와의 차이 검토용, 적용용 패치 아님.
- `manifest.json`: 검증한 Python 코드 159파일, 새 2파일, 원본 보존 파일,
  테스트 로그·결과·fixture·OS 정책의 SHA-256.
- `offline.sb`, `package.py`: 오프라인 정책과 새 경로 전용 패키지 생성기.

원래 프로젝트와 보존한 보안 클론에는 새 collector를 설치하지 않았다. 실제 실행본은
위 scratch 사본이며, scratch가 삭제돼도 패치로 새 collector/test를 복원할 수 있다.
패치는 검색·생성·보안·Judge·원본 collector를 수정하지 않는다.

남은 것은 세 서버 구현의 전체 파일 해시와 실제 프로세스를 연결하는 실행 전 검사,
소규모 정상/공격 진단의 사례 수·순서·호출 상한 확정, 해당 전송 범위에 대한 API 승인이다.
공식 holdout 절차나 사람 calibration은 그대로 남아 있다. 이번에 해결한 것은
**보안 평가의 수집 호환성**이며, 실제 성능 재측정이나 안전성 향상을 완료한 것이 아니다.

이전 발견 기록: [사전 점검](security-eval-preflight-20260908.md).
