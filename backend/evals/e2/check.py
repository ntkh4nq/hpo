"""Check evals/e2/dataset.jsonl against docs/e2-plan.md §5.1 (no model calls). Exit code 1 when something is wrong.

    python -m evals.e2.check
"""

import json
import re
import sys
from pathlib import Path
from typing import get_args

from evals.e2.pool import usable_phenotypes
from core.db import connect
from phenotype.schema import Assertion, Assessment, Subject

HERE = Path(__file__).parent
DATASET = HERE / "dataset.jsonl"
PROMPTS = [HERE.parents[1] / "phenotype" / "prompts" / f for f in ("split.md", "choose_term.md")]
N_CASES = 40
ITEMS_PER_CASE = (3, 12)
MIN_FROM_DISEASE = 3
KINDS = {  # e2-plan §4.3: kind of item -> (minimum number of cases containing it, test)
    "any item not PRESENT": (40, lambda i: i["assertion"] != "PRESENT"),
    "SUSPECTED": (10, lambda i: i["assertion"] == "SUSPECTED"),
    "ABSENT": (15, lambda i: i["assertion"] == "ABSENT"),
    "UNCERTAIN, structure only (hpo_id null)": (8, lambda i: i["assertion"] == "UNCERTAIN" and i["hpo_id"] is None),
    "UNCERTAIN, specific abnormality": (5, lambda i: i["assertion"] == "UNCERTAIN" and i["hpo_id"] is not None),
    "MOTHER or FAMILY": (8, lambda i: i["subject"] in ("MOTHER", "FAMILY")),
    "'không thấy X' as PRESENT": (5, lambda i: i["assertion"] == "PRESENT"
                                  and i["phrase"].lower().startswith(("không thấy", "không có"))),
}


def load_cases() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def example_phrases() -> set[str]:
    """Backtick strings in the 'Ví dụ minh hoạ tiêu chí' section of each prompt."""
    out = set()
    for path in PROMPTS:
        text = path.read_text(encoding="utf-8")
        section = text.split("# Ví dụ minh hoạ tiêu chí", 1)[1].split("\n# ", 1)[0]
        out |= {s.lower() for s in re.findall(r"`([^`]+)`", section)}
    return out


def check(cases: list[dict]) -> list[str]:
    errors = []
    ids = [c["id"] for c in cases]
    if len(cases) != N_CASES:
        errors.append(f"{len(cases)} cases, expected {N_CASES}")
    errors += [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]

    codes = {i["hpo_id"] for c in cases for i in c["items"] if i["hpo_id"]}
    diseases = [c["disease_id"] for c in cases]
    with connect() as conn:
        current = {h for (h,) in conn.execute(
            """SELECT t.hpo_id FROM hpo_term t WHERE t.hpo_id = ANY(%s) AND NOT t.is_obsolete
               AND EXISTS (SELECT 1 FROM hpo_name n WHERE n.hpo_id = t.hpo_id AND n.lang = 'vi')""", (sorted(codes),))}
        usable = usable_phenotypes(conn, diseases)
        genes: dict[str, set[str]] = {}
        for d, g in conn.execute("SELECT disease_id, ncbi_gene_id FROM gene_disease WHERE disease_id = ANY(%s)", (diseases,)):
            genes.setdefault(d, set()).add(g)
    examples = example_phrases()
    enums = {"assertion": get_args(Assertion), "assessment": get_args(Assessment), "subject": get_args(Subject)}

    for c in cases:
        cid, items = c["id"], c["items"]
        if not ITEMS_PER_CASE[0] <= len(items) <= ITEMS_PER_CASE[1]:
            errors.append(f"{cid}: {len(items)} items, expected {ITEMS_PER_CASE[0]}-{ITEMS_PER_CASE[1]}")
        for i in items:
            label = f"{cid} '{i['phrase']}'"
            errors += [f"{label}: {f} = {i[f]!r}" for f, allowed in enums.items() if i[f] not in allowed]
            if i["hpo_id"] is None and i["assertion"] != "UNCERTAIN":
                errors.append(f"{label}: hpo_id null but assertion {i['assertion']}")
            if i["hpo_id"] and i["hpo_id"] not in current:
                errors.append(f"{label}: {i['hpo_id']} not a current term with a Vietnamese name")
            if i["phrase"].lower() in examples:
                errors.append(f"{label}: phrase is a prompt example")
        own = {p["hpo_id"] for p in usable.get(c["disease_id"], [])}
        n_own = sum(1 for i in items if i["hpo_id"] in own)
        if n_own < MIN_FROM_DISEASE:
            errors.append(f"{cid}: {n_own} items from {c['disease_id']}'s usable phenotypes, expected >= {MIN_FROM_DISEASE}")
    for a in range(len(cases)):
        for b in range(a + 1, len(cases)):
            if genes.get(cases[a]["disease_id"], set()) & genes.get(cases[b]["disease_id"], set()):
                errors.append(f"{cases[a]['id']} and {cases[b]['id']}: diseases share a gene")
    return errors


def tally(cases: list[dict]) -> dict[str, int]:
    return {kind: sum(any(test(i) for i in c["items"]) for c in cases) for kind, (_, test) in KINDS.items()}


def main() -> None:
    cases = load_cases()
    errors = check(cases)
    for e in errors:
        print("ERROR", e)
    print(f"\n{len(cases)} cases, {sum(len(c['items']) for c in cases)} items")
    short = False
    for kind, n in tally(cases).items():
        minimum = KINDS[kind][0]
        short |= n < minimum
        print(f"  {kind:42} {n:3} (min {minimum}){'   <- below minimum' if n < minimum else ''}")
    sys.exit(1 if errors or short else 0)


if __name__ == "__main__":
    main()
