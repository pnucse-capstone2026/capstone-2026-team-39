# 보안 재평가 실행 가드 준비와 중단 복구 — 2026-09-08

상태: **DONE_WITH_CONCERNS / 오프라인 구성요소 검증 완료, 실측 실행 연결 미완료**.
서비스·동결 사본은 변경하지 않았다. 외부 LLM 0회, 실제 HTTP 서버 시작 0회,
Git 변경 작업 0회, 실제 holdout 및 비밀 파일 접근 없음.

## 중단 원인과 별도 호환성 결함

앞선 도구 결과는 `aborted by user`이며, 이어진 실행 환경 기록도 turn interruption을
명시한다. 디스크에는 `runtime_guard.py` 하나만 있었고, 다음 패치의 준비 스크립트와
테스트 파일은 없었다. **확인된 것은 파일 추가 도구의 중단과 미완성 상태**다.
서비스 예외나 LLM API 실패 기록은 아니다. 사용자가 본 별도 앱 오류의 원인까지
이 기록만으로 확정하거나 앱 오류를 수정했다고 주장하지 않는다.

복구 과정에서 새 초안의 `hashlib.file_digest` 호출이 로컬 Python **3.9.6**에서
`AttributeError`를 내는 것을 직접 재현했다. 이 오류는 앞선 화면 오류와 구분한다.
`investigate` 절차에 따라 재현 → 실패하는 회귀 테스트 → 최소 수정 → 전체 새 도구
테스트 순서로 처리했다. 최신 Python 전용 함수를 1 MiB 단위 스트리밍 SHA-256으로
교체했다. 테스트는 해당 함수가 없는 조건에서도 동일한 SHA를 요구한다.

수정 범위는 새 평가 도구 3파일과 이 문서·progress-log다. 스킬의 전역 설정,
텔레메트리, 동기화, 자동 커밋은 이번 승인 범위에 없어 실행하지 않았다.

## 준비한 구성요소

소스: `evidence/security-runtime-guard-20260908-v1/`.

- `prepare_guard.py`: 이미 고정한 정상14와 공격 v2 명세의 SHA를 확인하고
  생성102·Judge102의 고유 요청 ID, 모델, 인덱스, 설정 및 최대 시도 수를 새 정책에 기록.
  질문 텍스트로 새 문항을 고르거나 점수·실패를 보고 순서를 바꾸지 않는다.
- `runtime_guard.py`: 전체 파일 목록·SHA, 실제 import 경로, handler의 인덱스,
  자식 프로세스 PID·일회용 식별값·리스닝 주소·health 시작 정보 대조 함수.
  `/health`의 `startup_code_sha256`는 API 파일 하나의 SHA이므로 전체 사본 SHA와 구분한다.
- 같은 모듈의 SQLite 장부: 전송 **전에** 트랜잭션을 확정하고 시도를 차감한다.
  같은 장부·정책으로 재개하며 설정 변경, 완료 요청 재호출, 요청 본문 변경을 거부한다.
  미완료 예약이 있으면 다른 요청도 막는다. 강제 종료 뒤에는 명시적 확인이 필요하며
  해당 시도는 돌려주지 않는다. 응답 수신 성공과 평가 결과의 검증 완료는 별개다.
- `GuardedTransport`: collector 바깥 반복 횟수가 아니라 실제 `urlopen` 전송을 감싼다.
  고정 Gemini 모델·HTTPS 주소·POST만 허용하고, redirect와 환경변수 proxy를 사용하는
  우회 전송을 피하도록 별도 opener를 제공한다. 키·원문·응답은 장부에 저장하지 않는다.

정책의 `api_execution_authorized=false`, `live_runner_ready=false`를 유지했다.
이는 실측 승인 파일이 아니다. 현재 공개 함수들은 통합용 구성요소이며, 새 DB를
임의로 만들거나 wrapper 밖에서 호출하는 프로그램까지 통제하는 보안 경계가 아니다.

## 검증 결과

정본: `processed/eval/preflight-20260908/security-runtime-guard-v1/verification-v1/verification.json`.

