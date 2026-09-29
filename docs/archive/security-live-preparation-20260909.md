# 보안 실측 준비: 사본 복구·실행 제안·재개 검사 (2026-09-09)

**완료:** 사라진 코드 사본 3종 복구, 지속 보관본, 미승인 실측 설정,
읽기 전용 중단 기록 검사. **미완료:** 실제 API 실행기/키 전달 연결 및 그 통합 검증.
외부 LLM 0회, 서버 시작 0회, 실측 장부 생성 0회. 새로운 성능 결과는 없다.

정본 루트: `processed/eval/preflight-20260909/security-live-preparation-v1/preparation-v1/`.
`preparation.json` SHA-256:
`d18ef3204023ee0bc5efdcf90ef9a1275f2a9aff5052e7c34979c754996273cb`.

## 1. 사본 복구

이번 실행 시작 시 `/private/tmp/pnu-security-pilot-20260908.fa9QFA/`와 이전 검토/
수집 클론 경로를 확인했으며, 이 세 경로는 존재하지 않았다.
삭제 원인은 확인되지 않았다. 따라서 9월 9일 앞선 성공 기록만으로 현재 실행본도
존재한다고 가정하지 않았다.

로컬 Git에는 기존 두 기준 커밋 객체가 남아 있었다. 체크아웃/브랜치 변경/네트워크
fetch 없이 `git archive`로 명세에 고정된 소스만 읽고, 보관된 SHA 고정 패치를
메모리에서 정확히 재적용했다. 문맥/행 번호가 다르면 중단하며 fuzzy 적용은 없다.
모든 파일 SHA와 전체 snapshot SHA가 원래 정본과 일치한 뒤 새 경로에 기록했다.
이는 이미 승인·검증된 코드의 바이트 복원이지 새로운 방어/검색/생성 수정이 아니다.

| 조건 | 파일 수 | 원래 snapshot SHA와 비교 |
|---|---:|---|
| c1-pre-security | 97 | 일치 (`620e681b…`) |
| c1-sec-merged | 102 | 일치 (`ce761bc0…`) |
| c1-sec-fixed | 103 | 일치 (`d3f5c889…`) |

총 **302파일**. 정본 루트 `snapshots/<조건>/`에 지속 보관본을 만들고,
현재 없어진 기존 임시 경로에도 동일 바이트를 복원해 이전의 경로 고정 검증을
다시 사용할 수 있게 했다. 기존 preparation/policy/result의 경로·SHA는 수정하지 않았다.
복구 대상이 이미 있으면 전체 목록/SHA를 확인하기만 한다. 하나라도 다르면 덮어쓰지 않는다.
부분 복구 후 실패해도 자동 삭제/재작성하지 않고 수동 확인 대상으로 남긴다.

기존 입력 67파일, 원본 서비스 6파일 및 앞선 HTTP mock 정본의 보조 40파일 SHA 보존.
새 보관본 302파일과 JSON 3개, 총 **305개** 보조 산출물 SHA를 재계산하여 확인했다.
Git 객체가 남아 있는 동안 새 경로에 재구성 가능하고, 지속 보관본도 파일별 SHA로 검증 가능하다.

## 2. 미승인 실측 제안

`execution-proposal.json`은 기존 204개 slot의 순서·문항 hash·모델·재시도·sampling을
그대로 보존한다. 각 slot에 기존 입력에서 확인한 `source_manifest_sha256`만 추가하고,
인덱스를 read-only로 열어 metadata의 값과 일치하는지 대조했다. 정상 인덱스의 이 값은
chunk manifest SHA(`5d1b5fee…`)가 아니라 source manifest SHA(`1fa7e0f2…`)다.

| 범위 | 생성 | Judge | 재시도 포함 provider 상한 |
|---|---:|---:|---:|
| 정상 14문항 × 3조건 | 42 | 42 | 378 |
| 공격 10종 + 짝 정상 10종 × 3조건 | 60 | 60 | 540 |
| 합계 | **102** | **102** | **918** |

