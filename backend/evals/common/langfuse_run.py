"""Names and metadata shared by every eval run logged to Langfuse (docs/eval-strategy.md §8).

Importing this module sends every trace of the process to the Langfuse environment "eval", apart from the app's
"app" (core/tracing.py), so it must be imported before anything uses Langfuse: every eval script imports it first.

Each eval set is one Langfuse experiment (`EXPERIMENTS`). Each run of it is named

    <set> · <split> · <config> · <stamp> @ <git>        e.g.  E3 · dev · ppo · 2026-11-24T162543 @ cafdd7c

where <stamp> is the name of the saved result file under evals/<set>/results/ (the file a run can be rescored
from), or the time of the run when the set saves no file (E1 search). Every run carries the same metadata keys:
eval, split, git, result_file, plus what is specific to the set (prompt versions, models, config).
"""

import json
from datetime import datetime
from pathlib import Path

from core import tracing

tracing.setup("eval")

BACKEND = Path(__file__).resolve().parents[2]
EXPERIMENTS = {   # eval id -> (short label used in run names, experiment name)
    "split-check": ("Split check", "Split check · description → items"),
    "E1-search": ("E1 search", "E1 · phrase → HPO candidates"),
    "E1-choose": ("E1 choose", "E1 · choose the HPO term"),
    "E2": ("E2", "E2 · description → profile"),
    "E2-E3": ("E2→E3", "E2→E3 · effect of extraction errors on ranking"),
    "E3": ("E3", "E3 · ranking of simulated patients"),
    "E4": ("E4", "E4 · explanations"),
    "E5": ("E5", "E5 · passage search"),
}


def stamp(path: Path | None) -> str:
    return path.stem if path is not None else datetime.now().strftime("%Y-%m-%dT%H%M%S")


def result_file(path: Path | None) -> str:
    return str(path.resolve().relative_to(BACKEND)) if path is not None else "(none: logged to Langfuse only)"


def experiment(eval_id: str) -> str:
    return EXPERIMENTS[eval_id][1]


def run_name(eval_id: str, split: str, config: str, path: Path | None, git: str) -> str:
    return f"{EXPERIMENTS[eval_id][0]} · {split} · {config} · {stamp(path)} @ {git}"


def description(text: str, path: Path | None) -> str:
    return f"{text} Result file: {result_file(path)}."


def metadata(eval_id: str, split: str, path: Path | None, git: str, **extra) -> dict:
    """Run metadata: the shared keys, then the set's own values (non-string values as JSON)."""
    meta = {"eval": eval_id, "split": split, "git": git, "result_file": result_file(path)}
    meta.update({k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for k, v in extra.items()})
    return meta
