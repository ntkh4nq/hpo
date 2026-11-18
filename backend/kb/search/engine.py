"""Shared retrieval engine: dense + lexical legs -> RRF -> prior -> optional ColBERT rerank.

A Corpus says WHERE to search (tables, columns, the unit results are grouped by, mandatory filter).
A profile in profiles.toml says HOW (legs, depths, weights, prior weights, rerank).
The engine never drops a result: each Candidate carries the absolute scores (dense cosine, trigram
similarity, ColBERT) from which the caller decides what counts as "no match".
Design: docs/retrieval-plan.md.
"""

import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from langfuse import get_client, observe

from core.db import connect
from core.embedding import colbert, embed_texts, maxsim
from core.text import norm

PROFILES = tomllib.loads(Path(__file__).with_name("profiles.toml").read_text())
TRIGRAM_FUNCTIONS = {  # function -> (index-backed operator, its threshold setting)
    "word_similarity": ("<%%", "pg_trgm.word_similarity_threshold"),
    "similarity": ("%%", "pg_trgm.similarity_threshold"),
}


@dataclass(frozen=True)
class Corpus:
    name: str
    source: str     # FROM clause, joins included
    key: str        # expression of the unit results are grouped by (hpo_id, chunk_id, ...)
    text: str       # expression of the matched text
    embedding: str  # vector column
    norm: str       # normalized-text column for the trigram leg
    where: str = "TRUE"  # mandatory filter; may reference %(name)s params given to retrieve()
    prior_labels: Callable | None = None  # (conn, keys) -> {key: set of labels}; the profile weights labels


@dataclass
class Hit:
    """A unit's best row in one leg."""
    rank: int
    score: float
    text: str


@dataclass
class Candidate:
    key: str
    legs: dict[str, Hit] = field(default_factory=dict)
    rrf: float = 0.0
    prior: float = 0.0
    fused: float = 0.0
    rerank: float | None = None

    @property
    def text(self) -> str:
        return min(self.legs.values(), key=lambda h: h.rank).text


def load_profile(name: str) -> dict:
    profile = PROFILES[name]
    unknown = set(profile["legs"]) - {"dense", "trigram"}
    if unknown or not profile["legs"]:
        raise ValueError(f"profile {name}: unknown or no legs {unknown}")
    if profile.get("rerank", {}).get("method", "colbert") != "colbert":
        raise ValueError(f"profile {name}: only colbert rerank exists")
    return profile


def _group(rows) -> dict[str, Hit]:
    """Rows come best first; a unit's rank is the position of its first (best) row among units."""
    hits: dict[str, Hit] = {}
    for key, text, score in rows:
        if key not in hits:
            hits[key] = Hit(rank=len(hits) + 1, score=float(score), text=text)
    return hits


def _dense(cur, corpus: Corpus, query: str, leg: dict, params: dict):
    vector = str(embed_texts([query])[0])
    cur.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(max(leg["depth"], 40)),))
    return cur.execute(
        f"SELECT {corpus.key}, {corpus.text}, 1 - ({corpus.embedding} <=> %(qvec)s::vector) "
        f"FROM {corpus.source} WHERE ({corpus.where}) AND {corpus.embedding} IS NOT NULL "
        f"ORDER BY {corpus.embedding} <=> %(qvec)s::vector LIMIT %(depth)s",
        {**params, "qvec": vector, "depth": leg["depth"]},
    ).fetchall()


def _trigram(cur, corpus: Corpus, query: str, leg: dict, params: dict):
    fn = leg.get("function", "word_similarity")
    operator, setting = TRIGRAM_FUNCTIONS[fn]
    cur.execute("SELECT set_config(%s, %s, true)", (setting, str(leg["threshold"])))
    return cur.execute(
        f"SELECT {corpus.key}, {corpus.text}, {fn}(%(q)s, {corpus.norm}) AS s "
        f"FROM {corpus.source} WHERE ({corpus.where}) AND %(q)s {operator} {corpus.norm} "
        # word_similarity is 1.0 for every name containing the whole query: break ties with the
        # symmetric similarity so an exact name beats longer names that merely contain it
        f"ORDER BY s DESC, similarity(%(q)s, {corpus.norm}) DESC LIMIT %(depth)s",
        {**params, "q": norm(query), "depth": leg["depth"]},
    ).fetchall()


LEG_FUNCTIONS = {"dense": _dense, "trigram": _trigram}


def fuse(per_leg: dict[str, dict[str, Hit]], profile: dict, labels: dict[str, set[str]]) -> list[Candidate]:
    """Weighted RRF over the legs, multiplied by (1 + prior); best first."""
    rrf_k, weights = profile.get("rrf_k", 60), profile.get("prior", {})
    candidates: dict[str, Candidate] = {}
    for leg_name, hits in per_leg.items():
        for key, hit in hits.items():
            c = candidates.setdefault(key, Candidate(key))
            c.legs[leg_name] = hit
            c.rrf += profile["legs"][leg_name].get("weight", 1.0) / (rrf_k + hit.rank)
    for c in candidates.values():
        c.prior = max((weights.get(label, 0.0) for label in labels.get(c.key, ())), default=0.0)
        c.fused = c.rrf * (1 + c.prior)
    return sorted(candidates.values(), key=lambda c: (-c.fused, c.key))


@observe(name="retrieve", as_type="retriever", capture_input=False, capture_output=False)
def retrieve(corpus: Corpus, query: str, profile_name: str, k: int = 20, params: dict | None = None) -> list[Candidate]:
    profile, params, timings = load_profile(profile_name), params or {}, {}
    with connect() as conn, conn.cursor() as cur:
        per_leg = {}
        for leg_name, leg in profile["legs"].items():
            t0 = time.perf_counter()
            per_leg[leg_name] = _group(LEG_FUNCTIONS[leg_name](cur, corpus, query, leg, params))
            timings[leg_name] = round(time.perf_counter() - t0, 3)

        keys = list(dict.fromkeys(key for hits in per_leg.values() for key in hits))
        labels = corpus.prior_labels(conn, keys) if profile.get("prior") and corpus.prior_labels else {}

    ranked = fuse(per_leg, profile, labels)
    if rerank := profile.get("rerank"):
        t0 = time.perf_counter()
        head = ranked[:rerank["top"]]
        texts = list(dict.fromkeys(h.text for c in head for h in c.legs.values()))
        if texts:
            q, *vectors = colbert([query, *texts])
            score = {t: maxsim(q, v) for t, v in zip(texts, vectors)}
            for c in head:
                c.rerank = max(score[h.text] for h in c.legs.values())
            ranked = sorted(head, key=lambda c: (-c.rerank, c.key)) + ranked[rerank["top"]:]
        timings["rerank"] = round(time.perf_counter() - t0, 3)

    result = ranked[:k]
    get_client().update_current_span(
        input={"corpus": corpus.name, "query": query, "profile": profile_name, "k": k, "params": params},
        output=[{"key": c.key, "text": c.text, "fused": round(c.fused, 5), "rerank": c.rerank,
                 "legs": {n: [h.rank, round(h.score, 3)] for n, h in c.legs.items()}} for c in result],
        metadata={"timings_s": timings, "profile_config": profile},
    )
    return result
