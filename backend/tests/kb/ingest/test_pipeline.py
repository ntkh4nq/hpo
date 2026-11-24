"""Queue, worker and pipeline (docs/ingestion-plan.md §7, §8) on the dev DB, without the gateway."""

import uuid

import pytest
from PIL import Image

from core.db import connect
from kb.ingest import figures, pipeline
from kb.ingest.model import REPO_ROOT, PermanentIngestError, rel
from kb.ingest.sources import orphanet_def

NOONAN = "orphanet_def/docs/ORPHA_648.xml"           # in scope
SICK_SINUS = "orphanet_def/docs/ORPHA_166282.xml"   # declared codes, none in scope


def chunks_of(doc_id: int) -> list[dict]:
    with connect() as conn:
        cur = conn.execute("SELECT * FROM chunk WHERE doc_id = %s ORDER BY ord", (doc_id,))
        names = [c.name for c in cur.description]
        return [dict(zip(names, r)) for r in cur]


def test_ingest_writes_document_sections_chunks(env, fake_models):
    path = env.copy(NOONAN)
    run_id = env.run([("orphanet_def", "TEST:noonan", path)])
    assert [j["state"] for j in env.jobs(run_id)] == ["done"]
    (doc,) = env.documents("TEST:noonan")
    assert doc["status"] == "ACTIVE" and doc["title"] == "Noonan syndrome" and doc["license"].startswith("CC BY 4.0")
    assert doc["disease_ids"] == ["OMIM:163950", "ORPHA:648"]
    (chunk,) = chunks_of(doc["doc_id"])
    assert chunk["strategy"] == "s400_ctx" and chunk["is_active"] and chunk["disease_ids"] == doc["disease_ids"]
    assert chunk["embed_text"].startswith("Noonan syndrome › Definition\nDiseases: Noonan syndrome")
    assert chunk["embedding"] is not None and chunk["tsv"] is not None
    with connect() as conn:
        run = conn.execute("SELECT finished_at, counts FROM ingest_run WHERE run_id = %s", (run_id,)).fetchone()
    assert run[0] is not None and run[1] == {"done": 1, "skipped": 0, "failed": 0}


def test_rerun_same_content_creates_no_duplicate(env, fake_models):
    path = env.copy(NOONAN)
    env.run([("orphanet_def", "TEST:noonan", path)])
    run_id = env.run([("orphanet_def", "TEST:noonan", path)])
    assert [j["state"] for j in env.jobs(run_id)] == ["skipped"]
    (doc,) = env.documents("TEST:noonan")
    assert len(chunks_of(doc["doc_id"])) == 1


def test_changed_content_new_version_old_retired(env, fake_models):
    path = env.copy(NOONAN)
    env.run([("orphanet_def", "TEST:noonan", path)])
    file = REPO_ROOT / path
    file.write_text(file.read_text().replace("A rare, highly variable", "A rare and highly variable"))
    env.run([("orphanet_def", "TEST:noonan", path)])
    old, new = env.documents("TEST:noonan")
    assert (old["version"], old["status"], new["version"], new["status"]) == (1, "RETIRED", 2, "ACTIVE")
    assert not any(c["is_active"] for c in chunks_of(old["doc_id"]))
    assert all(c["is_active"] for c in chunks_of(new["doc_id"]))


def test_kb_change_updates_disease_ids(env, fake_models, monkeypatch):
    path = env.copy(NOONAN)
    env.run([("orphanet_def", "TEST:noonan", path)])
    monkeypatch.setattr(orphanet_def, "declared_disease_ids", lambda doc, conn: ["ORPHA:648"])
    env.run([("orphanet_def", "TEST:noonan", path)])
    (doc,) = env.documents("TEST:noonan")
    assert doc["disease_ids"] == ["ORPHA:648"] and doc["status"] == "ACTIVE"
    assert [c["disease_ids"] for c in chunks_of(doc["doc_id"])] == [["ORPHA:648"]]
    monkeypatch.setattr(orphanet_def, "declared_disease_ids", lambda doc, conn: ["OMIM:608687"])  # out of scope now
    env.run([("orphanet_def", "TEST:noonan", path)])
    (doc,) = env.documents("TEST:noonan")
    assert doc["status"] == "RETIRED" and not chunks_of(doc["doc_id"])[0]["is_active"]


