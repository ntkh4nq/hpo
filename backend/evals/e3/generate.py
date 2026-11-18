"""E3: simulated patients from HPO annotations (docs/ranking-plan.md §9.1-§9.3), Köhler 2009 protocol.

Diseases: evals.e2.pool.pool() minus the 40 E2 diseases, split dev/test by gene groups (diseases sharing a gene
stay on one side), then walked in sha1(disease_id) order on each side until diseases_per_side have patients.
Per disease four patients: optimal (phenotypes drawn by frequency), imprecision (each replaced by a random
ancestor), noise (plus unrelated terms), noise_imprecision. Deterministic: rerunning with the same KB and
config.toml rewrites the same dataset.jsonl.

    python -m evals.e3.generate
"""

import hashlib
import json
import random
import tomllib
from pathlib import Path

from core.db import connect
from evals.common.provenance import git_version
from evals.e2.check import load_cases as load_e2_cases
from evals.e2.pool import pool
from ranking.index import Index, load_index

HERE = Path(__file__).parent
CONFIG_TEXT = (HERE / "config.toml").read_text()
CONFIG = tomllib.loads(CONFIG_TEXT)
DATASET = HERE / "dataset.jsonl"
META = HERE / "dataset_meta.json"
ROOT = "HP:0000118"  # Phenotypic abnormality: IC 0, carries no information
VARIANTS = ("optimal", "imprecision", "noise", "noise_imprecision")
SIDES = ("dev", "test")


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def assign_sides(genes: dict[str, set[str]]) -> dict[str, str]:
    """disease_id -> "dev" | "test"; diseases sharing a gene (ncbi_gene_id) form one group (connected components).

    Gene ids, not symbols: the KB has distinct genes sharing a symbol (e.g. 7 genes named "-").
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = x = parent[parent[x]]
        return x

    by_gene: dict[str, str] = {}
    for d, gs in genes.items():
        for g in gs:
            parent[find(d)] = find(by_gene.setdefault(g, d))
    groups: dict[str, list[str]] = {}
    for d in genes:
        groups.setdefault(find(d), []).append(d)
    side, size = {}, {"dev": 0, "test": 0}
    for members in sorted(groups.values(), key=lambda m: sha1(min(m))):
        s = "dev" if size["dev"] <= size["test"] else "test"
        size[s] += len(members)
        side.update((d, s) for d in members)
    return side


def descendants_of(index: Index) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for t, ancestors in index.ancestors.items():
        for a in ancestors:
            out.setdefault(a, set()).add(t)
    return out


def noise_pool(index: Index) -> list[str]:
    """Candidate noise terms before removing those related to a disease: current, prenatal, with an IC."""
    return sorted(t for t in index.prenatal if t in index.ic and t in index.ancestors)


def patients(index: Index, disease_id: str, seed: int, descendants: dict[str, set[str]], pool_terms: list[str],
             config: dict = CONFIG) -> dict[str, list[str]] | None:
    """The four patients of one disease (§9.2), or None when no draw keeps min_terms phenotypes."""
    rng = random.Random(f"{seed}:{disease_id}")
    keep = config["missing_frequency_keep"]
    usable = sorted((t, keep if f is None else f) for t, f in index.direct.get(disease_id, {}).items()
                    if t in index.prenatal and index.labels[t][1] is not None)  # has a Vietnamese name, like E2
    optimal = None
    for _ in range(config["max_attempts"]):
        drawn = [t for t, f in usable if rng.random() < f]
        if len(drawn) >= config["min_terms"]:
            optimal = drawn
            break
    if optimal is None:
        return None

    imprecise = set()
    for t in optimal:
        candidates = sorted(a for a in index.ancestors[t] if a not in (t, ROOT) and ROOT in index.ancestors[a])
        imprecise.add(rng.choice(candidates) if candidates else t)
    imprecise = sorted(imprecise)

    annotated = set(index.direct[disease_id]) | set(index.excluded_terms.get(disease_id, {}))
    related = set().union(*(index.ancestors[t] | descendants[t] for t in annotated))
    noise_terms = [t for t in pool_terms if t not in related]

    noise = optimal + rng.sample(noise_terms, int(len(optimal) * config["noise_ratio"]))
    noise_imprecise = imprecise + rng.sample(noise_terms, int(len(imprecise) * config["noise_ratio"]))
    return {"optimal": sorted(optimal), "imprecision": imprecise, "noise": sorted(noise),
            "noise_imprecision": sorted(noise_imprecise)}


def candidate_diseases(conn) -> dict[str, set[str]]:
    """pool() without the E2 diseases (§9.1 step 1), each with its gene ids."""
    e2 = {c["disease_id"] for c in load_e2_cases()}
    out: dict[str, set[str]] = {d["disease_id"]: set() for d in pool(conn) if d["disease_id"] not in e2}
    for d, g in conn.execute("SELECT disease_id, ncbi_gene_id FROM gene_disease WHERE disease_id = ANY(%s)",
                             (list(out),)):
        out[d].add(g)
    return out


def generate(conn, index: Index, config: dict = CONFIG) -> tuple[list[dict], dict]:
    """(dataset rows, summary per side)."""
    side_of = assign_sides(candidate_diseases(conn))
    descendants, pool_terms = descendants_of(index), noise_pool(index)
    rows, summary = [], {}
    for side in SIDES:
        seed = config[f"{side}_seed"]
        ids = sorted((d for d, s in side_of.items() if s == side), key=sha1)
        count = skipped = 0
        for d in ids:
            if count == config["diseases_per_side"]:
                break
            found = patients(index, d, seed, descendants, pool_terms, config)
            if found is None:
                skipped += 1
                continue
            count += 1
            rows += [{"id": f"E3-{side}-{count:03d}-{v}", "split": side, "disease_id": d, "variant": v,
                      "terms": found[v]} for v in VARIANTS]
        summary[side] = {"pool_diseases": len(ids), "diseases": count, "patients": count * len(VARIANTS),
                         "skipped_diseases": skipped}
    return rows, summary


def load_rows() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    index = load_index()
    with connect() as conn:
        rows, summary = generate(conn, index)
    DATASET.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    meta = {"split_version": CONFIG["split_version"], "hpo_release": index.hpo_release, "config": CONFIG_TEXT,
            "git": git_version(), "sides": summary,
            "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest()}
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{DATASET.name}: {len(rows)} patients, sha256 {meta['dataset_sha256'][:12]}")
    for side, s in summary.items():
        print(f"  {side}: {s['diseases']} diseases, {s['patients']} patients "
              f"({s['pool_diseases']} diseases on this side, {s['skipped_diseases']} skipped: < {CONFIG['min_terms']} "
              f"phenotypes after {CONFIG['max_attempts']} draws)")


if __name__ == "__main__":
    main()
