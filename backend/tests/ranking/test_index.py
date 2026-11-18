"""The index loaded from the real KB (docs/ranking-plan.md §3, §11). Needs `make seed`."""

import pytest

from core.db import connect
from ranking.index import load_index

POSITIVE = """aspect = 'P' AND NOT is_negated AND freq_num IS DISTINCT FROM 0 AND freq_pct IS DISTINCT FROM 0
              AND freq_hpo IS DISTINCT FROM 'HP:0040285'"""
# a (disease, term) pair whose only rows are excluded, with no positive annotation of the disease at or below it
ONLY_EXCLUDED = f"""
    SELECT dp.disease_id, dp.hpo_id FROM disease_phenotype dp
    WHERE dp.aspect = 'P' AND ({{cond}})
      AND EXISTS (SELECT 1 FROM disease_phenotype p WHERE p.disease_id = dp.disease_id AND {POSITIVE})
      AND NOT EXISTS (SELECT 1 FROM disease_phenotype p JOIN hpo_ancestor a ON a.hpo_id = p.hpo_id
                      WHERE p.disease_id = dp.disease_id AND a.ancestor_id = dp.hpo_id AND {POSITIVE})
    ORDER BY dp.disease_id, dp.hpo_id LIMIT 1"""


@pytest.fixture(scope="module")
def db():
    with connect() as conn:
        if not conn.execute("SELECT count(*) FROM hpo_term").fetchone()[0]:
            pytest.skip("KB not seeded")
        yield conn


@pytest.fixture(scope="module")
def index(db):
    return load_index()


def test_disease_count(db, index):
    n = db.execute(f"SELECT count(DISTINCT disease_id) FROM disease_phenotype WHERE {POSITIVE}").fetchone()[0]
    assert index.n == n


@pytest.mark.parametrize("cond", ["dp.freq_num = 0", "dp.is_negated"])
def test_excluded_rows_do_not_count_as_positive(db, index, cond):
    d, t = db.execute(ONLY_EXCLUDED.format(cond=cond)).fetchone()
    assert t in index.excluded_terms[d]
    assert t not in index.direct[d]
    assert index.position[d] not in set(index.under.get(t, ([],))[0])
