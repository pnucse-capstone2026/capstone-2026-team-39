from dataclasses import replace
import unittest

from contract import Assertion, EvidenceFact, Source, ScopedSpan, TableLink, Value, pairs, span, verify
from fixtures import Probe, SCOPE, fact, known_probes


def simple():
    source = Source("synthetic#1", "제7회 ALPHA 공모전", "신청 마감: 2028년 8월 5일")
    evidence = fact(source, "deadline", "신청 마감", Value("date", "2028년 8월 5일"))
    return source, evidence


def table_fixture():
    source = Source("table#1", "일정표", "프로그램\t접수 마감\t결과 발표\nALPHA\t2028-08-05\t2028-08-07\nBETA\t2028-08-07\t2028-08-09")
    value, header = span(source, "2028-08-05"), span(source, "접수 마감")
    assertion = Assertion(pairs({"entity": "ALPHA"}), "deadline", Value("date", "2028-08-05"))
    evidence = EvidenceFact("table-f1", assertion, span(source, source.text.splitlines()[1]), header, value,
                            body_scope=(ScopedSpan("entity", "ALPHA", span(source, "ALPHA")),),
                            table=TableLink(span(source, source.text), header, value))
    return source, evidence


def extra_probes():
    """Expected cases fixed in source before executing the candidate."""
    source, evidence = simple()
    claim = evidence.assertion
    result = []

    def add(name, expected, *, c=claim, q=SCOPE, ss=(source,), fs=(evidence,), requested=()):
        result.append(Probe(name, c, q, ss, fs, expected, requested))

    add("exact_body_and_title", True)
    add("missing_query_scope", False, q=())
    add("query_entity_mismatch", False, q=pairs({"entity": "BETA", "edition": "7"}))
    add("unknown_source", False, ss=())
    add("source_revision_changed", False, ss=(replace(source, text=source.text + " 변경"),))
    add("tampered_quote", False, fs=(replace(evidence, value_span=replace(evidence.value_span, quote="2028년 8월 7일")),))
    add("tampered_offset", False, fs=(replace(evidence, value_span=replace(evidence.value_span, start=0)),))
    add("missing_title_scope_binding", False, fs=(replace(evidence, title_scope=()),))
    add("duplicate_source_identity", False, ss=(source, source))
    add("duplicate_fact_identity", False, fs=(evidence, evidence))
    add("unknown_extra_citation", False, requested=("unseen-source",))
    extra = Source("unrelated#2", "제7회 ALPHA 공모전", "개인정보 수집 양식")
    add("same_title_irrelevant_extra_citation", False, ss=(source, extra), requested=(source.source_id, extra.source_id))
    duplicate = replace(source, source_id="copy#2")
    duplicate_fact = fact(duplicate, "deadline", "신청 마감", claim.value, fact_id="copy-f2")
    add("same_fact_two_sources_minimal_citation", True, ss=(source, duplicate), fs=(evidence, duplicate_fact))
    changed = replace(source, source_id="conflict#2", text="신청 마감: 2028년 8월 7일")
    conflict = fact(changed, "deadline", "신청 마감", Value("date", "2028년 8월 7일"), fact_id="conflict-f2")
    add("same_scope_conflicting_dates", False, ss=(source, changed), fs=(evidence, conflict))
    conditional_source = Source("condition#1", "제7회 ALPHA 공모전", "취소자는 참가비 환수 대상입니다.")
    conditional = fact(conditional_source, "fee_action", "참가비", Value("action", "참가비", "", "repayment"),
                       conditions={"audience": "취소자"}, operator_quote="환수")
    add("condition_retained", True, c=conditional.assertion, ss=(conditional_source,), fs=(conditional,))
    add("condition_dropped", False, c=replace(conditional.assertion, conditions=()), ss=(conditional_source,), fs=(conditional,))
    add("condition_changed", False, c=replace(conditional.assertion, conditions=pairs({"audience": "참가자"})),
        ss=(conditional_source,), fs=(conditional,))
    add("missing_operator_span", False, c=conditional.assertion, ss=(conditional_source,), fs=(replace(conditional, operator_span=None),))
    tsource, tfact = table_fixture()
    add("tsv_same_column_same_row", True, c=tfact.assertion, q=tfact.assertion.scope, ss=(tsource,), fs=(tfact,))
    wrong_header = span(tsource, "결과 발표")
    wrong = replace(tfact, relation_span=wrong_header, table=replace(tfact.table, header=wrong_header))
    add("tsv_swapped_header", False, c=tfact.assertion, q=tfact.assertion.scope, ss=(tsource,), fs=(wrong,))
    wrong_scope = replace(tfact, body_scope=(ScopedSpan("entity", "BETA", span(tsource, "BETA")),),
                          assertion=replace(tfact.assertion, scope=pairs({"entity": "BETA"})))
    add("tsv_borrow_other_row_entity", False, c=wrong_scope.assertion, q=wrong_scope.assertion.scope, ss=(tsource,), fs=(wrong_scope,))
    add("tsv_whole_table_as_one_fact", False, c=tfact.assertion, q=tfact.assertion.scope, ss=(tsource,),
        fs=(replace(tfact, unit=span(tsource, tsource.text)),))
    add("value_kind_changed", False, c=replace(claim, value=replace(claim.value, kind="quantity")))
    add("value_unit_changed", False, c=replace(claim, value=replace(claim.value, unit="USD")))
    add("absent_relation", False, c=replace(claim, relation="result_announcement"))
    add("extra_unbound_claim_scope", False, c=replace(claim, scope=pairs({**dict(SCOPE), "track": "추천"})))
    return tuple(result)


