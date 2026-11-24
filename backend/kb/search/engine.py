"""Shared retrieval engine: legs (dense, trigram, fulltext, visual) -> RRF -> prior -> require_leg -> optional
ColBERT rerank.

A Corpus says WHERE to search (tables, columns, the unit results are grouped by, mandatory filter).
A profile in profiles.toml says HOW (legs, depths, weights, prior weights, rerank).
The engine never drops a result: each Candidate carries the absolute scores (dense cosine, trigram
similarity, ColBERT) from which the caller decides what counts as "no match".
Design: docs/retrieval-plan.md; fulltext, visual, require_leg: docs/evidence-impl-plan.md task 1.
"""

import contextlib
import re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from langfuse import get_client, observe

from core.db import connect
from core.embedding import colbert, embed_texts, embed_visual_query, maxsim
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
    embedding: str | None = None        # vector column (dense leg)
    norm: str | None = None             # normalized-text column (trigram leg)
    tsv: str | None = None              # tsvector column (fulltext leg)
    image_embedding: str | None = None  # halfvec column (visual leg)
    where: str = "TRUE"  # mandatory filter; may reference %(name)s params given to retrieve()
    prior_labels: Callable | None = None  # (conn, keys) -> {key: set of labels}; the profile weights labels


LEG_COLUMN = {"dense": "embedding", "trigram": "norm", "fulltext": "tsv", "visual": "image_embedding"}


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
    unknown = set(profile["legs"]) - set(LEG_COLUMN)
    if unknown or not profile["legs"]:
        raise ValueError(f"profile {name}: unknown or no legs {unknown}")
    if "require_leg" in profile and profile["require_leg"] not in profile["legs"]:
        raise ValueError(f"profile {name}: require_leg {profile['require_leg']!r} is not one of its legs")
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


def _hnsw_settings(cur, leg: dict) -> None:
    """ef_search for the leg; iterative scan so a filtered HNSW scan still returns `depth` rows (T80, T82)."""
    for name, value in (("hnsw.ef_search", max(leg["depth"], 40)), ("hnsw.iterative_scan", "strict_order"),
                        ("hnsw.max_scan_tuples", PROFILES["hnsw_max_scan_tuples"])):
        cur.execute("SELECT set_config(%s, %s, true)", (name, str(value)))


def _dense(cur, corpus: Corpus, query: str, leg: dict, params: dict, query_vector=None):
    vector = str(query_vector if query_vector is not None else embed_texts([query])[0])
    _hnsw_settings(cur, leg)
    return cur.execute(
        f"SELECT {corpus.key}, {corpus.text}, 1 - ({corpus.embedding} <=> %(qvec)s::vector) "
        f"FROM {corpus.source} WHERE ({corpus.where}) AND {corpus.embedding} IS NOT NULL "
        f"ORDER BY {corpus.embedding} <=> %(qvec)s::vector LIMIT %(depth)s",
        {**params, "qvec": vector, "depth": leg["depth"]},
    ).fetchall()


def _trigram(cur, corpus: Corpus, query: str, leg: dict, params: dict, query_vector=None):
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


def _fulltext(cur, corpus: Corpus, query: str, leg: dict, params: dict, query_vector=None):
    """Any word of the query, English stemming or as-is ('simple', for Vietnamese documents) (evidence-plan §3)."""
    terms = " or ".join(re.findall(r"\w+", query.lower()))
    if not terms:
        return []
    return cur.execute(
        f"SELECT {corpus.key}, {corpus.text}, ts_rank_cd({corpus.tsv}, x.q) FROM {corpus.source} "
        f"CROSS JOIN LATERAL (SELECT websearch_to_tsquery('english', %(terms)s) "
        f"|| websearch_to_tsquery('simple', %(terms)s) AS q) x "
        f"WHERE ({corpus.where}) AND {corpus.tsv} @@ x.q ORDER BY 3 DESC LIMIT %(depth)s",
        {**params, "terms": terms, "depth": leg["depth"]},
    ).fetchall()


def _visual(cur, corpus: Corpus, query: str, leg: dict, params: dict, query_vector=None):
    """The query embedded by the multimodal model against figure images (T72); never uses query_vector."""
    from kb.ingest.config import INGEST

    qimg = embed_visual_query(query, model=INGEST["visual_model"], instruction=INGEST["visual_query_instruction"],
                              timeout=INGEST["visual_timeout"])
    _hnsw_settings(cur, leg)
    col = corpus.image_embedding
    return cur.execute(
        f"SELECT {corpus.key}, {corpus.text}, 1 - ({col} <=> %(qimg)s::halfvec) FROM {corpus.source} "
        f"WHERE ({corpus.where}) AND {col} IS NOT NULL ORDER BY {col} <=> %(qimg)s::halfvec LIMIT %(depth)s",
        {**params, "qimg": str(qimg), "depth": leg["depth"]},
    ).fetchall()


LEG_FUNCTIONS = {"dense": _dense, "trigram": _trigram, "fulltext": _fulltext, "visual": _visual}


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
def retrieve(corpus: Corpus, query: str, profile_name: str, k: int = 20, params: dict | None = None,
             query_vector: list[float] | None = None, conn=None) -> list[Candidate]:
    """`query_vector`: the dense leg uses it instead of embedding `query`. `conn`: run on this connection (never
    closed or committed here) instead of opening one."""
    profile, params, timings = load_profile(profile_name), params or {}, {}
    for leg_name in profile["legs"]:
        if getattr(corpus, LEG_COLUMN[leg_name]) is None:
            raise ValueError(f"corpus {corpus.name} has no {LEG_COLUMN[leg_name]} for leg {leg_name}")
    with (contextlib.nullcontext(conn) if conn is not None else connect()) as c, c.cursor() as cur:
        per_leg = {}
        for leg_name, leg in profile["legs"].items():
            t0 = time.perf_counter()
            per_leg[leg_name] = _group(LEG_FUNCTIONS[leg_name](cur, corpus, query, leg, params, query_vector))
            timings[leg_name] = round(time.perf_counter() - t0, 3)

        keys = list(dict.fromkeys(key for hits in per_leg.values() for key in hits))
        labels = corpus.prior_labels(c, keys) if profile.get("prior") and corpus.prior_labels else {}

    ranked = fuse(per_leg, profile, labels)
    if required := profile.get("require_leg"):   # e.g. keep only candidates the fulltext leg also found (T81)
        ranked = [x for x in ranked if required in x.legs]
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
        input={"corpus": corpus.name, "query": query, "profile": profile_name, "k": k, "params": params,
               "query_vector": query_vector is not None},
        output=[{"key": c.key, "text": c.text, "fused": round(c.fused, 5), "rerank": c.rerank,
                 "legs": {n: [h.rank, round(h.score, 3)] for n, h in c.legs.items()}} for c in result],
        metadata={"timings_s": timings, "profile_config": profile},
    )
    return result
