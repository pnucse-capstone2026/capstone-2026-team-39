# 2026-09-14 범위 우선 추출 후보: 구현 완료, 실제 호출 승인 대기

상태: **BLOCKED — 실측 후보와 로컬 검증은 완료했으나, Google로 전송하는
구체적 데이터에 대한 승인 부족으로 외부 실행이 프로세스 시작 전에 차단됨.**
API 호출0, 새 모델 출력0, 실제 성능 개선 미측정이다.

## 바뀐 실험 설계

기존 동결 서비스가 아니라 `evidence/scope-first-20260914-v1/`의 별도 후보다.
서비스·기존 prompt/검증기·보안·Judge·기존 산출물은 수정하지 않았다.
직전 분석에서 확인한 query_scope 누락을 모델이 직접 해소하는지 시험한다.

| 단계 | 모델 | 전송할 내용 | 다음 단계 조건 |
|---|---|---|---|
| 1. 질문 범위 추출 | gemini-3.5-flash-lite | 기존 DEV1문항의 질문만 | 7차원 명시적 결정, 원문 인용, 미확정 없음, 지정 범위 존재 |
| 2. 주장·근거 추출 | gemini-3.5-flash-lite | 같은 질문·기존 초안·검색 근거8개 및 새 범위 제안 | 원문 결속·고정 typed 검증·질문/근거 범위 검사 통과 |
| 3. 독립 의미 검토 | gemini-3.1-flash-lite | 원래 질문·초안·근거8개·제안 출처 ID | 고정 의미검증 형식으로 판정; 부정 판정도 그대로 보존 |

한 문항당 최대3회, 단일 기본 키, 재시도0, 호출 간15초, 요청 timeout45초,
첫 오류/검증 보류에서 중단한다. 실패 후 남은 호출을 다른 실험에 재사용하지 않는다.
목적지는 Google Gemini `generateContent`다. 실제 holdout·정답·이전 Judge 점수는
전송하지 않는다. 계정 일일 총량/무료 잔여량은 알 수 없으며 관측된 프로젝트
기존 누적172시도에 이번 새 호출이 더해진 것은 없다.

범위 제안을 정답처럼 복사하는 방식이 아니다. 원래 질문과 대조하고, 근거의
사업명·연도 등은 제목/본문에서 모델이 직접 인용해야 한다. 코드가 빈 범위를
채우거나 인용을 고치지 않는다. claim.scope는 질문에서 정상 상속할 수 있다.
마지막 의미 검토에는 범위 제안·추출기의 태그/이유/판정이 들어가지 않는다.

## 고정 검증과 새 후보의 경계

새 범위/추출 응답은 `pnu.scope-first.v1`과 단계별 고유 request ID를 사용한다.
고정 검증기에 연결할 때 version/request_id만 명시적으로 변환하고 내용은
수정하지 않는다. 원본 새 모델 출력과 변환된 검증 입력을 구분한다.
인용은 앞서 만든 부모 근거 결속 계약을 사용한다. non-table 본문 자식은
전역 유일한 부모 안에서 유일해야 하며, 제목/표 참조는 전역 유일성을 유지한다.
과거 v2의 quote_ambiguous 실패를 성공으로 재분류하지 않는다.

제공자 schema에서는 이미 마련된 string 길이/maxItems 제거 projection을
재사용하고, host에서는 전체 schema와 고정 근거 검증을 그대로 수행한다.
이번 새 요청의 제공자 호환성은 아직 실제 호출로 확인하지 못했다.

알려진 한계: 모든 단계가 같은 범위를 공동 누락하거나 환수/지급 의미를 함께
잘못 해석하면 로컬 검사를 통과할 수 있다. 합성 반례로 보존했으며
`eligible_for_service=false`, `candidate_gfc=null`을 유지한다.
선택된 DEV1건의 추출 pilot이지 새 답변 생성·GFC 비교·holdout 평가가 아니다.

## 검증 결과

- 신규26개 시험 통과. 정상3단계·각 단계 오류 중단·quota429 중단·키 전환 없음·
  SQLite 호출 상한·기존 ledger 재개 거부·잘못된 승인 시 키 로딩 전 중단을 시험했다.
