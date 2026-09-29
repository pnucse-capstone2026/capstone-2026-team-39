# 보안 재평가 수집·봉인 연결 — 2026-09-08

**오프라인 연결 검증 완료. 실제 서버 소켓 연결은 승인 대기.** 외부 LLM 0회,
HTTP 서버 시작 0회, 실제 holdout·비밀 파일 접근 없음. 원본 서비스·동결 사본·기존
runtime guard v1·이전 산출물은 유지했다. Git 변경 작업도 하지 않았다.

## 이번에 연결한 부분

소스: `evidence/security-execution-bridge-20260908-v1/`.

- `execution_bridge.py`: 고정 디렉터리의 실행 기록·호출 장부·결과 파일을 연결한다.
  실행 잠금 없이 시작/봉인할 수 없고, 다른 실행이 미완료이면 다음 요청도 시작하지 않는다.
  collector의 설정을 `/chat` 전에 저장하고, 저장된 답변을 검증한 뒤 완료로 봉인한다.
  저장 직후 중단된 경우에도 파일을 먼저 다시 검증하며, 답변을 새로 생성하지 않는다.
- 기존 수집기의 `main`, 기존 Judge의 입력·prompt·호출·guard·기록 생성 함수를 그대로
  사용한다. 생성 prompt/Judge rubric/방어 규칙을 수정한 것이 아니다. 같은 검증 코드
  3파일의 SHA는 pre/merged/fixed 사본에서 동일하다.
- 답변 봉인에는 case·요청·설정·보안 계약·trace·실제 전송 본문의 SHA 일치가 필요하다.
  Judge 봉인은 봉인된 answer 파일 SHA, answer record SHA, Judge 입력·prompt·config,
  raw 응답을 재해석한 판정/guard 및 장부의 실제 전송 기록까지 대조한다.
- 보안상 모든 근거를 제외한 응답은 **생성 전송 0회**일 때만 그 경로로 인정한다.
  검증 후 Judge에는 전달할 수 있다. 모든 빈 trace를 허용하는 일반 우회 옵션은 추가하지 않았다.
- `OwnedChild`는 직접 시작한 자식의 private pipe, PID, 일회용 nonce를 확인한다.
  오류·시간 초과 때도 그 자식만 정리한다. 임의 PID 검색/기존 서버 종료는 하지 않는다.
- 실제 health의 freeze는 최상위가 아닌 `service_config.freeze`에 있다. 연결부에서 이
  구조를 정확히 투영하고 추가 모델 fallback, 인덱스 및 source manifest 불일치를 거부한다.
  source manifest 핀이 누락돼도 중단한다. 동결 서비스 수정은 아니다.

## 검증 결과와 해석 범위

정본: `processed/eval/preflight-20260908/security-execution-bridge-v1/verification-v3/verification.json`.

| 검증 | 결과 |
|---|---|
| 새 연결 도구 unittest | **27 / skip 0 / 실패 0 / 오류 0**, 0.806초 |
| 기존 runtime guard 회귀 테스트 | **30 / skip 0 / 실패 0 / 오류 0**, 0.314초 |
| 정상 생성 계약 | pre/merged/fixed 3건, 답변 저장 → Judge → 봉인 통과 |
| 생성 생략 보안 계약 | merged/fixed 2건, 생성0회 → Judge → 봉인 통과 |
| 위 별도 합성 시나리오 | 답변5 + 판정5파일, 가짜 provider 전송 **8회** |
| 보존 확인 | 코드 사본3, 원본 서비스6파일, 기존 가드3파일, 입력67파일 SHA 일치 |

세 조건 비교는 **계약별로 만든 합성 응답**을 같은 동결 수집기에 전달한 것이다.
실제 각 서비스 handler, 검색, 방어 gate를 다시 실행한 비교가 아니다. Judge도 실제
판정 코드 경로에 가짜 모델 응답을 연결했으며, 산출물의 score/GFC는 성능 수치가 아니다.
정상14 또는 공격 v2 실험의 실행 횟수로 계산하지 않는다. 이전 정책의 생성102·Judge102,
총204 요청 및 최대918 provider 시도는 그대로이며 이번에 집행하지 않았다.

