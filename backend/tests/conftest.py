import os

import pytest

from core.db import connect

os.environ["LANGFUSE_TRACING_ENABLED"] = "false"  # tests must not send traces to Langfuse


def vec(i: int, n: int = 1024) -> list[float]:
    v = [0.0] * n
    v[i] = 1.0
    return v


class Rows:
    """Hand-built layer 3 rows (document, one section, chunks) under source_ref 'TEST:lit…'; commit to share them."""

    def __init__(self, conn):
        self.conn, self.n = conn, 0

    def doc(self, disease_ids, source="genereviews", status="ACTIVE", title=None) -> int:
        self.n += 1
        title = title or f"Doc {self.n}"
        doc_id = self.conn.execute(
            """INSERT INTO document (source, source_ref, status, title, license, doc_type, release, raw_path,
                                     content_hash, disease_ids)
               VALUES (%s, %s, %s, %s, 'test', 'test', 'test', 'test', 'test', %s) RETURNING doc_id""",
            (source, f"TEST:lit{self.n}", status, title, disease_ids)).fetchone()[0]
        self.conn.execute("INSERT INTO section (doc_id, ord, heading_path) VALUES (%s, 0, %s)",
                          (doc_id, [title, "Clinical Description"]))
        return doc_id

    def chunk(self, doc_id, text, *, v=0, active=True, strategy="s400_ctx", role="leaf", parent=None,
              image=False, embedded=True) -> int:
        section = self.conn.execute("SELECT section_id FROM section WHERE doc_id = %s", (doc_id,)).fetchone()[0]
        ord_ = self.conn.execute("SELECT count(*) FROM chunk WHERE doc_id = %s", (doc_id,)).fetchone()[0]
        disease_ids = self.conn.execute("SELECT disease_ids FROM document WHERE doc_id = %s", (doc_id,)).fetchone()[0]
        return self.conn.execute(
            """INSERT INTO chunk (doc_id, section_id, strategy, ord, kind, role, parent_id, display_text, embed_text,
                                  tsv, embedding, image_embedding, disease_ids, is_active)
               VALUES (%s, %s, %s, %s, 'text', %s, %s, %s, %s, to_tsvector('english', %s), %s::vector, %s::halfvec,
                       %s, %s) RETURNING chunk_id""",
            (doc_id, section, strategy, ord_, role, parent, text, text if embedded else None, text,
             str(vec(v)) if embedded else None, str(vec(0, 2048)) if image else None, disease_ids, active)
        ).fetchone()[0]


@pytest.fixture
def rows():
    with connect() as conn:
        yield Rows(conn)
        conn.commit()
    with connect() as conn:
        conn.execute("DELETE FROM document WHERE source_ref LIKE 'TEST:lit%%'")
