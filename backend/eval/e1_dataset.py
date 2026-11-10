"""Build Langfuse dataset E1 (Vietnamese clinical phrase -> HPO term) from the advisor's 234 cases.

Source rows: sheet HPO_long, fetal-phenotype rows that carry an HPO code. One item per distinct
phrase (normalized); a phrase coded differently in different cases keeps every code as gold.
Labels are SILVER: ChatGPT-assigned, not expert-reviewed. Queries are the raw phrases, status words
("theo dõi", "nghi"...) included — cleaning them is phase 3's job and is measured there.

Idempotent: item ids derive from the phrase; items no longer produced are archived.

    python -m eval.e1_dataset
"""

import hashlib
from collections import defaultdict

import openpyxl
from langfuse import get_client

from eval.prenatal_coverage import CASES
from kb.db import connect
from kb.seed import norm

DATASET = "e1-phrase-to-hpo"


def build_items() -> list[dict]:
    with connect() as conn:
        current = {h for (h,) in conn.execute("SELECT hpo_id FROM hpo_term WHERE NOT is_obsolete")}
        alt = dict(conn.execute("SELECT alt_id, hpo_id FROM hpo_alt_id"))

    rows = openpyxl.load_workbook(CASES, read_only=True)["HPO_long"].iter_rows(values_only=True)
    header = next(rows)
    col = {n: header.index(n) for n in ("Case_ID", "Cụm từ lâm sàng gốc", "HPO_ID", "Loại bằng chứng")}

    groups: dict[str, dict] = defaultdict(lambda: {"phrases": set(), "gold": set(), "cases": set()})
    for r in rows:
        phrase, code = str(r[col["Cụm từ lâm sàng gốc"]] or "").strip(), r[col["HPO_ID"]]
        if r[col["Loại bằng chứng"]] != "FETAL_PHENOTYPE" or not code or not phrase:
            continue
        code = alt.get(code.strip(), code.strip())
        if code not in current:
            raise ValueError(f"case {r[col['Case_ID']]}: {code} is not a current term in this release")
        g = groups[norm(phrase)]
        g["phrases"].add(phrase)
        g["gold"].add(code)
        g["cases"].add(str(r[col["Case_ID"]]))

    with connect() as conn:  # queries equal to an indexed name of the gold term are trivially easy: tag them
        indexed = {(t, h) for t, h in conn.execute("SELECT text_norm, hpo_id FROM hpo_name")}
    return [{
        "id": "e1-" + hashlib.sha1(key.encode()).hexdigest()[:16],
        "input": {"query": min(g["phrases"], key=len)},
        "expected_output": {"hpo_ids": sorted(g["gold"])},
        "metadata": {
            "case_ids": sorted(g["cases"], key=int),
            "query_already_indexed": any((key, h) in indexed for h in g["gold"]),
        },
    } for key, g in sorted(groups.items())]


DATASET_DESCRIPTION = "Vietnamese clinical phrase -> expected HPO term. query_already_indexed = easy item."


def main() -> None:
    items = build_items()
    langfuse = get_client()
    langfuse.create_dataset(name=DATASET, description=DATASET_DESCRIPTION,
                            metadata={})
    for item in items:
        langfuse.create_dataset_item(dataset_name=DATASET, **item)
    wanted = {i["id"] for i in items}
    stale = [i for i in langfuse.get_dataset(DATASET).items if i.id not in wanted and i.status != "ARCHIVED"]
    for i in stale:
        langfuse.create_dataset_item(dataset_name=DATASET, id=i.id, input=i.input, status="ARCHIVED")
    langfuse.flush()

    multi = sum(len(i["expected_output"]["hpo_ids"]) > 1 for i in items)
    print(f"{DATASET}: {len(items)} items ({multi} with >1 gold code), {len(stale)} archived")


if __name__ == "__main__":
    main()
