# 보안 비교 파일럿 — 코드·정상 입력 준비 체크포인트

2026-09-08. 상태: **NORMAL_PREPARED / ATTACK_DESIGN_NEEDS_DECISION**.
전체 실행계획 확정이나 성능 재측정 완료를 뜻하지 않는다. 이번 승인은 준비 작업이며,
서버 시작·교체, 외부 API 호출, Git 변경 작업은 모두 0회다. 실제 holdout은 읽지 않았다.

`plan-eng-review`의 아키텍처 검토에서 공격 입력 전달 방식에 대한 사용자 선택을
요청했다. 응답 전에는 해당 방식을 확정하거나 후속 검토를 완료 처리하지 않는다.
그와 독립적인 기존 합의 범위의 코드 사본·공통 설정·정상 파일럿 입력을 준비하고 검증했다.

## 1. 세 조건의 실행 코드 사본

원래 dirty 작업 폴더를 실행본으로 쓰지 않는다. 보존 클론의 로컬 Git 객체에서
Python 소스와 필요한 설정 2개·requirements만 allowlist로 읽어 새 디렉터리에 만들었다.
`.env`, 실제 holdout, 검토 패킷, 과거 답변은 코드 사본에 복사하지 않았다.

공통 루트: `/private/tmp/pnu-security-pilot-20260908.fa9QFA/`.

| 조건 / 하위 디렉터리 | 기준 | 보안 수집 계약 | 후보 포트 |
|---|---|---|---:|
| `c1-pre-security` | `5f8230329196a6f007c78098642d0e658720fe30` | absent | 18931 |
| `c1-sec-merged` | `c3e581bc708f2811ce73dec4a278657a72f7fff1` | enforce | 18932 |
| `c1-sec-fixed` | 같은 보안 커밋 + 승인된 수정 4개 | enforce | 18933 |

후보 포트는 예약하거나 서버로 열지 않았다. 고정한 snapshot SHA는
`파일 상대 경로 → SHA-256` 사전을 정렬한 compact JSON의 SHA-256이다.
이는 `/health`의 search_api.py 단일 파일 SHA와 다른 식별자다.

| 조건 | snapshot SHA-256 |
|---|---|
| pre | `620e681bae37ff221e2f0ebdd36d1700d1b503310ed68b07ea7ab7953d2f2426` |
| merged | `ce761bc0b4a0a48b421b0a8e126425027dd776953d35eff8ec26fbce122402f6` |
| fixed | `d3f5c889c6b83970a252239bcbfa87c1734033e962da09f758c47568707e5314` |

모든 파일별 해시는 `preparation.json → code_conditions`에 있다. 세 조건에 같은
별도 `evaluate_security_service_answers.py`를 넣었다. 원래 수집기·BM25·Judge·답변
artifact 유틸리티의 SHA는 세 조건 모두 같다. merged와 fixed의 차이는 정확히 다음
4개이며, 이전 승인·오프라인 검증된 manifest와 SHA를 대조해 복사했다.

- `scripts/rag/context_fields.py`
- `scripts/rag/generators.py`
- `scripts/rag/security/context_gate.py`
- `scripts/rag/security/output_gate.py`

원본 서비스에 새 수정은 없다. `RAG_CONTEXT_SECURITY_MODE=off`로 pre를 흉내 내지
않는다. 보안 렌더링·입력 게이트·출력 게이트를 포함한 **패키지 비교**다.
임시 디렉터리 삭제·재부팅 등에 대비한 경로 존재 및 SHA 재검증은 실제 실행 전 필수다.
없어졌다면 새 경로에 재구성하고 동일 파일별 SHA인지 확인하며 기존 산출물은 덮어쓰지 않는다.

## 2. 정상 파일럿 입력과 공통 설정

입력은 이미 공개된 `config/pnu-service-shadow60-v1.jsonl`의 core 중
7개 category × simple/multi 2개 bucket에서 1개씩, 총 **14문항**이다.
seed `security-normal-pilot-v1:20260908`와 ID의 SHA 순위로 골랐다.
질문 문장·기존 Judge 점수·실패 여부는 선정에 사용하지 않았고, 선정된 원래 case 객체는
변경하지 않았다. 이전 실패 중심 사람검수용 Shadow14와는 별도 목록이다.

산출물: `processed/eval/preflight-20260908/security-pilot-preparation-v1/normal14.cases.jsonl`.
선정 ID·family·category·bucket·문항별 SHA는 같은 경로 `preparation.json`에 기록했다.
Shadow는 개발 진단 자료이며 미공개 holdout이나 최종 일반화 검증이 아니다.

