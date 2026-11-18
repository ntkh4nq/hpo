"""Score and order every indexed disease against the query terms (docs/ranking-plan.md §4-§6).

IC (Resnik 1995 / Phenomizer): a term scores the largest IC of an ancestor it shares with the disease's positive
annotations; the disease score is the weighted mean over terms (one-way, Gong 2018 Eq. 12).
PPO (PhenoBrain): a term scores w · ln(P(q | D) / P_bg(q)); the disease score is the sum.
A term excluded at the disease (§3.4) scores −IC*(q) and w · ln(not_likelihood_ratio) respectively.
"""

from typing import NamedTuple

import numpy as np

from ranking.index import CONFIG, Index
from ranking.schema import QueryTerm

METHODS = ("ic", "ppo", "rrf")


class RankedDisease(NamedTuple):
    disease_id: str
    score: float
    rank: float                        # mean rank of its tie group (Gong 2018)


def excluded_mask(index: Index, q: str) -> np.ndarray:
    """Diseases where q is excluded: q or an ancestor of q is excluded, and no positive annotation is at or below q."""
    mask = np.zeros(index.n, dtype=bool)
    for a in index.ancestors.get(q, ()):
        if a in index.excluded_by_term:
            mask[index.excluded_by_term[a]] = True
    if q in index.under:
        mask[index.under[q][0]] = False
    return mask


def best_ic(index: Index, q: str) -> float:
    """IC*(q): the most a full match of q can score: ic[q], or the largest ancestor IC when ic[q] is NULL."""
    if q in index.ic:
        return index.ic[q]
    return max((index.ic[a] for a in index.ancestors.get(q, ()) if a in index.ic), default=0.0)


def _ic(index: Index, terms: list[QueryTerm]) -> np.ndarray:
    total = np.zeros(index.n)
    for term in terms:
        s = np.zeros(index.n)
        # ancestors by increasing IC: a later (higher) IC overwrites, so each disease keeps its best shared ancestor
        for a in sorted((a for a in index.ancestors.get(term.hpo_id, ()) if a in index.ic), key=index.ic.get):
            if a in index.under:
                s[index.under[a][0]] = index.ic[a]
        s[excluded_mask(index, term.hpo_id)] = -best_ic(index, term.hpo_id)
        total += term.weight * s
    weight = sum(t.weight for t in terms)
    return total / weight if weight else total


def _ppo(index: Index, terms: list[QueryTerm], config: dict = CONFIG) -> np.ndarray:
    total = np.zeros(index.n)
    default = config["default_frequency"]
    for term in terms:
        q = term.hpo_id
        background = max(len(index.under[q][0]) / index.n if q in index.under else 0.0, 1 / index.n)
        p = np.full(index.n, background)
        for table in (index.under, index.direct_by_term):  # direct annotation (case 1) overrides case 2
            if q in table:
                idx, freq = table[q]
                p[idx] = np.where(np.isnan(freq), default, freq)
        s = np.log(p / background)
        s[excluded_mask(index, q)] = np.log(config["not_likelihood_ratio"])
        total += term.weight * s
    return total


def ic_scores(index: Index, terms: list[QueryTerm]) -> dict[str, float]:
    return dict(zip(index.disease_ids, _ic(index, terms).tolist()))


def ppo_scores(index: Index, terms: list[QueryTerm]) -> dict[str, float]:
    return dict(zip(index.disease_ids, _ppo(index, terms).tolist()))


def _order(scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(positions by score desc then disease_id asc, mean rank of each position's tie group, per position)."""
    order = np.argsort(-scores, kind="stable")  # disease_ids are sorted, so a stable sort breaks ties by id
    ordered = scores[order]
    starts = np.concatenate(([0], np.flatnonzero(np.diff(ordered) != 0) + 1))
    ends = np.append(starts[1:], len(scores))
    rank = np.empty(len(scores))
    rank[order] = np.repeat((starts + 1 + ends) / 2, ends - starts)
    return order, rank


def order_all(index: Index, terms: list[QueryTerm], method: str, config: dict = CONFIG) -> list[RankedDisease]:
    """Every indexed disease, best first."""
    if method == "ic":
        scores = _ic(index, terms)
    elif method == "ppo":
        scores = _ppo(index, terms, config)
    elif method == "rrf":  # Cormack 2009 over the mean ranks of both methods
        _, rank_ic = _order(_ic(index, terms))
        _, rank_ppo = _order(_ppo(index, terms, config))
        scores = 1 / (config["rrf_k"] + rank_ic) + 1 / (config["rrf_k"] + rank_ppo)
    else:
        raise ValueError(f"unknown method {method!r}; expected one of {METHODS}")
    order, rank = _order(scores)
    ids = index.disease_ids
    return [RankedDisease(ids[i], float(scores[i]), float(rank[i])) for i in order.tolist()]