- 원문/스키마/요청 ID·범위 상속·누락 미보정·제목 결속·독립 검토 정보 분리와
  부정 의미 판정 보존을 시험했다. 모의 HTTP200의 형식 오류에서도 receipt가
  먼저 저장되는 것을 확인했다. 실제 네트워크를 사용한 시험이 아니다.
- 관련154 tests, 실패/오류/skip0. 신규26은 이154에 포함된다.
- 전체 Python 회귀862 tests in33.874s, 실패/오류0, skip6.
  보호 audit `blocked_accesses=[]`; 외부 네트워크/실제 holdout/비밀은 차단하고
  합성 localhost 서버만 허용했다. skip은 optional NumPy, openpyxl/python-docx다.
- 입력672개 SHA, 준비 manifest와 첫 요청 body의 재생성 일치 확인.
  `git diff --check` 통과. 프런트엔드 변경0, lint/build 재실행0.

초기 모의 HTTP 시험2개는 macOS 임시 경로의 `/var` symlink 때문에 실패했다.
시험 임시 디렉터리만 `.resolve()`해서 정상 물리 경로를 사용하도록 고쳤다.
원래 출력 symlink 거부 보호는 완화하지 않았다. 초기26개 실행은 실패1/오류1,
수정 후26개0.329s OK, 최종 관련 실행에서26개0.443s OK였다.

## 외부 실행 차단

다음 실행을 `require_escalated`로 요청했으나 자동 승인 검토가 거부했다.

```sh
python3 -B evidence/scope-first-20260914-v1/run_pilot.py live --manifest-sha256 3ae2db8749cff1f448660bc5c1eaffbfa08dc7d75580e499b6f2befed6b42b72 --authorize I_APPROVE_SCOPE_FIRST_THREE_ATTEMPTS --approval-message '다시 이어서 해주면 돼'
```

거부 사유는 일반적인 API/진행 승인과 별개로, **DEV 질문·초안·검색 근거를
Google Gemini에 전송하는 구체적 동의가 부족**하다는 것이다. 우회하지 않았다.
CLI 프로세스가 생성되지 않았으므로 `live-v1/`과 호출 ledger가 존재하지 않으며
키를 읽거나 HTTP를 보낸 것도 없다. 이는 Gemini 오류나 모델 성능 실패가 아니다.

필요한 사용자 확인: “기존 DEV1문항의 질문·기존 초안·검색 근거8개와 이번에
생성되는 범위 제안을 Google Gemini에 전송해, 위3단계에서 최대3회 API 호출을
실행해도 되는가?” 범위/출처/상한이 같으면 새로운 도구나 schema를 더 만들 필요
없이 준비된 실행을 재개할 수 있다. 승인 응답은 새 실행 기록에 정확히 남긴다.

## 산출물

| 경로 | SHA-256 |
|---|---|
| `evidence/scope-first-20260914-v1/scope_first.py` | `493f5f500b3273c3f72ab05b6f376256f027fb96bb515d65bde5feaba50ac141` |
| `evidence/scope-first-20260914-v1/run_pilot.py` | `8081a0febaa76c35f9bc191dd15f4123a0a328722b669144426407415aeb1cbf` |
| `evidence/scope-first-20260914-v1/test_scope_first.py` | `1b78dcb6810c53935b6f0de80ace5ceed3545e7ff3c345f0c9ef2a8652fcb872` |
| `processed/eval/preflight-20260914/scope-first-v1/preparation-v1/manifest.json` | `3ae2db8749cff1f448660bc5c1eaffbfa08dc7d75580e499b6f2befed6b42b72` |
| `processed/eval/preflight-20260914/scope-first-v1/verification-v1/related-tests.json` | `3072e5bff2ef4ed38c9715f43b7b45cb8ed21fe411aa29f94b10bdbdc79f6121` |
| `processed/eval/preflight-20260914/scope-first-v1/verification-v1/output-sha256.json` | `38844075e9752d8fab2bc508dccaa82e89872656e7a900c52cc3ee6c15690191` |

첫 scope provider body SHA는 `9f994770fe4b266b2befa3333ea93f57b9beec8ce635297451588f2e14439f83`.
추출/의미검토 body는 앞 단계의 실제 응답에 의해 결정되며 그때 원문과 함께 저장한다.
기존 성능 숫자/정본 보고서 변경0, Git staging/commit/tag/push0,
생성/평가 백그라운드 작업0. 실제 성능 판단은 승인 후 관측 결과가 있어야 가능하다.