재개·변조 테스트는 저장 후 봉인 전 중단, Judge 저장 후 중단, 미완료 provider 예약,
재해시한 질문·설정·prompt 변조, Judge의 답변 연결/판정 변조, 숨겨진 생성 전송,
동시 실행 잠금, 실제 자식 PID·nonce·시간 초과 정리를 포함한다.

네트워크는 테스트 프로세스에서 차단했다. 자식 프로세스/pipe는 실제 OS 기능으로
검증했지만 **HTTP listener/인증은 아직 검증하지 않았다**. 서비스 전체 테스트와
frontend lint/build도 이번 미실행이다. 새 Python 3파일 AST와 `git diff --check`는 통과했다.

초기 테스트 import는 macOS의 전역 Python 캐시 경로가 `__pycache__` 밖으로 바뀌어
오프라인 읽기 차단기에 걸렸다. 캐시를 읽도록 허용하지 않고, 보호 이름의 `.pyc`는
경로와 무관하게 없는 파일로 처리한 뒤 핀으로 확인한 `.py`만 읽게 했다. 회귀 테스트 포함.
실제 holdout 데이터는 읽지 않았다. 서비스 결함이 아닌 새 테스트 환경 처리 문제다.

## 로컬 테스트 서버 시작 스크립트와 남은 조건

`local_mock_bootstrap.py`를 준비했다. 기본 실행은 입력 파일을 읽기 전 중단하며,
`--start-local-mock-server`가 있어야 진행한다. synthetic 실행 기록과 공격 fixture만
허용하고, 깨끗한 환경·고정 코드/인덱스·미시작 slot nonce를 확인한다.
실제 서비스의 서버 생성 시점에서 bind 후 attestation을 private pipe로 보내도록
연결했다. `127.0.0.1`의 임시 포트만 쓰며 생성은 고정 가짜 응답이다.
**이 시작 경로는 아직 실행하지 않았다.** 외부 LLM backend는 구현하지 않았다.

다음은 별도 승인 후 진행한다.

1. 기존 공격 v2 입력에서 고정 소규모 사례를 사용하는 **새 synthetic 실행 정책**을 만들고,
   원본 정책의 환경·조건·인덱스·source manifest 핀을 연결한다. 이전 정책을 수정하거나
   기존 합성 장부를 실측 장부로 재사용하지 않는다.
2. 일회용 로컬 테스트 서버를 직접 시작하여 실제 `/health` → `/chat` → collector/Judge
   → 봉인 → 종료까지 확인한다. provider는 가짜 응답으로 고정하고 외부 LLM 호출은 0회다.
   실제 bind/health와 private pipe의 PID/nonce, 요청 전 코드 신원 재확인을 검사한다.
3. 이 검증 후에야 실측용 고정 장부와 승인된 API backend를 별도로 연결한다.

현재 `Run.create_offline`은 `SYNTHETIC-` 실험만 허용한다. 이것은 안전한 테스트 기본값이지
사용자의 승인 기록이나 악의적인 같은 프로세스/파일 변조를 막는 보안 경계가 아니다.
실측 실행기와 API 승인은 아직 없으며, 성능 재측정 완료로 표시하지 않는다.

## 재현과 추적

```sh
python3 -B evidence/security-execution-bridge-20260908-v1/test_execution_bridge.py
python3 -B evidence/security-execution-bridge-20260908-v1/test_execution_bridge.py --output processed/eval/preflight-20260908/security-execution-bridge-v1/verification-v4
git diff --check
```

재현 명령은 새 경로 v4를 사용한다. 이번 정본은 v3, 이전 v1(19개)·v2(25개) 검증
산출물은 보존한다. 전체 43개 보조 산출물 SHA는 정본 `artifact_sha256`에 있으며,
도구/정본/로그 SHA는 progress-log의 같은 날짜 절에도 기록했다.
