"""Candidate diseases for E2 cases (docs/e2-plan.md §3.1), in a fixed order: sha1(disease_id).

A disease qualifies when it has a name, at least one gene, and at least MIN_USABLE usable phenotypes:
non-negated phenotype annotations (aspect P) of a current, prenatal_relevant term with a Vietnamese name.
Claude walks this list in order and picks diseases by hand (e2-plan §3.2).

    python -m evals.e2.pool --limit 300
"""

import argparse
import hashlib

from core.db import connect
from kb.search.engine import PROFILES

MIN_USABLE = 5  # e2-plan §3.1

USABLE_SQL = """
    SELECT dp.disease_id, t.hpo_id, t.label_en,
           (SELECT n.text FROM hpo_name n WHERE n.hpo_id = t.hpo_id AND n.lang = 'vi'
            ORDER BY array_position(%(vi_sources)s::text[], n.source) LIMIT 1) AS label_vi,
           string_agg(DISTINCT dp.freq_raw, ', ') AS freq_raw
    FROM disease_phenotype dp JOIN hpo_term t USING (hpo_id)
    WHERE dp.aspect = 'P' AND NOT dp.is_negated AND t.prenatal_relevant AND NOT t.is_obsolete
      AND EXISTS (SELECT 1 FROM hpo_name n WHERE n.hpo_id = t.hpo_id AND n.lang = 'vi')
      {where}
    GROUP BY dp.disease_id, t.hpo_id, t.label_en
    ORDER BY dp.disease_id, t.hpo_id
"""


def usable_phenotypes(conn, disease_ids: list[str] | None = None) -> dict[str, list[dict]]:
    """disease_id -> its usable phenotypes (hpo_id, label_en, label_vi, freq_raw); all diseases when ids is None."""
    where = "" if disease_ids is None else "AND dp.disease_id = ANY(%(ids)s)"
    out: dict[str, list[dict]] = {}
    for d, h, en, vi, freq in conn.execute(USABLE_SQL.format(where=where),
                                           {"vi_sources": PROFILES["hpo_vi_label_sources"], "ids": disease_ids}):
        out.setdefault(d, []).append({"hpo_id": h, "label_en": en, "label_vi": vi, "freq_raw": freq})
    return out


def disease_genes(conn, disease_ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for d, sym in conn.execute("""SELECT gd.disease_id, g.symbol FROM gene_disease gd JOIN gene g USING (ncbi_gene_id)
                                  WHERE gd.disease_id = ANY(%s) ORDER BY g.symbol""", (disease_ids,)):
        out.setdefault(d, []).append(sym)
    return out


def pool(conn) -> list[dict]:
    """Qualifying diseases in sha1(disease_id) order, with name, genes and usable phenotypes."""
    usable = {d: p for d, p in usable_phenotypes(conn).items() if len(p) >= MIN_USABLE}
    names = dict(conn.execute("SELECT disease_id, name FROM disease WHERE name IS NOT NULL AND disease_id = ANY(%s)",
                              (list(usable),)))
    genes = disease_genes(conn, list(names))
    ids = sorted((d for d in names if d in genes), key=lambda d: hashlib.sha1(d.encode()).hexdigest())
    return [{"disease_id": d, "name": names[d], "genes": genes[d], "phenotypes": usable[d]} for d in ids]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args()
    with connect() as conn:
        diseases = pool(conn)
    print(f"{len(diseases)} qualifying diseases; first {min(args.limit, len(diseases))} in sha1(disease_id) order\n")
    for n, d in enumerate(diseases[:args.limit], 1):
        print(f"#{n} {d['disease_id']} | {d['name']} | genes: {', '.join(d['genes'])}")
        for p in d["phenotypes"]:
            print(f"    {p['hpo_id']} | {p['label_en']} | {p['label_vi']} | freq: {p['freq_raw'] or '—'}")
        print()


if __name__ == "__main__":
    main()
