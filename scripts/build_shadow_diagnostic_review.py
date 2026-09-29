#!/usr/bin/env python3
"""Build an offline, immutable Shadow14 diagnostic review (never a holdout review).

Only the seven explicitly pinned Shadow60 inputs are accepted. No retrieval,
generation, Judge requests, or changes to existing artifacts are performed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_shadow_generation import (  # noqa: E402
    CASES_SHA, file_sha, load_bound_run, select_cases, summarize,
)
from analyze_service_ab import load_cases  # noqa: E402
from immutable_outputs import publish_immutable_texts, reject_symlink_inputs  # noqa: E402
from service_eval_artifacts import sha256_json  # noqa: E402

CASES_PATH = "config/pnu-service-shadow60-v1.jsonl"
ANSWER_DIR = "processed/eval/preflight-20260905/shadow60-generation-v1"
JUDGE_DIR = "processed/eval/preflight-20260906/shadow60-judge-v11-v1/judge"
ANSWER_SHAS = (
    "9202f4fd9e0bf3ad8447d4f3f72ef2ddeffff0672fb8e26dc10d48970772d8c6",
    "d85a141c53e3f3b33003ed78f46f009aab11a67af408916cca39ecae53e7f599",
    "dcbc047e4b1a10005bcb2866b5b7db3c758f64148bcc02dd9fa2ad94954ccaf2",
)
JUDGE_SHAS = (
    "ee836fd7525e9b3a3053492dff4ebbc438a2c59b86816ed9009d58a5216580b8",
    "948a0565cc75b3dd4362b1b7c90a4dcbb0aae83bc7f87a0a010f6021a769b0a2",
    "9696e70080c9744f51d498870cd3af557918a2cb808e2dc911f3d7b4b25af2b1",
)
CONTROL_REASONS = {
    "shadow_adm_01": "simple / 3회 모두 GFC / 필수 근거 모두 포함",
    "shadow_emp_07": "multi / 3회 모두 GFC / 필수 근거 모두 포함",
    "shadow_intl_04": "multi / majority GFC지만 run1 비GFC / 회차 차이 대조",
}
TEMPLATE_PATH = ROOT / "scripts/templates/shadow_diagnostic_review.html"
PACKET_SCHEMA = "pnu.shadow-diagnostic-review.v1"
LABEL_SCHEMA = "pnu.shadow-diagnostic-human-labels.v1"


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def script_json(value: object) -> str:
    # The source texts are untrusted document content, not HTML or JavaScript.
    return (json.dumps(value, ensure_ascii=False, allow_nan=False)
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def select_review_rows(rows: list[dict]) -> list[dict]:
    by_id = {r["case_id"]: r for r in rows}
    if len(by_id) != len(rows) or len(rows) != 42:
        raise ValueError("expected exactly 42 unique core rows")
    if any(r["bucket"] not in {"simple", "multi"} for r in rows):
        raise ValueError("only predefined core buckets are allowed")
    if any(not r["retrieval_identical_across_runs"] for r in rows):
        raise ValueError("retrieval changed across runs")
    failures = [r for r in rows if r["gfc_majority"] is False
                and r["atomic_all_evidence_at_8_runs"] == [True, True, True]]
    if len(failures) != 11:
        raise ValueError("expected all 11 evidence-complete majority failures")
    selected = [{**r, "selection_group": "evidence_complete_majority_failure",
                 "selection_reason": "필수 근거 모두 포함@8 · 2/3 majority 비GFC 전수"}
                for r in failures]
    for cid, reason in CONTROL_REASONS.items():
        row = by_id.get(cid)
        if (row is None or row["gfc_majority"] is not True
                or row["atomic_all_evidence_at_8_runs"] != [True, True, True]):
            raise ValueError(f"invalid fixed success control: {cid}")
        selected.append({**row, "selection_group": "success_control", "selection_reason": reason})
    # Deterministic interleaving, not an ordering by score. This is not blinding.
    return sorted(selected, key=lambda r: sha256_json(["shadow14-review-v1", r["case_id"]]))


def project_run(record: dict) -> dict:
    trace = record["evaluation_trace"]
    if not isinstance(trace.get("raw_draft"), str):
        raise ValueError("raw draft trace missing")
    contexts = trace["retrieval_stages"]["final_contexts"]
    if not contexts or any(not isinstance(c.get("text"), str) for c in contexts):
        raise ValueError("retrieved text trace missing")
    context_fields = ("source_number", "rank", "chunk_id", "document_id", "text", "text_sha256",
                      "source_title", "source_url", "corpus_revision", "table_ids", "section_path")
    claim_fields = ("text", "supported", "validation_reason", "missing_critical_values",
                    "source_numbers", "source_ids", "best_score")
    citation_fields = ("source_number", "chunk_id", "document_id", "excerpt", "source_title")
    claims = []
    if not isinstance(record.get("claims"), list):
        raise ValueError("service claim trace missing")
    for claim in record["claims"]:
        projected = {k: claim.get(k) for k in claim_fields}
        seen = set()
        projected["citations"] = []
        for citation in claim.get("citations", []):
            value = {k: citation.get(k) for k in citation_fields}
            key = sha256_json(value)
            if key not in seen:
                seen.add(key)
                projected["citations"].append(value)
        claims.append(projected)
    return {
        "generation_run_id": record["generation_run_id"],
        "answer_id": record["answer_id"], "answer_sha256": record["answer_sha256"],
        "answer": record["answer"], "cited_answer": record.get("cited_answer"),
        "raw_draft": trace["raw_draft"], "sanitized_draft": trace.get("sanitized_draft"),
        "contexts": [{k: c.get(k) for k in context_fields} for c in contexts],
        "claims": claims, "postprocessing": record.get("postprocessing"),
        "judge": record["judge"],
        "judge_guard": record.get("deterministic_guard", {}),
        "judgment_id": record["judgment"]["judgment_id"],
        "judge_config_sha256": sha256_json(record["judgment"]["judge_config"]),
    }


def build_packet(cases: dict, runs: list[dict], selected: list[dict], inputs: list[dict]) -> dict:
    items = []
    for number, row in enumerate(selected, 1):
        cid = row["case_id"]
        case = cases[cid]
        items.append({
            "review_id": f"R{number:02d}", "case_id": cid, "question": case["query"],
            "bucket": case["shadow_bucket"], "category": case["category"],
            "selection": row, "required_claims": case["required_claims"],
            "runs": [project_run(run[cid]) for run in runs],
        })
    return {
        "schema_version": PACKET_SCHEMA, "date": "2026-09-07",
        "scope": "Shadow60 core 진단용 목적 표집. holdout 아님. 공식 사람 calibration/성능 추정 아님.",
        "selection_policy": {
            "failures": "core42 중 필수 근거 모두 포함@8 + majority 비GFC 전수 11문항",
            "controls": CONTROL_REASONS,
            "order": "SHA-256 canonical JSON of [shadow14-review-v1, case_id] ascending",
            "primary_run": "run1", "optional_runs": ["run2", "run3"],
            "limitation": "성공 대조군 3개는 단순·복합·회차 차이를 보기 위한 목적 표집이며 대표 표본이 아님.",
        },
        "inputs": inputs, "items": items,
    }


def label_template(packet: dict, packet_sha: str) -> dict:
    return {
        "schema_version": LABEL_SCHEMA, "packet_sha256": packet_sha,
        "reviewer": "", "purpose": "diagnostic_only_not_holdout_signoff",
        "reviews": [{
            "review_id": item["review_id"], "case_id": item["case_id"],
            "generation_run_id": run["generation_run_id"], "answer_id": run["answer_id"],
            "answer_sha256": run["answer_sha256"], "status": "PENDING",
            "support": "", "completeness": "", "postprocessing": "not_checked",
            "judge_agreement": "not_checked", "note": "",
            "judge_revealed_at": None, "labels_before_judge": None,
        } for item in packet["items"] for run in item["runs"]],
    }


def selection_csv(packet: dict) -> str:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["review_id", "case_id", "selection_group", "reason", "gfc_run1", "gfc_run2", "gfc_run3"])
    for item in packet["items"]:
        row = item["selection"]
        writer.writerow([item["review_id"], item["case_id"], row["selection_group"], row["selection_reason"],
                         row["gfc_run1"], row["gfc_run2"], row["gfc_run3"]])
    return out.getvalue()


def render_markdown(packet: dict) -> str:
    """Readable fallback for environments that cannot open the offline HTML."""
    def block(value: object) -> str:
        value = str(value or "")
        fence = "```"
        while fence in value:
            fence += "`"
        return f"\n{fence}text\n{value}\n{fence}\n"

    parts = ["# Shadow14 · run1 원문·초안·최종 답변 검토\n",
             "HTML을 열기 어려울 때 사용하는 읽기 전용 대체본입니다. 모든 내용은 저장 산출물 그대로입니다.\n"
             "14문항의 run1을 먼저 검토하세요. 사람 판정은 모두 PENDING이며 아래 Judge는 참고 기록입니다.\n"
             "원문은 당시 모델에 전달된 파싱 텍스트이지 PDF/HWP 원본 화면이 아닙니다.\n"
             "각 문항에서 근거 일치·완전성·후처리 영향과 근거 메모를 별도로 적어 전달해도 됩니다.\n"
             "이 목적 표집으로 정확도나 공식 calibration 수치를 계산하지 않습니다.\n"]
    for item in packet["items"]:
        run = item["runs"][0]
        parts += [f"\n## {item['review_id']} · {item['case_id']}\n", block(item["question"]),
                  "\n### 최종 답변 · Judge 대상\n", block(run["answer"]),
                  "\n### 모델 초안 · 후처리 이전\n", block(run["raw_draft"]),
                  "\n### 검색 원문 · 전체 context\n"]
        for context in run["contexts"]:
            parts += [f"\n출처 #{context['source_number']} · `{context['chunk_id']}`\n",
                      block(context["source_title"]),
                      "\n<details>\n<summary>저장 원문 펼치기</summary>\n",
                      block(context["text"]), "\n</details>\n"]
        parts += ["\n### 내 판정 · PENDING\n",
                  "\n- 근거 일치: \n- 답변 완전성: \n- 후처리 영향: \n- 근거 번호·판단 이유: \n",
                  "\n<details>\n<summary>내 판단 후 Judge·정답 기준·후처리 로그 확인</summary>\n",
                  block(json_text({"judge": run["judge"], "judge_guard": run["judge_guard"],
                                   "required_claims": item["required_claims"], "service_claims": run["claims"]})),
                  "\n</details>\n"]
    return "".join(parts)


def render_html(packet: dict, packet_sha: str) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return (template.replace("__PACKET_JSON__", script_json(packet))
            .replace("__LABELS_JSON__", script_json(label_template(packet, packet_sha))))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="new directory under processed/eval; no input overrides accepted")
    args = parser.parse_args()
    directory = args.output_dir.resolve()
    allowed = (ROOT / "processed/eval").resolve()
    if directory == allowed or not directory.is_relative_to(allowed) or directory.exists():
        parser.error("output must be a NEW subdirectory under processed/eval")
    pinned = [(ROOT / CASES_PATH, CASES_SHA)]
    answers = [ROOT / ANSWER_DIR / f"c1-run{i}.answers.jsonl" for i in (1, 2, 3)]
    judgments = [ROOT / JUDGE_DIR / f"c1-run{i}-judge-v11-r1.jsonl" for i in (1, 2, 3)]
    pinned.extend(zip(answers, ANSWER_SHAS))
    pinned.extend(zip(judgments, JUDGE_SHAS))
    reject_symlink_inputs([p for p, _ in pinned])
    for path, expected in pinned:
        if file_sha(path) != expected:
            parser.error(f"frozen input SHA mismatch: {path.relative_to(ROOT)}")
    inputs = [{"path": str(p.relative_to(ROOT)), "sha256": sha} for p, sha in pinned]
    _, cases = load_cases(ROOT / CASES_PATH)
    runs = [load_bound_run(a, j, cases, CASES_SHA, core_only=True) for a, j in zip(answers, judgments)]
    configs = {sha256_json(r["judgment"]["judge_config"]) for run in runs for r in run.values()}
    if len(configs) != 1:
        parser.error("Judge config changed across runs")
    summary, rows = summarize(select_cases(cases, core_only=True), runs)
    if summary["groups"]["core"]["majority_gfc_count"] != 15:
        parser.error("core majority no longer matches frozen 15/42")
    packet = build_packet(cases, runs, select_review_rows(rows), inputs)
    packet_bytes = json_text(packet)
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    packet_sha = digest(packet_bytes)
    guide = """# Shadow14 진단 검토 안내