def test_out_of_scope_document_is_skipped_and_removed(env, fake_models):
    run_id = env.run([("orphanet_def", "TEST:sicksinus", env.copy(SICK_SINUS))])
    assert [j["state"] for j in env.jobs(run_id)] == ["skipped"]
    assert env.documents("TEST:sicksinus") == []


def test_retry_reuses_document_and_stops_after_max_attempts(env, fake_models, monkeypatch):
    def broken(texts, model=None):
        raise ConnectionError("gateway down")

    monkeypatch.setattr(pipeline, "embed_texts", broken)
    run_id = env.run([("orphanet_def", "TEST:noonan", env.copy(NOONAN))])
    (job,) = env.jobs(run_id)
    assert (job["state"], job["attempts"]) == ("failed", 3) and "gateway down" in job["last_error"]
    (doc,) = env.documents("TEST:noonan")  # one document across the three attempts
    assert doc["status"] == "FAILED" and doc["doc_id"] == job["doc_id"]


def test_retry_then_success_keeps_one_document(env, fake_models, monkeypatch):
    real, failures = pipeline.embed_texts, []

    def flaky(texts, model=None):
        if not failures:
            failures.append(1)
            raise TimeoutError
        return real(texts)

    monkeypatch.setattr(pipeline, "embed_texts", flaky)
    monkeypatch.setitem(pipeline.INGEST, "embed_retry_waits", [])  # one call per attempt, so the job is retried
    run_id = env.run([("orphanet_def", "TEST:noonan", env.copy(NOONAN))])
    (job,) = env.jobs(run_id)
    assert (job["state"], job["attempts"]) == ("done", 2)
    (doc,) = env.documents("TEST:noonan")
    assert doc["status"] == "ACTIVE" and doc["version"] == 1 and len(chunks_of(doc["doc_id"])) == 1


def test_permanent_error_is_not_retried(env, fake_models, monkeypatch):
    def unreadable(raw_path):
        raise PermanentIngestError("no text layer")

    monkeypatch.setattr(orphanet_def, "parse", unreadable)
    run_id = env.run([("orphanet_def", "TEST:noonan", env.copy(NOONAN))])
    (job,) = env.jobs(run_id)
    assert (job["state"], job["attempts"], job["last_error"]) == ("failed", 1, "no text layer")
    assert env.documents("TEST:noonan")[0]["status"] == "FAILED"


def test_figures_and_rechunk(env, fake_models):
    """GeneReviews chapter: figure chunks get description in embed_text and an image vector; rechunk adds
    another strategy reusing the sections. SCA20 is out of scope, so its codes are patched in."""
    chapter = rel(env.copy_tree("genereviews") / "docs/gene_NBK1116/sca20.nxml")
    import kb.ingest.sources.genereviews as gr

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gr, "declared_disease_ids", lambda doc, conn: ["OMIM:163950"])
        env.run([("genereviews", "TEST:sca20", chapter)])
    (doc,) = env.documents("TEST:sca20")
    assert doc["status"] == "ACTIVE"
    chunks = chunks_of(doc["doc_id"])
    figs = [c for c in chunks if c["kind"] == "figure"]
    assert len(figs) == 2 and fake_models["describe"] == 1 and fake_models["image"] == 1
    with_image = [c for c in figs if c["figure_path"]]
    assert len(with_image) == 1 and with_image[0]["image_embedding"] is not None
    assert "Figure description: A test description." in with_image[0]["embed_text"]
    assert "test description" not in with_image[0]["display_text"]

    run_id = env.run([("genereviews", "TEST:sca20", chapter)], kind="rechunk", strategy="parent_child",
                     doc_id=doc["doc_id"])
    assert [j["state"] for j in env.jobs(run_id)] == ["done"]
    pc = [c for c in chunks_of(doc["doc_id"]) if c["strategy"] == "parent_child"]
    parents = {c["chunk_id"] for c in pc if c["role"] == "parent"}
    assert parents and all(c["parent_id"] in parents for c in pc if c["role"] == "child")
    assert all(c["embedding"] is None for c in pc if c["role"] == "parent")
    assert {c["section_id"] for c in pc} <= {c["section_id"] for c in chunks}  # sections reused
    again = env.run([("genereviews", "TEST:sca20", chapter)], kind="rechunk", strategy="parent_child",
                    doc_id=doc["doc_id"])
    assert [j["state"] for j in env.jobs(again)] == ["done"]  # a rechunk job replaces that strategy's chunks
    pc2 = [c for c in chunks_of(doc["doc_id"]) if c["strategy"] == "parent_child"]
    assert len(pc2) == len(pc) and not {c["chunk_id"] for c in pc2} & {c["chunk_id"] for c in pc}
    assert len({c["ord"] for c in pc2}) == len(pc2)


