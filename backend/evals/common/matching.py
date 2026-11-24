"""Pair model items with gold items by the words of `phrase` (split check, E2).

Each gold item pairs with at most one model item, greedily by descending word-set Dice overlap of `phrase`;
pairs under `match_threshold` (matching.toml) stay unmatched.
"""

import re
import tomllib
import unicodedata
from pathlib import Path

CONFIG = tomllib.loads(Path(__file__).with_name("matching.toml").read_text())


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


def measurement_ok(gold: dict, pred: dict) -> bool | None:
    """For a paired item whose gold has a measurement (T119): the model's value equals it, whitespace aside.
    None when the gold item has no measurement (not scored)."""
    g = gold.get("measurement")
    if not g:
        return None
    p = pred.get("measurement") or {}
    return " ".join(str(p.get("value", "")).split()) == " ".join(g["value"].split())
