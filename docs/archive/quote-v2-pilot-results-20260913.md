# 원문 인용 연결 v2 첫 문항 pilot — 2026-09-13

## 결과

**STOPPED_INCOMPLETE. 요청 1회가 HTTP 400으로 거부되어 모델 출력 없이 중단했다.**
의미검증 0회, 재시도 0회, 새 GFC 점수 없음, 서비스 적용 없음이다.
이는 API 연결 검사 실패이며 생성 품질이 0점이라는 뜻이 아니다.

승인 범위는 이미 본 개발 문항 `shadow_emp_01` 하나에 추출 1회, 추출 검증 통과
시에만 의미검증 1회인 최대 2회였다. 첫 오류 즉시 중단 조건에 도달했으므로
남은 호출 여유를 다른 요청·모델·키·재시도에 사용하지 않았다.

| 항목 | 관측값 |
|---|---|
| 실행 시각 | 2026-09-13 22:27:59–22:28:00 KST |
| 추출 요청 모델 | `gemini-3.5-flash-lite` |
| 의미검증 예정 모델 | `gemini-3.1-flash-lite` — 실행 안 함 |
| provider 시도 / HTTP 200 | 1 / 0 |
| 반환 오류 | HTTP 400, `INVALID_ARGUMENT` |
| 오류 상세 | 구체적인 문제 필드·schema 경로 없음 |
| 추출 출력 / 의미검증 출력 | 0 / 0 |
| 운영 성능·GFC | 미측정, `candidate_gfc=null` |

관측된 이전 프로젝트 호출 165회에 이번 거부된 요청 1회를 더하면 누적 시도는
166회다. HTTP 400에 token usage receipt는 없으므로 생성 성공 건수·과금 건수와
동일시하지 않는다. 계정 전체 일일 사용량과 무료 tier 잔여량은 확인하지 않았다.

## 실행기와 시험

새 실행기 `evidence/quote-pilot-20260913-v2/pilot.py`는 고정된 이전 transport와
v2 adapter를 재사용한다. 요청 전에 SQLite 시도 예약을 확정하며, 한 키·재시도 0·
고정 문항·각 단계 최대 1회·오류 즉시 중단을 강제한다. 요청은 준비된 메시지와
`responseJsonSchema`를 그대로 전송했다. 운영 코드·보안·Judge·gold는 변경하지 않았다.

실호출 전 첫 시험은 `run`이라는 모듈명이 다른 실험 파일과 충돌하여
`AttributeError: module 'run' has no attribute 'Budget'`로 실패했다(1 test, error 1).
첫 prepare도 같은 이유로 실패했으며 두 경우 모두 API 호출·준비 산출물 생성은
없었다. 새 실행기에서 transport를 SHA로 고정한 절대 경로로 import하도록 수정하고
회귀시험을 추가했다. 기존 파일에는 손대지 않았다.

최종 재확인: pilot **9 tests in 0.021s OK**, adapter **26 tests in 0.041s OK**;
각 failures 0, errors 0, skipped 0. `git diff --check` 통과.
전체 root unittest·lint·build는 이번에 재실행하지 않았다. 이전 root 862개/skip 6
결과를 이번 실행 결과로 재기재하지 않는다. 합성 시험 통과는 API 수용 증거가 아니다.

```sh
python3 -B -m unittest discover -s evidence/quote-pilot-20260913-v2 -p 'test_*.py'
python3 -B -m unittest discover -s evidence/quote-adapter-20260913-v2 -p 'test_*.py'
git diff --check
```

준비 명령은 `python3 -B evidence/quote-pilot-20260913-v2/pilot.py prepare`였다.
실제 실행은 다음 명령으로 한 번만 수행했고 exit 2로 종료했다. 아래는 감사 기록이며
재실행 지시가 아니다. 기존 live 경로를 재사용하는 자동 resume은 허용하지 않는다.

```sh
python3 -B evidence/quote-pilot-20260913-v2/pilot.py live --manifest-sha256 cb6438ca3f09ad7ce7ca3c510164b0f256f8a456d0ff8abaaf4baf7eaeeff0c1 --authorize I_APPROVE_QUOTE_V2_ONE_CASE_TWO_ATTEMPTS
```

## 추가 호출 없는 진단

`investigate` 절차로 저장 요청과 오류를 보존하고 공식 문서를 대조했다. 아직
HTTP 400의 **정확한 원인은 미확정**이다. 응답이 포괄적인 invalid argument만
반환하므로 아래 관측만으로 원인을 확정하거나 수정 완료라고 주장하지 않는다.

- 저장된 전송 body의 정규 JSON UTF-8 크기: 24,074 bytes.
- 그 안의 responseJsonSchema: 8,700 bytes, schema 객체 113개, 최대 깊이 11.
  깊이는 schema root를 1로 두고 properties/items/anyOf 등 자식 schema를 따라 센다.
