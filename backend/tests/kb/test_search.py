import os

import numpy as np
import pytest

from core.embedding import maxsim
from kb.search.engine import PROFILES, Hit, _group, fuse, load_profile


def test_group_ranks_units_by_their_best_row():
    rows = [("A", "a1", 0.9), ("A", "a2", 0.8), ("B", "b1", 0.7), ("A", "a3", 0.6), ("C", "c1", 0.5)]
    hits = _group(rows)
    assert {k: h.rank for k, h in hits.items()} == {"A": 1, "B": 2, "C": 3}
    assert hits["A"].text == "a1"


def test_fuse_rrf_and_prior():
    profile = {"legs": {"dense": {}, "trigram": {"weight": 2.0}}, "rrf_k": 60, "prior": {"official": 0.5}}
    per_leg = {"dense": {"A": Hit(1, 0.9, "a"), "B": Hit(2, 0.8, "b")},
               "trigram": {"B": Hit(1, 1.0, "b")}}
    ranked = fuse(per_leg, profile, labels={"A": {"official"}})
    assert ranked[0].key == "B"  # in both legs, double-weighted trigram
    assert ranked[0].rrf == pytest.approx(1 / 62 + 2 / 61)
    assert ranked[1].fused == pytest.approx((1 / 61) * 1.5)


def test_maxsim_averages_best_token_match():
    query = np.array([[1.0, 0.0], [0.0, 1.0]])
    passage = np.array([[1.0, 0.0], [0.6, 0.8]])
    assert maxsim(query, passage) == pytest.approx((1.0 + 0.8) / 2)


def test_all_profiles_load():
    names = [n for n, v in PROFILES.items() if isinstance(v, dict)]
    assert PROFILES["hpo_default"] in names
    for name in names:
        load_profile(name)


@pytest.mark.skipif(not os.environ.get("LLM_API_KEY"), reason="needs the gateway")
def test_search_hpo_finds_vsd():
    from kb.search.hpo import search_hpo

    top = search_hpo("thông liên thất", k=5)
    assert top[0]["hpo_id"] == "HP:0001629"
    assert top[0]["label_vi"]
