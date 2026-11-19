"""The steps of one ingest or rechunk job (docs/ingestion-plan.md §7, §8).

Each step commits its own work, so a retried job sees what an earlier attempt already did (e.g. the
`document` row in job.doc_id) and the figure caches. Only `write` touches section/chunk, in one transaction.
"""

import os
from dataclasses import dataclass, field

from core.embedding import embed_texts
from kb.ingest import chunking, figures
from kb.ingest.config import CHUNKING, INGEST
from kb.ingest.fetch import sha256
from kb.ingest.model import Figure, ParsedDocument, absolute
from kb.ingest.scope import in_scope
from kb.ingest.sources import SOURCES

UPLOAD_FILENAME_SUFFIXES = (".pdf", ".docx")


class Skipped(Exception):
    """The job has nothing to do (state 'skipped')."""


@dataclass
class Job:
    job_id: int
    run_id: int
    kind: str
    source: str
    source_ref: str
    raw_path: str
    strategy: str
    doc_id: int | None
    attempts: int


@dataclass
class Work:
    """What the steps of one job hand to each other."""

    job: Job
    parsed: ParsedDocument | None = None
    disease_ids: list[str] = field(default_factory=list)
    language: str = "en"
    figure_data: dict = field(default_factory=dict)
    chunks: list = field(default_factory=list)
    vectors: dict[int, list[float]] = field(default_factory=dict)  # chunk ord -> text vector


def resolve_strategy(name: str | None) -> str:
    name = name or CHUNKING["default"]
    return CHUNKING["default"] if name == "default" else name


def release_of(raw_path: str) -> str:
    """data/raw/<source>/<release>/... -> <release>; '' for a path outside data/raw (tests)."""
    parts = raw_path.split("/")
    return parts[3] if parts[:2] == ["data", "raw"] and len(parts) > 3 else ""


def _set_step(conn, job: Job, step: str) -> None:
    conn.execute("UPDATE ingest_job SET step = %s WHERE job_id = %s", (step, job.job_id))
    conn.commit()


def _retire(conn, doc_ids: list[int]) -> None:
    conn.execute("UPDATE document SET status = 'RETIRED' WHERE doc_id = ANY(%s)", (doc_ids,))
    conn.execute("UPDATE chunk SET is_active = false WHERE doc_id = ANY(%s)", (doc_ids,))


# ---- steps --------------------------------------------------------------------------------------
def register(conn, w: Work) -> None:
    job = w.job
    if job.kind == "rechunk":
        if conn.execute("SELECT 1 FROM chunk WHERE doc_id = %s AND strategy = %s LIMIT 1",
                        (job.doc_id, job.strategy)).fetchone():
            raise Skipped("chunks of this strategy exist")
        return
    if job.doc_id is not None:  # a retry, or an upload registered by the API
        return
    if job.source == "pmc" and job.source_ref.startswith("PMID:") and conn.execute(
            "SELECT 1 FROM document WHERE source = 'pubmed' AND source_ref = %s AND status = 'ACTIVE'",
            (job.source_ref,)).fetchone():
        raise Skipped("abstract already ingested from source pubmed")
    content_hash = sha256(absolute(job.raw_path))
    current = conn.execute(
        """SELECT doc_id, content_hash FROM document WHERE source = %s AND source_ref = %s
           AND status IN ('ACTIVE', 'PENDING') ORDER BY version DESC LIMIT 1""",
        (job.source, job.source_ref)).fetchone()
    if current and current[1] == content_hash:
        refresh_disease_ids(conn, current[0], job)
        raise Skipped("same content as the current version")
    version = conn.execute("SELECT coalesce(max(version), 0) + 1 FROM document WHERE source = %s AND source_ref = %s",
                           (job.source, job.source_ref)).fetchone()[0]
    # title/licence/doc_type are filled from the parsed file in the next step
    doc_id = conn.execute(
        """INSERT INTO document (source, source_ref, version, status, title, license, doc_type, release,
                                 raw_path, content_hash)
           VALUES (%s, %s, %s, 'PROCESSING', %s, '', '', %s, %s, %s) RETURNING doc_id""",
        (job.source, job.source_ref, version, job.source_ref, release_of(job.raw_path), job.raw_path,
         content_hash)).fetchone()[0]
    conn.execute("UPDATE ingest_job SET doc_id = %s WHERE job_id = %s", (doc_id, job.job_id))
    conn.commit()
    job.doc_id = doc_id


def refresh_disease_ids(conn, doc_id: int, job: Job) -> None:
    """Same content: only recompute disease_ids, since the KB may have changed (§7)."""
    module = SOURCES[job.source]
    if not module.DECLARES_CODES:
        return
    ids = module.declared_disease_ids(module.parse(absolute(job.raw_path)), conn)
    if not in_scope(conn, ids):
        _retire(conn, [doc_id])
    else:
        conn.execute("UPDATE document SET disease_ids = %s WHERE doc_id = %s AND disease_ids <> %s", (ids, doc_id, ids))
        conn.execute("UPDATE chunk SET disease_ids = %s WHERE doc_id = %s AND disease_ids <> %s", (ids, doc_id, ids))
    conn.commit()


