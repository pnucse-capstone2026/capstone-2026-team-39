# 부모 근거 범위 결속: 오프라인 재생 결과 — 2026-09-14

## 결론

**기존 출력의 인용 위치 모호성2개를 별도 분석 계약에서 정확히 해소했다.**
원래 응답/검증기/실패 판정은 그대로다. 새 모델 실행이나 서비스 수정 완료를
의미하지 않는다. 원본을 새 규칙으로 다시 읽는 사후 분석(counterfactual replay)이다.

새 방식으로 구조 검사를 끝까지 실행하니 **질문 범위 누락으로 주장7개 전부가
uncertain/query_scope_required**였다. matched0, 의미검증0, GFC 미측정이다.
인용 위치를 고치는 것만으로 채택 가능한 답변이 생기지는 않았다.

| 항목 | 기존 전체 본문 유일성 계약 | 별도 부모 범위 결속 분석 |
|---|---|---|
| 동일 원본 응답 | quote_ambiguous로 구조 파싱 중단 | 구조 파싱 완료 |
| 모호한 인용 | relation_span/ operator_span 2개 | 유일한 부모 문장 안에서 각1개 위치 확정 |
| 최종 symbolic 근거 판정 | 첫 오류 뒤까지 실행되지 않음 | 7개 모두 uncertain, matched0 |
| 실제 원래 실행 상태 | STOPPED_INCOMPLETE | 변경하지 않음 |
| 새 API/생성/의미검증 호출 | — | 모두0 |

## 원인과 변경 경계

직전 DEV 출력의 ‘자격증 응시료’는 원문 전체에서2회, ‘한도’는3회 나온다.
기존 quote_adapter.py:135가 이를 거부하는 것은 고정 계약대로인 동작이다.
두 인용은 모델이 이미 지정한 다음 evidence_quote 안에서는 각각1회다.

> ▷ 자격증 응시료 : 1인 연간 10만원 한도 실비 지원

이 부모 인용 자체는 원문 전체에서 유일한[8,39) 범위다. 출처는
`doc_cb574d4321358d2eb447a2af:cascade#0002`, field=text. relation_span의
절대 시작은10, operator_span은31이다. 모두 Python Unicode 문자 인덱스다.

분석 계약 `pnu.parent-bound-analysis.v1`의 일반 규칙:

1. 부모 evidence_quote를 원문 전체에서 정확히 한 번만 찾을 수 있어야 한다.
2. 자식 인용은 같은 source_id와 text field여야 한다.
3. 자식 인용은 그 부모 범위 안에서 정확히 한 번만 존재해야 한다.
4. 원문 그대로의 인용에 host가 계산한 절대 위치만 추가하고, 기존 source SHA/
   offset/포함관계/typed 근거 검증으로 다시 검사한다.

이는 **본문 전체 유일성을 부모 문장 내부 유일성으로 바꾸는 별도 계약**이다.
기존 검증을 그대로 통과했다고 표현하지 않는다. 코드에 DEV 문항 id·한국어
트리거 키워드·정답을 넣지 않는다. 단순 첫 위치 선택이나 fuzzy match는 없다.
부모 중복, 부모 밖 자식, 부모 안 중복(겹치는 문자열 포함), 다른 출처/field,
원문 변경·offset/hash 위조는 거부한다.

적용은 non-table fact의 relation/value/operator/body_scope/condition_spans뿐이다.
query_scope·claim_quote·title_scope·table의 전역 유일성 요구는 그대로다.
독립 의미검증도 변경하지 않았다. 표의 행/열/헤더와 의미 충돌 검사는 기존
고정 검증기를 사용한다. 부모가 의미적으로 올바른 근거인지, 모델이 조건이나
상충 근거를 빠뜨렸는지는 위치 결속만으로 증명할 수 없다.

## 구현과 재현

- `evidence/parent-bound-20260914-v1/parent_bound.py`: 부모/자식의 정확한 위치
  결속과 사후 구조 분석. 기존 v2 schema/요청 결속을 검사하고 기존 v1 typed
  verifier로 넘긴다. frozen 모듈의 전역 함수 교체나 파일 수정은 없다.
- `test_parent_bound.py`: 합성 반례21개. 기존 정상 fixture의 변환 결과와 동일성,
  인용/출처/범위/표/조건/충돌/스키마 검증 유지, 비서비스 표기를 시험한다.
- `replay.py`: SHA 고정된 실패 DEV 한 문항만 오프라인에서 다시 읽고 원본 계약과
  새 분석 계약의 결과를 분리 저장한다. 네트워크/키 접근 capability는 꺼져 있다.

```sh
python3 -B -m unittest discover -s evidence/parent-bound-20260914-v1 -p 'test_*.py'
python3 -B evidence/parent-bound-20260914-v1/replay.py
python3 -B -m unittest discover -s evidence/quote-adapter-20260913-v2 -p 'test_*.py'
python3 -B -m unittest discover -s evidence/semantic-adapter-20260913-v1 -p 'test_*.py'
python3 -B -m unittest discover -s evidence/evidence-contract-20260913-v1 -p 'test_*.py'
git diff --check
```

