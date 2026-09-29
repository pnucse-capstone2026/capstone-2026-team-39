# 보안 포함 재평가 — 설계 사전 점검 2026-09-08

상태: **NEEDS_DECISION — 아키텍처 점검에서 수집 계약의 공백 발견. 실행계획 미확정.**
이번 승인 범위는 계획·오프라인 사전 점검이다. 외부 API 호출 0회, 서비스·보안·Judge
코드 변경 0개, Git 변경 작업 0회. 실제 holdout 자료는 읽지 않았다.
`plan-eng-review`에 따라 중요한 설계 변경은 사용자 확인 전에 확정하지 않는다.
다른 모델 호출, 글로벌 설정·리뷰 로그 변경, 자동 커밋은 승인 범위 밖이라 수행하지 않았다.

## 1. 이미 합의한 비교 범위

| 조건 명칭 | 코드 기준 | 목적 |
|---|---|---|
| C1-pre-security | `5f8230329196a6f007c78098642d0e658720fe30` 기반 | 보안 적용 전 서비스 |
| C1-sec-merged | `c3e581bc708f2811ce73dec4a278657a72f7fff1` 보존 클론 | 현재 머지된 보안 패키지 |
| C1-sec-fixed | 위 보안 커밋 + 별도 수정 패치 manifest | 결함 수정 보안 패키지 |

C0/C1의 기존 검색 조건 명칭을 보안 on/off 명칭으로 재사용하지 않는다.
`RAG_CONTEXT_SECURITY_MODE=off`는 출력 게이트·보안 렌더링까지 끄지 않으므로
보안 적용 전 조건의 대체물이 아니다. 원래 dirty 작업 폴더는 실제 수집 전에
명시적 파일 allowlist와 해시로 고정해야 하며, HEAD만으로 실행본을 확정하지 않는다.

소규모 실제 생성 진단 후 Shadow core42 × 독립 생성 3회를 비교하는 방향이다.
같은 생성 모델·요청 설정·인덱스·Judge v11을 사용한다. GFC가 주지표이며,
부적절 회피·보안 성공/실패·응답 지연을 함께 본다. 세 패키지 비교이며
개별 보안 구성요소 하나의 인과 효과라고 주장하지 않는다.

Shadow는 이미 열어 본 개발 진단셋이다. 최종 holdout·사람 calibration을 대체하지 않는다.
기존 저장 답변 재검사는 새 생성 결과로 세지 않는다. 이번 점검에서 실제 질문 본문이나
저장 답변을 새로 입력할 필요는 없었으며, 아래 동작 재현에는 합성 자료만 사용했다.

### 정상 core 비교의 산술적 예산 — 실행 승인 아님

| 범위 | 생성 slot | Judge slot | 논리 slot 합계 |
|---|---:|---:|---:|
| 조건당 42 × 3 | 126 | 126 | 252 |
| 세 조건 | 378 | 378 | **756** |

생성 slot 최대 3시도·Judge slot 최대 6시도를 선택하면 산술 상한은
`378 × 3 + 378 × 6 = 3,402`시도다. 이는 모든 slot을 실행·판정하는 경우의
상한 계산이지 승인된 예산이나 실제 사용량이 아니다. 소규모 진단·공격 평가 예산은
포함하지 않았고, 사례 수·실행 순서·반환 모델 버전 핀은 아직 미확정이다.
기존 900 출력 token·sampling 미지정 조건을 임의로 바꾸지 않는다.

## 2. 재사용할 기존 경로

```text
버전별 /chat
  → evaluate_service_answers.py (재시도·조건 검사·답변 해시)
  → *.answers.jsonl / 오류 기록
  → 동결 judge_service_answers.py v11
  → 반복 생성 GFC 집계
```

기존 수집기는 검색·생성 trace, 초안, 지연, 답변·인용, 입력 게이트 요약을 보존한다.
기존 Judge와 답변 ID/record SHA 검증을 재사용할 수 있으므로 새 Judge나 검색 경로를
만들 이유는 없다. 그러나 다음 수집 공백을 해결하지 않은 채 전체 실행을 시작할 수 없다.

## 3. 동결 후 발견 — 평가 수집 계약

### F1. 출력 보안 판정이 저장되지 않음 — P1, 확신 10/10

보안 클론 `scripts/search_api.py:8154`는 다음 값을 반환한다.

```python
"security": {
    "context_gate": context_security.summary(),
    "output_gate": output_security.summary(),
},
```

반면 `scripts/evaluate_service_answers.py:1736`의 저장 구문은
`rec["postprocessing"] = resp.get("postprocessing") or {}`이며,
1758행은 `rec["retrieval"] = resp.get("retrieval") or {}`다. 이 저장 블록에는
`resp["security"]` 복사가 없다. 출력 게이트는 원래 `postprocessing`에 자신의
요약을 넣지 않는다. 따라서 입력 게이트는 `retrieval.security_gate`로 남지만
출력 게이트의 `decision`·`invalid_citations`는 사라진다.

가짜 `/health`·`/chat` 응답으로 실제 collector main을 실행했다. 정상 answer와
abstain 두 경우 모두 security가 누락됐고, raw draft·timing·입력 게이트는 보존됐다.
이는 실제 서버/LLM 평가가 아니라 저장 경로의 합성 통합 재현이다.

### F2. 전부 차단한 회피를 provider 불일치로 처리 — P1, 확신 9/10

