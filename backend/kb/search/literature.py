"""find_passages: literature passages (KB layer 3) about one disease, by one HPO term or an English topic.

Always filtered by the disease codes documents declare (T75, T80); an ORPHA code also reaches documents that
declare an OMIM code Orphanet records as the same disease (Exact, T67). The query never holds the disease name:
the filter already keeps that disease (T80). Design: docs/evidence-plan.md §3; docs/evidence-impl-plan.md task 1.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from core.db import connect
from core.embedding import embed_texts
from kb.ingest.config import resolve_strategy
from kb.search.engine import PROFILES, Corpus, load_profile, retrieve


@dataclass(frozen=True)
class PassageRequest:
    disease_id: str
    hpo_id: str | None = None
    topic: str | None = None      # English free text


class Passage(BaseModel):
    chunk_id: int
    ref: str                      # "CHUNK:<chunk_id>", the code answers and explanations cite
    kind: str                     # chunk.kind: text | table | figure
    display_text: str
    figure_path: str | None
    section_path: list[str]       # section.heading_path
    doc_id: int
    source: str
    title: str
    citation: str | None
    url: str | None
    license: str
    disease_ids: list[str]        # chunk.disease_ids
    active: bool                  # chunk.is_active (always true in find results)
    scores: dict[str, float | None] = {}   # fused, rerank, <leg>_rank, <leg>_score


# A parent_child strategy searches the children and reports their parent (the key); `role <> 'parent'` because a
# parent row has no embedding of its own.
DOC_CHUNKS = Corpus(
    name="doc_chunks",
    source="chunk c",
    key="COALESCE(c.parent_id, c.chunk_id)",
    text="c.display_text",
    embedding="c.embedding",
    tsv="c.tsv",
    image_embedding="c.image_embedding",
    where="c.is_active AND c.strategy = %(strategy)s AND c.role <> 'parent' AND c.disease_ids && %(disease_ids)s",
)


def matched_ids(conn, disease_id: str) -> list[str]:
    """The disease, plus for an ORPHA code the OMIM codes its Orphanet entry declares as the same disease."""
    if not disease_id.startswith("ORPHA:"):
        return [disease_id]
    rows = conn.execute("SELECT DISTINCT unnest(disease_ids) FROM document WHERE source = 'orphanet_def' "
                        "AND status = 'ACTIVE' AND %s = ANY(disease_ids)", (disease_id,)).fetchall()
    return [disease_id, *sorted(r[0] for r in rows if r[0].startswith("OMIM:"))]


def query_text(conn, hpo_id: str, kinds: list[str]) -> str:
    """English names of the term of the given kinds: the label first, the rest alphabetically, joined by '; '."""
    rows = conn.execute("SELECT kind, text FROM hpo_name WHERE hpo_id = %s AND lang = 'en' AND kind = ANY(%s)",
                        (hpo_id, kinds)).fetchall()
    names = sorted(rows, key=lambda r: (r[0] != "label", r[1]))
    return "; ".join(dict.fromkeys(text for _, text in names))


def load_passages(conn, chunk_ids: list[int]) -> dict[int, Passage]:
    rows = conn.execute(
        """SELECT c.chunk_id, c.kind, c.display_text, c.figure_path, s.heading_path, c.doc_id, d.source, d.title,
                  d.citation, d.url, d.license, c.disease_ids, c.is_active
           FROM chunk c JOIN document d ON d.doc_id = c.doc_id JOIN section s ON s.section_id = c.section_id
           WHERE c.chunk_id = ANY(%s)""", (list(chunk_ids),)).fetchall()
    return {r[0]: Passage(chunk_id=r[0], ref=f"CHUNK:{r[0]}", kind=r[1], display_text=r[2], figure_path=r[3],
                          section_path=r[4], doc_id=r[5], source=r[6], title=r[7], citation=r[8], url=r[9],
                          license=r[10], disease_ids=r[11], active=r[12]) for r in rows}


def _scores(c) -> dict[str, float | None]:
    return {"fused": c.fused, "rerank": c.rerank, **{f"{leg}_rank": h.rank for leg, h in c.legs.items()},
            **{f"{leg}_score": h.score for leg, h in c.legs.items()}}


def find_passages_many(requests: list[PassageRequest], k: int = 5, strategy: str | None = None,
                       profile: str | None = None, query_kinds: list[str] | None = None,
                       exclude_sources: list[str] | None = None) -> list[list[Passage]]:
    """One DB connection and one embedding call for every request (an explanation has ~37 pairs, T81).
    `exclude_sources`: document sources never returned (the explanations keep disease overviews apart, T110)."""
    for r in requests:
        if (r.hpo_id is None) == (r.topic is None):
            raise ValueError(f"{r.disease_id}: give exactly one of hpo_id, topic")
    strategy = resolve_strategy(strategy)
    profile = profile or PROFILES["literature_profile"]
    kinds = query_kinds or PROFILES["literature_query_kinds"]
    with connect() as conn:
        ids = [matched_ids(conn, r.disease_id) for r in requests]
        queries = [query_text(conn, r.hpo_id, kinds) if r.hpo_id else r.topic.strip() for r in requests]
        todo = [q for q in queries if q]
        vectors = dict(zip(todo, embed_texts(todo))) if todo and "dense" in load_profile(profile)["legs"] else {}
        out = []
        for query, disease_ids in zip(queries, ids):
            if not query:
                out.append([])
                continue
            found = retrieve(DOC_CHUNKS, query, profile, k * 3, params={"disease_ids": disease_ids, "strategy": strategy},
                             query_vector=vectors.get(query), conn=conn)
            rows = load_passages(conn, [c.key for c in found])
            passages, seen = [], set()
            for c in found:
                p = rows[c.key]
                if p.display_text in seen or p.source in (exclude_sources or ()):
                    continue
                seen.add(p.display_text)
                passages.append(p.model_copy(update={"scores": _scores(c)}))
                if len(passages) == k:
                    break
            out.append(passages)
    return out


def find_passages(disease_id: str, *, hpo_id: str | None = None, topic: str | None = None, k: int = 5,
                  strategy: str | None = None, profile: str | None = None,
                  query_kinds: list[str] | None = None) -> list[Passage]:
    return find_passages_many([PassageRequest(disease_id, hpo_id, topic)], k, strategy, profile, query_kinds)[0]


def find_overviews(disease_ids: list[str], sources: list[str], strategy: str | None = None) -> dict[str, Passage]:
    """The overview passage of each disease: the first chunk of an active document of `sources` that declares the
    code (an Orphanet definition also declares its Exact OMIM codes, T67). Diseases without one are absent (T110)."""
    if not disease_ids or not sources:
        return {}
    with connect() as conn:
        rows = conn.execute(
            """SELECT DISTINCT ON (ids.id) ids.id, c.chunk_id
               FROM unnest(%(ids)s::text[]) AS ids(id)
               JOIN document d ON ids.id = ANY(d.disease_ids) AND d.status = 'ACTIVE' AND d.source = ANY(%(sources)s)
               JOIN chunk c ON c.doc_id = d.doc_id AND c.is_active AND c.strategy = %(strategy)s AND c.role <> 'child'
               ORDER BY ids.id, cardinality(d.disease_ids), d.doc_id, c.chunk_id""",
            {"ids": list(disease_ids), "sources": list(sources), "strategy": resolve_strategy(strategy)}).fetchall()
        passages = load_passages(conn, [r[1] for r in rows])
    return {disease_id: passages[chunk_id] for disease_id, chunk_id in rows}
