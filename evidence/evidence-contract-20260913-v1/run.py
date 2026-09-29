"""Offline experiment runner: typed fixtures + unchanged C1 replay, never API calls."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest


def offline_guard(event, args):
    if event.startswith(("socket.", "urllib.")):
        raise PermissionError("offline_only")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0]))
        if path.name.startswith(".env") or ("holdout" in str(path).lower() and path.suffix != ".py"):
            raise PermissionError("protected_data_disabled")


sys.addaudithook(offline_guard)
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "evidence/stage-diagnosis-20260913-v1"))
import diagnose as frozen
from contract import Value, Source, verify
from fixtures import ORIGINAL, ORIGINAL_SHA, SCOPE, fact, known_probes
from test_contract import extra_probes

BASE = ROOT / "processed/eval/preflight-20260913/evidence-contract-v1"
PACKET = frozen.BASE / "packet-v1/packet.json"
PACKET_SHA = "75805c95ce92352ec43d35d31c5acd59916f4b4b5f36a27729d46f9479ed7c59"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def run(out):
    if out.exists() or out.resolve().parent != BASE or any(p.is_symlink() for p in (out, *out.parents)):
        raise ValueError("new_nonsymlink_experiment_path_required")
    pins = frozen.checked_inputs()
    pins.update({str(PACKET): PACKET_SHA, str(ORIGINAL): ORIGINAL_SHA})
    pins.update({str(p): sha(p) for p in HERE.glob("*.py")})
    if any(sha(p) != expected for p, expected in pins.items()):
        raise ValueError("input_pin_changed")
    # Typed inputs and expected labels materialized before invoking the contract.
    groups = {"known_29_manually_typed": known_probes(), "additional_contract_26": extra_probes()}
    fixture_values = {name: [asdict(p) for p in probes] for name, probes in groups.items()}
    fixture_sha = hashlib.sha256(encoded(fixture_values).encode()).hexdigest()
    results, group_counts = [], {}
    for name, probes in groups.items():
        rows = []
        for probe in probes:
            verdict = verify(probe.claim, probe.query_scope, probe.sources, probe.facts,
                             requested_sources=probe.requested_sources)
            rows.append({"group": name, "probe_id": probe.probe_id, "expected_contract_match": probe.expected_match,
                         "verdict": asdict(verdict), "passes_contract_expectation": (verdict.status == "matched") == probe.expected_match})
        group_counts[name] = {"total": len(rows), "expected_match": sum(p.expected_match for p in probes),
                             "expected_nonmatch": sum(not p.expected_match for p in probes),
                             "unexpected_match": sum(not r["expected_contract_match"] and r["verdict"]["status"] == "matched" for r in rows),
                             "unexpected_nonmatch": sum(r["expected_contract_match"] and r["verdict"]["status"] != "matched" for r in rows),
                             "status_counts": dict(Counter(r["verdict"]["status"] for r in rows))}
        results.extend(rows)
    # A red-team witness for the boundary we explicitly do NOT solve: a parser
    # lies about the operator while supplying a valid exact quote.
    source = Source("semantic-tag-witness", "제7회 ALPHA 공모전", "취소자는 참가비 환수 대상입니다.")
    mislabeled = fact(source, "fee_action", "참가비", Value("action", "참가비", "", "payment"),
                      conditions={"audience": "취소자"}, operator_quote="환수")
    witness = {"source": asdict(source), "mislabeled_proposal": asdict(mislabeled),
               "verdict": asdict(verify(mislabeled.assertion, SCOPE, (source,), (mislabeled,))),
               "limitation": "환수 quote를 지급/payment로 잘못 태깅하면 구조상 matched. 의미 검증은 해결되지 않았고 서비스 사용은 불허."}
    replay = []
    for item in frozen.read(PACKET)["items"]:
        original = frozen.read(item["source_answer_path"])
        stage = frozen.lineage(original)
        replay.append({"case_id": item["case_id"], "source_answer_path": item["source_answer_path"],
                       "source_answer_sha256": sha(item["source_answer_path"]), "baseline_exact": True,
                       "claim_count": len(stage["claims"]), "candidate_adapter_available": False,
                       "candidate_replayed": False, "candidate_gfc": None})
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py")
    tested = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    if not tested.wasSuccessful():
        raise ValueError("unit_tests_failed:\n" + stream.getvalue())
    if len(replay) != 42 or any(sha(p) != expected for p, expected in pins.items()):
        raise ValueError("sample_or_input_drift")
    summary = {"status": "CONTRACT_IMPLEMENTED_NOT_DEPLOYABLE", "external_calls": 0,
               "service_changed": False, "human_review": False, "groups": group_counts,
               "unit_tests": {"total": tested.testsRun, "skipped": len(tested.skipped), "failures": len(tested.failures), "errors": len(tested.errors)},
               "frozen_baseline_exact": len(replay), "real_candidate_replays": 0,
               "candidate_gfc": None, "semantic_tag_witness_matched": witness["verdict"]["status"] == "matched",
               "semantic_tag_witness_eligible_for_service": witness["verdict"]["eligible_for_service"],
               "typed_fixtures_sha256": fixture_sha, "input_pin_count": len(pins),
               "limitation": "Hand-authored typed extraction fixtures. Not natural-language extraction, service safety, GFC improvement, or independent holdout evidence."}
    report = """# 근거 연결 구조 v1 구현 결과

