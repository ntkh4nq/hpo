"""Admin screen: upload, review, declared disease codes; passages and disease lookup (docs/ingestion-plan.md §10,
docs/evidence-plan.md §5–§6, docs/evidence-impl-plan.md task 2). MVP: no login yet.

Uploads go through the same queue and worker as public sources and stop at PENDING until a reviewer
approves (ACTIVE) or rejects (REJECTED) them. Sources that declare no disease codes (pmc, upload) get them from
the admin (table declared_disease); a document without codes is never found by find_passages.
"""

import hashlib
from datetime import date
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.db import connect
from kb.ingest.config import INGEST, SOURCE_SETTINGS, resolve_strategy
from kb.ingest.model import absolute
from kb.ingest.scope import known_disease_ids
from kb.ingest.sources import SOURCES
from kb.search.disease import search_disease
from kb.search.literature import Passage, load_passages

router = APIRouter()
UPLOAD_DIR = SOURCE_SETTINGS["upload"]["dir"]
MAGIC = {".pdf": b"%PDF", ".docx": b"PK\x03\x04"}  # a DOCX is a zip
JOB_FIELDS = ("state", "step", "attempts", "last_error")
DECLARING = sorted(name for name, module in SOURCES.items() if module.DECLARES_CODES)


class DiseasesIn(BaseModel):
    disease_ids: list[str]


def _codes(conn, disease_ids: list[str]) -> list[str]:
    """Declared codes: stripped, de-duplicated, sorted; at least one, each in the disease table (#8)."""
    codes = sorted({c.strip() for c in disease_ids if c.strip()})
    if not codes:
        raise HTTPException(422, "at least one disease code is required")
    if unknown := sorted(set(codes) - set(known_disease_ids(conn, codes))):
        raise HTTPException(422, f"unknown disease codes: {', '.join(unknown)}")
    return codes


def _declare(conn, source: str, source_ref: str, codes: list[str]) -> None:
    conn.execute("""INSERT INTO declared_disease (source, source_ref, disease_ids) VALUES (%s, %s, %s)
                    ON CONFLICT (source, source_ref) DO UPDATE SET disease_ids = EXCLUDED.disease_ids,
                                                                   declared_at = now()""",
                 (source, source_ref, codes))


def _job(conn, doc_id: int) -> dict | None:
    row = conn.execute(f"SELECT {', '.join(JOB_FIELDS)} FROM ingest_job WHERE doc_id = %s AND kind = 'ingest'"
                       " ORDER BY job_id DESC LIMIT 1", (doc_id,)).fetchone()
    return dict(zip(JOB_FIELDS, row)) if row else None


