"""E3 simulated patients (docs/ranking-plan.md §9, §11). Needs `make seed` and evals/e3/dataset.jsonl."""

import hashlib
import json

import pytest

from core.db import connect
from evals.e3.generate import (CONFIG, META, ROOT, SIDES, descendants_of, load_rows, noise_pool, patients)
from ranking.index import load_index


@pytest.fixture(scope="module")
def index():
    with connect() as conn:
        if not conn.execute("SELECT count(*) FROM hpo_term").fetchone()[0]:
            pytest.skip("KB not seeded")
    return load_index()


@pytest.fixture(scope="module")
def rows():
    return load_rows()


@pytest.fixture(scope="module")
def by_disease(rows):
    out: dict[tuple[str, str], dict[str, list[str]]] = {}
    for r in rows:
        out.setdefault((r["split"], r["disease_id"]), {})[r["variant"]] = r["terms"]
    return out


def test_file_matches_meta(rows):
    meta = json.loads(META.read_text())
    assert meta["dataset_sha256"] == hashlib.sha256(META.with_name("dataset.jsonl").read_bytes()).hexdigest()
    assert len(rows) == 2 * CONFIG["diseases_per_side"] * 4


def test_same_seed_same_patients(index, by_disease):
    descendants, pool_terms = descendants_of(index), noise_pool(index)
    for (side, d), variants in list(by_disease.items())[:: 100]:
        assert patients(index, d, CONFIG[f"{side}_seed"], descendants, pool_terms) == variants


def test_optimal_terms_are_positive_annotations(index, by_disease):
    for (_, d), v in by_disease.items():
        assert len(v["optimal"]) >= CONFIG["min_terms"]
        assert set(v["optimal"]) <= set(index.direct[d])


def test_noise_terms_unrelated_to_the_disease(index, by_disease):
    descendants = descendants_of(index)
    for (_, d), v in list(by_disease.items())[:: 10]:
        annotated = set(index.direct[d]) | set(index.excluded_terms.get(d, {}))
        related = set().union(*(index.ancestors[t] | descendants[t] for t in annotated))
        for variant, base in (("noise", "optimal"), ("noise_imprecision", "imprecision")):
            added = set(v[variant]) - set(v[base])
            assert len(added) == int(len(v[base]) * CONFIG["noise_ratio"])
            assert not added & related


def test_imprecision_terms_are_ancestors_under_root(index, by_disease):
    for (_, d), v in by_disease.items():
        for t in v["imprecision"]:
            if t in v["optimal"]:  # kept: it had no ancestor under the root other than itself
                continue
            assert ROOT in index.ancestors[t] and t != ROOT
            assert any(t in index.ancestors[o] for o in v["optimal"])


def test_dev_and_test_share_no_disease_or_gene(index, by_disease):
    diseases = {s: {d for side, d in by_disease if side == s} for s in SIDES}
    assert not diseases["dev"] & diseases["test"]
    with connect() as conn:
        genes = {s: {g for (g,) in conn.execute("SELECT ncbi_gene_id FROM gene_disease WHERE disease_id = ANY(%s)",
                                                (list(diseases[s]),))} for s in SIDES}
    assert not genes["dev"] & genes["test"]