def parse(conn, w: Work) -> None:
    job = w.job
    w.parsed = doc = SOURCES[job.source].parse(absolute(job.raw_path))
    w.language = doc.language
    if job.kind == "rechunk":
        return
    title = doc.title
    if job.source == "upload":  # the uploader's title wins; the file name only when Docling found no title
        stored = conn.execute("SELECT title FROM document WHERE doc_id = %s", (job.doc_id,)).fetchone()[0]
        if not (stored.lower().endswith(UPLOAD_FILENAME_SUFFIXES) and doc.title != absolute(job.raw_path).stem):
            title = stored
    conn.execute("""UPDATE document SET title = %s, citation = %s, url = %s, license = %s, language = %s,
                    doc_type = %s WHERE doc_id = %s""",
                 (title, doc.citation, doc.url, doc.license, doc.language, doc.doc_type, job.doc_id))
    conn.commit()
    doc.title = title


def ids(conn, w: Work) -> None:
    job = w.job
    if job.kind == "rechunk":
        w.disease_ids = conn.execute("SELECT disease_ids FROM document WHERE doc_id = %s", (job.doc_id,)).fetchone()[0]
        return
    module = SOURCES[job.source]
    w.disease_ids = module.declared_disease_ids(w.parsed, conn)
    if module.DECLARES_CODES and not in_scope(conn, w.disease_ids):
        conn.execute("DELETE FROM document WHERE doc_id = %s", (job.doc_id,))
        conn.commit()
        raise Skipped("no declared disease code in scope")


def figure_step(conn, w: Work) -> None:
    paths = [b.image_path for s in w.parsed.sections for b in s.blocks if isinstance(b, Figure) and b.image_path]
    w.figure_data = figures.figure_data(conn, paths)


def chunk(conn, w: Work) -> None:
    names = dict(conn.execute("SELECT disease_id, coalesce(name, disease_id) FROM disease WHERE disease_id = ANY(%s)",
                              (w.disease_ids[:3],)).fetchall())
    descriptions = {path: data[0] for path, data in w.figure_data.items()}
    w.chunks = chunking.chunk_document(w.parsed, CHUNKING[w.job.strategy],
                                       [names.get(d, d) for d in w.disease_ids[:3]], descriptions)


def embed(conn, w: Work) -> None:
    todo = [c for c in w.chunks if c.embed_text is not None]
    size = INGEST["embed_batch"]
    for i in range(0, len(todo), size):
        batch = todo[i:i + size]
        vectors = figures.with_retries(lambda: embed_texts([c.embed_text for c in batch]))
        w.vectors.update({c.ord: v for c, v in zip(batch, vectors)})


def write(conn, w: Work) -> str:
    """One transaction; returns the document status after the write."""
    job = w.job
    model = os.environ["EMBEDDING_MODEL"]
    visual = INGEST["visual_model"]
    ts_config = "english" if w.language == "en" else "simple"
    with conn.transaction():
        if job.kind == "ingest":
            conn.execute("DELETE FROM section WHERE doc_id = %s", (job.doc_id,))  # leftovers of an earlier attempt
            status = "PENDING" if job.source == "upload" else "ACTIVE"
            section_ids = [conn.execute("INSERT INTO section (doc_id, ord, heading_path) VALUES (%s, %s, %s)"
                                        " RETURNING section_id", (job.doc_id, i, s.heading_path)).fetchone()[0]
                           for i, s in enumerate(w.parsed.sections)]
        else:
            status = conn.execute("SELECT status FROM document WHERE doc_id = %s", (job.doc_id,)).fetchone()[0]
            by_ord = dict(conn.execute("SELECT ord, section_id FROM section WHERE doc_id = %s", (job.doc_id,)).fetchall())
            section_ids = [by_ord[i] for i in range(len(w.parsed.sections))]
        chunk_ids: dict[int, int] = {}
        for c in w.chunks:
            image = w.figure_data.get(c.figure_path, (None, None))[1] if c.figure_path else None
            vector = w.vectors.get(c.ord)
            chunk_ids[c.ord] = conn.execute(
                """INSERT INTO chunk (doc_id, section_id, strategy, ord, kind, role, parent_id, display_text, embed_text,
                                      figure_path, tsv, embedding, embedding_model, image_embedding, image_model,
                                      disease_ids, is_active)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           CASE WHEN %s::text IS NULL THEN NULL ELSE to_tsvector(%s::regconfig, %s) END,
                           %s::vector, %s, %s::halfvec, %s, %s, %s) RETURNING chunk_id""",
                (job.doc_id, section_ids[c.section], job.strategy, c.ord, c.kind, c.role,
                 chunk_ids.get(c.parent_ord), c.display_text, c.embed_text, c.figure_path,
                 c.embed_text, ts_config, c.embed_text,
                 str(vector) if vector else None, model if vector else None,
                 str(image) if image else None, visual if image else None,
                 w.disease_ids, status == "ACTIVE")).fetchone()[0]
        if job.kind == "ingest":
            conn.execute("UPDATE document SET status = %s, disease_ids = %s WHERE doc_id = %s",
                         (status, w.disease_ids, job.doc_id))
            older = [r[0] for r in conn.execute(
                """SELECT doc_id FROM document WHERE source = %s AND source_ref = %s AND doc_id <> %s
                   AND status IN ('ACTIVE', 'PENDING')""", (job.source, job.source_ref, job.doc_id))]
            if older:
                _retire(conn, older)
    return status


STEPS = [("register", register), ("parse", parse), ("ids", ids), ("figures", figure_step),
         ("chunk", chunk), ("embed", embed), ("write", write)]


def run(conn, job: Job) -> str:
    """Run every step; returns 'done' or 'skipped'. Exceptions propagate to the worker."""
    w = Work(job)
    try:
        for name, step in STEPS:
            _set_step(conn, job, name)
            step(conn, w)
    except Skipped:
        conn.commit()
        return "skipped"
    return "done"
