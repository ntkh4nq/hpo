"""Score a split (description -> items) against the gold answers in dataset.jsonl (next to this file).

Matching: evals/common/matching.py.
Per description: missed (gold without a pair), extra (model item without a pair), wrong status
(paired, but assertion / assessment / subject differ).
"""

import json
import tomllib
from pathlib import Path

from evals.common.matching import CONFIG as MATCH_CONFIG, match

HERE = Path(__file__).parent
CONFIG = tomllib.loads((HERE / "config.toml").read_text())
DATASET = HERE / "dataset.jsonl"
STATUS_FIELDS = ("assertion", "assessment", "subject")


def load_cases() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def score_case(gold: list[dict], pred: list[dict], threshold: float | None = None) -> dict:
    threshold = MATCH_CONFIG["match_threshold"] if threshold is None else threshold
    pairs = match(gold, pred, threshold)
    paired_g, paired_p = {gi for gi, _, _ in pairs}, {pi for _, pi, _ in pairs}
    wrong = [
        {"gold": gold[gi], "pred": pred[pi], "fields": [f for f in STATUS_FIELDS if gold[gi][f] != pred[pi].get(f)]}
        for gi, pi, _ in pairs
        if any(gold[gi][f] != pred[pi].get(f) for f in STATUS_FIELDS)
    ]
    return {
        "matched": len(pairs),
        "fuzzy": [(gold[gi]["phrase"], pred[pi]["phrase"], round(s, 2)) for gi, pi, s in pairs if s < 1],
        "missed": [g for i, g in enumerate(gold) if i not in paired_g],
        "extra": [p for i, p in enumerate(pred) if i not in paired_p],
        "wrong_status": wrong,
    }


def totals(scores: list[dict], gold_counts: list[int], pred_counts: list[int]) -> dict:
    matched = sum(s["matched"] for s in scores)
    wrong = sum(len(s["wrong_status"]) for s in scores)
    return {
        "recall": matched / sum(gold_counts),
        "precision": matched / sum(pred_counts) if sum(pred_counts) else 0.0,
        "status_accuracy": (matched - wrong) / matched if matched else 0.0,
        "missed": sum(len(s["missed"]) for s in scores),
        "extra": sum(len(s["extra"]) for s in scores),
        "wrong_status": wrong,
    }