def test_figure_cache(monkeypatch):
    """describe/image_vector call the model once per image, then read the cache. Own image, own cleanup."""
    from kb.ingest.fetch import sha256

    path = REPO_ROOT / "data/uploads" / f"TEST_{uuid.uuid4().hex}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (uuid.uuid4().int % 255, 1, 2)).save(path)
    calls = []
    monkeypatch.setattr(figures, "describe_image", lambda *a, **k: calls.append("d") or "desc")
    monkeypatch.setattr(figures, "embed_image", lambda *a, **k: calls.append("e") or [0.5] * 2048)
    try:
        with connect() as conn:
            for _ in range(2):
                assert figures.describe(conn, rel(path)) == "desc"
                assert figures.image_vector(conn, rel(path)) == [0.5] * 2048
        assert calls == ["d", "e"]
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM figure_description WHERE image_sha256 = %s", (sha256(path),))
            conn.execute("DELETE FROM figure_embedding WHERE image_sha256 = %s", (sha256(path),))
        path.unlink()


def declare(ref: str, codes: list[str]) -> None:
    with connect() as conn:
        conn.execute("""INSERT INTO declared_disease (source, source_ref, disease_ids) VALUES ('upload', %s, %s)
                        ON CONFLICT (source, source_ref) DO UPDATE SET disease_ids = EXCLUDED.disease_ids""",
                     (ref, codes))


def test_declared_codes_reach_chunks_and_new_version_keeps_them(env, fake_models):
    """Codes an admin declared (declared_disease) are read like a source's own codes (evidence-plan §6)."""
    declare("TEST:up1", ["OMIM:163950"])
    env.run([("upload", "TEST:up1", env.copy("upload/guide_en.pdf"))])
    (v1,) = env.documents("TEST:up1")
    chunks = chunks_of(v1["doc_id"])
    assert v1["disease_ids"] == ["OMIM:163950"] and all(c["disease_ids"] == ["OMIM:163950"] for c in chunks)
    assert all("Diseases: Noonan syndrome 1" in c["embed_text"] for c in chunks if c["embed_text"])

    other = env.copy("upload/guide_vi.docx")
    env.run([("upload", "TEST:up1", other)])  # same source document, new content -> version 2
    v1, v2 = env.documents("TEST:up1")
    assert v2["version"] == 2 and v2["disease_ids"] == ["OMIM:163950"]

    declare("TEST:up1", ["ORPHA:648"])  # same content again: codes refreshed from the table
    run_id = env.run([("upload", "TEST:up1", other)])
    assert [j["state"] for j in env.jobs(run_id)] == ["skipped"]
    assert env.documents("TEST:up1")[1]["disease_ids"] == ["ORPHA:648"]
    assert {tuple(c["disease_ids"]) for c in chunks_of(v2["doc_id"])} == {("ORPHA:648",)}


def test_undeclared_document_has_no_codes_and_is_kept(env, fake_models):
    env.run([("upload", "TEST:up2", env.copy("upload/guide_en.pdf"))])
    (doc,) = env.documents("TEST:up2")
    assert doc["disease_ids"] == [] and doc["status"] == "PENDING"
