"""Scoring on the hand-built index of conftest.py (docs/ranking-plan.md §11)."""

import math

import pytest

from ranking.index import CONFIG
from ranking.schema import QueryTerm
from ranking.score import _order, ic_scores, order_all, ppo_scores
from tests.ranking.conftest import A, B, C, E


def q(code, weight=1.0):
    return QueryTerm(hpo_id=code, assertion="PRESENT" if weight == 1.0 else "SUSPECTED", weight=weight)


# ---------- IC

def test_ic_exact_beats_sibling(toy):
    s = ic_scores(toy, [q(B)])
    assert s["D1"] == pytest.approx(toy.ic[B])
    assert s["D2"] == pytest.approx(toy.ic[A])  # C shares A with B
    assert s["D1"] > s["D2"]


def test_ic_query_more_specific_gets_annotation_ic(toy):
    assert ic_scores(toy, [q(B)])["D3"] == pytest.approx(toy.ic[A])


def test_ic_excluded_gets_minus_best_ic(toy):
    assert ic_scores(toy, [q(B)])["D4"] == pytest.approx(-toy.ic[B])


def test_ic_positive_wins_over_excluded(toy):
    assert ic_scores(toy, [q(B)])["D5"] == pytest.approx(toy.ic[B])


def test_ic_weighted_mean(toy):
    s = ic_scores(toy, [q(B), q(C, 0.5)])
    assert s["D2"] == pytest.approx((toy.ic[A] + 0.5 * toy.ic[C]) / 1.5)


# ---------- PPO

def bg(toy, code):
    return max(len(toy.under[code][0]) / toy.n, 1 / toy.n)


def test_ppo_direct_annotation_keeps_its_own_frequency(toy):
    # D6 has A at 0.1 and B (a child of A) at 0.8: a query for A uses 0.1
    assert ppo_scores(toy, [q(A)])["D6"] == pytest.approx(math.log(0.1 / bg(toy, A)))


def test_ppo_ancestor_takes_max_of_descendants(toy):
    s = ppo_scores(toy, [q(A)])
    assert s["D1"] == pytest.approx(math.log(0.9 / bg(toy, A)))
    assert s["D5"] == pytest.approx(math.log(1.0 / bg(toy, A)))  # 1/1 positive; the 0/23 row is excluded
    assert s["D3"] == pytest.approx(math.log(CONFIG["default_frequency"] / bg(toy, A)))  # no frequency


def test_ppo_query_more_specific_falls_back_to_background(toy):
    assert ppo_scores(toy, [q(B)])["D3"] == pytest.approx(0.0)  # ln(bg / bg)


def test_ppo_excluded(toy):
    assert ppo_scores(toy, [q(B)])["D4"] == pytest.approx(math.log(CONFIG["not_likelihood_ratio"]))


def test_ppo_weight_halves_term_score(toy):
    full, half = ppo_scores(toy, [q(B)]), ppo_scores(toy, [q(B, 0.5)])
    for d in full:
        assert half[d] == pytest.approx(full[d] / 2)


# ---------- order_all

def test_mean_rank_and_id_order_on_ties(toy):
    # query E: D4 matches E exactly; the other five share only the root (IC 0) and tie at positions 2-6
    ranked = order_all(toy, [q(E)], "ic")
    assert [r.disease_id for r in ranked] == ["D4", "D1", "D2", "D3", "D5", "D6"]
    assert [r.rank for r in ranked] == [1, 4, 4, 4, 4, 4]


def test_order_helper_mean_rank():
    import numpy as np
    order, rank = _order(np.array([1.0, 3.0, 1.0, 1.0, 0.0]))
    assert order.tolist() == [1, 0, 2, 3, 4]
    assert rank.tolist() == [3.0, 1.0, 3.0, 3.0, 5.0]


def test_rrf_uses_mean_ranks(toy):
    terms = [q(B)]
    ic_rank = {r.disease_id: r.rank for r in order_all(toy, terms, "ic")}
    ppo_rank = {r.disease_id: r.rank for r in order_all(toy, terms, "ppo")}
    k = CONFIG["rrf_k"]
    for r in order_all(toy, terms, "rrf"):
        assert r.score == pytest.approx(1 / (k + ic_rank[r.disease_id]) + 1 / (k + ppo_rank[r.disease_id]))


def test_unknown_method(toy):
    with pytest.raises(ValueError):
        order_all(toy, [q(B)], "llm")