| 항목 | 공통 설정 |
|---|---|
| public provider / 생성 모델 | frontier / `gemini-3.5-flash-lite` |
| parser / 검색 | cascade / BM25, tuning=true |
| context | top_k=8, 문서당 2개, 24,000자 |
| 생성 출력 | 최대 900 tokens, sampling 파라미터 미전송 |
| 모델 fallback | 다른 모델로 전환하지 않음 |
| 전송 제어 | 생성 deadline 150초, provider timeout 120초, 동시 생성 1 |
| 수집기 | timeout 180초, sleep 3초, 최대 시도 3, retry_backoff 3초 |
| Judge | 기존 v11, `gemini-3.1-flash-lite`, 최대 1,600 tokens |
| Judge 재시도 | timeout 180초, sleep 3초, 최대 시도 6 |

모델명은 저장된 실행 설정을 재사용했으며 현재 가용성을 API로 확인하지 않았다.
가용성 실패 시 임의 모델 대체 없이 멈춘다. 정상 pilot의 최대 시도 3은 과거 수집기
설정 7과 다르며 비용 제한을 위한 공통 제안이다. 150/120초 역시 이번 공통 제어값으로,
과거 전송 설정과 모두 동일하다는 주장은 하지 않는다.

`preparation.json → nonsecret_environment`를 기준으로 깨끗한 자식 환경을 구성하고,
기존 RAG_/GEMINI_/PYTHONPATH 제어값은 상속하지 않는다. `.env`는 읽지 않았고
키 주입은 API 실행 승인 뒤의 별도 단계다. fallback 환경값은 동일 모델명 하나로
명시한다. 빈 값은 서버 측 기본 fallback을 되살리므로 사용하지 않는다.
격리 import 검사에서 서버·생성기 후보가 모두 해당 모델 하나임을 확인했다.

고정 입력:

| 파일 | SHA-256 |
|---|---|
| `processed/index/pnu-20260725-curated-cascade-v5-allow-suspect.sqlite` | `a4c1ca6318cb14721866a6512f3dfeacdca58c62ecff98ea7b81df2e75c74c31` |
| `processed/curation/20260725-pnu-curated-v5/curated-manifest.jsonl` | `1fa7e0f2f5d03a2a1249584d91f7232c9b2cb4642b39115bf3c9e6a871845a2f` |
| `config/pnu-service-shadow60-v1.jsonl` | `0906e6c8de40040915e668e6cf61639306657e6f94f41e5bcdf62197a6d90754` |

corpus revision은 `20260725-pnu-curated-cascade-v5:cascade:5d1b5fee3d2eafa1a7c77d33`.
Judge config SHA는 `c165059daa5903b74d9b7fe260856419b697721ecc1e869a701aa0c85dc18fce`.
해시와 오프라인 함수 설정은 확인했지만 실제 serving process의 index·config 적용 확인은
아직 하지 않았다.

### 순서와 산술적 호출 예산 — 승인·집행 기능 아님

독립 생성 n=1. seed 기반 조건 순서 **fixed → merged → pre**이며, 각 조건에서
동일한 category/bucket 순서의 14문항을 실행하는 42개 slot 계획을 저장했다.
조건 블록 실행은 시간·서비스 부하 효과를 분리하지 못하므로 기술적 pilot에만 사용한다.
결과에 따라 유리한 순서·답변·재시도만 고르거나 통계적 우위·지연 우위를 주장하지 않는다.

| 정상 파일럿 범위 | 논리 slot | slot당 최대 provider 시도 | 산술 상한 |
|---|---:|---:|---:|
| 생성 | 42 | 3 | 126 |
| Judge | 42 | 6 | 252 |
| 합계 | **84** | — | **378** |

전부 차단되어 생성하지 않은 경우나 Judge 부적격 입력은 실제 호출이 줄 수 있다.
표는 단일 모델 후보 및 고정 시도 수 전제의 최대치이며, 공격 평가와 본실험
core42 × 생성3 × 조건3은 포함하지 않는다. 호출 횟수는 무료 사용량 보장이 아니다.
새 프로세스·새 run ID로 재실행하는 경우까지 포함한 전역 예산 장부/중단 장치는
아직 없다. 이를 준비하기 전에는 이 표만 믿고 실행하지 않는다.
resume 시 collector config SHA가 같아야 하므로 sleep·시도 수·기타 인자를 바꾸지
않고, 서버 재시작 식별 변경도 확인해야 한다.

## 3. 오프라인 검증 결과

