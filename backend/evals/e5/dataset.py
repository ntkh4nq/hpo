"""E5 pairs (disease, phenotype) for choosing the find_passages configuration (docs/ingestion-plan.md §11,
docs/evidence-plan.md §8). Deterministic: building twice gives the same file.

    python -m evals.e5.dataset build     # writes evals/e5/dataset.jsonl from the KB (layers 2 and 3)
    python -m evals.e5.dataset upload    # Langfuse dataset e5-literature-search (archives items no longer in the file)
"""

import argparse
import hashlib
import json
import tomllib
from collections import Counter
from pathlib import Path

from core.db import connect
from kb.ingest.scope import POSITIVE, scope_disease_ids, scope_tags

HERE = Path(__file__).parent
CONFIG = tomllib.loads((HERE / "config.toml").read_text())
DATASET = HERE / "dataset.jsonl"
LANGFUSE_DATASET = "e5-literature-search"


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def load_rows() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def components(edges: list[set[str]], nodes: list[str]) -> dict[str, str]:
    """Node -> group key (smallest node of its connected component); nodes sharing a set are connected."""
    parent = {n: n for n in nodes}

    def find(n: str) -> str:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for group in edges:
        members = sorted(group & parent.keys())
        for m in members[1:]:
            a, b = find(members[0]), find(m)
            if a != b:
                parent[max(a, b)] = min(a, b)
    return {n: find(n) for n in nodes}


def build() -> list[dict]:
    with connect() as conn:
        docs = [set(r[0]) for r in conn.execute(
            "SELECT disease_ids FROM document WHERE source = 'genereviews' AND status = 'ACTIVE'")]
        scope = set(scope_disease_ids(conn))
        candidates = sorted({d for ids in docs for d in ids} & scope, key=sha1)
        terms = conn.execute(
            f"""SELECT DISTINCT dp.disease_id, dp.hpo_id, t.label_en, d.name
                FROM disease_phenotype dp JOIN hpo_term t USING (hpo_id) JOIN disease d USING (disease_id)
                WHERE dp.disease_id = ANY(%s) AND {POSITIVE} AND NOT t.is_obsolete
                  AND string_to_array(t.prenatal_basis, ';') && %s""",
            (candidates, scope_tags())).fetchall()
    by_disease: dict[str, list[tuple]] = {}
    for d, h, label, name in terms:
        by_disease.setdefault(d, []).append((h, label, name))
    pairs = []
    for d in candidates:
        chosen = sorted(by_disease.get(d, []), key=lambda t: sha1(d + t[0]))[:CONFIG["terms_per_disease"]]
        pairs += [{"disease_id": d, "disease_name": name, "hpo_id": h, "hpo_label": label} for h, label, name in chosen]
        if len(pairs) >= CONFIG["pairs"]:
            break
    pairs = pairs[:CONFIG["pairs"]]

    diseases = sorted({p["disease_id"] for p in pairs})
    group_of = components(docs, diseases)
    sizes = Counter(group_of[p["disease_id"]] for p in pairs)
    if max(sizes.values()) > CONFIG["max_group_share"] * len(pairs):
        raise SystemExit(f"largest group holds {max(sizes.values())}/{len(pairs)} pairs: stop and report (§11 step 4)")
    side, count = {}, {"dev": 0, "test": 0}
    for g in sorted(sizes, key=sha1):            # eval-strategy §4: each group to the smaller side, ties to dev
        side[g] = "dev" if count["dev"] <= count["test"] else "test"
        count[side[g]] += sizes[g]
    rows = [{"id": "E5-" + sha1(p["disease_id"] + p["hpo_id"])[:12], **p, "group": group_of[p["disease_id"]],
             "split": side[group_of[p["disease_id"]]], "split_version": CONFIG["split_version"]} for p in pairs]
    return rows


def upload() -> None:
    from langfuse import get_client

    rows = load_rows()
    langfuse = get_client()
    langfuse.create_dataset(name=LANGFUSE_DATASET, description="(disease, phenotype) pairs -> passages that support "
                            "'the disease has the phenotype' (E5, docs/ingestion-plan.md §11)", metadata={})
    for r in rows:
        langfuse.create_dataset_item(
            dataset_name=LANGFUSE_DATASET, id=r["id"],
            input={k: r[k] for k in ("disease_id", "disease_name", "hpo_id", "hpo_label")},
            metadata={"split": r["split"], "split_version": r["split_version"], "group": r["group"]})
    wanted = {r["id"] for r in rows}
    for item in langfuse.get_dataset(LANGFUSE_DATASET).items:
        if item.id not in wanted and item.status != "ARCHIVED":
            langfuse.create_dataset_item(dataset_name=LANGFUSE_DATASET, id=item.id, input=item.input, status="ARCHIVED")
    langfuse.flush()
    print(f"{LANGFUSE_DATASET}: {len(rows)} items")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "upload"))
    args = parser.parse_args()
    if args.command == "upload":
        upload()
        return
    rows = build()
    DATASET.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    groups = {}
    for r in rows:
        groups.setdefault(r["split"], set()).add(r["group"])
    print(f"{len(rows)} pairs, {len({r['disease_id'] for r in rows})} diseases; "
          + ", ".join(f"{s}: {sum(r['split'] == s for r in rows)} pairs / {len(g)} groups" for s, g in sorted(groups.items()))
          + f"; largest group {max(Counter(r['group'] for r in rows).values())}")


if __name__ == "__main__":
    main()
