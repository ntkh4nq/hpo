"""Phase 3 steps 1-2: preprocess the description, then split it into items with one LLM call (decisions T30).

The output is not checked item by item in code; quality is measured by the split check
(eval/split_check_run.py) and E2. `locate` finds each item's source sentence in the text (T33).
"""

import hashlib
import re
import tomllib
import unicodedata
from pathlib import Path

from langfuse import observe

from core.llm import invoke_json
from phenotype.schema import Item, Split

HERE = Path(__file__).parent
CONFIG = tomllib.loads((HERE / "split.toml").read_text())
PROMPT = (HERE / "prompts" / "split.md").read_text(encoding="utf-8")
PROMPT_VERSION = hashlib.sha256(PROMPT.encode()).hexdigest()[:12]


def preprocess(text: str) -> str:
    """NFC + collapse runs of spaces/tabs; line breaks are kept (they carry structure)."""
    text = unicodedata.normalize("NFC", text)
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def locate(text: str, source_text: str) -> tuple[int, int] | None:
    """[start, end) of the first verbatim occurrence of source_text in text (retried stripped); no fuzzy match."""
    for needle in (source_text, source_text.strip()):
        start = text.find(needle) if needle else -1
        if start >= 0:
            return start, start + len(needle)
    return None


@observe(name="split")
def split(text: str, model: str | None = None, thinking: bool | None = None) -> list[Item] | None:
    """Items of the description, or None when the model failed twice (description not analysed)."""
    thinking = CONFIG["thinking"] if thinking is None else thinking
    out = invoke_json(PROMPT, preprocess(text), Split, thinking=thinking, timeout=CONFIG["timeout"], model=model)
    return None if out is None else out.items
