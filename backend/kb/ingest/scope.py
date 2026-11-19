"""Disease scope of layer 3 (docs/ingestion-plan.md §4; kb-architecture.md §2, T17c).

A disease is in scope when it has a positive annotation on a term whose prenatal flag comes from one of
`scope_rule_sources` (ingest.toml). Positive = not negated and not recorded with frequency zero.
"""

import csv

from kb.ingest.config import INGEST
from kb.seed.hpo import PRENATAL_RULES

# SQL condition on disease_phenotype dp: the annotation says the disease HAS the phenotype
POSITIVE = ("NOT dp.is_negated AND dp.freq_num IS DISTINCT FROM 0 AND dp.freq_pct IS DISTINCT FROM 0"
            " AND dp.freq_hpo IS DISTINCT FROM 'HP:0040285'")


def scope_tags() -> list[str]:
    """prenatal_basis tags (e.g. 'subtree:HP:0001197') produced by the configured rule sources."""
    with PRENATAL_RULES.open() as f:
        return sorted(f"{r['kind']}:{r['hpo_id']}" for r in csv.DictReader(f)
                      if r["source"] in INGEST["scope_rule_sources"])


def in_scope(conn, disease_ids: list[str]) -> list[str]:
    """The subset of disease_ids that is in scope, sorted."""
    rows = conn.execute(
        f"""SELECT DISTINCT dp.disease_id FROM disease_phenotype dp JOIN hpo_term t USING (hpo_id)
            WHERE dp.disease_id = ANY(%s) AND {POSITIVE}
              AND string_to_array(t.prenatal_basis, ';') && %s""",
        (disease_ids, scope_tags()))
    return sorted(r[0] for r in rows)


def scope_disease_ids(conn) -> list[str]:
    """Every in-scope disease, sorted (6,452 on 2026-11-19)."""
    rows = conn.execute(
        f"""SELECT DISTINCT dp.disease_id FROM disease_phenotype dp JOIN hpo_term t USING (hpo_id)
            WHERE {POSITIVE} AND string_to_array(t.prenatal_basis, ';') && %s""",
        (scope_tags(),))
    return sorted(r[0] for r in rows)


def known_disease_ids(conn, disease_ids: list[str]) -> list[str]:
    """Keep only codes present in the disease table; deduplicated and sorted (§4)."""
    rows = conn.execute("SELECT disease_id FROM disease WHERE disease_id = ANY(%s)", (list(set(disease_ids)),))
    return sorted(r[0] for r in rows)
