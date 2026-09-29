"""Offline relation/binding contract, NOT a natural-language verifier.

Inputs are typed extraction proposals. This module checks their internal scope,
value, condition and provenance consistency. It cannot establish that a parser's
semantic interpretation is correct. All results are ineligible for service use;
no route changes, answer rewriting, model calls, or gold access are provided.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

Pairs = tuple[tuple[str, str], ...]


def pairs(value: dict[str, str]) -> Pairs:
    return tuple(sorted(value.items()))


def mapping(value: Pairs) -> dict[str, str]:
    if not isinstance(value, tuple) or any(not isinstance(p, tuple) or len(p) != 2 for p in value):
        raise ValueError("invalid_pairs")
    if any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in value):
        raise ValueError("empty_or_nonstring_slot")
    if len(dict(value)) != len(value):
        raise ValueError("duplicate_slot")
    return dict(value)


@dataclass(frozen=True)
class Source:
    source_id: str
    title: str
    text: str

    @property
    def sha256(self) -> str:
        raw = json.dumps([self.source_id, self.title, self.text], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Span:
    source_id: str
    source_sha256: str
    field: str
    start: int
    end: int
    quote: str

    def validate(self, source: Source) -> None:
        if self.source_id != source.source_id or self.source_sha256 != source.sha256:
            raise ValueError("source_identity_or_revision_mismatch")
        if self.field not in {"title", "text"}:
            raise ValueError("invalid_span_field")
        content = getattr(source, self.field)
        if (type(self.start) is not int or type(self.end) is not int
                or not 0 <= self.start < self.end <= len(content)
                or content[self.start:self.end] != self.quote):
            raise ValueError("invalid_span_offset_or_quote")


def span(source: Source, quote: str, *, field: str = "text", occurrence: int = 0) -> Span:
    """Fixture/adapter utility; exact match only, no fuzzy search or normalization."""
    if field not in {"title", "text"} or not quote or type(occurrence) is not int or occurrence < 0:
        raise ValueError("invalid_span_request")
    content, start = getattr(source, field), -1
    for _ in range(occurrence + 1):
        start = content.find(quote, start + 1)
        if start < 0:
            raise ValueError("quote_not_found")
    return Span(source.source_id, source.sha256, field, start, start + len(quote), quote)


@dataclass(frozen=True)
class Value:
    kind: str
    text: str
    unit: str = ""
    operator: str = "eq"

    def validate(self):
        if any(not isinstance(v, str) for v in (self.kind, self.text, self.unit, self.operator)):
            raise ValueError("nonstring_value")
        if not self.kind or not self.text or not self.operator:
            raise ValueError("incomplete_value")


@dataclass(frozen=True)
class Assertion:
    scope: Pairs
    relation: str
    value: Value
    conditions: Pairs = ()

    def validate(self):
        mapping(self.scope)
        mapping(self.conditions)
        if not isinstance(self.relation, str) or not self.relation:
            raise ValueError("empty_relation")
        self.value.validate()


@dataclass(frozen=True)
class ScopedSpan:
    dimension: str
    value: str
    span: Span


@dataclass(frozen=True)
class TableLink:
    """A physical TSV value cell and its same-column header, not a guessed table."""
    table: Span
    header: Span
    value: Span

    def validate(self, source: Source):
        for part in (self.table, self.header, self.value):
            part.validate(source)
            if part.field != "text":
                raise ValueError("table_must_be_body")
        if self.table.start and source.text[self.table.start - 1] != "\n":
            raise ValueError("table_not_line_aligned")
        if self.table.end != len(source.text) and source.text[self.table.end] != "\n":
            raise ValueError("table_not_line_aligned")

        def cell(part):
            if not contains(self.table, part):
                raise ValueError("cell_outside_table")
            before = source.text[self.table.start:part.start]
            if part.start != self.table.start and source.text[part.start - 1] not in "\t\n":
                raise ValueError("cell_not_boundary_aligned")
            if part.end != self.table.end and source.text[part.end] not in "\t\n":
                raise ValueError("cell_not_boundary_aligned")
            if "\n" in part.quote or "\t" in part.quote:
                raise ValueError("span_is_not_one_cell")
            return before.count("\n"), before.rsplit("\n", 1)[-1].count("\t")

        header_row, header_col = cell(self.header)
        value_row, value_col = cell(self.value)
        if header_row != 0 or value_row <= 0 or header_col != value_col:
            raise ValueError("wrong_table_row_or_column")


def contains(outer: Span, inner: Span) -> bool:
    return (outer.source_id == inner.source_id and outer.source_sha256 == inner.source_sha256
            and outer.field == inner.field and outer.start <= inner.start < inner.end <= outer.end)


@dataclass(frozen=True)
class EvidenceFact:
    fact_id: str
    assertion: Assertion
    unit: Span
    relation_span: Span
    value_span: Span
    body_scope: tuple[ScopedSpan, ...] = ()
    title_scope: tuple[ScopedSpan, ...] = ()
    condition_spans: tuple[ScopedSpan, ...] = ()
    operator_span: Span | None = None
    table: TableLink | None = None

    def validate(self, source: Source):
        self.assertion.validate()
        if not self.fact_id:
            raise ValueError("empty_fact_id")
        parts = [self.unit, self.relation_span, self.value_span]
        parts += [p.span for p in (*self.body_scope, *self.title_scope, *self.condition_spans)]
        if self.operator_span:
            parts.append(self.operator_span)
        for part in parts:
            part.validate(source)
        if self.unit.field != "text" or not contains(self.unit, self.value_span):
            raise ValueError("value_outside_body_unit")
        # Exact value surface is required. Normalization must be separately
        # validated by a future adapter; this v1 cannot silently invent it.
        if self.value_span.quote != self.assertion.value.text:
            raise ValueError("value_surface_mismatch")
        if self.table:
            self.table.validate(source)
            if self.table.value != self.value_span or self.table.header != self.relation_span:
                raise ValueError("table_binding_mismatch")
            if not contains(self.table.table, self.unit):
                raise ValueError("unit_outside_table")
            if ("\n" in self.unit.quote
                    or (self.unit.start and source.text[self.unit.start - 1] != "\n")
                    or (self.unit.end != len(source.text) and source.text[self.unit.end] != "\n")):
                raise ValueError("table_unit_must_be_one_complete_row")
        elif not contains(self.unit, self.relation_span):
            raise ValueError("relation_outside_unit")
        if self.assertion.value.operator != "eq" and self.operator_span is None:
            raise ValueError("operator_requires_span")
        if self.operator_span and not contains(self.unit, self.operator_span):
            raise ValueError("operator_outside_unit")

        def scoped(entries, expected_field):
            result = {}
            for entry in entries:
                if not entry.dimension or not entry.value or entry.dimension in result:
                    raise ValueError("duplicate_or_empty_scope")
                if entry.span.field != expected_field or entry.value != entry.span.quote:
                    raise ValueError("scope_surface_mismatch")
                if expected_field == "text" and not contains(self.unit, entry.span):
                    raise ValueError("scope_outside_unit")
                result[entry.dimension] = entry.value
            return result

        body, title = scoped(self.body_scope, "text"), scoped(self.title_scope, "title")
        if any(body[k] != title[k] for k in body.keys() & title.keys()):
            raise ValueError("body_title_scope_conflict")
        if {**title, **body} != mapping(self.assertion.scope):
            raise ValueError("scope_binding_incomplete")
        conditions = scoped(self.condition_spans, "text")
        if conditions != mapping(self.assertion.conditions):
            raise ValueError("condition_binding_incomplete")


@dataclass(frozen=True)
class Verdict:
    status: str
    reason: str
    fact_ids: tuple[str, ...] = ()
    citations: tuple[Span, ...] = ()
    eligible_for_service: bool = False
    semantic_extraction_verified: bool = False


def verify(claim: Assertion, query_scope: Pairs, sources: tuple[Source, ...],
           facts: tuple[EvidenceFact, ...], *, requested_sources: tuple[str, ...] = ()) -> Verdict:
    """Check a supplied symbolic claim. `matched` is conditional, NOT semantic truth.

    Parser-generated relation/operator/type tags and omitted source facts cannot
    be verified here. A missing/ambiguous parser must not use this as an oracle.
    """
    try:
        claim.validate()
        query, explicit = mapping(query_scope), mapping(claim.scope)
        if not query:
            return Verdict("uncertain", "query_scope_required")
        if any(explicit[k] != query[k] for k in explicit.keys() & query.keys()):
            return Verdict("mismatch", "claim_query_scope_conflict")
        effective = {**query, **explicit}
        by_source = {s.source_id: s for s in sources}
        if len(by_source) != len(sources) or any(not s.source_id for s in sources):
            raise ValueError("duplicate_or_empty_source_id")
        if len({f.fact_id for f in facts}) != len(facts):
            raise ValueError("duplicate_fact_id")
        if len(set(requested_sources)) != len(requested_sources):
            raise ValueError("duplicate_requested_source")
        for fact in facts:
            if fact.unit.source_id not in by_source:
                raise ValueError("unknown_source")
            fact.validate(by_source[fact.unit.source_id])
    except (ValueError, TypeError, AttributeError) as error:
        return Verdict("uncertain", "invalid_evidence_contract:" + str(error))

    # Require complete scope and condition equality: additional track, audience
    # or year restrictions cannot be silently dropped by an unscoped answer.
    scoped_facts = [f for f in facts if mapping(f.assertion.scope) == effective]
    relevant = [f for f in scoped_facts if f.assertion.relation == claim.relation]
    conditioned = [f for f in relevant if f.assertion.conditions == claim.conditions]
    good = [f for f in conditioned if f.assertion.value == claim.value]
    conflicts = [f for f in conditioned if f.assertion.value != claim.value]
    if good and conflicts:
        return Verdict("uncertain", "same_scope_conflicting_evidence", tuple(f.fact_id for f in good + conflicts))
    if not good:
        if conflicts:
            return Verdict("mismatch", "same_scope_value_or_operator_mismatch", tuple(f.fact_id for f in conflicts))
        return Verdict("insufficient", "no_complete_scope_relation_condition_match")
    source_ids = {f.unit.source_id for f in good}
    if any(source_id not in source_ids for source_id in requested_sources):
        return Verdict("insufficient", "requested_citation_not_supported")
    # Output one supporting unit per explicitly requested source; otherwise one
    # deterministic supporting unit, never every similarly titled source.
    selected = []
    for source_id in sorted(requested_sources) if requested_sources else [sorted(source_ids)[0]]:
        selected.append(min((f for f in good if f.unit.source_id == source_id),
                            key=lambda f: (f.unit.end - f.unit.start, f.fact_id)))
    citations = []
    for fact in selected:
        for part in (fact.unit, *(s.span for s in fact.title_scope),
                     *((fact.table.header,) if fact.table else ())):
            if part not in citations:
                citations.append(part)
    return Verdict("matched", "conditional_on_typed_extraction", tuple(f.fact_id for f in selected), tuple(citations))
