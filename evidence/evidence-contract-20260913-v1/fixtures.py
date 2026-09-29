"""Hand-authored typed interpretations of known synthetic probes, not an NLP adapter.

Original source/claim strings and expected labels are pinned and retained. Their
relation/type/operator interpretations below are test scaffolding, never derived
from gold or an expected result at runtime. Passing these tests measures only the
contract given these interpretations; it does not replay the natural-language
service verifier or measure extraction accuracy.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path

from contract import Assertion, EvidenceFact, Source, ScopedSpan, Value, pairs, span

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = ROOT / "processed/eval/preflight-20260913/scope-bound-c3-v1/ordinal-current-v1/stress.json"
ORIGINAL_SHA = "76ca8dbf7f728f6b76f8fee13d1c765a9dcefe353663fc50d7f4bce35932f7e5"
SCOPE = pairs({"entity": "ALPHA", "edition": "7"})


@dataclass(frozen=True)
class Probe:
    probe_id: str
    claim: Assertion
    query_scope: tuple
    sources: tuple
    facts: tuple
    expected_match: bool
    requested_sources: tuple = ()
    original: dict | None = None


def fact(source, relation, relation_quote, value, *, fact_id="f1", unit=None,
         body=None, title=None, conditions=None, operator_quote=None):
    body = body or {}
    title = {"entity": "ALPHA", "edition": "7"} if title is None else title
    conditions = conditions or {}
    unit_span = span(source, unit or source.text)
    assertion = Assertion(pairs({**title, **body}), relation, value, pairs(conditions))
    return EvidenceFact(fact_id, assertion, unit_span, span(source, relation_quote), span(source, value.text),
                        tuple(ScopedSpan(k, v, span(source, v)) for k, v in body.items()),
                        tuple(ScopedSpan(k, v, span(source, v, field="title")) for k, v in title.items()),
                        tuple(ScopedSpan(k, v, span(source, v)) for k, v in conditions.items()),
                        span(source, operator_quote) if operator_quote else None)


def known_probes():
    raw = ORIGINAL.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ORIGINAL_SHA:
        raise ValueError("known_probe_input_changed")
    originals = {r["probe_id"]: r for r in json.loads(raw)["rows"]}
    result = []

    def add(name, relation, label, value, *, claim_value=None, claim_scope=SCOPE,
            body=None, conditions=None, operator_quote=None):
        original = originals[name]
        sources = tuple(Source(c["chunk_id"], c["source_title"], c["text"]) for c in original["contexts"])
        item = fact(sources[0], relation, label, value, body=body, conditions=conditions, operator_quote=operator_quote)
        result.append(Probe(name, Assertion(claim_scope, relation, claim_value or value, pairs(conditions or {})),
                            SCOPE, sources, (item,), original["expected"], original=original))

    for stem, relation, label, value in [
        ("date", "application_deadline", "신청 마감", Value("date", "2028년 8월 5일")),
        ("time", "application_deadline", "신청 마감", Value("datetime", "2028년 8월 5일 18:00")),
        ("amount", "participation_fee", "참가비", Value("amount", "30,000원", "KRW")),
        ("frequency", "support_frequency", "지원 횟수", Value("frequency", "3회", "회")),
    ]:
        add(stem + "_correct", relation, label, value)
        add(stem + "_wrong_entity", relation, label, value, claim_scope=pairs({"entity": "BETA", "edition": "7"}))
        add(stem + "_wrong_edition", relation, label, value, claim_scope=pairs({"entity": "ALPHA", "edition": "8"}))
    date = Value("date", "2028년 8월 5일")
    add("wrong_date", "application_deadline", "신청 마감", date, claim_value=Value("date", "2028년 8월 7일"))
    add("wrong_time", "application_deadline", "신청 마감", Value("datetime", "2028년 8월 5일 18:00"),
        claim_value=Value("datetime", "2028년 8월 5일 10:00"))
    add("wrong_amount", "participation_fee", "참가비", Value("amount", "30,000원", "KRW"),
        claim_value=Value("amount", "50,000원", "KRW"))
    add("title_ordinal_as_frequency", "support_frequency", "지원 횟수", Value("frequency", "3회", "회"),
        claim_value=Value("frequency", "7회", "회"))
    add("body_other_entity", "application_deadline", "신청 마감", date, body={"entity": "BETA"})
    add("body_other_edition", "application_deadline", "신청 마감", date, body={"edition": "8", "entity": "ALPHA"})
    for name, desired in [("deadline_result_swap", "2028년 8월 7일"), ("deadline_result_correct", "2028년 8월 5일")]:
        original = originals[name]
        c = original["contexts"][0]
        source = Source(c["chunk_id"], c["source_title"], c["text"])
        facts = (fact(source, "application_deadline", "접수 마감", date, unit=source.text.splitlines()[0]),
                 fact(source, "result_announcement", "결과 발표", Value("date", "2028년 8월 7일"),
                      fact_id="f2", unit=source.text.splitlines()[1]))
        result.append(Probe(name, Assertion(SCOPE, "application_deadline", Value("date", desired)), SCOPE,
                            (source,), facts, original["expected"], original=original))
    for stem, relation, label, value, marker, bad_op, conditions in [
        ("maximum", "participation_fee", "참가비", Value("amount", "30,000원", "KRW", "le"), "최대", "ge", {}),
        ("threshold", "semester_eligibility", "신청 자격", Value("quantity", "3학기", "학기", "ge"), "이상", "le", {}),
        ("required", "submission_obligation", "서류 제출", Value("action", "서류 제출", "", "required"), "필수", "optional", {}),
        ("payment", "fee_action", "참가비", Value("action", "참가비", "", "repayment"), "환수", "payment", {"audience": "취소자"}),
    ]:
        add(stem + "_correct", relation, label, value, conditions=conditions, operator_quote=marker)
        add(stem + "_reversed", relation, label, value, claim_value=replace(value, operator=bad_op),
            conditions=conditions, operator_quote=marker)
    original = originals["cross_document_date"]
    sources = tuple(Source(c["chunk_id"], c["source_title"], c["text"]) for c in original["contexts"])
    result.append(Probe("cross_document_date", Assertion(SCOPE, "application_deadline", date), SCOPE, sources,
                        (fact(sources[0], "application_deadline", "신청 마감", Value("date", "2028년 8월 7일")),
                         fact(sources[1], "application_deadline", "신청 마감", date, fact_id="f2",
                              title={"entity": "BETA", "edition": "7"})), False, original=original))
    if {r.probe_id for r in result} != set(originals) or len(result) != 29:
        raise ValueError("known_probe_coverage_mismatch")
    return tuple(result)
