"""What ranking reads from KB layers 1-2, loaded once per process (docs/ranking-plan.md §3).

An annotation row (aspect P) is *excluded* when it carries the NOT qualifier or a zero frequency (0/m, 0%,
HP:0040285): the disease is recorded as not having the phenotype. Every other row is *positive*. The ranked
diseases are those with at least one positive row; `ic` in the KB is computed over exactly these (§3.3).
"""

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

import numpy as np

from core.db import connect
from kb.search.engine import PROFILES
from phenotype.mapping import hpo_release
from ranking.schema import Gene

CONFIG = tomllib.loads(Path(__file__).with_name("config.toml").read_text())


def frequency(freq_hpo: str | None, freq_num: int | None, freq_den: int | None, freq_pct: float | None,
              terms: dict[str, float] | None = None) -> float | None:
    """P(phenotype | disease) of one annotation row (§3.2); None = no information."""
    if freq_hpo is not None:
        return (terms or CONFIG["frequency_terms"])[freq_hpo]
    if freq_num is not None:
        return freq_num / freq_den
    if freq_pct is not None:
        return freq_pct / 100
    return None


def pair_frequency(values: Iterable[float | None]) -> float | None:
    """Several rows for one (disease, term) pair: the largest known frequency; None when none is known."""
    known = [v for v in values if v is not None]
    return max(known) if known else None


class Annotation(NamedTuple):          # one disease_phenotype row with aspect = 'P'
    disease_id: str
    hpo_id: str
    is_negated: bool
    freq_raw: str | None
    freq_num: int | None
    freq_den: int | None
    freq_hpo: str | None
    freq_pct: float | None
    evidence: str | None
    references: list[str] | None


class SourceRow(NamedTuple):           # what an explanation shows of one source row (principle #6)
    freq_raw: str | None
    evidence: str | None
    references: list[str]
    patients: int = 0                  # freq_den of an "n/m" row: how many reported patients it rests on


class TraitRow(NamedTuple):            # one disease_phenotype row with aspect I (inheritance) or C (clinical course)
    disease_id: str
    aspect: str
    hpo_id: str
    freq_raw: str | None
    references: list[str] | None


@dataclass
class Index:
    hpo_release: str
    disease_ids: list[str]                               # ranked diseases, sorted; array positions follow this order
    ancestors: dict[str, frozenset[str]]                 # current term -> its ancestors, itself included
    ic: dict[str, float]                                 # terms with an IC (others are NULL in the KB)
    direct: dict[str, dict[str, float | None]]           # D -> positive term -> pair frequency (§3.2)
    excluded_terms: dict[str, dict[str, float | None]]   # D -> excluded term -> pair frequency (0 or None)
    rows: dict[tuple[str, str], list[SourceRow]]         # (D, t) -> positive source rows
    excluded_rows: dict[tuple[str, str], list[SourceRow]]
    disease_name: dict[str, str | None]
    disease_db: dict[str, str]
    genes: dict[str, list[Gene]]                         # D -> genes, by symbol
    labels: dict[str, tuple[str, str | None]]            # term -> (label_en, label_vi)
    prenatal: frozenset[str]
    traits: dict[str, list[TraitRow]] = field(default_factory=dict)  # D -> aspect I/C rows, in KB order
    # derived (build_index): numpy arrays of disease positions, for scoring every disease at once
    position: dict[str, int] = field(default_factory=dict)
    under: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)           # a -> (D, max freq)
    direct_by_term: dict[str, tuple[np.ndarray, np.ndarray]] = field(default_factory=dict)  # t -> (D, freq)
    excluded_by_term: dict[str, np.ndarray] = field(default_factory=dict)                   # t -> D

    @property
    def n(self) -> int:
        return len(self.disease_ids)


def _freq_array(values: list[float | None]) -> np.ndarray:
    return np.array([np.nan if v is None else v for v in values], dtype=float)