보안 클론 `scripts/search_api.py:7985`에서 게이트 통과 원문으로 results를 바꾸고,
8007–8014행의 generation 초기값은 `used="none"`, `model=None`,
`fallback_reason="no_results"`다. 빈 results이면 생성 호출 없이 회피를 반환한다.

기존 수집기 `scripts/evaluate_service_answers.py:734`:

```python
if provider != "auto" and used != expected_provider:
    raise RuntimeError(
        "generation provider fallback: "
        f"expected {expected_provider!r}, used {used!r}"
    )
```

전부 차단된 응답 형태를 합성하여 `validate_response_controls`를 호출하면
`generation provider fallback: expected 'frontier', used 'none'`을 재현한다.
같은 오류의 collector 처리 경로는 1657–1677행에서 `response_control`을 기록하고
수집 루프를 멈춘다. 실제 보안 `/chat`을 실행한 재현은 아직 아니다.

정상적인 보안 차단과 진짜 모델 fallback/네트워크 실패를 구분하는 계약이 필요하다.
모델 검사를 통째로 완화하거나 `--allow-unpinned`로 우회해 해결했다고 볼 수 없다.
공격 평가에서 의도된 회피와 정상 질문의 부적절 회피는 분모·의미가 다르므로
차단=무조건 GFC 또는 차단=무조건 실패로 합산하지 않는다.

### F3. 런타임 식별 한계 재확인 — 기존 발견, 확신 10/10

보안 클론 `scripts/search_api.py:173`:

```python
"startup_code_sha256": _sha256_file(Path(__file__).resolve()),
```

머지 보안과 수정 사본의 search_api SHA는 같고 두 게이트 SHA는 다르다.
따라서 이 health 값과 `*-security-v1` 정책 문자열만으로 수정 적용을 증명할 수 없다.
전체 실행 파일 manifest 및 실제 프로세스 경로 연결 검증이 필요하다.
또한 수집기 632–653행의 source manifest 기대 해시 검사도 Git/index 기대 핀
분기 안에 있으므로, 단독 `--expected-source-manifest-sha256` 인자만으로
검사가 수행된다고 가정하면 안 된다. 기존 코드는 변경하지 않았다.

## 4. 오프라인 검증 결과

결과 디렉터리: `processed/eval/preflight-20260908/security-eval-plan-v1/`.

| 검사 | 수 / skip / 실패 / 오류 | 의미 |
|---|---|---|
| `collector-probes.json` | 6 / 0 / 0 / 0 | 현재 누락·오분류·식별 한계의 재현 성공 |
| `selected-tests.json` | 39 / 0 / 0 / 0 | 기존 collector 29 + artifact 10 회귀 검사 |
| 최초 `collector-tests.json` | 1 / 0 / 0 / 1 | 이전 runner가 `holdout_gold.py` 모듈 읽기까지 차단 |
| 최초 `artifact-tests.json` | 10 / 0 / 0 / 0 | 기존 artifact 검사 |

6개 재현 검사가 통과했다는 것은 결함이 수정됐다는 뜻이 아니다. **채택 gate는 미통과**다.
최초 환경 오류는 새 실행기의 보호 대상을 실제 자료 확장자로 한정해 해결했다.
이전 실행기와 실패 기록은 보존했다. 새 실행기는 네트워크·DNS·자식 프로세스를
차단하고 `.env` 및 holdout 데이터 파일 읽기를 차단한다. Judge CLI는 실행하지 않았다.
프런트엔드·서비스 변경이 없어 전체 828개 테스트·lint·build는 이번에 재실행하지 않았다.

```sh
python3 -B evidence/security-eval-preflight-20260908-v1/probe_collector.py --out processed/eval/preflight-20260908/security-eval-plan-v1/collector-probes.json
python3 -B evidence/security-eval-preflight-20260908-v1/run_selected_tests.py --out processed/eval/preflight-20260908/security-eval-plan-v1/selected-tests.json
git diff --check
```

재실행에는 새 출력 경로를 사용한다. 두 스크립트 모두 덮어쓰기를 거부한다.
명령·각 산출물 SHA는 progress-log의 같은 날짜 “보안 재평가 사전 점검” 절에 기록한다.

## 5. 범위 밖과 미완료 결정

- 검색·생성 prompt·보안 판단 규칙·Judge 수정: 이번 범위 밖.
- 원본 서비스 교체·Git 커밋/머지/푸시: 별도 승인 전 수행하지 않음.
- 외부 생성·Judge 및 미지 공격의 효과 측정: 별도 전송 범위·예산 승인 전 수행하지 않음.
- 실제 holdout·사람 calibration: 현재 자료 열람/대체 판정 없이 유지.
- 공식 성능 수치·보고서 headline 갱신: 새 결과가 없어 수행하지 않음.

설계 검토는 아키텍처의 F1/F2 수집 계약 결정에서 멈췄다. 평가 전용 수집 사본의
보완 여부는 사용자 확인 전이며, 일반 서비스/동결 수집기에 반영하지 않았다.
코드 품질·테스트 전체 경로·성능에 대한 나머지 설계 검토와 실행계획 확정은 미완료다.
위 39개 기존 테스트 통과를 전체 설계 검토 완료나 새 평가 경로 검증으로 표현하지 않는다.

참조: [수정 검증](security-layer-fixes-20260908.md),
[기존 Shadow 비교 규약](shadow-c2-controlled-plan-20260907.md).
