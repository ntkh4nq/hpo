"""Add measurements to the split-check and E2 gold answers (docs/followup-plan.md §B.5, T119). No model calls here.

Labels come from two blind agents of different models and a third that settles disagreements (the user has no
medical background; these are agent labels, not expert labels). Each agent reads `task` output and returns, per
description, every measured value of the fetus or its adnexa:
    {"<case id>": [{"quantity": "...", "value": "...", "subject": "FETUS", "item": <gold item index> | null}]}
`item` is the gold item the value belongs to (a measurement with a conclusion); null = no conclusion, a new item.

    python -m evals.split_check.relabel task  <split_check|e2> > task.json
    python -m evals.split_check.relabel merge <split_check|e2> a.json b.json > merged.json   # agreed + conflicts
    python -m evals.split_check.relabel apply <split_check|e2> final.json                     # rewrite dataset.jsonl
"""

import json
import sys
from pathlib import Path

from phenotype.split import preprocess

DATASETS = {"split_check": Path(__file__).parent / "dataset.jsonl",
            "e2": Path(__file__).parents[1] / "e2" / "dataset.jsonl"}


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in DATASETS[name].read_text(encoding="utf-8").splitlines() if line.strip()]


def task(name: str) -> list[dict]:
    """What an agent sees: the description and its current gold items, numbered."""
    return [{"id": c["id"], "text": c["text"],
             "items": [{"item": i, "phrase": it["phrase"], "assertion": it["assertion"]} for i, it in enumerate(c["items"])]}
            for c in load(name)]


def key(m: dict) -> tuple:
    return (" ".join(m["value"].split()), m["quantity"].strip().lower(), m.get("item"), m.get("subject", "FETUS"))


def merge(a: dict, b: dict) -> dict:
    """Per case: labels both agents gave (same value, quantity, item, subject) and the rest as a conflict."""
    out = {}
    for cid in sorted(set(a) | set(b)):
        ka, kb = {key(m): m for m in a.get(cid, [])}, {key(m): m for m in b.get(cid, [])}
        agreed = [ka[k] for k in ka if k in kb]
        conflict = {"a_only": [ka[k] for k in ka if k not in kb], "b_only": [kb[k] for k in kb if k not in ka]}
        out[cid] = {"agreed": agreed, **({"conflict": conflict} if conflict["a_only"] or conflict["b_only"] else {})}
    return out


def phrase_of(quantity: str) -> str:
    """Convention of T31 (2): "bất thường" + the measured quantity, so the status words read right."""
    first = quantity[:1] if quantity[1:2].isupper() else quantity[:1].lower()   # keep abbreviations: BPD, ĐKLĐ
    return "bất thường " + first + quantity[1:]


def apply(name: str, final: dict) -> list[str]:
    """Write the labels into the dataset; every quantity and value must be verbatim in the description."""
    cases, problems = load(name), []
    for case in cases:
        text = preprocess(case["text"])
        for it in case["items"]:
            it.pop("measurement", None)
        for m in final.get(case["id"], []):
            if m["quantity"] not in text or m["value"] not in text:
                problems.append(f"{case['id']}: not verbatim: {m['quantity']!r} / {m['value']!r}")
                continue
            measurement = {"quantity": m["quantity"], "value": m["value"]}
            if m.get("item") is not None:
                case["items"][m["item"]]["measurement"] = measurement
            else:
                new = {"phrase": phrase_of(m["quantity"]), "assertion": "UNCERTAIN", "assessment": "ASSESSED",
                       "subject": m.get("subject", "FETUS"), "measurement": measurement}
                if name == "e2":
                    new["hpo_id"] = None   # the doctor decides: expected UNMAPPED (T118)
                case["items"].append(new)
    if not problems:
        DATASETS[name].write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    return problems


if __name__ == "__main__":
    cmd, name, *files = sys.argv[1:]
    if cmd == "task":
        print(json.dumps(task(name), ensure_ascii=False, indent=1))
    elif cmd == "merge":
        a, b = (json.loads(Path(f).read_text(encoding="utf-8")) for f in files)
        print(json.dumps(merge(a, b), ensure_ascii=False, indent=1))
    elif cmd == "apply":
        problems = apply(name, json.loads(Path(files[0]).read_text(encoding="utf-8")))
        print("\n".join(problems) or "written")
        sys.exit(1 if problems else 0)
