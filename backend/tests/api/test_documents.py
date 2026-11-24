"""Upload and review API (docs/ingestion-plan.md §10) with TestClient and the worker (--once --run).

An upload's source_ref is the file's sha256 (not 'TEST:…'), so this test removes what it made by doc_id.
"""

import hashlib
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import app
from core.db import connect
from kb.ingest import worker
from kb.ingest.model import absolute
from kb.ingest.sources import upload as upload_source

FIX = Path(__file__).parents[1] / "kb/ingest/fixtures/upload"
client = TestClient(app)


@pytest.fixture
def uploads():
    made: list[int] = []
    yield made
    with connect() as conn:
        rows = conn.execute("SELECT raw_path FROM document WHERE doc_id = ANY(%s)", (made,)).fetchall()
        conn.execute("DELETE FROM declared_disease WHERE source = 'upload' AND source_ref IN "
                     "(SELECT source_ref FROM document WHERE doc_id = ANY(%s))", (made,))
        runs = [r[0] for r in conn.execute("SELECT DISTINCT run_id FROM ingest_job WHERE doc_id = ANY(%s)", (made,))]
        conn.execute("DELETE FROM ingest_job WHERE run_id = ANY(%s)", (runs,))
        conn.execute("DELETE FROM ingest_run WHERE run_id = ANY(%s)", (runs,))
        conn.execute("DELETE FROM document WHERE doc_id = ANY(%s)", (made,))
    for (raw_path,) in rows:
        absolute(raw_path).unlink(missing_ok=True)
        shutil.rmtree(upload_source.figures_dir(absolute(raw_path)), ignore_errors=True)


def post(name: str, data: bytes | None = None, title: str | None = None, codes: list[str] | None = None):
    content = data if data is not None else (FIX / name).read_bytes()
    form = {"disease_ids": ["OMIM:163950"] if codes is None else codes} | ({"title": title} if title else {})
    return client.post("/documents", files={"file": (name, content)}, data=form)


def work(job_id: int) -> None:
    with connect() as conn:
        run_id = conn.execute("SELECT run_id FROM ingest_job WHERE job_id = %s", (job_id,)).fetchone()[0]
    worker.work(once=True, run_id=run_id)


def test_upload_review_approve(fake_models, uploads):
    r = post("guide_vi.docx")
    assert r.status_code == 202
    doc_id, job_id = r.json()["doc_id"], r.json()["job_id"]
    uploads.append(doc_id)
    assert post("guide_vi.docx").status_code == 409  # same file while PROCESSING

    work(job_id)
    view = client.get(f"/documents/{doc_id}").json()
    assert view["document"]["status"] == "PENDING" and "raw_path" not in view["document"]
    assert view["document"]["title"] == "Hướng dẫn siêu âm thai quý một"  # Docling title replaces the file name
    assert view["document"]["language"] == "vi" and view["document"]["disease_ids"] == ["OMIM:163950"]
    assert view["job"]["state"] == "done"
    assert [s["heading_path"] for s in view["sections"]] == [["Đặc điểm lâm sàng"],
                                                              ["Đặc điểm lâm sàng", "Dấu hiệu trước sinh"]]
    chunks = [c for s in view["sections"] for c in s["chunks"]]
    assert {c["kind"] for c in chunks} == {"text", "table", "figure"}
    with connect() as conn:
        assert not any(r[0] for r in conn.execute("SELECT is_active FROM chunk WHERE doc_id = %s", (doc_id,)))

    fig = next(c for c in chunks if c["has_figure"])
    image = client.get(f"/chunks/{fig['chunk_id']}/figure")
    assert image.status_code == 200 and image.content[:8] == b"\x89PNG\r\n\x1a\n"
    text_chunk = next(c for c in chunks if c["kind"] == "text")
    assert client.get(f"/chunks/{text_chunk['chunk_id']}/figure").status_code == 404

    listed = client.get("/documents", params={"status": "PENDING"}).json()
    assert any(d["doc_id"] == doc_id and d["job"]["state"] == "done" for d in listed)

    assert client.post(f"/documents/{doc_id}/approve").json() == {"doc_id": doc_id, "status": "ACTIVE"}
    with connect() as conn:
        assert all(r[0] for r in conn.execute("SELECT is_active FROM chunk WHERE doc_id = %s", (doc_id,)))
    assert client.post(f"/documents/{doc_id}/approve").status_code == 409
    assert client.post(f"/documents/{doc_id}/reject").status_code == 409
    assert post("guide_vi.docx").status_code == 409  # ACTIVE copy exists


def test_reject_then_upload_again_makes_new_version(fake_models, uploads):
    r = post("guide_en.pdf", title="Ultrasound guide")
    doc_id = r.json()["doc_id"]
    uploads.append(doc_id)
    work(r.json()["job_id"])
    assert client.get(f"/documents/{doc_id}").json()["document"]["title"] == "Ultrasound guide"
    assert client.post(f"/documents/{doc_id}/reject").json()["status"] == "REJECTED"
    again = post("guide_en.pdf")
    assert again.status_code == 202
    uploads.append(again.json()["doc_id"])
    assert client.get(f"/documents/{again.json()['doc_id']}").json()["document"]["version"] == 2


