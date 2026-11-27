"""Figure description (vlm) and image vector (vl-embedding), cached by image content (docs/ingestion-plan.md §6).

Cache keys: figure_description (image sha256, model, PROMPT_VERSION); figure_embedding (image sha256, model).
Re-ingesting or rechunking never calls a model again for an image already seen.
The description only goes into embed_text (principle #1: never shown, never used as evidence).
"""

import hashlib
import json
import time
from pathlib import Path

from core.embedding import embed_image
from core.llm import describe_image
from kb.ingest.config import INGEST
from kb.ingest.fetch import sha256
from kb.ingest.model import absolute

PROMPT = (Path(__file__).parent / "prompts" / "figure.md").read_text()
PROMPT_VERSION = hashlib.sha256(PROMPT.encode()).hexdigest()[:12]


def with_retries(call):
    """Embedding calls: up to len(embed_retry_waits) + 1 attempts, waiting between them (§6)."""
    waits = INGEST["embed_retry_waits"]
    for attempt in range(len(waits) + 1):
        try:
            return call()
        except Exception:
            if attempt == len(waits):
                raise
            time.sleep(waits[attempt])
    raise AssertionError("unreachable")


def describe(conn, image_path: str) -> str | None:
    """Cached vlm description; None when every attempt failed (the chunk then embeds only context + caption)."""
    sha, model = sha256(absolute(image_path)), INGEST["figure_model"]
    row = conn.execute("SELECT description FROM figure_description WHERE image_sha256 = %s AND model = %s"
                       " AND prompt_version = %s", (sha, model, PROMPT_VERSION)).fetchone()
    if row:
        return row[0]
    text = describe_image(absolute(image_path), PROMPT, model=model, timeout=INGEST["figure_timeout"],
                          name="figure_description")
    if text:
        conn.execute("INSERT INTO figure_description (image_sha256, model, prompt_version, description)"
                     " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", (sha, model, PROMPT_VERSION, text))
        conn.commit()
    return text


def image_vector(conn, image_path: str) -> list[float]:
    """Cached vl-embedding vector of the image; raises after the retries (the job is retried later)."""
    sha, model = sha256(absolute(image_path)), INGEST["visual_model"]
    row = conn.execute("SELECT embedding::text FROM figure_embedding WHERE image_sha256 = %s AND model = %s",
                       (sha, model)).fetchone()
    if row:
        return json.loads(row[0])
    vector = with_retries(lambda: embed_image(
        absolute(image_path), model=model, instruction=INGEST["visual_document_instruction"],
        timeout=INGEST["visual_timeout"]))
    conn.execute("INSERT INTO figure_embedding (image_sha256, model, embedding) VALUES (%s, %s, %s::halfvec)"
                 " ON CONFLICT DO NOTHING", (sha, model, str(vector)))
    conn.commit()
    return vector


def figure_data(conn, image_paths: list[str]) -> dict[str, tuple[str | None, list[float]]]:
    """{image_path: (description, image vector)} for the distinct image files of one document."""
    return {p: (describe(conn, p), image_vector(conn, p)) for p in dict.fromkeys(image_paths)}
