"""Fill hpo_name.embedding for rows that have none (or one from another model).

Resumable: every batch commits, so re-running continues where it stopped. Identical texts
(e.g. ChatGPT and Claude giving the same translation) are embedded once.

    python -m kb.embed
"""

import os
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

from kb.db import connect

BATCH = 64
WORKERS = 4  # ponytail: fixed small fan-out; tune against the gateway's rate limit if it throttles


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    client = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"])
    data = client.embeddings.create(model=model or os.environ["EMBEDDING_MODEL"], input=texts).data
    return [d.embedding for d in data]


def embed_names() -> None:
    model = os.environ["EMBEDDING_MODEL"]
    with connect(autocommit=True) as conn:
        texts = [r[0] for r in conn.execute(
            "SELECT DISTINCT text FROM hpo_name WHERE embedding_model IS DISTINCT FROM %s", (model,))]
        batches = [texts[i:i + BATCH] for i in range(0, len(texts), BATCH)]
        print(f"{len(texts)} texts in {len(batches)} batches", flush=True)

        def work(batch):
            return batch, embed_texts(batch, model)

        with ThreadPoolExecutor(WORKERS) as pool:
            for done, (batch, vectors) in enumerate(pool.map(work, batches), 1):
                with conn.transaction(), conn.cursor() as cur:
                    cur.executemany(
                        "UPDATE hpo_name SET embedding = %s::vector, embedding_model = %s WHERE text = %s",
                        [(str(v), model, t) for t, v in zip(batch, vectors)],
                    )
                if done % 50 == 0 or done == len(batches):
                    print(f"{done}/{len(batches)} batches", flush=True)


if __name__ == "__main__":
    embed_names()