replay는 실행 기록이며 이미 생성된 출력 경로에 재실행하지 않는다.
출력: `processed/eval/preflight-20260914/parent-bound-v1/offline-v1/`.
input651개 SHA와 출력4개 SHA·목록 포함5개 파일 집합이 모두 일치했다.
부모 결속을 적용한 본문 참조16개 중 전역 중복이었던2개만 새로 위치가 확정됐다.

원본을 기존 q.parse_extraction에 넣으면 여전히 quote_ambiguous가 재현된다.
새 resolve_child를 메모리상 기존 전역 resolver로 바꾼 mutation에서는 대표
회귀 시험1개가 같은 quote_ambiguous로 의도대로 실패했다(errors1,0.001s).
실제 코드/산출물을 되돌리거나 수정한 시험은 아니다.

새21 tests in0.031s, 기존 quote26 in0.044s, semantic23 in0.011s,
contract13 in0.005s 모두 OK(failures0/errors0/skips0), 서로 다른 관련 시험83개다.
전체 tests/test_*.py는 보호 audit를 설치한 Python discovery로 실행했다.
**862 tests in33.334s, OK(skipped=6), failures0/errors0, blocked_accesses=[]**.
skip 사유는 optional NumPy, 미설치 openpyxl/python-docx다. 출력의 holdout
authorization 거부/timeout은 합성 부정 시험이며 실제 holdout을 읽지 않았다.
이번 lint/build는 재실행하지 않았다. git diff --check 통과.

## 다음 병목: 범위 정보가 전부 비어 있음

보존된 원본 모델 출력에서 확인한 값:

- query_scope 항목0개.
- claim.scope: 7개 중7개가 빈 객체.
- evidence.assertion.scope: 7개 중7개가 빈 객체.
- body_scope/title_scope 참조: 각각0개.

질문에 사업명·연도가 있지만 추출 출력은 이를 결속하지 않았다. 기존 verifier는
query_scope가 비어 있으면 근거를 matched로 인정하지 않는다. 질문 범위만 임의로
채워도 claim/근거 범위의 누락이 함께 해결되는 것은 아니다. 따라서 새 호출이나
점수 집계보다 범위 정보를 누락하는 추출 계약/모델 동작을 먼저 검토하는 것이
다음 우선순위다. 이번에는 값을 채우거나 범위 요구를 완화하지 않았고, 이를
겨냥한 prompt·정규식·후처리 규칙도 추가하지 않았다.

위 수치는 기존 DEV 출력 한 건의 사후 진단이지 새 모델의 정확도 결과가 아니다.
기존 실패를 성공으로 재분류하지 않는다. semantic_verified=false,
eligible_for_service=false, candidate_gfc=null을 유지한다. 새 호출 계획도 생성하지
않았다. 관측 프로젝트 시도172회는 그대로이며 일일 계정 총량/무료 잔여량은 모른다.

## 보존 및 상태

investigate 스킬의 원인 재현→최소 독립 변경→반례/회귀/전체 시험 절차를 적용했다.
전역 스킬 설정·telemetry·동기화·자동 commit은 범위 밖이므로 실행하지 않았다.
서비스/보안/Judge/기존 검증기/원래 prompt/기존 answers·judgment·summary·README
수정0, 실제 holdout 열람0, git 쓰기0, 외부 LLM 호출0이다.

**DONE_WITH_CONCERNS: 별도 오프라인 인용 결속 실험 완료. 서비스 미적용,
범위 누락으로 근거 채택0, 생성 성능 개선 미확인.**

| 산출물 | SHA-256 |
|---|---|
| `parent_bound.py` | `27b68873265bbe0641c554b86f3a630a4329c1e9234f95ff2f01e6b0a1f0c822` |
| `replay.py` | `868c7ec00d84c6544664427513b12d300b0eefcc6172ddd82c925cbe28499931` |
| `test_parent_bound.py` | `385a6e4bdc225d660decce6edc015b24310a0211be4f3ce0c054483b4213671b` |
| `offline-v1/manifest.json` | `20c1efb939e4f85447b363b94581528d26ea85e85320fd8cb5dbe9a12b7b7232` |
| `offline-v1/analysis.json` | `f6206faa72cceaf166a9ef815cb6421614aa59448885e38f23a0963580daf0e0` |
| `offline-v1/summary.json` | `ef32a71944a43c8a951ca716d85a7fd2da03c0df438434179b9a35002b2e3f18` |
| `offline-v1/input-sha256.json` | `b9fc80615268b4eed147687b54e7d227fa77238810dbfdb8a76598100a3d499e` |
| `offline-v1/output-sha256.json` | `a3d25d42e526410ad87d72ae31967b6d8cded6d7ad0284ec655084e1769ae84b` |