class EvidenceContractTests(unittest.TestCase):
    def test_known_29_typed_interpretations(self):
        probes = known_probes()
        self.assertEqual((len(probes), sum(p.expected_match for p in probes)), (29, 9))
        for probe in probes:
            with self.subTest(probe=probe.probe_id):
                result = verify(probe.claim, probe.query_scope, probe.sources, probe.facts)
                self.assertEqual(result.status == "matched", probe.expected_match, result)
                self.assertFalse(result.eligible_for_service)
                self.assertFalse(result.semantic_extraction_verified)

    def test_additional_contract_probes(self):
        probes = extra_probes()
        self.assertEqual(len(probes), 26)
        for probe in probes:
            with self.subTest(probe=probe.probe_id):
                result = verify(probe.claim, probe.query_scope, probe.sources, probe.facts, requested_sources=probe.requested_sources)
                self.assertEqual(result.status == "matched", probe.expected_match, result)

    def test_minimal_citation_and_explicit_source_verification(self):
        p = next(p for p in extra_probes() if p.probe_id == "same_fact_two_sources_minimal_citation")
        result = verify(p.claim, p.query_scope, p.sources, p.facts)
        self.assertEqual(len({c.source_id for c in result.citations}), 1)
        both = verify(p.claim, p.query_scope, p.sources, p.facts, requested_sources=tuple(s.source_id for s in p.sources))
        self.assertEqual(len({c.source_id for c in both.citations}), 2)

    def test_conflicting_evidence_is_uncertain_not_last_source_wins(self):
        p = next(p for p in extra_probes() if p.probe_id == "same_scope_conflicting_dates")
        for fs in (p.facts, p.facts[::-1]):
            self.assertEqual(verify(p.claim, p.query_scope, p.sources, fs).status, "uncertain")

    def test_query_scope_inheritance_does_not_borrow_other_program(self):
        source, evidence = simple()
        claim = replace(evidence.assertion, scope=())
        self.assertEqual(verify(claim, SCOPE, (source,), (evidence,)).status, "matched")
        self.assertEqual(verify(claim, pairs({"entity": "BETA", "edition": "7"}), (source,), (evidence,)).status, "insufficient")

    def test_title_body_conflicts_are_explicit(self):
        for p in known_probes():
            if p.probe_id in {"body_other_entity", "body_other_edition"}:
                result = verify(p.claim, p.query_scope, p.sources, p.facts)
                self.assertEqual(result.status, "uncertain")
                self.assertIn("body_title_scope_conflict", result.reason)

    def test_literal_surface_difference_is_not_called_semantic_contradiction(self):
        source, evidence = simple()
        claim = replace(evidence.assertion, value=Value("date", "2028-08-05"))
        result = verify(claim, SCOPE, (source,), (evidence,))
        self.assertEqual(result.status, "mismatch")
        self.assertFalse(result.semantic_extraction_verified)

    def test_span_uses_unicode_offsets_and_binds_source_revision(self):
        source = Source("유니코드", "제목", "한글😀 근거\n두 번째 근거")
        a = span(source, "근거", occurrence=1)
        a.validate(source)
        self.assertEqual(source.text[a.start:a.end], "근거")
        for changed in (replace(a, start=True), replace(a, end=-1), replace(a, field="url")):
            with self.assertRaises(ValueError):
                changed.validate(source)

    def test_duplicate_scope_and_empty_values_fail_closed(self):
        source, evidence = simple()
        for claim in (replace(evidence.assertion, scope=(("entity", "ALPHA"), ("entity", "BETA"))),
                      replace(evidence.assertion, relation=""),
                      replace(evidence.assertion, value=Value("date", ""))):
            self.assertEqual(verify(claim, SCOPE, (source,), (evidence,)).status, "uncertain")

    def test_table_cell_substring_cannot_be_a_complete_binding(self):
        source, evidence = table_fixture()
        link = replace(evidence.table, value=span(source, "2028-08"))
        with self.assertRaisesRegex(ValueError, "boundary_aligned"):
            link.validate(source)

    def test_cross_source_unit_borrowing_rejected(self):
        source, evidence = simple()
        other = replace(source, source_id="other")
        changed = replace(evidence, value_span=span(other, evidence.assertion.value.text))
        self.assertEqual(verify(evidence.assertion, SCOPE, (source, other), (changed,)).status, "uncertain")

    def test_duplicate_requested_source_rejected(self):
        source, evidence = simple()
        self.assertEqual(verify(evidence.assertion, SCOPE, (source,), (evidence,),
                                requested_sources=(source.source_id, source.source_id)).status, "uncertain")

    def test_contract_does_not_certify_a_mislabeled_semantic_operator(self):
        # Deliberately dishonest extraction: real text says repayment; both typed
        # propositions say payment. Span integrity cannot detect this tag error.
        source = Source("tag-error", "제7회 ALPHA 공모전", "취소자는 참가비 환수 대상입니다.")
        evidence = fact(source, "fee_action", "참가비", Value("action", "참가비", "", "payment"),
                        conditions={"audience": "취소자"}, operator_quote="환수")
        result = verify(evidence.assertion, SCOPE, (source,), (evidence,))
        self.assertEqual(result.status, "matched")
        self.assertFalse(result.eligible_for_service)
        self.assertFalse(result.semantic_extraction_verified)


if __name__ == "__main__":
    unittest.main()
