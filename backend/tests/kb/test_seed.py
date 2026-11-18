"""Checks on the seeded KB (docs/kb-architecture.md §4.1). Need `make seed` first."""

import pytest

from core.db import connect
from core.text import norm
from kb.seed.hpo import load, parse_frequency


def test_norm():
    assert norm("  Thông Liên   Thất ") == "thong lien that"
    assert norm("Đầu nhỏ") == "dau nho"


def test_parse_frequency():
    assert parse_frequency("") == (None, None, None, None)
    assert parse_frequency("HP:0040282") == (None, None, "HP:0040282", None)
    assert parse_frequency("12/45") == (12, 45, None, None)
    assert parse_frequency("22.5%") == (None, None, None, 22.5)
    with pytest.raises(ValueError):
        parse_frequency("frequent")


@pytest.fixture(scope="module")
def db():
    with connect() as conn:
        if not conn.execute("SELECT count(*) FROM hpo_term").fetchone()[0]:
            pytest.skip("KB not seeded")
        yield conn


def one(db, sql, *params):
    return db.execute(sql, params or None).fetchone()[0]


def test_vsd_ancestors_include_abnormal_heart_morphology(db):
    assert one(db, """SELECT count(*) FROM hpo_ancestor a JOIN hpo_term t ON t.hpo_id = a.ancestor_id
                      WHERE a.hpo_id = 'HP:0001629' AND t.label_en = 'Abnormal heart morphology'""") == 1


def test_prenatal_flag_is_wider_than_prenatal_branch(db):
    # VSD and microcephaly sit outside HP:0001197 — why filtering by that branch alone is too narrow
    for term in ("HP:0001629", "HP:0000252"):
        assert one(db, "SELECT count(*) FROM hpo_ancestor WHERE hpo_id = %s AND ancestor_id = 'HP:0001197'", term) == 0
        assert one(db, "SELECT prenatal_relevant FROM hpo_term WHERE hpo_id = %s", term)


def test_no_obsolete_or_orphan_names(db):
    assert one(db, "SELECT count(*) FROM hpo_name n JOIN hpo_term t USING (hpo_id) WHERE t.is_obsolete") == 0
    assert one(db, "SELECT count(*) FROM hpo_name WHERE text ILIKE '%obsolete%' AND kind <> 'definition'") == 0
    assert one(db, """SELECT count(*) FROM hpo_term t WHERE NOT is_obsolete AND NOT EXISTS
                      (SELECT 1 FROM hpo_name n WHERE n.hpo_id = t.hpo_id AND n.kind = 'label')""") == 0


def test_ic(db):
    assert one(db, "SELECT ic FROM hpo_term WHERE hpo_id = 'HP:0000118'") == pytest.approx(0)  # every disease
    assert one(db, "SELECT ic FROM hpo_term WHERE hpo_id = 'HP:0001629'") > 2


def test_negated_annotations_kept(db):
    assert one(db, "SELECT count(*) FROM disease_phenotype WHERE is_negated") > 0


def test_reload_is_idempotent(db):
    fingerprint = """SELECT (SELECT count(*) FROM hpo_name), (SELECT count(embedding) FROM hpo_name),
                            (SELECT count(*) FROM hpo_ancestor), (SELECT count(*) FROM disease_phenotype),
                            (SELECT round(sum(ic)::numeric, 6) FROM hpo_term),
                            (SELECT count(*) FROM hpo_term WHERE prenatal_relevant)"""
    before = db.execute(fingerprint).fetchone()
    db.commit()
    load()
    assert db.execute(fingerprint).fetchone() == before
