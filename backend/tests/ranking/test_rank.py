"""Query terms and explanations on the hand-built index of conftest.py (docs/ranking-plan.md §11)."""

import pytest

from phenotype.schema import Finding, Term
from ranking.index import frequency, pair_frequency
from ranking.rank import explain_match, query_terms, rank_terms, unmentioned
from ranking.schema import QueryTerm
from ranking.score import excluded_mask
from tests.ranking.conftest import A, B, C, E


def term(code):
    return Term(hpo_id=code, label_en=code, label_vi=None, label_vi_reviewed=False, definition_en=None)


def finding(phrase, code=None, *, assertion="PRESENT", subject="FETUS", status="MAPPED", options=()):
    return Finding(phrase=phrase, source_text=phrase, assertion=assertion, assessment="ASSESSED", temporality=None,
                   subject=subject, mapping_status=status, term=term(code) if code else None,
                   options=[term(o) for o in options])


def q(code, weight=1.0):
    return QueryTerm(hpo_id=code, assertion="PRESENT" if weight == 1.0 else "SUSPECTED", weight=weight)


# ---------- query_terms

def test_query_terms_keeps_fetal_present_suspected_mapped(toy):
    terms, skipped = query_terms([
        finding("b", B), finding("c", C, assertion="SUSPECTED"),
        finding("mẹ", E, subject="MOTHER"), finding("không có", E, assertion="ABSENT"),
        finding("chưa rõ", E, assertion="UNCERTAIN"), finding("lạ", status="UNMAPPED"),
    ], index=toy)
    assert [(t.hpo_id, t.assertion, t.weight) for t in terms] == [(B, "PRESENT", 1.0), (C, "SUSPECTED", 0.5)]
    assert [(s.phrase, s.hpo_id, s.reason) for s in skipped] == [
        ("mẹ", E, "không phải của thai"),
        ("không có", E, "trạng thái ABSENT không dùng để xếp hạng"),
        ("chưa rõ", E, "trạng thái UNCERTAIN không dùng để xếp hạng"),
        ("lạ", None, "chưa ánh xạ được"),
    ]


def test_ambiguous_uses_most_informative_common_ancestor(toy):
    terms, skipped = query_terms([finding("b hay c", status="AMBIGUOUS", options=(B, C))], index=toy)
    assert [(t.hpo_id, t.from_options) for t in terms] == [(A, [B, C])] and not skipped


def test_ambiguous_sharing_only_the_root_is_skipped(toy):
    terms, skipped = query_terms([finding("b hay e", status="AMBIGUOUS", options=(B, E))], index=toy)
    assert not terms
    assert skipped[0].reason == "các mã phân vân không có tổ tiên chung đủ cụ thể"


def test_duplicate_code_keeps_heavier_assertion(toy):
    terms, _ = query_terms([finding("b nghi", B, assertion="SUSPECTED"), finding("b", B)], index=toy)
    assert [(t.hpo_id, t.assertion, t.weight) for t in terms] == [(B, "PRESENT", 1.0)]


def test_unknown_or_obsolete_code_is_skipped(toy):
    result = rank_terms([q(B), q("HP:9999999")], index=toy)
    assert [t.hpo_id for t in result.terms] == [B]
    assert [(s.phrase, s.hpo_id, s.reason) for s in result.skipped] == [
        (None, "HP:9999999", "mã không có trong KB hoặc đã obsolete")]
    assert rank_terms([q("HP:9999999")], index=toy).suggestions == []


# ---------- frequency

def test_frequency_forms():
    assert frequency("HP:0040282", None, None, None) == 0.545
    assert frequency("HP:0040285", None, None, None) == 0.0
    assert frequency(None, 3, 4, None) == 0.75
    assert frequency(None, None, None, 22.5) == 0.225
    assert frequency(None, None, None, None) is None


def test_pair_frequency_ignores_unknown_rows():
    assert pair_frequency([None, 0.3, 0.2]) == 0.3
    assert pair_frequency([None, None]) is None


def test_index_pair_frequency_and_exclusion(toy):
    assert toy.direct["D5"] == {B: 1.0}
    assert toy.excluded_terms["D5"] == {B: 0.0}
    assert toy.excluded_terms["D4"] == {B: None}
    assert toy.direct["D3"] == {A: None}


# ---------- Match.kind

@pytest.mark.parametrize("disease, code, kind, disease_hpo_id, common", [
    ("D4", B, "excluded", B, None),
    ("D1", B, "exact", B, None),
    ("D1", A, "disease_more_specific", B, None),
    ("D3", B, "disease_more_general", A, None),
    ("D2", B, "related", C, A),
    ("D4", C, "none", None, None),
    ("D5", B, "exact", B, None),             # positive and excluded at B: positive wins
])
def test_match_kind(toy, disease, code, kind, disease_hpo_id, common):
    excluded = bool(excluded_mask(toy, code)[toy.position[disease]])
    m = explain_match(toy, disease, q(code), excluded)
    assert (m.kind, m.disease_hpo_id, m.common_ancestor_id) == (kind, disease_hpo_id, common)


def test_match_lists_every_source_row(toy):
    m = explain_match(toy, "D5", q(B), excluded=False)
    assert m.frequency_raw == ["1/1"] and m.references == [["PMID:1"]]
    m = explain_match(toy, "D4", q(B), excluded=True)
    assert m.frequency_raw == [None]


def test_unmentioned_skips_terms_related_to_the_query(toy):
    # D6: A (0.1, an ancestor of the query C, and rare) and B (0.8, unrelated to C)
    assert [u.hpo_id for u in unmentioned(toy, "D6", [q(C)])] == [B]
    assert unmentioned(toy, "D6", [q(B)]) == []


def test_query_terms_finding_ids(toy):
    terms, skipped = query_terms([finding("b nghi", B, assertion="SUSPECTED"), finding("b", B),
                                  finding("mẹ", E, subject="MOTHER")], index=toy, ids=["f1", "f2", "f3"])
    assert [(t.hpo_id, t.assertion, t.finding_ids) for t in terms] == [(B, "PRESENT", ["f1", "f2"])]
    assert [(s.phrase, s.finding_id) for s in skipped] == [("mẹ", "f3")]
    terms, skipped = query_terms([finding("b", B), finding("mẹ", E, subject="MOTHER")], index=toy)
    assert terms[0].finding_ids == [] and skipped[0].finding_id is None