@router.post("/documents", status_code=202)
async def upload(file: UploadFile = File(...), title: str | None = Form(None),
                 disease_ids: list[str] = Form([])) -> dict:
    """disease_ids: the codes the document is about (repeated form field, at least one, each in the KB)."""
    suffix = Path(file.filename or "").suffix.lower()
    limit = INGEST["upload_max_mb"] * 1024 * 1024
    data = await file.read(limit + 1)
    if suffix not in MAGIC:
        raise HTTPException(415, "only PDF (with a text layer) and DOCX are accepted")
    if len(data) > limit:
        raise HTTPException(413, f"file larger than {INGEST['upload_max_mb']} MB")
    if not data.startswith(MAGIC[suffix]):
        raise HTTPException(415, "the file content is not a PDF or DOCX")
    sha = hashlib.sha256(data).hexdigest()
    raw_path = f"{UPLOAD_DIR}/{sha}{suffix}"
    with connect() as conn:
        codes = _codes(conn, disease_ids)
        statuses = {r[0] for r in conn.execute(
            "SELECT status FROM document WHERE source = 'upload' AND source_ref = %s", (sha,))}
        if statuses & {"PROCESSING", "PENDING", "ACTIVE"}:
            raise HTTPException(409, "this file has already been uploaded")
        absolute(raw_path).parent.mkdir(parents=True, exist_ok=True)
        absolute(raw_path).write_bytes(data)
        version = conn.execute("SELECT coalesce(max(version), 0) + 1 FROM document"
                               " WHERE source = 'upload' AND source_ref = %s", (sha,)).fetchone()[0]
        run_id = conn.execute("INSERT INTO ingest_run (command, source) VALUES ('POST /documents', 'upload')"
                              " RETURNING run_id").fetchone()[0]
        doc_id = conn.execute(
            """INSERT INTO document (source, source_ref, version, status, title, license, doc_type, release,
                                     raw_path, content_hash)
               VALUES ('upload', %s, %s, 'PROCESSING', %s, %s, 'upload', %s, %s, %s) RETURNING doc_id""",
            (sha, version, (title or "").strip() or file.filename, SOURCE_SETTINGS["upload"]["license"],
             date.today().isoformat(), raw_path, sha)).fetchone()[0]
        _declare(conn, "upload", sha, codes)
        job_id = conn.execute(
            """INSERT INTO ingest_job (run_id, kind, source, source_ref, raw_path, strategy, doc_id)
               VALUES (%s, 'ingest', 'upload', %s, %s, %s, %s) RETURNING job_id""",
            (run_id, sha, raw_path, resolve_strategy(None), doc_id)).fetchone()[0]
    return {"doc_id": doc_id, "job_id": job_id}


