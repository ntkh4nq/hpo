"""Query terms and explanations on the hand-built index of conftest.py (docs/ranking-plan.md §11)."""

import pytest

from phenotype.schema import Finding, Term
from ranking.index import frequency, pair_frequency
from ranking.rank import explain_match, query_terms, rank_terms, unmentioned
from ranking.schema import QueryTerm
from ranking.score import excluded_mask
from tests.ranking.conftest import A, B, C, E


def term(code):
    return Term(hpo_id=code, label_en=code, definition_en=None)


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
        ("mẹ", E, "not_fetal"),
        ("không có", E, "absent_checked"),
        ("chưa rõ", E, "state_not_used"),
        ("lạ", None, "unmapped"),
    ]


def test_ambiguous_uses_most_informative_common_ancestor(toy):
    terms, skipped = query_terms([finding("b hay c", status="AMBIGUOUS", options=(B, C))], index=toy)
    assert [(t.hpo_id, t.from_options) for t in terms] == [(A, [B, C])] and not skipped


def test_ambiguous_sharing_only_the_root_is_skipped(toy):
    terms, skipped = query_terms([finding("b hay e", status="AMBIGUOUS", options=(B, E))], index=toy)
    assert not terms
    assert skipped[0].reason == "ambiguous_no_common_ancestor"


def test_duplicate_code_keeps_heavier_assertion(toy):
    terms, _ = query_terms([finding("b nghi", B, assertion="SUSPECTED"), finding("b", B)], index=toy)
    assert [(t.hpo_id, t.assertion, t.weight) for t in terms] == [(B, "PRESENT", 1.0)]


def test_unknown_or_obsolete_code_is_skipped(toy):
    result = rank_terms([q(B), q("HP:9999999")], index=toy)
    assert [t.hpo_id for t in result.terms] == [B]
    assert [(s.phrase, s.hpo_id, s.reason) for s in result.skipped] == [
        (None, "HP:9999999", "not_in_kb")]
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


def test_unmentioned_cross_reference(toy):
    # T114: D6's not-mentioned B is also recorded at D1 (positive) and recorded absent at D4 (NOT)
    result = rank_terms([q(C)], index=toy, top_n=6)
    d6 = next(s for s in result.suggestions if s.disease_id == "D6")
    b = next(u for u in d6.unmentioned if u.hpo_id == B)
    assert "D1" in b.also_in and "D6" not in b.also_in
    assert "D4" in b.excluded_in
    assert b.references == [["PMID:1"]]


def test_unmentioned_prefers_more_patients():
    # same frequency 1.0: 5/5 rests on more patients than 1/1, so it comes first
    from ranking.index import Annotation, build_index
    rows = [Annotation("D", t, False, raw, n, n, None, None, "PCS", ["PMID:1"]) for t, raw, n in
            (("HP:X", "1/1", 1), ("HP:Y", "5/5", 5), ("HP:Q", "1/1", 1))]
    index = build_index(hpo_release="t", ancestors={t: {t} for t in ("HP:X", "HP:Y", "HP:Q")}, ic={"HP:Q": 1.0},
                        annotations=rows, diseases={"D": ("d", "TEST")}, genes={},
                        labels={t: (t, None) for t in ("HP:X", "HP:Y", "HP:Q")}, prenatal={"HP:X", "HP:Y"})
    assert [u.hpo_id for u in unmentioned(index, "D", [q("HP:Q")])] == ["HP:Y", "HP:X"]


