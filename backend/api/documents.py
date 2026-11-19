"""Upload and review of user documents (docs/ingestion-plan.md §10, T65). MVP: no login yet.

Uploads go through the same queue and worker as public sources and stop at PENDING until a reviewer
approves (ACTIVE) or rejects (REJECTED) them.
"""

import hashlib
from datetime import date
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from core.db import connect
from kb.ingest.config import INGEST, SOURCE_SETTINGS
from kb.ingest.model import absolute
from kb.ingest.pipeline import resolve_strategy

router = APIRouter()
UPLOAD_DIR = SOURCE_SETTINGS["upload"]["dir"]
MAGIC = {".pdf": b"%PDF", ".docx": b"PK\x03\x04"}  # a DOCX is a zip
JOB_FIELDS = ("state", "step", "attempts", "last_error")


def _job(conn, doc_id: int) -> dict | None:
    row = conn.execute(f"SELECT {', '.join(JOB_FIELDS)} FROM ingest_job WHERE doc_id = %s AND kind = 'ingest'"
                       " ORDER BY job_id DESC LIMIT 1", (doc_id,)).fetchone()
    return dict(zip(JOB_FIELDS, row)) if row else None


@router.post("/documents", status_code=202)
async def upload(file: UploadFile = File(...), title: str | None = Form(None)) -> dict:
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
        job_id = conn.execute(
            """INSERT INTO ingest_job (run_id, kind, source, source_ref, raw_path, strategy, doc_id)
               VALUES (%s, 'ingest', 'upload', %s, %s, %s, %s) RETURNING job_id""",
            (run_id, sha, raw_path, resolve_strategy(None), doc_id)).fetchone()[0]
    return {"doc_id": doc_id, "job_id": job_id}


@router.get("/documents")
def list_documents(status: str | None = None) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT doc_id, title, status, created_at FROM document WHERE source = 'upload'"
            + (" AND status = %s" if status else "") + " ORDER BY doc_id DESC",
            (status,) if status else ()).fetchall()
        return [{"doc_id": d, "title": t, "status": s, "created_at": c, "job": _job(conn, d)} for d, t, s, c in rows]


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