| 검사 | 결과 |
|---|---|
| 새 도구 전체 unittest | **30 / skip 0 / 실패 0 / 오류 0**, 0.336초 |
| 전체 정책의 모의 전송 | **204 요청 ID / 최대 918회**, 919번째 전송 차단 |
| 실제 동결 생성·Judge 함수, 가짜 HTTP 응답 | 세 조건 각각 생성3 + Judge6 = **27회**, 추가6회 차단 |
| 코드 사본 | pre 97 / merged 102 / fixed 103파일의 목록·SHA 일치 |
| 입력 보존 | 고정 입력 67파일 및 원본 서비스 6파일 SHA 일치 |
| 새 Python AST·공백·개행 / `git diff --check` | 통과 |

30개 테스트에는 별도 자식 프로세스가 장부 기록 직후 강제 종료하는 재현,
8개 동시 예약 중 1개만 통과, 전체/요청별 상한, 설정 drift, 완료/미완료 재개,
429·timeout·응답 읽기 실패, 코드·인덱스·PID·주소 불일치 거부가 포함된다.
918회와 27회는 unittest 수에 합산하지 않는 별도 **모의 전송 수**다.
세 조건의 Judge는 가짜 429에 대해 동결 코드의 실제 내부 재시도 6회를 수행했다.

검증 프로세스는 socket/urllib 접근과 `.env`·실제 holdout 데이터 읽기를 차단한다.
실제 생성·Judge 코드를 import했지만 입력·키·HTTP 응답은 합성이다. 실제 서버 소켓과
PID 연결 절차는 모형으로 검사했다. 모델 품질·GFC·ASR·응답 지연의 실측 결과가 아니다.
서비스 전체 857개 회귀 테스트와 frontend lint/build는 이번 미실행이다.

## 다음 연결 단계 / 실측 전 필수

1. 평가 전용 실행기가 직접 소유한 자식 프로세스의 private pipe에서 신원 확인 정보를
   받고, 실제 bind된 서버 및 해당 health 응답에 연결할 것. 현재 모의 socket 검사는
   이 실환경 확인을 대체하지 않는다. 기존 서버를 추정해서 재사용하지 않는다.
2. 생성과 Judge 양쪽에 wrapper를 설치하고, 모든 요청을 같은 **고정 경로의 장부**에
   연결할 것. 깨끗한 환경·모델·sampling·수집기 설정도 검증해야 한다. 별도 HTTP 라이브러리나
   wrapper를 설치하지 않은 실행은 이 도구의 예산 보장 범위 밖이다.
3. 엄격한 answer/Judge artifact 검증을 통과한 뒤에만 `seal_slot`을 호출할 것.
   현재 이 함수 자체는 artifact를 검증하지 않는다. 저장 후 봉인 전 중단 및 미완료 예약은
   자동 재호출하지 말고 기존 결과·장부를 먼저 대조할 것. 정상 보안 회피는 검증 후 0회로 봉인 가능.
4. 실제 서버 시작·API 실행은 연결 검증 후 별도 승인. 이번에는 실행하지 않았다.

새 서비스 결함 수정이나 검색·생성·방어 규칙 튜닝은 없다. 이번에 고친 호환성 결함은
동결 서비스가 아닌 **새 평가 도구 초안**의 결함이다.

## 재현

```sh
python3 -B evidence/security-runtime-guard-20260908-v1/test_runtime_guard.py
python3 -B evidence/security-runtime-guard-20260908-v1/test_runtime_guard.py --verify-output processed/eval/preflight-20260908/security-runtime-guard-v1/verification-v2
git diff --check
```

위 두 번째 명령은 재현용 **새 경로**다. 이번 정본은 `verification-v1`이고, 기존 디렉터리가
있으면 도구는 덮어쓰지 않고 실패한다. 합성 장부를 실제 실험의 사용 이력으로 재사용하지 않는다.
각 파일 SHA는 정본의 `artifact_sha256`·`tool_sha256`과 progress-log의 같은 날짜 절을 참조한다.