`review.html`을 브라우저로 열어 검토자 이름을 입력합니다. 인터넷 연결은 필요 없습니다.
HTML을 열기 어려우면 `review-run1.md`를 읽고 문항별 판단을 메시지로 전달해도 됩니다.
14문항 각각 run1만 먼저 검토합니다. run2·run3는 차이가 궁금할 때 선택적으로 봅니다.

1. 질문과 왼쪽 검색 원문을 읽고 최종 답변의 근거 일치·완전성을 직접 고릅니다.
2. 초안과 최종 답변을 비교해 맞는 내용 소실 또는 잘못된 내용 제거 여부를 고릅니다.
3. 메모에 확인한 출처 번호와 핵심 문구를 남깁니다. 판단이 어려우면 판단 보류를 고릅니다.
4. 그 다음 Judge 판정을 펼쳐 동의 여부를 선택합니다. Judge가 정답이라는 전제는 없습니다.
5. 검토 결과 JSON을 내려받아 전달합니다. 브라우저 임시 저장만 믿지 말고 파일을 보관합니다.

최종 답변은 원본 artifact의 `answer`(Judge 대상)입니다. 출처 표기 버전은 별도 접힘 영역에
그대로 제공됩니다. 원문은 당시 저장한 검색 context 전체이며 PDF/HWP의 시각적 원본은 아닙니다.
표·줄바꿈은 재해석하지 않습니다. 원문 전체·정답 기준·주장별 검증 로그는 생략 없이 펼칠 수 있습니다.

