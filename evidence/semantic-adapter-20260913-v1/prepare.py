"""Prepare 42 DEV extraction requests and an 84-call ceiling; API calls are zero."""
from __future__ import annotations

import argparse
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
import adapter as a
from test_adapter import request, extraction, review

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASE = ROOT / "processed/eval/preflight-20260913/semantic-adapter-v1"
PRIOR = ROOT / "processed/eval/preflight-20260913/evidence-contract-v1/run-v1"
PINS_SHA = "ad846f1571975f52c877a253a5e85e1cec31fb660e0df964906bf1020c5e9660"
REPLAY_SHA = "98d35c7287e506d2d0fa8593ac653b9bfa79ff0cf16a0c48b594827dfa5b63e5"
EXPERIMENT = ROOT / "processed/eval/preflight-20260913/scope-bound-c3-v1"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def mock_chain(*, wrong, false_positive=False):
    req = request(wrong=wrong)
    proposed = extraction(req, mislabeled=wrong)
    semantic = a.build_semantic_request(req, a.encode(proposed))
    judged = review(semantic, verdict="entailed" if not wrong or false_positive else "contradicted")
    return {"synthetic_only": True, "real_model_calls": 0, "extraction_request": req,
            "mock_extraction_response": proposed, "semantic_request": semantic, "mock_semantic_response": judged,
            "decision": a.combine(req, a.encode(proposed), a.encode(judged))}


