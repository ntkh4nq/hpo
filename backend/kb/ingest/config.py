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
