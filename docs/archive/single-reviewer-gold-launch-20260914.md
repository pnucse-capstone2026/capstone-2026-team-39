# 1인 정답지 검수 화면 실행 기록

2026-09-14 사용자가 즉시 실행과 작성 화면 표시를 요청해,
[1인 평가 개정안](evaluation-amendment-single-reviewer-20260914.md)의 **사전 gold 검수**
화면을 구현·실행했다. 기존 DEV 답변으로 최종 holdout을 대체하지 않았다.

## 현재 사용

- 주소: `http://127.0.0.1:8772/` (접속 토큰이 포함된 링크는 대화에서 전달).
- 실제 36문항을 사용자가 검토하도록 로컬 서버가 읽어 표시한다.
- 실행 직후 PASS 0/36, 기록된 판정 0, revision 0을 질문 본문을 보지 않고 확인했다.
- 생성 답변 63개 채점·Judge 호출·일치도 집계는 아직 시작하지 않았다.
- 코드는 `evidence/single-reviewer-20260914-v1/`에 분리했다.
- 입력 내용은 SQLite와 append-only revision JSON으로 이중 저장한다.
- 저장 위치: `processed/reviews/single-reviewer-gold-20260914-v1/`.
- 원본 holdout cases, 기존 검토 패킷과 reviewer A/B 파일, 기존 2인 gate는 수정하지 않는다.

## 사람이 할 일

검수자 이름/ID를 적고 문항별 원문을 연다. 실제 확인한 4항목만 체크한다.
문제가 없으면 PASS를 선택하고, 문제는 수정 필요/보류와 메모로 기록한다.
`저장 확인 · revision …`가 표시되는지 확인한다. 모든 문항 PASS 이후에만
검수 확정을 누를 수 있다. 확정본은 변경할 수 없고 정정은 별도 기록한다.

다운로드 버튼을 누르지 않아도 서버에 저장한다. 긴급 백업은 아직 저장되지 않은
현재 입력을 별도로 내려받는다. 다른 탭의 오래된 데이터는 최신 revision을
덮어쓰지 못한다. 확정된 1인 결과는 기존 2인 signoff가 아니다.

## 검증

합성 데이터만으로 저장·서버 재구성 복원·동시 저장 충돌·동일 요청 재시도·
출력 덮어쓰기 금지·불완전 PASS 거부·필수 메모·입력 SHA·확정 후 수정 거부·
백업 실패 후 DB 복구·변조·심볼릭 링크·JSON 중복 키·원문 경로 제한·
HTTP 인증/Origin/저장/내보내기를 검증했다.

```sh
python3 -B -m unittest discover -s evidence/single-reviewer-20260914-v1 -p 'test_*.py'
node --check evidence/single-reviewer-20260914-v1/app.js
python3 -B evidence/single-reviewer-20260914-v1/server.py --synthetic --port 8773
python3 -B evidence/single-reviewer-20260914-v1/server.py --port 8772
git diff --check
```

첫 합성 테스트 **16개, 0.587초, 실패0/오류0/skip0**. 최초 샌드박스 시험은
localhost bind가 차단돼 오류1이었다. 그때 테스트 클래스 상속으로 중복 실행된
시험을 정리한 뒤 localhost 실행 권한으로 재실행했다. 실제 데이터 오류가 아니다.
JS 구문 검사와 git diff --check도 통과했다.

실제 화면을 연 뒤 원문 경로 메타데이터 점검에서 `downloads/` 폴더가 새 화면의
다운로드 허용 목록에 없어 27개 원문이 연결되지 않은 것을 발견했다. 모두 실제
로컬 파일이 존재했고 PDF9/HTML10/XLSX1/HWP7이었다. 새 검수 서버에 한해서
프로젝트 `downloads/`와 스프레드시트 다운로드를 추가하고 합성 회귀시험을
추가했다. 최종 **17 tests in0.594s, OK, failures0/errors0/skips0**.
같은 주소·저장소로 서버를 재시작해 다운로드 연결27개, 36문항, revision0,
미확정 상태를 재확인했다. 실제 브라우저를 새로고침하거나 입력을 초기화하지 않았다.
원문 내용의 타당성 검증은 사람이 수행하며 파일 다운로드 시 작성된 SHA를 검사한다.

별도 QA 저장소에만 합성 검수자와 복원 시험 메모를 입력하고 revision1 저장,
새로고침 후 같은 이름·메모·revision 복원을 브라우저에서 확인했다.
합성 화면 스크린샷으로 원문/검수 분할 화면을 확인했다. frontend-design 스킬의
원문 대조 중심 레이아웃, 네이비·블루 강조, 명확한 저장 상태 표기를 적용했다.
실제 화면은 질문 본문·정답을 캡처하지 않고 문항 버튼36개·저장 revision·
입력 폼 표시만 확인했다. 실제 사람 입력은 대신 작성하지 않았다.

## 남은 사항

이것은 실행 가능한 사전 검수 화면이다. 새 1인 **답변** 검수/일치도 도구 및
새 protocol 식별자의 final generation 실행 경로는 여전히 준비가 필요하다.
실제 gold 검수 완료, 코드/인덱스 동결, 호출 수·외부 전송 승인 확인 전에는
기존 final runner의 2인 gate를 우회하지 않는다. 서비스 품질 숫자는 바뀌지 않았다.

산출물 SHA 및 세부 실행 기록은 [progress-log](progress-log-20260901.md)의
“1인 정답지 검수 화면 구현·실행·사용자에게 표시” 절에 기록한다.