def prepare(out):
    if out.exists() or out.resolve().parent != BASE or any(p.is_symlink() for p in (out, *out.parents)):
        raise ValueError("new_nonsymlink_experiment_path_required")
    pin_path, replay_path = PRIOR / "input-sha256.json", PRIOR / "frozen-baseline-replay.json"
    if sha(pin_path) != PINS_SHA or sha(replay_path) != REPLAY_SHA:
        raise ValueError("prior_artifact_changed")
    pins = read(pin_path)
    pins.update({str(pin_path): PINS_SHA, str(replay_path): REPLAY_SHA})
    pins.update({str(p): sha(p) for p in HERE.glob("*.py")})
    if any(sha(path) != expected for path, expected in pins.items()):
        raise ValueError("frozen_input_changed")
    rows = read(replay_path)
    if len(rows) != 42 or len({r["case_id"] for r in rows}) != 42:
        raise ValueError("expected42_unique_development_cases")
    outputs, slots = {}, []
    for row in rows:
        cid = row["case_id"]
        path = EXPERIMENT / "live-v1" / ("c1-sec-control--" + cid + ".answers.jsonl")
        if str(path) != row["source_answer_path"] or sha(path) != row["source_answer_sha256"]:
            raise ValueError("answer_input_binding_mismatch")
        answer = read(path)
        if answer["case_id"] != cid:
            raise ValueError("case_id_mismatch")
        # Only raw content enters the extractor; never use final answer labels,
        # rejected/supported dispositions, required claims, Judge scores or gold.
        trace = answer["evaluation_trace"]
        req = a.build_extraction_request(answer["query"], trace["raw_draft"], trace["retrieval_stages"]["final_contexts"])
        messages = a.model_messages(req)
        filename = "extract--" + cid + ".request.json"
        outputs[filename] = {"record_type": "prepared_request_not_executed", "request": req, "messages": messages}
        slots.append({"case_id": cid, "request_file": filename, "request_id": req["request_id"],
                      "source_answer_path": str(path), "source_answer_sha256": sha(path),
                      "draft_units": len(req["payload"]["data"]["draft_units"]),
                      "source_count": len(req["payload"]["data"]["sources"]),
                      "wire_utf8_bytes": len(a.encode(messages).encode())})
    streams = io.StringIO()
    tested = unittest.TextTestRunner(stream=streams, verbosity=1).run(
        unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py"))
    if not tested.wasSuccessful():
        raise ValueError("tests_failed:" + streams.getvalue())
    previous = read(EXPERIMENT / "preparation-v1/manifest.json")
    plan = {"approved": False, "status": "AWAITING_EXPLICIT_EXTERNAL_CALL_APPROVAL", "external_calls_completed": 0,
            "one_api_key_only": True, "extraction_calls_max": 42, "semantic_review_calls_max": 42,
            "total_attempt_cap": 84, "retries": 0, "inter_call_seconds": 15,
            "extraction_model_proposal": previous["generation_model"],
            "semantic_model_proposal": previous["judge_model"],
            "model_names_from": str(EXPERIMENT / "preparation-v1/manifest.json"),
            "model_availability_rechecked": False, "max_output_tokens_each_stage_proposal": 8192,
            "fresh_generation_calls": 0, "final_judge_v11_calls": 0,
            "known_previous_experiment_calls": 164, "known_project_total_if_all84_used": 248,
            "account_daily_usage_verified": False, "input_tokens_measured": False,
            "quota_behavior": "Stop on quota/rate-limit/error; no retry, model/key switch or paid fallback.",
            "semantic_stage_dependency": "Only after a valid extraction for that exact request; at most one review per valid case.",
            "critical_limit": "Same-family models and separate prompts do not establish statistical independence or human calibration."}
    summary = {"status": "PREPARED_OFFLINE", "cases": len(slots), "extraction_requests_ready": len(slots),
               "semantic_requests_ready": 0, "semantic_request_builder_ready": True,
               "raw_line_units": sum(s["draft_units"] for s in slots),
               "total_wire_utf8_bytes": sum(s["wire_utf8_bytes"] for s in slots),
               "largest_request_wire_utf8_bytes": max(s["wire_utf8_bytes"] for s in slots),
               "external_calls": 0, "unit_tests": {"total": tested.testsRun, "failures": len(tested.failures),
               "errors": len(tested.errors), "skipped": len(tested.skipped)}, "input_pin_count": len(pins),
               "candidate_gfc": None, "human_review": False, "eligible_for_service": False,
               "limitation": "Prepared real raw-input requests, but all response-chain tests are mocks; extraction and semantic accuracy unmeasured."}
    outputs.update({"manifest.json": {"version": a.VERSION, "slots": slots, "call_plan": plan},
                    "summary.json": summary, "input-sha256.json": pins, "test-output.txt": streams.getvalue(),
                    "mock-positive.json": mock_chain(wrong=False), "mock-contradiction.json": mock_chain(wrong=True),
                    "mock-semantic-false-positive.json": mock_chain(wrong=True, false_positive=True)})
    outputs["report.md"] = """# 자연어 추출 연결·독립 입력 의미검증 준비

상태: 오프라인 구현/요청 준비 완료. **API 호출 0회, 실제 모델 성능 미측정**.

## 달라진 부분

새 adapter는 모델의 JSON을 그대로 신뢰하지 않는다. 중복/누락 key, 잘못된 자료형,
다른 실행의 request ID, 누락 문장/인용, 조작된 원문 위치, 불일치 source를 거부한다.
조건 dictionary 순서를 정렬해 직전 계약의 tuple 순서 민감성도 입력 단계에서 해소했다.
기존 계약/서비스/보안/Judge 코드는 수정하지 않았다.

추출 요청은 원래 질문·원시 초안·검색 source의 ID/제목/본문만 받는다. 원시 초안의
비어 있지 않은 각 줄에 원문 offset을 부여해 전부 응답하게 한다. 문장 하나에 사실이
여러 개일 수 있으므로 줄 coverage는 의미적 완전성을 보장하지 않는다.
gold/required_claims, 정답률, 기존 허용/거부 판정이나 Judge 결과는 입력에서 제외한다.

별도 의미검증 요청은 추출기의 구조화 태그·설명·자체 판정을 제외한다. 원래 질문과
초안, 모든 원문 sources, 제안된 출처 ID만 본다. 같은 원문에서 추출기의 지급/환수
태그를 바꿔도 이 검증 요청은 동일해야 한다는 테스트를 넣었다. 이는 **입력 분리**이며
같은 계열 모델의 편향 독립이나 실제 판정 정확성이 확보됐다는 뜻은 아니다.

각 제안된 인용은 별도로 검토한다. 제목만 인용해서 지지됨으로 처리할 수 없다.
전체 지지 판정과 부분 판정이 모순되면 거부한다. 구조/의미 두 조건을 만족해도
현재 결과는 offline_candidate_only이며 eligible_for_service=false, candidate_gfc=null이다.

## 검증의 한계도 보존

`mock-contradiction.json`: 잘못 지급으로 구조화한 경우에도 독립 입력의 원문 검토가
contradicted를 반환하면 후보로 채택하지 않는 연결 동작을 확인한다. **실제 LLM이
이 반례를 맞혔다는 실험이 아니라 합성 응답을 넣은 소프트웨어 테스트**다.

`mock-semantic-false-positive.json`: 의미검증 모델까지 잘못 entailed라고 응답하면
연결 검사만으로 의미 오류를 보장해 잡을 수 없다는 한계도 저장했다. 따라서 현재
서비스에는 적용하지 않았다. 실제 추출/의미검증 성능과 추가 인용 precision을
측정해야 한다. 새로운 GFC나 성능 향상 수치는 아직 없다.

## 실제 개발42 입력의 실행 준비

각 `extract--<case_id>.request.json`은 실제 C1 core42 원시 입력에서 만든 미실행
요청과 provider-neutral messages다. 첫 단계 결과가 아직 없어 실제 semantic
요청은 생성하지 않았다. 올바른 추출 응답이 들어오면 request binding을 확인한
뒤 `build_semantic_request`가 별도로 만든다. 완성된 실제 자연어 추출기가 아니라
그 요청/응답 연결부와 검증 규약이 준비된 상태다.

실행 승인 시 계획: 한 키로 추출 최대42회 + 의미검증 최대42회 = **최대84회**,
재시도0, 15초 간격. 오류/한도 응답에서 중단하고 다른 키·모델·유료 경로로 바꾸지
않는다. 앞선 실험의 모델명을 제안값으로 유지했지만 이번 준비에서는 모델 가용성,
계정의 전체 일일 사용량, token 한도를 확인하지 않았다. 알려진 직전164회와 더하면
프로젝트 관측치로 최대248회이며 다른 작업의 계정 사용량까지 포함한 보장은 아니다.
새 생성 및 최종 Judge v11 호출은 포함하지 않는다. 호출 transport 실행기는 아직 없다.
실제 모델 호출은 별도 명시 승인 전에는 수행하지 않는다.

## 재현

`python3 -B -m unittest discover -s evidence/semantic-adapter-20260913-v1 -p 'test_*.py'`

`python3 -B evidence/semantic-adapter-20260913-v1/prepare.py --out processed/eval/preflight-20260913/semantic-adapter-v1/preparation-v1`

기존 출력 경로는 덮어쓰지 않는다. 재실행 시 동일 base 아래 새 경로를 지정한다.

## 기계 집계

""" + "```json\n" + encoded(summary) + "```\n"
    if any(sha(path) != expected for path, expected in pins.items()):
        raise ValueError("inputs_changed_during_preparation")
    out.mkdir(parents=True, exist_ok=False)
    for name, value in outputs.items():
        with (out / name).open("x", encoding="utf-8") as handle:
            handle.write(value if isinstance(value, str) else encoded(value))
    inventory = {name: sha(out / name) for name in outputs}
    with (out / "output-sha256.json").open("x", encoding="utf-8") as handle:
        handle.write(encoded(inventory))
    print(encoded(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    prepare(parser.parse_args().out)
