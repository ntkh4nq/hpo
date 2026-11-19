"""Shared fixtures for phase 6 tests (docs/ingestion-plan.md Appendix D).

- No gateway: text embedding, figure description and image embedding are replaced.
- The dev DB is shared: every test creates its own ingest_run, uses source_ref 'TEST:…', runs the worker with
  --once --run, and deletes what it created afterwards, even when it fails.
"""

import shutil
import uuid
from pathlib import Path

import pytest

from core.db import connect
from kb.ingest import figures, pipeline, worker
from kb.ingest.config import INGEST
from kb.ingest.model import REPO_ROOT, rel

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def fake_models(monkeypatch):
    """Deterministic stand-ins; figure functions are replaced whole so no fake row reaches the caches."""
    calls = {"embed": 0, "describe": 0, "image": 0}

    def embed_texts(texts, model=None):
        calls["embed"] += 1
        return [[0.001 * (len(t) % 97 + 1)] * 1024 for t in texts]

    def describe(conn, path):
        calls["describe"] += 1
        return "A test description."

    def image_vector(conn, path):
        calls["image"] += 1
        return [0.01] * 2048

    monkeypatch.setattr(pipeline, "embed_texts", embed_texts)
    monkeypatch.setattr(figures, "describe", describe)
    monkeypatch.setattr(figures, "image_vector", image_vector)
    monkeypatch.setitem(INGEST, "retry_delay_seconds", 0)
    monkeypatch.setitem(INGEST, "embed_retry_waits", [0, 0])
    return calls


class Env:
    """Runs, jobs and documents made by one test, removed at teardown."""

    def __init__(self):
        self.runs: list[int] = []
        self.dirs: list[Path] = []

    def copy(self, fixture: str, name: str | None = None) -> str:
        """A writable copy of a fixture inside the repo (raw_path must be repo-relative)."""
        d = REPO_ROOT / "data/uploads" / f"TEST_{uuid.uuid4().hex}"
        d.mkdir(parents=True)
        self.dirs.append(d)
        dst = d / (name or Path(fixture).name)
        shutil.copy(FIX / fixture, dst)
        return rel(dst)

    def copy_tree(self, fixture_dir: str) -> Path:
        """A copy of a whole fixture release directory (e.g. genereviews/ with its index files)."""
        d = REPO_ROOT / "data/uploads" / f"TEST_{uuid.uuid4().hex}"
        self.dirs.append(d)
        shutil.copytree(FIX / fixture_dir, d)
        return d

    def run(self, jobs: list[tuple[str, str, str]], kind: str = "ingest", strategy: str | None = None,
            doc_id: int | None = None) -> int:
        """Queue (source, source_ref, raw_path) jobs in a new run and work them off."""
        with connect() as conn:
            run_id = conn.execute("INSERT INTO ingest_run (command, source) VALUES ('test', 'test') RETURNING run_id"
                                  ).fetchone()[0]
            for source, ref, path in jobs:
                conn.execute("INSERT INTO ingest_job (run_id, kind, source, source_ref, raw_path, strategy, doc_id)"
                             " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                             (run_id, kind, source, ref, path, pipeline.resolve_strategy(strategy), doc_id))
        self.runs.append(run_id)
        worker.work(once=True, run_id=run_id)
        return run_id

    def jobs(self, run_id: int) -> list[dict]:
        with connect() as conn:
            cur = conn.execute("SELECT * FROM ingest_job WHERE run_id = %s ORDER BY job_id", (run_id,))
            names = [c.name for c in cur.description]
            return [dict(zip(names, r)) for r in cur]

    def documents(self, ref: str) -> list[dict]:
        with connect() as conn:
            cur = conn.execute("SELECT * FROM document WHERE source_ref = %s ORDER BY version", (ref,))
            names = [c.name for c in cur.description]
            return [dict(zip(names, r)) for r in cur]

    def cleanup(self) -> None:
        with connect() as conn:
            conn.execute("DELETE FROM document WHERE source_ref LIKE 'TEST:%%'")
            conn.execute("DELETE FROM ingest_job WHERE run_id = ANY(%s)", (self.runs,))
            conn.execute("DELETE FROM ingest_run WHERE run_id = ANY(%s)", (self.runs,))
        for d in self.dirs:
            shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def env():
    e = Env()
    yield e
    e.cleanup()