이 표본은 필수 근거가 모두 있었던 majority 실패 11문항과 고정 성공 대조 3문항의 목적 표집입니다.
run1 자체가 실패라는 뜻은 아닙니다. 일부 문항은 회차별로 다릅니다.
공식 정확도, Judge 일치율 추정, 2인 holdout signoff를 대신하지 않습니다.
Judge를 접는 기능은 초기 점수 노출을 줄일 뿐 완전한 블라인드 평가를 보장하지 않습니다.
정답 기준과 Judge의 원래 점수는 수정하지 않습니다. 모든 사람 판정은 PENDING에서 시작합니다.
다른 원인도 의심되면 메모에 기록합니다. 이 화면은 답변을 고치거나 점수를 재계산하지 않습니다.

`selection.csv`: 선정 근거 감사용(사전 판정 전에 읽을 필요 없음).
`packet.json`: 선택된 14문항 × 3회차 근거·답변·로그와 입력 SHA-256.
`review-template.json`: 42개 PENDING 사람 판정 템플릿. 우선 완료 대상은 run1 14개뿐입니다.
`manifest.json`: 입력/도구/산출물 해시. 파일은 모두 새 경로에 불변으로 생성됐습니다.
"""
    texts = {
        "packet.json": packet_bytes,
        "review.html": render_html(packet, packet_sha),
        "review-run1.md": render_markdown(packet),
        "review-template.json": json_text(label_template(packet, packet_sha)),
        "selection.csv": selection_csv(packet), "guide.md": guide,
    }
    # Ensure pinned evidence did not change during rendering, before any publication.
    for path, expected in pinned:
        if file_sha(path) != expected:
            parser.error("input changed during packet construction")
    manifest = {
        "schema_version": "pnu.shadow-diagnostic-manifest.v1", "date": "2026-09-07",
        "questions": len(packet["items"]), "primary_answer_reviews": 14,
        "optional_answer_reviews": 28, "human_reviews_completed": 0,
        "external_api_calls": 0, "holdout_accessed": False,
        "inputs": inputs,
        "tools": [{"path": str(p.relative_to(ROOT)), "sha256": file_sha(p)} for p in
                  (Path(__file__).resolve(), TEMPLATE_PATH, ROOT / "scripts/analyze_shadow_generation.py",
                   ROOT / "scripts/service_eval_artifacts.py", ROOT / "scripts/immutable_outputs.py")],
        "outputs": [{"path": name, "sha256": digest(value)} for name, value in texts.items()],
    }
    texts["manifest.json"] = json_text(manifest)
    publish_immutable_texts({directory / name: value for name, value in texts.items()},
                           authoritative_path=directory / "manifest.json")
    print(json.dumps({"directory": str(directory), "questions": len(packet["items"]),
                      "primary_run": "run1", "human_reviews_completed": 0,
                      "packet_sha256": packet_sha, "manifest_sha256": file_sha(directory / "manifest.json")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