@router.put("/documents/{doc_id}/diseases", status_code=202)
def put_diseases(doc_id: int, body: DiseasesIn) -> dict:
    """Declare the codes of a document whose source declares none: chunks are findable at once; one rechunk job per
    strategy rebuilds the embedded text with its `Diseases:` line (evidence-plan §6)."""
    with connect() as conn:
        row = conn.execute("SELECT source, source_ref, raw_path, status FROM document WHERE doc_id = %s FOR UPDATE",
                           (doc_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "document not found")
        source, source_ref, raw_path, status = row
        if source in DECLARING:
            raise HTTPException(409, f"source {source} declares its own codes")
        if status not in ("ACTIVE", "PENDING"):
            raise HTTPException(409, f"document is {status}")
        codes = _codes(conn, body.disease_ids)
        _declare(conn, source, source_ref, codes)
        conn.execute("UPDATE document SET disease_ids = %s WHERE doc_id = %s", (codes, doc_id))
        conn.execute("UPDATE chunk SET disease_ids = %s WHERE doc_id = %s", (codes, doc_id))
        strategies = [r[0] for r in conn.execute(
            "SELECT DISTINCT strategy FROM chunk WHERE doc_id = %s ORDER BY 1", (doc_id,))]
        run_id = conn.execute("INSERT INTO ingest_run (command, source) VALUES (%s, 'rechunk') RETURNING run_id",
                              (f"PUT /documents/{doc_id}/diseases",)).fetchone()[0]
        job_ids = [conn.execute(
            """INSERT INTO ingest_job (run_id, kind, source, source_ref, raw_path, strategy, doc_id)
               VALUES (%s, 'rechunk', %s, %s, %s, %s, %s) RETURNING job_id""",
            (run_id, source, source_ref, raw_path, s, doc_id)).fetchone()[0] for s in strategies]
    return {"doc_id": doc_id, "job_ids": job_ids}


@router.get("/documents")
def list_documents(status: str | None = None, source: str | None = None, missing_codes: bool = False) -> list[dict]:
    """missing_codes=true: current documents of sources that declare no codes and have none yet (work for the admin)."""
    where, params = ["TRUE"], {"declaring": DECLARING}
    if status:
        where.append("d.status = %(status)s")
        params["status"] = status
    if source:
        where.append("d.source = %(source)s")
        params["source"] = source
    if missing_codes:
        where.append("d.source <> ALL(%(declaring)s) AND d.disease_ids = '{}' AND d.status IN ('ACTIVE', 'PENDING')")
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT d.doc_id, d.title, d.status, d.created_at, d.source, d.source_ref, d.disease_ids,
                       j.state, j.step, j.attempts, j.last_error
                FROM document d LEFT JOIN LATERAL (
                    SELECT state, step, attempts, last_error FROM ingest_job
                    WHERE doc_id = d.doc_id AND kind = 'ingest' ORDER BY job_id DESC LIMIT 1) j ON TRUE
                WHERE {" AND ".join(where)} ORDER BY d.doc_id DESC""", params).fetchall()
    return [{"doc_id": r[0], "title": r[1], "status": r[2], "created_at": r[3], "source": r[4], "source_ref": r[5],
             "disease_ids": r[6], "job": dict(zip(JOB_FIELDS, r[7:])) if r[7] is not None else None} for r in rows]


@router.get("/diseases")
def get_diseases(q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=50)) -> list[dict]:
    """Disease codes by approximate English name, for the admin's code picker."""
    return search_disease(q, limit)


@router.get("/passages/{chunk_id}", response_model=Passage)
def get_passage(chunk_id: int) -> Passage:
    """A cited passage; 404 when it no longer exists (rechunked or deleted): the UI says it is gone."""
    with connect() as conn:
        found = load_passages(conn, [chunk_id])
    if chunk_id not in found:   # {code, message} like the case routes: the doctor's UI tells it from an unknown route
        raise HTTPException(404, {"code": "passage_not_found", "message": "Đoạn này không còn trong kho."})
    return found[chunk_id]


@router.get("/documents/{doc_id}")
def get_document(doc_id: int) -> dict:
    with connect() as conn:
        cur = conn.execute("SELECT * FROM document WHERE doc_id = %s", (doc_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "document not found")
        document = {c.name: v for c, v in zip(cur.description, row) if c.name != "raw_path"}
        sections = []
        for section_id, heading_path in conn.execute(
                "SELECT section_id, heading_path FROM section WHERE doc_id = %s ORDER BY ord", (doc_id,)).fetchall():
            chunks = [{"chunk_id": c, "kind": k, "display_text": t, "has_figure": f is not None}
                      for c, k, t, f in conn.execute(
                          """SELECT chunk_id, kind, display_text, figure_path FROM chunk
                             WHERE section_id = %s AND strategy = %s ORDER BY ord""",
                          (section_id, resolve_strategy(None)))]
            sections.append({"section_id": section_id, "heading_path": heading_path, "chunks": chunks})
        return {"document": document, "job": _job(conn, doc_id), "sections": sections}


def _review(doc_id: int, new_status: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT status FROM document WHERE doc_id = %s FOR UPDATE", (doc_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "document not found")
        if row[0] != "PENDING":
            raise HTTPException(409, f"document is {row[0]}, not PENDING")
        conn.execute("UPDATE document SET status = %s WHERE doc_id = %s", (new_status, doc_id))
        conn.execute("UPDATE chunk SET is_active = %s WHERE doc_id = %s", (new_status == "ACTIVE", doc_id))
    return {"doc_id": doc_id, "status": new_status}


@router.post("/documents/{doc_id}/approve")
def approve(doc_id: int) -> dict:
    return _review(doc_id, "ACTIVE")


@router.post("/documents/{doc_id}/reject")
def reject(doc_id: int) -> dict:
    return _review(doc_id, "REJECTED")


@router.get("/chunks/{chunk_id}/figure")
def figure(chunk_id: int) -> FileResponse:
    with connect() as conn:
        row = conn.execute("SELECT figure_path FROM chunk WHERE chunk_id = %s", (chunk_id,)).fetchone()
    if row is None or row[0] is None or not absolute(row[0]).is_file():
        raise HTTPException(404, "no figure for this chunk")
    return FileResponse(absolute(row[0]))
