# 1인 검수: 저장 청크와 HWP 변환 미리보기

2026-09-14. 기존 검수 화면에 추가했으며 서비스 검색·생성·Judge 코드는 바꾸지 않았다.

## 사용법

1. 현재 검수 탭에서 **지금 저장 → 저장 확인**을 확인한 뒤 새로고침한다.
2. **저장 청크 보기**로 해당 문서의 cascade 저장 청크를 먼저 읽는다. 각 청크를 펼치면 전체 텍스트·청크 ID·파서·기록된 페이지가 보인다. 인용문이 정확히 포함된 청크는 자동으로 펼친다.
3. HWP에는 **여기서 HWP 변환본 보기**가 추가된다. PDF 원본 미리보기와 기존 파일 위치 보기는 유지한다.
4. 청크나 변환본만으로 표의 행·열, 예외 조건, 원문과의 일치를 확신할 수 없으면 원본을 확인한다. 판단할 수 없다면 해당 확인 항목을 체크하지 않고 보류 이유를 기록한다.

청크 읽기를 원문 확인으로 자동 판정하지 않는다. 보기 버튼은 검수 체크·메모·PASS 여부를 변경하지 않는다. Agent는 실제 사용자 입력을 작성하지 않았다.

## 무엇을 보여주는가

- 청크: 질문으로 재검색하거나 새로 생성하지 않는다. 정답지의 문서 ID와 원문 경로를 기존 cascade 인덱스에 대조한다. 원문 파일의 현재 SHA를 정답지와 확인하지만, 이것만으로 청크 파싱의 정확성이나 완전성을 증명하지는 않는다. 해당 문서 전체 청크이지 과거 답변 생성 시 실제로 검색된 context라고 주장하지 않는다.
- HWP: [pyhwp의 HTML 변환기](https://pyhwp.readthedocs.io/en/latest/converters.html)를 로컬 전용 환경에서 실행한다. 해당 변환기는 실험적 기능이며, 원문과 같은 쪽 배치·글꼴·모든 객체 보존을 보장하지 않는다. 표·그림 집계는 변환 HTML 기준이지 원본과의 정확도 평가가 아니다.
- 원본 파일은 변경하지 않는다. 새로운 변환 캐시는 `processed/reviews/single-reviewer-gold-20260914-v1/hwp-previews-v1/`에 원문 SHA와 변환 코드 SHA로 구분해 저장한다. 기존 캐시는 덮어쓰지 않는다.
- 변환 subprocess는 `sandbox-exec`로 네트워크와 임시 변환 디렉터리 밖 파일 쓰기를 차단한다. CPU 30초/벽시계 45초, 원본20MiB, 결과32MiB 제한을 둔다. 암호·미지원 형식·오류는 실패로 표시하고 다른 뷰어나 외부 업로드로 우회하지 않는다.
- HTML은 허용 태그/속성, 로컬 CSS와 확인 가능한 래스터 그림만 제공한다. 외부 링크·스크립트·이벤트를 제거하고, 네트워크 차단 CSP와 권한 없는 sandbox iframe으로 표시한다. HTML을 검수 페이지의 innerHTML에 삽입하지 않는다.

## 검증

- 원문 27개: 저장 청크 **939개** 연결, 표시 한도에 의한 잘림0.
- HWP **7/7** 변환 및 인증된 로컬 HTTP 응답 성공.
- 최종 합성 회귀 **33 tests in1.673s, OK; failure0/error0/skip0**.
- JavaScript 문법 검사와 `git diff --check` 통과.
- 실제 사람 저장본은 재시작 전후 revision3 / SHA `d1934a4427a26f27d261cd993f00fadffd7e61456647b4191404ee9ae8211119` 보존.
- 정본 결과: `processed/reviews/single-reviewer-gold-20260914-v1/live-preview-verification-v1.json`, SHA `ea95c9e372336dde33eb5bbc9bf164db96127a76d843c3b377bb6a7f7548d7d3`.
- 실제 문항/정답/메모는 Agent에게 출력하지 않았다. 로컬 서버/검증 프로그램이 허용된 자료를 처리하고 모델에는 건수·해시·오류 유형만 반환했다. 실제 holdout 원문 화면의 시각적 대조를 Agent가 완료했다고 주장하지 않는다.
- 전체 서비스 테스트·lint/build·생성/Judge 평가는 이번 UI 변경에서 실행하지 않았다. LLM API0회.

## 재현

기존 서비스 의존성과 별개의 로컬 환경이다. [PyPI pyhwp](https://pypi.org/project/pyhwp/)와 전용 requirements를 사용했다. pyhwp는 AGPLv3+ 패키지이며 서비스에 합치거나 배포하지 않았다.

```sh
python3 -m venv processed/tools/hwp-preview-20260914-v1
uv --no-cache pip install --python processed/tools/hwp-preview-20260914-v1/bin/python --index-url https://pypi.org/simple -r evidence/single-reviewer-20260914-v1/preview-requirements.txt
processed/tools/hwp-preview-20260914-v1/bin/python -B -m unittest discover -s evidence/single-reviewer-20260914-v1 -p 'test_*.py'
node --check evidence/single-reviewer-20260914-v1/app.js
python3 -B evidence/single-reviewer-20260914-v1/server.py --port 8772
```

기존 저장소를 삭제하거나 새로 초기화하지 않는다. 동일 포트 서버가 실행 중이면 중복 실행하지 않는다. `verify_previews.py`는 `--report-name preview-verification-vN.json`으로 **새 경로**만 사용한다. 최초 live 검증 보고서는 불변이며 `verify_live_previews.py`의 출력을 기존 파일에 덮어쓰지 않는다.

## 예전 Shadow14와 현재 검수 구분

[Shadow14 안내](shadow14-human-review-20260907.md)의 대상은 Shadow60에서 뽑은 실패11+성공대조3의 진단 표본이다. 당시 기본 검토량은 run1 14답변, run2/3는 선택 사항이었다. 실패 중심 표집이므로 전체 성능 또는 정식 calibration을 대체하지 않는다.

현재 [1인 평가 개정안](evaluation-amendment-single-reviewer-20260914.md)은 사전 정답지36문항과 생성 후 답변63개(Core54+Challenge9)를 구분한다. 최종평가를 위해 예전 Shadow14를 추가로 전부 완료해야 한다는 요구는 없다. 미완료 Shadow14를 완료로 보고하거나 이미 작성한 판정을 삭제하는 뜻도 아니다. 현재 우선순위는 새 화면의 정답지 검수다.
