"""Offline-only evidence-scope experiment; NOT connected to the service.

This is an additional conservative gate on frozen C2, not an entailment model.
It never upgrades a C2 rejection. Source rows/sentences, not an entire quote or
metadata number pool, must jointly support anchors, values and relation markers.
Ambiguous multi-column rows and unstructured prose still need independent eval.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from . import grounded_claims_v2 as baseline

VERSION = "pnu.evidence-binding-experiment.v1"

# Protect only syntactically bounded dates, not every number followed by a dot.
_DOTTED_DATE = re.compile(
    r"(?<![\d.])(?:[‘’']\d{2}|(?:19|20)\d{2})\.\s*"
    r"(?:1[0-2]|0?[1-9])\.(?:\s*(?:3[01]|[12]\d|0?[1-9])\.(?!\d))?"
    r"|(?<![\d.])(?:1[0-2]|0?[1-9])\.\s*(?:3[01]|[12]\d|0?[1-9])\.(?!\d)"
)
_BOUNDARY = re.compile(r"\n+|(?<=[.!?。！？])\s+")
_PHONE = re.compile(r"(?<!\d)\d{2,3}-\d{3,4}-\d{4}(?!\d)")
_VALUE = re.compile(r"(?<!\d)\d+(?:,\d{3})*(?:\.\d+)?\s*(?:만원|천원|원|명|회|호|점|학기|시간|주|%)")
_YEAR_SCOPE = re.compile(r"(?:19|20)\d{2}\s*(?:학년도|년도|년)")
_ORDINAL_SCOPE = re.compile(r"제\s*\d+\s*회")
_DATE = re.compile(
    r"(?<!\d)(?:(?P<y>(?:19|20)\d{2})\s*(?:년\s*|\.\s*|\s+))?"
    r"(?P<m>1[0-2]|0?[1-9])\s*(?:월\s*|\.\s*)"
    r"(?P<d>3[01]|[12]\d|0?[1-9])(?:일|\.)?(?!\d)"
)
_CONDITION = re.compile(r"(?:경우|한해|한하여|시만|시에는|조건|심사|평가|이수|달성)")
_NEGATION = re.compile(r"않|없|불가|금지|제외")
_DENIAL = re.compile(r"않|없")


def split_preserving_dates(text: str) -> list[str]:
    """Lossless (except inter-sentence whitespace), with no minimum/truncation.

    Periods belonging to dates are protected by offsets rather than replacing
    source characters. A terminal date dot still ends a sentence unless a range,
    weekday, time or Korean suffix follows. Invalid dates are not validated here.
    """
    protected: set[int] = set()
    for match in _DOTTED_DATE.finditer(text):
        dots = [i for i in range(match.start(), match.end()) if text[i] == "."]
        protected.update(dots[:-1])
        after = text[match.end():]
        if re.match(r"\s*(?:[~∼～–—-]|\([월화수목금토일]|\d{1,2}:)", after) or re.match(r"[가-힣]", after):
            protected.add(dots[-1])
    result: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        if "\n" not in match.group() and match.start() - 1 in protected:
            continue
        part = text[start:match.start()].strip()
        if part:
            result.append(part)
        start = match.end()
    if text[start:].strip():
        result.append(text[start:].strip())
    return result


def _units(source: str, quote: str) -> list[dict[str, Any]]:
    """Intersect quote with original row boundaries before whitespace collapse.

    All offsets are explicitly in NFKC/whitespace-normalized source coordinates;
    unit text remains a contiguous substring, never a stitched quotation.
    """
    norm = baseline.normalize_text(source)
    quote = baseline.normalize_text(quote)
    occurrences = [m.start() for m in re.finditer(re.escape(quote), norm)]
    parts = split_preserving_dates(source)
    cursor = 0
    result = []
    for index, raw in enumerate(parts):
        part = baseline.normalize_text(raw)
        start = norm.find(part, cursor)
        if start < 0:
            raise ValueError("source unit cannot be mapped to normalized source")
        end = start + len(part)
        cursor = end
        for q_start in occurrences:
            left, right = max(start, q_start), min(end, q_start + len(quote))
            if left < right and norm[left:right].strip():
                value = norm[left:right].strip()
                actual_start = norm.find(value, left, right)
                result.append({"unit_index": index, "text": value,
                               "normalized_start": actual_start,
                               "normalized_end": actual_start + len(value),
                               "sha256": baseline.sha256_text(value)})
    return result


def _anchors(text: str) -> set[str]:
    # Values AND relation directions must not attract us to the wrong subject.
    relation_markers = set().union(*baseline._RELATION_GROUPS)
    return {t for t in baseline._tokens(text) if not re.search(r"\d", t)
            and not any(baseline._has_relation_marker(t, m) for m in relation_markers)}


def _safe_metadata(text: str, context: Any) -> str:
    """Allow only explicit year/edition scope, never arbitrary metadata values.

    Entity names in metadata do not override a different entity in a body row.
    This intentionally rejects some legitimate inherited-title paraphrases.
    """
    metadata = " ".join(baseline._context_field(context, k)
                        for k in ("source_title", "section_path"))
    return " ".join(m.group() for pattern in (_YEAR_SCOPE, _ORDINAL_SCOPE)
                    for m in pattern.finditer(text)
                    if re.sub(r"\s+", "", m.group()) in re.sub(r"\s+", "", metadata))


def _dates(text: str) -> list[tuple[int | None, int, int]]:
    result = []
    year = None
    for match in _DATE.finditer(text):
        if match.group("y"):
            year = int(match.group("y"))
        result.append((year, int(match.group("m")), int(match.group("d"))))
    return result


def _typed_conflicts(claim: str, unit: str) -> list[str]:
    conflicts = []
    for phone in _PHONE.findall(claim):
        if phone not in _PHONE.findall(unit):
            conflicts.append("phone_not_in_unit:" + phone)
    # Pair values with units: a room number cannot supply a fee or headcount.
    for match in _VALUE.finditer(claim):
        value = re.sub(r"[\s,]", "", match.group())
        if value not in {re.sub(r"[\s,]", "", m.group()) for m in _VALUE.finditer(unit)}:
            conflicts.append("typed_value_not_in_unit:" + value)
    dates = _dates(unit)
    for year, month, day in _dates(claim):
        if not any(month == m and day == d and (year is None or y is None or year == y)
                   for y, m, d in dates):
            conflicts.append(f"date_not_in_unit:{year}-{month}-{day}")
    if bool(_NEGATION.search(claim)) != bool(_NEGATION.search(unit)):
        conflicts.append("negation_scope_ambiguous")
    if bool(_DENIAL.search(claim)) != bool(_DENIAL.search(unit)):
        conflicts.append("denial_scope_ambiguous")
    if _CONDITION.search(unit) and not _CONDITION.search(claim):
        conflicts.append("condition_not_preserved")
    return conflicts


def verify_claim(claim: Mapping[str, Any], contexts: Sequence[Any]) -> dict[str, Any]:
    """Return an auditable structural gate; never promote a baseline rejection.

    The most textually relevant source unit(s) are selected WITHOUT numbers.
    Each selected unit must independently support all claim values and relation
    markers. Combining rows or sources to construct a successful claim is banned.
    Passing is necessary, not sufficient, for semantic correctness.
    """
    payload = baseline.parse_response({"claims": [claim], "unanswered": []})
    parsed = payload["claims"][0]
    original = baseline._verify_claim(parsed, contexts)
    result = {"version": VERSION, "text": parsed["text"],
              "baseline_supported": original["supported"],
              "baseline_reason": original["validation_reason"],
              "scope_gate_passed": False, "units": []}
    if not original["supported"]:
        result["reason"] = "baseline_rejected"
        return result
    text = parsed["text"]
    anchors = _anchors(text)
    candidates = []
    for evidence in parsed["evidence"]:
        number = evidence["source_number"]
        context = contexts[number - 1]
        for unit in _units(baseline._context_text(context), evidence["quote"]):
            overlap = baseline._anchor_overlap(anchors, _anchors(unit["text"]))
            candidates.append({**unit, "source_number": number,
                               "anchors": sorted(overlap), "anchor_count": len(overlap)})
    maximum = max((u["anchor_count"] for u in candidates), default=0)
    for unit in candidates:
        selected = maximum > 0 and unit["anchor_count"] == maximum
        unit["selected"] = selected
        unit["passes"] = False
        if selected:
            context = contexts[unit["source_number"] - 1]
            scope = _safe_metadata(text, context)
            narrowed = {"text": unit["text"], "source_title": scope}
            local_claim = {"text": text, "evidence": [{"source_number": 1, "quote": unit["text"]}]}
            local = baseline._verify_claim(local_claim, [narrowed])
            conflicts = _typed_conflicts(text, unit["text"])
            unit.update(local_reason=local["validation_reason"], conflicts=conflicts,
                        metadata_scope=scope, passes=local["supported"] and not conflicts)
    result["units"] = candidates
    result["scope_gate_passed"] = any(u["passes"] for u in candidates)
    result["reason"] = "single_unit_gate_passed" if result["scope_gate_passed"] else "no_single_unit_support"
    return result