- `minLength`와 `maxLength`가 각각 72곳이다. 이 두 키는 확인한 generateContent
  schema 지원 목록에 없었다. 다만 문서는 JSON Schema 전체를 전달할 수 있어도
  모든 기능을 지원하지는 않는다고 설명한다. 따라서 목록에 없는 키가 반드시
  400을 일으킨다고 단정할 수는 없다.
  [Google GenerationConfig 문서](https://ai.google.dev/api/generate-content#v1beta.GenerationConfig)
- Google은 크거나 깊게 중첩된 schema가 거부될 수 있다고 설명한다. 위 크기·깊이가
  해당 모델의 한도를 넘었는지 여부는 확인되지 않았다.
  [Google structured outputs 문서](https://ai.google.dev/gemini-api/docs/structured-output)
- 요청의 API 버전·모델·기능 조합도 대안 가설이다. 이전 v1의 같은 추출 모델 호출은
  HTTP 200이었으나 요청 형식이 달라 이번 schema 수용의 대조군으로 충분하지 않다.
- HTTP 400은 자동 재시도 대상으로 보지 않는 공식 안내와 승인 조건에 따라 멈췄다.
  [Google troubleshooting 문서](https://ai.google.dev/gemini-api/docs/troubleshooting)

현재 가장 먼저 점검할 부분은 **로컬 엄격 검증용 schema를 provider 입력에 그대로
전달한 연결부의 호환성**이다. 모델의 의미 능력이나 서비스 검색 병목을 이번
거부 응답으로 평가할 수 없다. 서비스 코드를 고치거나 v2 schema를 덮어쓰지 않았다.

후속 제안(미구현·미실행): 별도 버전에서 provider 전송용 schema와 host의 완전한
검증 schema를 구분하고, 제거한 전송 제약도 host에서 계속 검사하도록 한다.
우선 오프라인 차이·회귀검사를 수행하고, 추가 실호출은 새 승인 아래 최소 합성
대조 요청부터 수행해 원인 가설을 확인한다. 전송용 제약 분리는 모델 출력 분포를
바꿀 수 있으므로 기존 요청과 동일 조건의 성능 결과로 합산하지 않는다.

## 산출물과 무결성

모든 새 실행 산출물은 `processed/eval/preflight-20260913/quote-adapter-v2/` 아래에
있다. source pin **586개**, live 출력 **6개 + 목록 자체 1개**의 SHA와 파일 집합을
검증했다. runtime에 기록된 전송 body SHA와 실제 저장 body SHA도 일치했다.
아래 request 파일 SHA는 전체 wrapper의 SHA이고, 그 내부 전송 body SHA
`ca1c74f63af58b89937e304b49579d5ad79627c633ef9968f51f5e2908d1c52b`와 구분한다.

| 산출물 | SHA-256 |
|---|---|
| `evidence/quote-pilot-20260913-v2/pilot.py` | `492c5bc341ee498f6c5d02619b539503d7229c036402a45753508ad21cd73167` |
| `evidence/quote-pilot-20260913-v2/test_pilot.py` | `e0bc40a3da20be2c833ec4b8c62c4cf8e12880a543ae6eee30c25b76d56a7a4b` |
| `pilot-preparation-v1/manifest.json` | `cb6438ca3f09ad7ce7ca3c510164b0f256f8a456d0ff8abaaf4baf7eaeeff0c1` |
| `pilot-live-v1/run.json` | `0ea1a911584cc99cb26e3220543449d8f4e3291f91ba8863ad6171163fb57d2c` |
| `pilot-live-v1/provider-attempts.sqlite` | `6ba8c12e0e41cbfe00bfa84fcdce8447d5a1cf4ea366786ce4617675f8002697` |
| `pilot-live-v1/shadow_emp_01--extract.request.json` | `20ac17d63b4da676f333d92d337977349fe6a903ac5fe7929f1f690c01f88444` |
| `pilot-live-v1/shadow_emp_01--extract.http-error.txt` | `b01105ed229707571186fdc753a734401dcd3dca044538c55285824604725bd3` |
| `pilot-live-v1/error.json` | `2c4aa1adf2f519e70826ec622321c79dbe1b96c1de6a7fd96dc00c67dd49b7ab` |
| `pilot-live-v1/completion.json` | `65a5f889f1b9b2da2bd6524d3d4f367aaf955da7782e612136bb6d90cbe19e4f` |
| `pilot-live-v1/output-sha256.json` | `92b43d0c2fbf0085de74fb896ae4b6533fe897f31a846674b4d9c92d20e41e13` |

실제 holdout 열람 0, git stage/commit/tag/push/branch 명령 0이다. 인증 키는 승인된
실행에서 메모리로만 사용했고 산출물·로그에 기록하지 않았다. 기존 결과 파일과
고정 코드에는 손대지 않았으며, 이 기록을 최종보고서의 성능 향상 수치로 쓰지 않는다.