생성 `gemini-3.5-flash-lite`, Judge v11 `gemini-3.1-flash-lite`, 독립 생성 n=1.
생성 최대 3회, Judge 최대 6회, 단일 스트림. 다른 모델 fallback 없음.
고정 모델의 현재 가용성이나 무료 잔여 한도는 API로 확인하지 않았다.
**918회는 호출 상한이지 무료 티어 보장이 아니다.**

`approval-request.json`은 `approved=false`인 요청 명세다. 실측 제안에도
`api_execution_authorized=false`, `live_runner_ready=false`를 유지했다.
예정 run 경로는 `processed/eval/preflight-20260909/security-pilot-live-v1/run`이며,
이번에는 생성하지 않았다. 이전 synthetic 장부를 실측 장부로 재사용하지 않는다.

승인 대상 전송 내용은 정상/가상 질문·검색 근거·생성 답변 및 Judge gold/rubric이다.
실제 holdout이나 API 키를 prompt에 넣지 않는다. 키의 출처/전달은 별도 명시하고
명령 인자·로그·산출물에 키를 남기지 않아야 한다. 이번 준비에서 키/`.env`는 읽지 않았다.
정상 입력은 이미 공개된 Shadow 진단 자료, 공격 20종은 가상 질문 **하나**의 변형이다.
이 파일럿을 최종 holdout 일반화 결과로 해석하지 않는다.

## 3. 읽기 전용 재개 상태 확인

`resume-assessments.json`은 run 경로·정책·장부 inode·slot 설정·완료 파일 SHA를
대조한다. SQLite `mode=ro`를 사용하며 기존 장부/답변/판정을 수정하거나 재생성하지 않는다.
아래 상태는 재개를 자동 허용하는 명령이 아니다. 실측 재개 전에는 기존 bridge의
엄격한 answer/Judge 내용 검증과 실행 프로세스 종료 확인이 추가로 필요하다.

| 이전 mock 실행 | 기존 provider 시도 | 검사 결과 |
|---|---:|---|
| verification-v1 | 0 | 시작 기록은 있지만 답변 없음 1건, 미시작 11건. 수동 확인 필요 |
| verification-v2 | 0 | 동일. 호출 0회여도 새 실행처럼 자동 재시도하지 않음 |
| verification-v3 | 10 | 12개 완료 파일 hash 일치, 미완료 실행 없음 |

저장 후 미봉인, 시작 후 파일 없음, 전송 완료 여부 불명, 완료 봉인 후 bookkeeping 미완료를
구분한다. 어느 경우도 이 검사기가 예약을 환급하거나 완료 상태를 임의로 바꾸지 않는다.

## 4. 검증

- 새 복구/재개/설정 테스트: **17 / skip 0 / 실패 0 / 오류 0**, 0.051초.
- 복구한 사본을 사용하는 기존 bridge 전체: **27 / 0 / 0 / 0**, 0.903초.
- 기존 runtime guard 전체: **30 / 0 / 0 / 0**, 0.255초.
- 합계 **74개 통과**. bridge의 합성 전송 8회는 별도이며 외부 호출은 아니다.
- Python AST/공백/개행 및 `git diff --check` 통과. 전체 서비스/frontend 검사는 이번 미실행.

최초 tests-v1은 **17 / skip 0 / 실패 0 / 오류 1**이었다. macOS의 `/tmp`가
`/private/tmp`의 symlink여서 테스트용 경로가 엄격한 symlink 거부 조건에 걸렸다.
fixture 경로만 `resolve()`로 정규화했고, 실제 복구 도구의 symlink 차단은 완화하지 않았다.
tests-v1 실패 로그를 보존했다. 일부 sandbox Python/Xcode 명령의 임시/캐시 경로 및
filesystem event stream 경고도 작업 로그에 기록했다.

동결 후 발견: 새 서비스 결함 없음. 임시 사본 소실은 실행 재현성의 운영 위험이며,
모델 성능 저하나 과거 평가 결과 손실로 확인된 것은 아니다.

전체 명령·산출물 SHA는 [작업 로그](progress-log-20260901.md)의
“2026-09-09 실측 준비 — 임시 사본 복구와 미승인 실행 명세” 절에 있다.
