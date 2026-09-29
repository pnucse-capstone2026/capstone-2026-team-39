# 배열 길이 제약 분리 검증 준비 — 2026-09-14

## 상태

**오프라인 준비 완료 / 실호출 승인 대기. HTTP400 해결은 아직 미검증이다.**
사용자 요청은 “오류 해결하고 빨리 계속 하자”다. 최대3회 실호출의 구체적인
범위·순서·중단 조건을 별도로 질문했으며, 이 문서 작성 시 응답은 아직 없다.
이번 외부 LLM 호출0회. 이전 승인2회를 재사용하지 않는다.

## 가설과 최소 변경

직전 진단은 실제 내용+최소 schema에서 HTTP200, 합성 내용+전체 schema에서
HTTP400이었다. 이번 가설은 **전체 schema의 중첩 배열 maxItems 조합이 이
요청 설정에서 거부를 유발한다**는 것이다. 제공자 내부 원인은 아직 확정하지 않는다.
maxItems 자체가 지원되지 않는다는 주장도 아니다.
[Gemini 공식 문서](https://ai.google.dev/gemini-api/docs/structured-output#limitations)는
maxItems를 지원 목록에 포함하지만 크거나 깊은 schema가 거부될 수 있다고 설명한다.
이는 가설의 배경이지 이번 요청의 원인을 증명하는 자료는 아니다. 문서의 새 모델/
Interactions 예제로 API나 모델을 전환하지 않는다.

`evidence/array-schema-20260914-v1/array_schema.py`는 이전 전송 형식을 복사한 뒤
schema 노드의 maxItems만 제거한다. 질문·출처·systemInstruction·모델·온도·토큰
상한·MIME type·타입·필수 필드·enum·중첩 구조는 바꾸지 않는다. 제거한 제약의
JSON pointer와 원래 값을 보존하며 복원하면 기존 전송 body와 완전히 같다.
기존 string 길이 projection도 그대로다.

| 비교 대상 | 배열 제한 제거 수 | 이전 body | 새 body | 새 schema |
|---|---:|---:|---:|---|
| 이전 실패와 동일한 합성 입력 | 10 | 9,376 bytes | 9,236 bytes | 6,284 bytes / 113 nodes / 깊이11 |
| 기존 고정 DEV shadow_emp_01 | 10 | 21,798 bytes | 21,658 bytes | 6,284 bytes / 113 nodes / 깊이11 |

140 bytes 감소는 성능·지연 개선 지표가 아니다. 합성/실제 body를 각각 이전
실패 요청과 비교하며 API 형식 수용성을 확인하는 실험이다.

## 검증을 약화하지 않음

- 전체 host schema는 기존 v2 SHA 고정본이다. 배열 길이·문자열 길이·타입·추가
  필드·요청 결속·정확한 인용 위치·출처 검사를 그대로 수행한다.
- 답변을 자르거나 고치거나 잘못된 값에 기본값을 넣지 않는다. 검증 실패는 중단이다.
- 합성 요청은 schema/요청 결속만 검사하고 정상 추출·정답으로 인정하지 않는다.
- 실제 DEV 추출은 전체 host 검증 뒤에만 독립 의미검증으로 넘어간다.
- 의미검증과 추출기가 함께 잘못 판단할 수 있는 기존 한계는 남는다.
  모든 산출물에서 eligible_for_service=false, candidate_gfc=null을 유지한다.
- 서비스·검색·생성 prompt·보안·Judge 변경0, 실제 holdout 열람0, 기존 산출물 변경0,
  git 쓰기0. investigate 스킬의 증거 우선/변수 분리/회귀 검증을 적용했다.
  전역 telemetry·설정·동기화·commit·freeze 상태 파일은 범위 밖이라 실행하지 않았다.

## 승인 요청 중인 실행

1. 합성 ALPHA 입력 + 전체 schema에서 maxItems만 제거: 추출 모델1회.
2. 1이 HTTP200/STOP/schema 검증을 통과하면15초 뒤 기존 DEV 한 문항 추출1회.
3. 2가 전체 host 검증을 통과하면15초 뒤 독립 의미검증1회.

추출 gemini-3.5-flash-lite / 의미검증 gemini-3.1-flash-lite. 기존 키 하나,
temperature0, maxOutputTokens8192, timeout45초, 전체 상한3회. 첫 HTTP 오류,
미완료 출력, JSON/schema/인용 검증 오류에서 중단한다. 재시도·키 교체·자동
resume·fallback 없음. 기존 프로젝트 관측 시도170회는 계정의 일일 총 사용량이나
무료 잔여량을 의미하지 않는다. 준비 manifest의 승인 상태는 대기로 기록했다.

준비 산출물: `processed/eval/preflight-20260914/array-schema-v1/preparation-v1/manifest.json`.
입력635개 SHA 고정. live-v1은 만들지 않았고 실호출 명령을 실행하지 않았다.

## 테스트

```sh
python3 -B -m unittest discover -s evidence/array-schema-20260914-v1 -p 'test_*.py'
python3 -B -m unittest discover -s evidence/provider-schema-20260913-v1 -p 'test_*.py'
python3 -B -m unittest discover -s evidence/quote-adapter-20260913-v2 -p 'test_*.py'
python3 -B evidence/array-schema-20260914-v1/run_array.py prepare
git diff --check
```

새14 tests in0.556s, 기존 projection14 in0.131s, quote adapter26 in0.041s,
모두 OK/failures0/errors0/skips0. 서로 다른 관련 시험54개다.
회귀 시험에서 배열 제거를 메모리에서 no-op으로 바꿨을 때1건이 의도대로
`10 != 0`으로 실패했다(0.003s). 이 시험은 전송 변경을 검증하며 실제 API의
HTTP400을 로컬에서 재현했다는 뜻은 아니다.

전체 `tests/test_*.py` discovery는 기존 보호 audit를 설치한 `python3 -B -`에서
실행했다. 비밀/실제 holdout/외부 API는 차단하고 합성 loopback HTTP만 허용했다.
결과862 tests in32.732s, failures0/errors0/skips6, blocked_accesses=[]이다.
skip 사유는 optional NumPy, openpyxl 미설치, python-docx 미설치다. 출력에 나온
holdout authorization 거부/timeout은 합성 부정 테스트이며 실제 holdout 실행이 아니다.
lint/build는 이 실험용 Python 수정에서 재실행하지 않았다. git diff --check 통과.

## SHA-256

| 경로 | SHA-256 |
|---|---|
| `evidence/array-schema-20260914-v1/array_schema.py` | `6022287a2713bca89f8977877a579b22136fd0d30d647aedb4df90378be9ea30` |
| `evidence/array-schema-20260914-v1/run_array.py` | `bbb385a759e1b48f4219c6a2f16f58515b7b60f7dcd8e687ed4d7446733a9c3f` |
| `evidence/array-schema-20260914-v1/test_array_schema.py` | `16d16f0bdec6f1590be63bb9e62eed9f0e70a264a8b4e496241354d63ae0766a` |
| `preparation-v1/manifest.json` | `c53c5424646605b75bb432660936eb87a1f6d2709b232206397b8963af6dfa02` |
| 합성 body (파일 wrapper 아님) | `e08ea15c17b78162f82d3773773d8bb923a1046418036ac0ff78834dae977550` |
| DEV body (파일 wrapper 아님) | `cafb48970c2df1a5cef4dc761eb797372039a51b7be6667c83c8a45d5c1e75ea` |

이 문서 이후의 승인/실행 결과는 별도 결과 경로에 기록한다. 현재 상태는
**DONE_WITH_CONCERNS: 검증용 변경과 오프라인 시험 완료, API 수정 효과는 미확인**이다.
