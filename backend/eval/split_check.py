"""Score a split (description -> items) against the gold answers in datasets/split_check.jsonl.

Matching: each gold item pairs with at most one model item, greedily by descending word-set Dice
overlap of `phrase`; pairs under `match_threshold` (split_check.toml) stay unmatched.
Per description: missed (gold without a pair), extra (model item without a pair), wrong status
(paired, but assertion / assessment / subject differ).
"""

import json
import re
import tomllib
import unicodedata
from pathlib import Path

HERE = Path(__file__).parent
CONFIG = tomllib.loads((HERE / "split_check.toml").read_text())
DATASET = HERE / "datasets" / "split_check.jsonl"
STATUS_FIELDS = ("assertion", "assessment", "subject")


def load_cases() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def words(text: str) -> set[str]:
    return set(re.findall(r"\w+", unicodedata.normalize("NFC", text).lower()))


def similarity(a: str, b: str) -> float:
    wa, wb = words(a), words(b)
    return 2 * len(wa & wb) / (len(wa) + len(wb)) if wa and wb else 0.0


def match(gold: list[dict], pred: list[dict], threshold: float) -> list[tuple[int, int, float]]:
    """(gold index, pred index, similarity) pairs, one-to-one."""
    candidates = sorted(
        ((similarity(g["phrase"], p["phrase"]), gi, pi) for gi, g in enumerate(gold) for pi, p in enumerate(pred)),
        reverse=True,
    )
    used_g, used_p, pairs = set(), set(), []
    for sim, gi, pi in candidates:
        if sim < threshold:
            break
        if gi not in used_g and pi not in used_p:
            used_g.add(gi)
            used_p.add(pi)
            pairs.append((gi, pi, sim))
    return pairs


def score_case(gold: list[dict], pred: list[dict], threshold: float | None = None) -> dict:
    threshold = CONFIG["match_threshold"] if threshold is None else threshold
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