상태: 계약/자료구조 구현 완료, **서비스 통합 불가**. API 호출 0회. 기존 서비스·보안·Judge·gold 변경 없음.

## 구현한 부분

- 질문 범위와 사실 범위(기관/프로그램/회차/방향/전형 등)의 정확한 결합.
- 속성·값의 타입/단위·상한/하한·의무/행위 방향·조건을 별도 필드로 비교.
- source ID와 제목/본문을 포함한 SHA, 원문 인용과 Unicode offset 검증.
- 본문/제목의 주체·회차 충돌 및 서로 다른 source의 값 대여 거부.
- TSV 표의 같은 열 header와 값, 같은 행의 주체 결합. 병합 셀·PDF 복원은 미구현.
- 같은 범위의 상충 근거가 있으면 uncertain. 마지막 source나 게시일로 자동 선택하지 않음.
- 최소 근거 선택 및 요청한 추가 출처 각각의 지지 여부 검사.

## 무엇을 시험했는가

기존 합성29건을 사람이 아니라 **AI가 수동으로 구조화한 fixture**에서 계약 검사했다.
원래 자연어·기대값은 보존했지만 새 검증기에 자연어만 넣은 결과가 아니다. 따라서 이전
검증기의 오허용5건과 새 계약의 결과를 같은 조건의 성능 A/B로 비교하지 않는다.
추가26건은 범위/조건, 잘못된 인용, SHA/offset, 표 행·열, 상충 근거의 계약 시험이다.
전체 테스트 소스와 기대값은 실행 전에 작성했고 fixture SHA를 결과에 고정했다.

기존 C1 core42는 동결 코드로 다시 재생해 원본 answer·citation·claim·보안 요약 일치를
검증했다. **새 구조로의 자동 자연어 adapter는 아직 없으므로 후보42 재생이나 GFC를
계산하지 않았다.** 기존 AI 정답 대조나 gold를 서비스 입력으로 사용하지 않았다.

## 해결하지 못한 경계도 재현

원문은 `취소자는 참가비 환수 대상입니다.`인데 추출기가 관계를 `payment`로 잘못
표시하면 정확한 quote가 있어도 이 계약은 구조적으로 matched를 반환한다.
`semantic-tag-witness.json`이 이를 보존한다. 이 1건을 정상적인 의미 안전성 통과로
세지 않았다. 모든 결과의 `eligible_for_service=false`, `semantic_extraction_verified=false`를
고정했다. 출처 무결성과 구조 일치는 자연어 의미 지지의 충분조건이 아니다.

따라서 추가 의미 라벨 검증 없이 이 모듈을 서비스 허용 게이트로 붙이는 것은 금지한다.
예외 regex로 이 반례만 막는 수정도 하지 않았다. `matched`는 제안된 구조가 서로
일치한다는 뜻이고 `mismatch`는 구조 불일치다. 어느 쪽도 현실의 참/거짓 판정이 아니다.

## 다음 단계의 정확한 범위

1. 원시 질문·초안·저장 contexts만 받는 추출 adapter를 별도 계약으로 구현한다.
   gold/required_claims/AI 정답 분류는 입력하지 않는다. 각 scope/relation/operator와
   생략된 조건의 추출 정확도를 별도 검증해야 한다.
2. 추출기와 독립적으로 **원문→자연어 claim**의 의미 지지를 확인한다. 자기 출력의
   태그끼리 일치하는지 검사하는 것만으로 통과시키지 않는다. 의미 미검증/충돌은 보류한다.
3. 고정 초안의 검증기 단일요인 비교를 먼저 한다. 새로 살린 문장의 각 인용도 대조한다.
   생성 prompt 변경과 함께 시험하지 않는다. 새로운 LLM 호출은 모델·payload·호출 상한을
   먼저 정해 별도 승인 후 실행한다. 이번 구현에서 호출한 것은 없다.

이 결과는 개발용 소프트웨어 계약 시험이다. 새 공격 성공률, 사람 검수, 독립 holdout,
서비스 정답률 향상으로 보고하지 않는다. 기존 GFC 13/42는 변경하지 않는다.

## 재현

`python3 -B -m unittest discover -s evidence/evidence-contract-20260913-v1 -p 'test_*.py'`

`python3 -B evidence/evidence-contract-20260913-v1/run.py --out processed/eval/preflight-20260913/evidence-contract-v1/run-v1`

기존 출력 경로는 덮어쓰지 않는다. 재실행 시 동일 base 아래 새 경로를 지정한다.

## 기계 집계

""" + "```json\n" + encoded(summary) + "```\n"
    values = {"summary.json": summary, "typed-fixtures.json": fixture_values, "results.json": results,
              "semantic-tag-witness.json": witness, "frozen-baseline-replay.json": replay,
              "test-output.txt": stream.getvalue(), "input-sha256.json": pins, "report.md": report}
    out.mkdir(parents=True, exist_ok=False)
    for name, value in values.items():
        with (out / name).open("x", encoding="utf-8") as handle:
            handle.write(value if isinstance(value, str) else encoded(value))
    print(encoded(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    run(parser.parse_args().out)
