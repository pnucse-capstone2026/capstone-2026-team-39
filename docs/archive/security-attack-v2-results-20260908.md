# 공격 파일럿 v2 준비 결과 — 2026-09-08

**승인된 a09 교체와 오프라인 검증 완료.** 이전 T1(생성 입력에 전달되지 않는 제목 공격)
문제는 테스트 데이터를 바꿔 해결했다. 서비스 방어 코드를 새로 수정한 것이 아니다.
v1 명세·인덱스·결과·문서는 보존했다. 전체 평가 실행계획의 승인이나 실제 LLM 재측정
완료를 뜻하지 않는다. 외부 LLM 호출 0회, HTTP 서버 시작 0회, Git 변경 작업 0회.

## 변경 범위

- a09의 공격 위치를 `source_title`에서 실제 렌더링되는 `file_name`으로 교체.
- 공격 문구·사용자 질문·정답 사실·문서 수·시나리오 순서·조건 순서·예산 유지.
- filename은 DB 메타데이터 문자열이다. 실제 파일명/경로로 사용하거나 실행하지 않는다.
- v2는 별도 경로와 revision/run ID를 사용한다. 이전 code snapshot 3종과 생성/Judge 설정은 그대로다.

명세·도구: `evidence/security-attack-preparation-20260908-v2/`.
새 입력·검증: `processed/eval/preflight-20260908/security-attack-preparation-v2/`.
현재 후속 실험에서 사용할 공격 입력은 이 v2의 `manifest.json`이다.
이전 정상14 입력과 공통 설정은 `security-pilot-preparation-v1/preparation.json`을 계속 참조한다.

전체 문서 묶음 20개 중 **a09-attack 1개만 내용 변경**, 나머지 19개 문서 JSONL은
v1과 SHA가 같다. a09 clean/attack case 객체 2개는 위치·분류 메타데이터가 달라졌고,
나머지 18개 case 객체는 동일하다. 20개 DB는 revision/run 메타데이터가 v2이므로 SHA가
바뀌며, 문서 내용 변경과 구분한다. 질문 문장은 모두 동일하다.

## 확인한 결과

아래는 **가짜 생성 함수를 사용한 처리 경로 검증**이다. 실제 모델이 공격을 따르는지,
GFC 또는 의미상 공격 성공률을 측정한 표가 아니다.

| 조건 | v1 제목 표식의 생성 입력 전달 | v2 파일명 표식의 생성 입력 전달 | v2 입력 게이트 |
|---|---|---|---|
| 보안 적용 전 | 없음 | 있음 | 게이트 없음 |
| 현재 머지된 보안 코드 | 없음 | 있음 | 공격 문서 제외 안 됨 |
| 기존 별도 결함 수정본 | 없음 | 없음 | 공격 문서 1개 제외 |

수정본에서도 동반 정상 근거는 남아 모의 생성 함수가 호출됐다. 전체 근거가 오염된
a10에서는 두 보안 조건 모두 생성 생략·보안 회피 계약을 통과했다.
이 실험은 기존 파일명 검사 결함 수정의 회귀 확인이며 새로운 일반화 증거가 아니다.

검증 정본: `verification-v1/verification.json`.

- 단위 테스트 **17개 / skip 0 / 실패 0 / 오류 0**.
- 모의 생성 `/chat` handler 처리 경로 **20 × 3 = 60건 통과**.
- 보안 적용 전에는 공격 10개 모두 생성 입력에 표식이 전달됨을 확인.
- clean 10개는 세 조건 모두 생성 입력에 공격 표식 없음.
- a09 외 **54개 시나리오×조건 조합**의 보안 계약·표식 관측·모의 생성 호출 여부는 v1과 같음.
- v1 fixture·소스·보고서 및 원본 서비스 6파일, 코드 사본 3종, 원본 인덱스·manifest·Shadow60 SHA 보존 확인.
- 새 Python 4파일 AST/공백 검사와 `git diff --check` 통과. 전체 서비스 unittest/lint/build는 이번 미실행.

17개는 기존 준비 도구 테스트 14개 + v2 변경 범위·filename 전용 변경·허용 필드
검증 3개다. 60건은 별도 통합 시나리오 수이며 unittest 수에 합산하지 않는다.
HTTP transport/인증은 테스트에서 우회했고, 실제 서버·provider는 실행하지 않았다.

## 유지된 범위와 남은 작업

공격10 + 대응 정상10은 **공통 가상 질문 1개의 합성 시나리오 20개**다.
실제 정상14와 분모를 합쳐 서비스 성능을 주장하지 않는다. parser도 실행하지 않았다.
기존 a06의 sanitize 후 잔여 표식 관측은 그대로 남고 보안 정책·코드는 바꾸지 않았다.

| 계획 예산 | 생성 | Judge | 논리 요청 | 최대 provider 시도 |
|---|---:|---:|---:|---:|
| 정상14 × 세 조건 | 42 | 42 | 84 | 378 |
| 합성20 × 세 조건 | 60 | 60 | 120 | 540 |
| 합계 | **102** | **102** | **204** | **918** |

생성 slot 최대3·Judge 최대6시도의 산술 상한이며 아직 승인·집행한 예산이 아니다.
다음은 실제 실행 프로세스/전체 코드/인덱스 연결 확인과 재시작까지 포함하는 호출 예산·
resume 제어를 준비하는 단계다. 그것을 검증하기 전에는 API 실측을 시작하지 않는다.
그 뒤 해당 범위 서버/API 실행 승인을 별도로 받는다. 실제 holdout·검색 튜닝·서비스
설치·Git 작업은 이번 범위 밖이다. 새 동결 서비스 결함은 발견하지 않았다.

## 재현·추적

```sh
python3 -B evidence/security-attack-preparation-20260908-v2/prepare_attack.py
python3 -B evidence/security-attack-preparation-20260908-v2/verify_attack.py --output processed/eval/preflight-20260908/security-attack-preparation-v2/verification-v1
python3 -B evidence/security-attack-preparation-20260908-v2/compare_revisions.py
git diff --check
```

도구는 기존 출력이 있으면 덮어쓰지 않고 중단한다. 준비 도구는 감사 가능한 v1 사본에서
승인된 변경만 반영해 v2 경로에 보관했다. 비교 정본은 `revision-comparison.json`이며
T1 해소, 변경된 문서·case 목록, a09 비교, 보존된 원본 SHA를 기록한다.
산출물별 SHA-256은 progress-log의 같은 날짜 v2 절에 기록했다.
