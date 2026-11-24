"""Phase 6 configuration files, loaded once."""

import tomllib
from pathlib import Path

_HERE = Path(__file__).parent


def _load(name: str) -> dict:
    with (_HERE / name).open("rb") as f:
        return tomllib.load(f)


INGEST = _load("ingest.toml")
SOURCE_SETTINGS = _load("sources.toml")
CHUNKING = _load("chunking.toml")


def resolve_strategy(name: str | None) -> str:
    """A chunking strategy name; None or 'default' -> the `default` of chunking.toml."""
    name = name or CHUNKING["default"]
    return CHUNKING["default"] if name == "default" else name
