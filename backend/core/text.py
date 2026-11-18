"""Text normalization shared by the KB seed (hpo_name.text_norm) and trigram search."""

import unicodedata


def norm(text: str) -> str:
    """Lowercase, strip Vietnamese diacritics, collapse spaces — the key for trigram search."""
    decomposed = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())
