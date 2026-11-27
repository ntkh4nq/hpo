"""The process's Langfuse client: which environment its traces go to and which spans it exports.

    tracing.setup("app")     # api/main.py, before any request
    tracing.setup("eval")    # evals/common/langfuse_run.py, imported by every eval that logs to Langfuse

Call it before anything else uses Langfuse: the first client created for a public key is the one every later
`get_client()` returns. The span filter keeps Langfuse's default filter and drops the names in tracing.toml.
"""

import re
import tomllib
from pathlib import Path

from langfuse import Langfuse, is_default_export_span

CONFIG = tomllib.loads(Path(__file__).with_name("tracing.toml").read_text())
HIDDEN = [re.compile(p) for p in CONFIG["hidden_span_patterns"]]


def keep(span) -> bool:
    return is_default_export_span(span) and not any(p.fullmatch(span.name or "") for p in HIDDEN)


def setup(environment: str) -> Langfuse:
    return Langfuse(environment=environment, should_export_span=keep)
