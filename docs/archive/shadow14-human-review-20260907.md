# Shadow14 사람 진단 검토 안내

작성일: 2026-09-07. 상태: 패킷 준비 완료, 사람 검토 미착수.

## 열기

- [문항별 비교 화면](../../processed/eval/preflight-20260907/shadow14-human-review-v1/review.html)
- [run1 읽기 전용 문서](../../processed/eval/preflight-20260907/shadow14-human-review-v1/review-run1.md)
- [판정 JSON 템플릿](../../processed/eval/preflight-20260907/shadow14-human-review-v1/review-template.json)

비교 화면은 외부 서버나 API 연결 없이 사용하는 독립 HTML 파일이다. 자동 열기는
브라우저 도구의 로컬 URL 보안 정책에 막혔으며 우회하지 않았다. 실제 화면의 시각적
검증과 다운로드 버튼의 브라우저 종단간 검증은 완료하지 못했다. HTML 대신 위 문서로
읽고 `R번호 / 근거 일치 / 완전성 / 후처리 영향 / 근거 메모`를 메시지로 보내도 된다.

## 사용자가 할 일

1. 비교 화면에서 이름 또는 이니셜을 입력한다.
2. 각 문항의 **run1만 먼저** 읽는다. run2·run3는 궁금한 문항에서만 선택한다.
3. 원문과 최종 답변을 대조해 사실의 근거 일치 여부와 답변의 완전성을 선택한다.
4. 초안과 최종 답변을 비교해 맞는 내용 소실, 잘못된 내용 제거, 혼합 또는 변화 없음인지 고른다.
5. 메모에 출처 번호와 판단의 근거가 된 문구를 적는다. 어려운 것은 판단 보류로 기록한다.
6. 그 다음 Judge를 펼쳐 동의 여부를 선택한다. Judge가 정답이라는 전제로 판단하지 않는다.
7. **검토 결과 JSON 내려받기**로 파일을 보관하고 전달한다. 브라우저 임시 저장만으로는
   다른 사람이나 에이전트에게 전달되지 않는다.

근거 일치·완전성·후처리 영향·메모를 입력하면 해당 답변은 REVIEWED로 표시된다.
판단 보류도 검토 기록으로 인정하지만 정답 판정으로 바꾸지는 않는다. Judge 동의 여부는
진단용 별도 입력이다. JSON에는 답변 ID·해시, 패킷 해시, 최초 Judge 노출 시점과 그 직전
입력이 남는다. 다른 패킷의 결과, 중복 답변, 틀린 해시·판정 값은 불러오지 않는다.

## 표본과 해석 범위

- Shadow60의 사전 정의 core42 중 **필수 근거 모두 포함@8 + majority 비GFC 11문항 전수**.
- 성공 대조 3문항: `shadow_adm_01`(단순·3/3 GFC), `shadow_emp_07`(복합·3/3 GFC),
  `shadow_intl_04`(복합·majority GFC이지만 run1 비GFC, 회차 차이 대조).
- 14문항 × 3회차를 제공하지만 우선 요청하는 검토량은 run1 14답변이다.
  성공·실패 구분은 문항의 majority 기준이므로 run1 판정과 항상 같지는 않다.
- 순서는 고정 문자열과 case ID의 SHA-256으로 섞었고, Judge와 선정 근거는 접어 두었다.
  이는 초기 점수 노출을 줄이는 진단 화면이지 완전한 블라인드 평가가 아니다.
- 실패 중심 목적 표집이다. 이 표본의 정답률이나 Judge 일치율을 전체 성능으로 일반화하지 않는다.
  정식 2인 calibration 또는 holdout gold signoff를 대신하지 않는다.
- 원문은 당시 저장한 검색 context 전체다. PDF/HWP 시각적 원본을 새로 열거나 문서를
  재파싱하지 않았다. 표와 줄바꿈을 재해석하지 않고 저장 텍스트를 그대로 제공한다.
- 최종 답변 기본 표시는 `answer`(Judge 입력)다. `cited_answer`는 별도로 펼쳐 볼 수 있다.
  원래 Judge 점수, guard 적용 전 값, 정답 기준은 별개로 보존한다. 재채점하지 않는다.

## 검증·재현

```sh
python3 -B scripts/build_shadow_diagnostic_review.py --output-dir processed/eval/preflight-20260907/shadow14-human-review-v1
python3 -B -m unittest tests.test_build_shadow_diagnostic_review tests.test_analyze_shadow_generation
bun run lint
bun run build
git diff --check
```

위 출력 디렉터리는 이미 존재하므로 재실행은 새 디렉터리명을 지정해야 한다.
같은 경로로 재실행하면 exit 2로 거부되어 기존 산출물이 보존된다. CLI는 입력 경로
재지정을 받지 않으며 핀된 Shadow 질문 1개·답변 3개·Judge 3개만 읽는다.

- 관련 unittest **27개 통과, skip 0, 실패 0**. 전체 서비스 테스트는 이번 작업에서 재실행하지 않았다.
- JS 전체 구문 컴파일, JSON 라벨 왕복, 오염 입력 12종 거부를 위 unittest 안에서 검증했다.
  이는 실제 브라우저 조작 테스트가 아니다.
- 입력 7개·산출물 6개 파일 해시, 답변–Judge 42쌍, 전체 context 336개를 별도로 대조했다.
- lint/build/diff check 통과. 외부 LLM 호출 0, 실제 holdout 접근 0, 커밋·푸시 0.
- 서비스/Judge 동결 파일 4개의 기존 SHA-256 유지.

정본: `processed/eval/preflight-20260907/shadow14-human-review-v1/manifest.json`.
SHA-256: `1ab5b65a66c74f12e38f41c85f5215b2258f7e323cf2ac9d0a2345e4a95157bd`.
패킷 SHA-256: `e469fb1ccc8457c57e5626a7839470d6eed465ecc97140f6210ab9cdac178212`.
전체 명령·해시는 `docs/archive/progress-log-20260901.md`의 2026-09-07 Shadow14 절에 기록했다.

## 이후

사람 검토 결과를 받아 후처리의 올바른 거부/오탐, 실제 생성 누락, Judge 문제를 구분한다.
확인 전에는 서비스 규칙·Judge·Shadow gold를 바꾸지 않는다. C1/C2 추가 비교 실행은
조건과 호출 범위를 별도로 정한 뒤 승인받아 진행한다.