def build_index(*, hpo_release: str, ancestors: dict[str, Iterable[str]], ic: dict[str, float],
                annotations: Iterable[Annotation], diseases: dict[str, tuple[str | None, str]],
                genes: dict[str, list[Gene]], labels: dict[str, tuple[str, str | None]],
                prenatal: Iterable[str], frequency_terms: dict[str, float] | None = None,
                traits: Iterable[TraitRow] = ()) -> Index:
    """Index from plain KB rows; `diseases` maps disease_id -> (name, db)."""
    ancestors = {t: frozenset(a) for t, a in ancestors.items()}
    pos_freqs: dict[tuple[str, str], list[float | None]] = {}
    neg_freqs: dict[tuple[str, str], list[float | None]] = {}
    rows: dict[tuple[str, str], list[SourceRow]] = {}
    excluded_rows: dict[tuple[str, str], list[SourceRow]] = {}
    for a in annotations:
        f = frequency(a.freq_hpo, a.freq_num, a.freq_den, a.freq_pct, frequency_terms)
        negative = a.is_negated or f == 0
        key = (a.disease_id, a.hpo_id)
        (neg_freqs if negative else pos_freqs).setdefault(key, []).append(f)
        (excluded_rows if negative else rows).setdefault(key, []).append(
            SourceRow(a.freq_raw, a.evidence, list(a.references or []), a.freq_den or 0))

    direct: dict[str, dict[str, float | None]] = {}
    for (d, t), fs in pos_freqs.items():
        direct.setdefault(d, {})[t] = pair_frequency(fs)
    disease_ids = sorted(direct)
    excluded_terms: dict[str, dict[str, float | None]] = {}
    for (d, t), fs in neg_freqs.items():
        if d in direct:  # a disease with no positive annotation is not ranked
            excluded_terms.setdefault(d, {})[t] = pair_frequency(fs)

    index = Index(
        hpo_release=hpo_release, disease_ids=disease_ids, ancestors=ancestors, ic=ic, direct=direct,
        excluded_terms=excluded_terms, rows=rows,
        excluded_rows={k: v for k, v in excluded_rows.items() if k[0] in direct},
        disease_name={d: diseases.get(d, (None, d.split(":", 1)[0]))[0] for d in disease_ids},
        disease_db={d: diseases.get(d, (None, d.split(":", 1)[0]))[1] for d in disease_ids},
        genes={d: genes.get(d, []) for d in disease_ids}, labels=labels, prenatal=frozenset(prenatal),
    )
    for row in traits:
        if row.disease_id in direct:
            index.traits.setdefault(row.disease_id, []).append(row)
    index.position = {d: i for i, d in enumerate(disease_ids)}

    # true-path rule: a positive annotation of t is also one of every ancestor of t; keep the largest frequency
    missing = object()
    under: dict[str, dict[int, float | None]] = {}
    by_term: dict[str, dict[int, float | None]] = {}
    for d, terms in direct.items():
        i = index.position[d]
        for t, f in terms.items():
            by_term.setdefault(t, {})[i] = f
            for a in ancestors.get(t, (t,)):
                cell = under.setdefault(a, {})
                old = cell.get(i, missing)
                cell[i] = f if old is missing or old is None else (old if f is None else max(old, f))
    index.under = {a: (np.fromiter(c.keys(), dtype=np.int32, count=len(c)), _freq_array(list(c.values())))
                   for a, c in under.items()}
    index.direct_by_term = {t: (np.fromiter(c.keys(), dtype=np.int32, count=len(c)), _freq_array(list(c.values())))
                            for t, c in by_term.items()}
    excluded_by_term: dict[str, list[int]] = {}
    for d, terms in excluded_terms.items():
        for t in terms:
            excluded_by_term.setdefault(t, []).append(index.position[d])
    index.excluded_by_term = {t: np.array(v, dtype=np.int32) for t, v in excluded_by_term.items()}
    return index


@lru_cache(maxsize=1)
def load_index() -> Index:
    with connect() as conn:
        ancestors: dict[str, set[str]] = {}
        for t, a in conn.execute("SELECT hpo_id, ancestor_id FROM hpo_ancestor"):
            ancestors.setdefault(t, set()).add(a)
        ic, labels, prenatal = {}, {}, []
        for t, value, en, vi, pre in conn.execute(
                """SELECT t.hpo_id, t.ic, t.label_en,
                          (SELECT n.text FROM hpo_name n WHERE n.hpo_id = t.hpo_id AND n.lang = 'vi'
                           ORDER BY array_position(%s::text[], n.source) LIMIT 1),
                          t.prenatal_relevant
                   FROM hpo_term t WHERE NOT t.is_obsolete""", (PROFILES["hpo_vi_label_sources"],)):
            labels[t] = (en, vi)
            if value is not None:
                ic[t] = value
            if pre:
                prenatal.append(t)
        annotations = [Annotation(*r) for r in conn.execute(
            """SELECT disease_id, hpo_id, is_negated, freq_raw, freq_num, freq_den, freq_hpo, freq_pct,
                      evidence, "references"
               FROM disease_phenotype WHERE aspect = 'P' ORDER BY id""")]
        traits = [TraitRow(*r) for r in conn.execute(
            """SELECT disease_id, aspect, hpo_id, freq_raw, "references"
               FROM disease_phenotype WHERE aspect IN ('I', 'C') AND NOT is_negated ORDER BY id""")]
        diseases = {d: (name, db) for d, name, db in conn.execute("SELECT disease_id, name, db FROM disease")}
        genes: dict[str, list[Gene]] = {}
        for d, gid, sym, assoc, src in conn.execute(
                """SELECT gd.disease_id, g.ncbi_gene_id, g.symbol, gd.association_type, gd.source
                   FROM gene_disease gd JOIN gene g USING (ncbi_gene_id) ORDER BY g.symbol, g.ncbi_gene_id"""):
            genes.setdefault(d, []).append(Gene(ncbi_gene_id=gid, symbol=sym, association_type=assoc, source=src))
    return build_index(hpo_release=hpo_release(), ancestors=ancestors, ic=ic, annotations=annotations,
                       diseases=diseases, genes=genes, labels=labels, prenatal=prenatal, traits=traits)
