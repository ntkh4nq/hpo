"""search_hpo: candidate HPO terms for a clinical phrase (Vietnamese or English).

One unit = one current HPO term; its labels, synonyms, definition and Vietnamese translations all
point to it (hpo_name). The prior is the prenatal flag, weighted per source (T17c).
"""

import csv

from kb.db import connect
from kb.query.retrieval import PROFILES, Corpus, retrieve
from kb.seed import PRENATAL_RULES

with PRENATAL_RULES.open() as f:
    _SOURCE_OF_TAG = {f"{r['kind']}:{r['hpo_id']}": r["source"] for r in csv.DictReader(f)}


def _prenatal_sources(conn, keys: list[str]) -> dict[str, set[str]]:
    rows = conn.execute("SELECT hpo_id, prenatal_basis FROM hpo_term WHERE hpo_id = ANY(%s) AND prenatal_basis IS NOT NULL",
                        (keys,))
    return {h: {_SOURCE_OF_TAG[tag] for tag in basis.split(";")} for h, basis in rows}


HPO_NAMES = Corpus(
    name="hpo_names",
    source="hpo_name n",
    key="n.hpo_id",
    text="n.text",
    embedding="n.embedding",
    norm="n.text_norm",
    prior_labels=_prenatal_sources,
)


def search_hpo(text: str, k: int = 20, profile: str | None = None) -> list[dict]:
    candidates = retrieve(HPO_NAMES, text, profile or PROFILES["hpo_default"], k=k)
    with connect() as conn:
        labels = dict(conn.execute(
            """SELECT t.hpo_id, json_build_object('en', t.label_en, 'vi', (
                   SELECT n.text FROM hpo_name n WHERE n.hpo_id = t.hpo_id AND n.lang = 'vi'
                   ORDER BY array_position(%s::text[], n.source) LIMIT 1))
               FROM hpo_term t WHERE t.hpo_id = ANY(%s)""",
            (PROFILES["hpo_vi_label_sources"], [c.key for c in candidates])))
    return [{
        "hpo_id": c.key,
        "label_en": labels[c.key]["en"],
        "label_vi": labels[c.key]["vi"],
        "matched_text": c.text,
        "scores": {"fused": c.fused, "prior": c.prior, "rerank": c.rerank,
                   **{f"{leg}_rank": h.rank for leg, h in c.legs.items()},
                   **{f"{leg}_score": h.score for leg, h in c.legs.items()}},
    } for c in candidates]
