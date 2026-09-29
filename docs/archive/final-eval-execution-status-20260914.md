# 최종평가 실행 준비 상태 (2026-09-14)

현재 상태: **사전 점검 완료, 외부 전송 범위 승인 대기. 실제 최종평가 결과 없음.**

기존 보고서의 DEV·Shadow·보안 실험 결과를 아래 최종평가 결과로 혼동하지 않는다.
교수님께 현재 보고서를 제출할 경우 “최종평가 결과는 별도 보완 예정”으로 명시한다.

## 고정한 평가

| 항목 | 설정 |
|---|---|
| 정답지 | 사용자 1인 검수 36문항, 원본 PASS23/REVISE13 보존 후 승인된 역할·표시 정정 |
| Core | 27문항, C0/C1 각각 독립 생성 3회 |
| Challenge | 9문항, C1 독립 생성 3회, Core와 분리 집계 |
| C0/C1 차이 | 같은 보안 enforce 소스, retrieval_tuning false/true |
| 생성 | gemini-3.5-flash-lite, 총189개 |
| Judge | gemini-3.1-flash-lite, 고정 v11, 각 답변 1회 총189개 |
| 최대 provider 시도 | 378회, 단일 키, 자동 재시도·키 교체·모델 교체 없음 |
| 지표 | GFC 주지표, 0~2점 평균 보조 지표 |
| 중간 결과 | run1 생성63개 + Judge63개 완료 후, 최종 n=3과 구분 |
| 사람-LLM 일치도 | 정답지 검수와 별개인 답변 채점63개 필요. 현재 미측정 |

Oracle 및 추가 Judge 반복 안정성 진단은 실행 전에 제외했다.
기존 2인·clean-Git gate는 통과로 표시하지 않고, 별도 1인 개정 프로토콜을 사용한다.
평가 대상 소스는 이전 실험의 보관 사본 102파일을 해시로 확인했다.
현재 작업 디렉터리의 미커밋 서비스 코드를 새 평가 코드로 사용하지 않는다.

## 검증 및 중단 사유

- 새로운 실행 도구 테스트18개 통과, C0/C1 `/health` 사전 점검 모두 통과.
- v1 실행 도구의 공개 공급자 이름 비교 오류는 원인 확인 후 v2에서 수정.
  서비스·검색·생성·보안·Judge 코드는 변경하지 않았다.
- 원본 검수 다운로드·SQLite·기존 평가 산출물은 보존했다.
- 실제 실행 요청은 외부 전송 승인 검사에서 거절됐다. 최종평가 질문과 역할,
  검색 문서 청크, 생성 답변 및 정답·근거 기준이 Google Gemini API
  (`generativelanguage.googleapis.com`)로 전송될 수 있어 그 범위의 명시적 승인이 필요하다.
- 거절 후 우회 실행하지 않았다. v1/v2 ledger 모두 provider 시도0,
  active0, 378슬롯 pending. 평가가 백그라운드에서 진행 중이라는 뜻이 아니다.

## 정본 경로

- 준비 산출물: `processed/eval/final-single-reviewer-20260914-v1/preparation-v1/`
- 데이터 SHA-256: `5a3e4e17bb991233c21b446f7fc17517024c4ac86b19ebc99d4ffbf431026411`
- 실행 계획: `processed/eval/final-single-reviewer-20260914-v1/execution-plan-v2.json`
- 계획 SHA-256: `dc39e90a68b391b74968baea48aefe4eadf7120bf3ca28a8a516b265ef8ba98a`
- 실행 경로: `processed/eval/final-single-reviewer-20260914-v1/live-v2/`
- 상세 승인·변경·테스트 기록: `docs/archive/progress-log-20260901.md`