def test_rejects_wrong_format_and_unknown_ids():
    assert post("notes.txt", b"plain text").status_code == 415
    assert post("fake.pdf", b"PK\x03\x04 not a pdf").status_code == 415
    assert client.get("/documents/999999999").status_code == 404
    assert client.post("/documents/999999999/approve").status_code == 404
    assert client.get("/chunks/999999999/figure").status_code == 404


def test_too_large(monkeypatch):
    monkeypatch.setitem(upload_source.INGEST, "upload_max_mb", 0)
    assert post("guide_en.pdf").status_code == 413


# ---- declared disease codes (docs/evidence-plan.md §6) ------------------------------------------
def test_upload_requires_known_codes(uploads):
    content = (FIX / "guide_vi.docx").read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    for codes in ([], ["OMIM:0000000"]):
        r = post("guide_vi.docx", codes=codes)
        assert r.status_code == 422, r.text
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM document WHERE source = 'upload' AND source_ref = %s AND status <> 'REJECTED'",
                            (sha,)).fetchone()[0] == 0


def test_put_diseases_updates_now_and_queues_rechunk(fake_models, uploads):
    r = post("guide_vi.docx")
    doc_id = r.json()["doc_id"]
    uploads.append(doc_id)
    assert client.put(f"/documents/{doc_id}/diseases", json={"disease_ids": ["ORPHA:648"]}).status_code == 409  # PROCESSING
    work(r.json()["job_id"])
    client.post(f"/documents/{doc_id}/approve")
    resp = client.put(f"/documents/{doc_id}/diseases", json={"disease_ids": ["ORPHA:648", " ORPHA:648"]})
    assert resp.status_code == 202 and len(resp.json()["job_ids"]) == 1
    with connect() as conn:
        assert {tuple(r[0]) for r in conn.execute("SELECT disease_ids FROM chunk WHERE doc_id = %s", (doc_id,))} \
            == {("ORPHA:648",)}   # findable at once
        run_id = conn.execute("SELECT run_id FROM ingest_job WHERE job_id = %s", (resp.json()["job_ids"][0],)).fetchone()[0]
    worker.work(once=True, run_id=run_id)
    with connect() as conn:
        texts = [r[0] for r in conn.execute("SELECT embed_text FROM chunk WHERE doc_id = %s AND embed_text IS NOT NULL",
                                            (doc_id,))]
        conn.execute("DELETE FROM ingest_job WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM ingest_run WHERE run_id = %s", (run_id,))
    assert texts and all("Diseases: Noonan syndrome" in t and "Noonan syndrome 1" not in t for t in texts)


def test_put_diseases_conflicts(env, rows):
    assert client.put("/documents/999999999/diseases", json={"disease_ids": ["ORPHA:648"]}).status_code == 404
    gr, pmc = rows.doc([]), rows.doc([], source="pmc")
    rows.conn.commit()
    assert client.put(f"/documents/{gr}/diseases", json={"disease_ids": ["ORPHA:648"]}).status_code == 409
    assert client.put(f"/documents/{pmc}/diseases", json={"disease_ids": ["OMIM:0000000"]}).status_code == 422
    ok = client.put(f"/documents/{pmc}/diseases", json={"disease_ids": ["ORPHA:648"]})
    assert ok.status_code == 202 and ok.json()["job_ids"] == []   # no chunks yet: nothing to rechunk
    with connect() as conn:
        conn.execute("DELETE FROM ingest_run WHERE command = %s", (f"PUT /documents/{pmc}/diseases",))


def test_list_documents_filters(rows):
    pmc, gr = rows.doc([], source="pmc"), rows.doc([])
    rows.conn.commit()
    missing = {d["doc_id"] for d in client.get("/documents", params={"missing_codes": "true"}).json()}
    assert pmc in missing and gr not in missing
    pmc_rows = client.get("/documents", params={"source": "pmc"}).json()
    row = next(d for d in pmc_rows if d["doc_id"] == pmc)
    assert row["source"] == "pmc" and row["disease_ids"] == [] and row["job"] is None
    assert all(d["source"] == "pmc" for d in pmc_rows)


def test_get_diseases():
    found = client.get("/diseases", params={"q": "Noonan"}).json()
    assert len(found) <= 10 and "OMIM:163950" in {d["disease_id"] for d in found}
    assert client.get("/diseases").status_code == 422


def test_get_passage(rows):
    chunk = rows.chunk(rows.doc(["OMIM:163950"], title="Doc"), "Some text.", active=False)
    rows.conn.commit()
    got = client.get(f"/passages/{chunk}").json()
    assert (got["ref"] == f"CHUNK:{chunk}" and got["active"] is False
            and got["section_path"] == ["Doc", "Clinical Description"])
    gone = client.get("/passages/999999999")
    assert gone.status_code == 404 and gone.json()["detail"]["code"] == "passage_not_found"