`evidence/security-pilot-preparation-20260908-v1/verify.py`:

- 합성 준비 도구 unittest **9 / skip 0 / 실패 0 / 오류 0**.
- 실제 산출물 검사 6종 통과: 점수 비참조 선정·원문 객체 보존, 입력 3종 SHA,
  42개 schedule, 세 사본 SHA 및 유효 설정, 공통 모듈 동일성, 수정 파일 정확히 4개.
- 각 사본의 순수 설정 함수를 깨끗한 별도 Python 프로세스에서 import했다.
  실제 로드 모듈 경로도 `verification.json`에 저장했다. 서버를 띄운 검증은 아니다.
- Python audit hook으로 socket/DNS/urllib와 `.env`·holdout 데이터 읽기를 막았다.
  네트워크 시도·외부 LLM 호출은 없었다.

이번에 전체 서비스 테스트를 다시 실행한 것은 아니다. 별도 수집기 전체 검증의 정본은
이전 `security-collector-v1/full-final.json`의 **857 / skip 6 / 실패 0 / 오류 0**이며,
이번 9개를 그 수치에 합산해 전체 재실행 결과처럼 표기하지 않는다.

## 4. 아키텍처 선택 대기와 실행 전 남은 조건

**A1 — P1 (confidence 10/10): 구성요소 검사만으로 전체 서비스 공격 성공률을 알 수 없음.**
보존 보안 클론 `scripts/evaluate_security_layers.py:75–78`의 공격 검사 구문:

```python
for index, case in enumerate(attacks):
    result = evaluate_contexts(
        [_context(f"attack-{index}", str(case["text"]))], mode="enforce"
    )
```

이는 게이트 직접 호출이며 검색→생성→출력까지 `/chat`을 거치는 평가가 아니다.
공격을 사용자 질문에만 넣으면 검색 문서의 간접 prompt injection과도 다른 실험이다.
선택 요청: 원본과 분리된 공격 문서 테스트 인덱스로 `/chat` 전체 경로를 평가할지,
기존 구성요소 직접 호출에 한정할지. **미응답 상태이며 어느 쪽도 확정하지 않았다.**
공격 질문·인덱스는 만들지 않았다. 공격 수·유형·순서·예산은 선택 후 별도로 고정한다.

다음 미완료 항목:

1. 공격 방식 사용자 선택 → 해당 아키텍처 검토 종료 → 코드 품질·테스트·성능 검토.
2. 실행 전 전체 코드 SHA와 owned PID/포트/실제 모듈 경로/인덱스 연결 확인 도구.
   `/health`의 단일 search_api SHA나 non-Git 사본의 clean 표시는 대체 증거가 아니다.
3. 전체 호출 예산 장부, 중단·resume 계약, 모델 불일치·trace 불량 시 fail-closed 점검.
4. 정상 질문의 주지표 GFC, 부적절 회피, 입력/출력 차단, 유효 판정 분모와 지연을
   구분해 보고. 공격 ASR 등은 선택한 공격 설계에 맞게 별도 분모로 정의해야 한다.
5. 검증된 보안 전부차단 기록만 기존 Judge `--allow-missing-trace` 예외 대상으로
   삼는 사전검사. record SHA와 새 보안 계약 검사 없이 일반 trace 누락을 허용하지 않는다.
6. 최종 작은 실험의 문항/호출 상한을 제시해 **서버 실행·외부 API의 별도 승인**을 받기.

범위 밖: 검색·prompt/DEV 규칙 튜닝, 새 보안 규칙, Judge rubric 수정, 원본 서비스 설치,
커밋·태그·push, 실제 holdout 또는 사람 검수 대신하기, 본실험 실행.
기존 동결 후 발견 목록은 유지한다. 이번 준비로 해결됐다고 간주하지 않는다.

## 5. 재현 명령과 산출물

실제 수행:

```sh
python3 -B evidence/security-pilot-preparation-20260908-v1/prepare.py
python3 -B evidence/security-pilot-preparation-20260908-v1/verify.py
git diff --check
git -C /private/tmp/pnu-security-review-20260908.HzXJKS/repo status --short
```

두 도구는 기존 출력이 있으면 덮어쓰지 않고 종료한다. 그대로 재실행하는 실행용 런북이
아니다. 파일별 `shasum -a 256` 및 AST/공백 검사도 수행했다. 보존 클론은 clean이고
원본 동결 서비스 6파일 해시는 이전과 동일하다. frontend lint/build는 변경 범위 밖이라
미실행. 준비 도구·입력·manifest·검증 결과 SHA는 progress-log에 기록한다.
