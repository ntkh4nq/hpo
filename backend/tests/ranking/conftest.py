"""A hand-built index (docs/ranking-plan.md §11): HP:0000118 -> A -> {B, C}, HP:0000118 -> E.

D1: B positive 0.9 · D2: C positive 0.5 · D3: A positive (no frequency) · D4: E positive, B excluded (NOT)
D5: B positive (1/1) and excluded (0/23) · D6: A positive 0.1 and B positive 0.8 (direct vs. propagated frequency)
"""

import math

import pytest

from ranking.index import Annotation, build_index

R, A, B, C, E = "HP:0000118", "HP:A", "HP:B", "HP:C", "HP:E"
PARENT = {A: R, B: A, C: A, E: R}


def ancestors_of(t: str) -> set[str]:
    out = {t}
    while t in PARENT:
        t = PARENT[t]
        out.add(t)
    return out


def row(d, t, *, neg=False, raw=None, num=None, den=None, hpo=None, pct=None):
    return Annotation(d, t, neg, raw, num, den, hpo, pct, "PCS", ["PMID:1"])


ROWS = [
    row("D1", B, raw="9/10", num=9, den=10),
    row("D2", C, raw="1/2", num=1, den=2),
    row("D3", A),
    row("D4", E), row("D4", B, neg=True),
    row("D5", B, raw="1/1", num=1, den=1), row("D5", B, raw="0/23", num=0, den=23),
    row("D6", A, raw="10%", pct=10.0), row("D6", B, raw="8/10", num=8, den=10),
]


def toy_index():
    ancestors = {t: ancestors_of(t) for t in (R, A, B, C, E)}
    positive = {(r.disease_id, r.hpo_id) for r in ROWS if not r.is_negated and r.freq_num != 0}
    n = len({d for d, _ in positive})
    ic = {}
    for t in ancestors:
        k = len({d for d, u in positive if t in ancestors[u]})
        if k:
            ic[t] = -math.log(k / n)
    return build_index(
        hpo_release="test", ancestors=ancestors, ic=ic, annotations=ROWS,
        diseases={d: (f"disease {d}", "TEST") for d in ("D1", "D2", "D3", "D4", "D5", "D6")},
        genes={}, labels={t: (t, None) for t in ancestors}, prenatal={A, B, C, E},
    )


@pytest.fixture(scope="session")
def toy():
    return toy_index()
