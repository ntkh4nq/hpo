"""bge-m3 calls shared by every user: indexing (hpo_name, later chunks) and query time.

    bge-m3          one 1024-d vector per text       -> embed_texts()
    bge-m3-colbert  one unit vector per token        -> colbert(), scored with maxsim()
"""

import os
from functools import lru_cache

import numpy as np
from openai import OpenAI

from core.image import data_url

COLBERT_MODEL = os.environ["COLBERT_MODEL"]


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    return OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"])


def embed_texts(texts: list[str], model: str | None = None) -> list[list[float]]:
    data = _client().embeddings.create(model=model or os.environ["EMBEDDING_MODEL"], input=texts).data
    return [d.embedding for d in data]


def colbert(texts: list[str]) -> list[np.ndarray]:
    # the gateway rejects the SDK's default base64 encoding for multi-vector models
    data = _client().embeddings.create(model=COLBERT_MODEL, input=texts, encoding_format="float").data
    return [np.asarray(d.embedding, dtype=np.float32) for d in sorted(data, key=lambda d: d.index)]


def maxsim(query: np.ndarray, passage: np.ndarray) -> float:
    """ColBERT late interaction in [0, 1]: each query token's best cosine in the passage, averaged."""
    return float((query @ passage.T).max(axis=1).mean())


def _visual(part: dict, *, model: str, instruction: str, timeout: float) -> list[float]:
    """Multimodal embedding through vLLM's chat-style /embeddings body (ingestion-plan.md §6)."""
    data = _client().with_options(timeout=timeout).embeddings.create(
        model=model, input=[""], encoding_format="float",
        extra_body={"messages": [{"role": "system", "content": instruction},
                                 {"role": "user", "content": [part]}]}).data
    return data[0].embedding


def embed_image(image_path, *, model: str, instruction: str, timeout: float) -> list[float]:
    return _visual({"type": "image_url", "image_url": {"url": data_url(image_path)}},
                   model=model, instruction=instruction, timeout=timeout)


def embed_visual_query(text: str, *, model: str, instruction: str, timeout: float) -> list[float]:
    return _visual({"type": "text", "text": text}, model=model, instruction=instruction, timeout=timeout)
