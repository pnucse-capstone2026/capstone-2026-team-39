# Shadow60 Judge v11 재개 체크포인트 — 2026-09-06

## 현재 상태

Gemini 3.5 생성은 60문항 × 3회 = 180개 완료됐다. 생성 오류·fallback·재시도는
0건이며 정본은 `evidence/20260914/shadow60-generation-20260905.json`이다.
이 결과는 탐색용 Shadow60 C1이며 최종 holdout 또는 C0 대비 향상 결과가 아니다.

Judge는 run1의 첫 15개만 완료됐다. GFC 5/15, 평균 0.9333/2는 **순서상 초반의
부분 결과**이며 전체 성능이나 3회 majority 결과로 사용하지 않는다. 이 중 2개는
Judge 인용 계약 검증으로 raw GFC true에서 false로 강등됐다. raw 판정을 대신
점수로 채택하지 않고, 실제 생성 실패와 Judge 인용 형식 문제를 구분해 분석한다.

기존 15개 판정 파일은 변경하지 않았다. 재개를 위해 아래 새 경로에 그대로
복사했으며 양쪽 SHA-256은 동일하다.

- 원본: `processed/eval/preflight-20260905/shadow60-generation-v1/judge/c1-run1-judge-v11-r1.jsonl`
- 재개본: `processed/eval/preflight-20260906/shadow60-judge-v11-v1/judge/c1-run1-judge-v11-r1.jsonl`
- 15행 체크포인트 SHA-256: `879ccb8fe7ab2a31ca2d4f14b28239734a47606226e34450a6298c36891aa0d3`

## 외부 전송 승인 대기

2026-09-06 재개 명령은 자동 승인 검사에서 **프로세스 시작 전에 거부**됐다.
사유는 기존의 일반적인 API 승인만으로 내부 평가 payload의 목적지와 전송 범위에
대한 구체적인 승인이 확인되지 않는다는 것이었다. 우회 실행하지 않았고 이번
재개 시도의 외부 LLM 호출은 **0회**다.

요청하는 승인 범위는 다음과 같다.

- 목적: 동결된 Shadow60 생성 답변의 Judge v11 품질 평가.
- 목적지: Google Gemini API,
  `https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent`.
- 내용: Shadow60 질문, gold/필수 claim, 생성 답변, 검색 근거 및 평가 지침을
  기존 `judge_service_answers.py`의 compact Judge 입력으로 전송한다.
- 제외: 실제 holdout, 저장소 전체, `.env` 내용. API 키는 기존 인증 헤더에만
  사용하며 prompt·문서·로그에는 기록하지 않는다.
- 규모: 남은 run1 45건 + run2 60건 + run3 60건 = **165건**.
  기존 `--retries 6`은 문항당 총 시도 최대 6회를 뜻하므로 기술적 재시도를 포함한
  HTTP 요청 상한은 990회다. 정상 처리 시 165회이며 의도적 반복 재채점은 없다.
- 위험: 위 평가 데이터가 외부 제공자에게 전송되며, API 사용량/무료 한도에
  반영될 수 있다. 다른 모델로 자동 변경하지 않는다.

## 검증된 입력과 재개 명령

`--validate-only`로 run1/run2/run3 모두 60개 eligible, terminal service error 0을
확인했다. pending은 각각 45/60/60이며 외부 API는 호출하지 않았다.

| 입력 | SHA-256 |
| --- | --- |
| Shadow60 cases | `0906e6c8de40040915e668e6cf61639306657e6f94f41e5bcdf62197a6d90754` |
| C1 run1 answers | `9202f4fd9e0bf3ad8447d4f3f72ef2ddeffff0672fb8e26dc10d48970772d8c6` |
| C1 run2 answers | `d85a141c53e3f3b33003ed78f46f009aab11a67af408916cca39ecae53e7f599` |
| C1 run3 answers | `dcbc047e4b1a10005bcb2866b5b7db3c758f64148bcc02dd9fa2ad94954ccaf2` |
| Judge config | `c165059daa5903b74d9b7fe260856419b697721ecc1e869a701aa0c85dc18fce` |

다음 명령은 **위 외부 전송 승인 후에만** 실행한다. 기존 출력의 검증된 순서상
prefix만 재사용하며 동일한 sleep/retry/model/output 한도를 유지한다.

```zsh
for shadow_judge_run in 1 2 3; do
  shadow_judge_output="processed/eval/preflight-20260906/shadow60-judge-v11-v1/judge/c1-run${shadow_judge_run}-judge-v11-r1.jsonl"
  shadow_resume=()
  if [ -f "$shadow_judge_output" ]; then shadow_resume=(--resume); fi
  python3 -B scripts/judge_service_answers.py \
    --cases config/pnu-service-shadow60-v1.jsonl \
    --answers "processed/eval/preflight-20260905/shadow60-generation-v1/c1-run${shadow_judge_run}.answers.jsonl" \
    --out "$shadow_judge_output" \
    --judge-run-id judge-v11-r1 --judge-model gemini-3.1-flash-lite \
    --max-output-tokens 1600 --timeout 180 --retries 6 --sleep 3 \
    --expected-experiment-id shadow60-generation-v1 --expected-condition-id c1 \
    --expected-generation-run-id "run${shadow_judge_run}" "${shadow_resume[@]}" || exit $?
done
```

## 완료 후 분석

세 판정 파일 모두 60행이고 terminal error가 없어야 최종 집계를 실행한다.
`analyze_shadow_generation.py`는 답변/Judge/cases 해시를 검증하고 문항별 2/3 GFC,
core·role·challenge별 결과, atomic evidence@8 교차표, Judge guard와 인용 계약
강등을 구분한다. 180개 답변을 독립적인 180문항으로 계산하지 않는다.

```zsh
python3 -B scripts/analyze_shadow_generation.py \
  --answers \
    processed/eval/preflight-20260905/shadow60-generation-v1/c1-run1.answers.jsonl \
    processed/eval/preflight-20260905/shadow60-generation-v1/c1-run2.answers.jsonl \
    processed/eval/preflight-20260905/shadow60-generation-v1/c1-run3.answers.jsonl \
  --judgments \
    processed/eval/preflight-20260906/shadow60-judge-v11-v1/judge/c1-run1-judge-v11-r1.jsonl \
    processed/eval/preflight-20260906/shadow60-judge-v11-v1/judge/c1-run2-judge-v11-r1.jsonl \
    processed/eval/preflight-20260906/shadow60-judge-v11-v1/judge/c1-run3-judge-v11-r1.jsonl \
  --json-out processed/eval/preflight-20260906/shadow60-judge-v11-v1/analysis/c1-3run-v11.json \
  --csv-out processed/eval/preflight-20260906/shadow60-judge-v11-v1/analysis/c1-3run-v11.csv
```

위 분석 출력은 아직 생성하지 않았다. 최종 수치가 없으므로 기존 보고서의
성능 수치는 변경하지 않는다. 실제 holdout과 동결된 서비스 규칙은 이 작업의
읽기·수정 대상이 아니다.