def test_traits_inheritance_and_course():
    from ranking.index import Annotation, TraitRow, build_index
    from ranking.rank import traits
    index = build_index(hpo_release="t", ancestors={"HP:P": {"HP:P"}}, ic={}, diseases={"D": ("d", "TEST")}, genes={},
                        annotations=[Annotation("D", "HP:P", False, None, None, None, None, None, "PCS", [])],
                        labels={"HP:P": ("P", None), "HP:0000006": ("Autosomal dominant inheritance", "trội")},
                        prenatal=set(), traits=[TraitRow("D", "I", "HP:0000006", None, ["PMID:2"]),
                                                TraitRow("NOT_RANKED", "I", "HP:0000006", None, [])])
    [t] = traits(index, "D", "I")
    assert (t.hpo_id, t.label_en, t.references) == ("HP:0000006", "Autosomal dominant inheritance", [["PMID:2"]])
    assert traits(index, "D", "C") == [] and "NOT_RANKED" not in index.traits


# ---------- grouping nodes (T131)

def test_related_never_goes_through_a_grouping_node(toy):
    from ranking.index import CONFIG
    assert explain_match(toy, "D2", q(B), False).kind == "related"
    assert explain_match(toy, "D2", q(B), False, {**CONFIG, "grouping_nodes": [A]}).kind == "none"


def test_short_femur_is_not_related_to_nuchal_translucency_through_abnormal_fetal_morphology():
    """Real KB: Lymphatic malformation 7 records Increased nuchal translucency; both sit under HP:0034058 only."""
    from ranking.index import load_index
    index = load_index()
    assert "HP:0010880" in index.direct["OMIM:617300"]
    assert explain_match(index, "OMIM:617300", q("HP:0011428"), False).kind == "none"


# ---------- absent findings and conflicts (T130)

def test_assessed_absent_finding_is_checked_not_scored(toy):
    from ranking.rank import absent_terms
    limited = finding("không b hạn chế", B, assertion="ABSENT").model_copy(update={"assessment": "LIMITED"})
    terms, skipped = query_terms([finding("e", E), finding("không b", B, assertion="ABSENT"), limited,
                                  finding("không b hay c", status="AMBIGUOUS", options=(B, C), assertion="ABSENT")],
                                 index=toy, ids=["f1", "f2", "f3", "f4"])
    assert [t.hpo_id for t in terms] == [E]
    assert [(s.finding_id, s.reason) for s in skipped] == [
        ("f2", "absent_checked"), ("f3", "state_not_used"), ("f4", "state_not_used")]
    assert [(a.hpo_id, a.finding_ids) for a in absent_terms(skipped)] == [(B, ["f2"])]


def test_contradicting_or_grouping_absent_term_is_not_checked(toy):
    from ranking.index import CONFIG
    _, skipped = query_terms([finding("b", B), finding("không a", A, assertion="ABSENT")], index=toy)
    assert skipped[0].reason == "state_not_used"        # A is an ancestor of the present B
    _, skipped = query_terms([finding("e", E), finding("không a", A, assertion="ABSENT")], index=toy,
                             config={**CONFIG, "grouping_nodes": [A]})
    assert skipped[0].reason == "state_not_used"


def test_conflicts_name_frequent_or_unquantified_annotations_and_never_change_ranks(toy):
    from ranking.index import CONFIG
    from ranking.rank import conflicts
    from ranking.schema import AbsentTerm
    absent = [AbsentTerm(hpo_id=A, finding_ids=["f2"])]
    found = {d: [(c.disease_hpo_id, c.finding_ids) for c in conflicts(toy, d, absent)] for d in ("D1", "D2", "D3", "D4")}
    assert found == {"D1": [(B, ["f2"])], "D2": [(C, ["f2"])], "D3": [(A, ["f2"])], "D4": []}
    assert conflicts(toy, "D1", absent, {**CONFIG, "conflict_min_frequency": 0.95}) == []
    without, with_ = rank_terms([q(E)], index=toy, top_n=6), rank_terms([q(E)], index=toy, top_n=6, absent=absent)
    assert [(s.disease_id, s.rank, s.score) for s in without.suggestions] == \
        [(s.disease_id, s.rank, s.score) for s in with_.suggestions]
    assert with_.absent[0].label_en == A and any(s.conflicts for s in with_.suggestions)
