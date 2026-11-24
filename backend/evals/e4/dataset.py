"""E4 cases: the 40 E2 machine profiles saved by the E2 run (docs/evidence-plan.md §9, docs/evidence-impl-plan.md
task 6). The saved E2 output is gitignored, so the analyses are copied here (without candidates and rejections,
which neither ranking nor explanations read) to make E4 reproducible from the repository.

    python -m evals.e4.dataset build     # evals/e4/dataset.jsonl from evals/e2/results/2026-11-17T144801.json
    python -m evals.e4.dataset upload    # Langfuse dataset e4-explanations
"""

import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE.parent / "e2" / "results" / "2026-11-17T144801.json"
DATASET = HERE / "dataset.jsonl"
LANGFUSE_DATASET = "e4-explanations"
SPLIT_VERSION = "v1"
DEV_SIZE = 20


def load_rows() -> list[dict]:
    return [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]


def build() -> list[dict]:
    record = json.loads(SOURCE.read_text(encoding="utf-8"))
    cases = sorted((c for c in record["cases"] if c["analysis"]), key=lambda c: c["id"])
    dev = set(sorted((c["id"] for c in cases), key=lambda i: hashlib.sha1(i.encode()).hexdigest())[:DEV_SIZE])
    rows = []
    for c in cases:
        a = {k: v for k, v in c["analysis"].items() if k != "rejections"}
        a["findings"] = [{k: v for k, v in f.items() if k != "candidates"} for f in a["findings"]]
        rows.append({"id": c["id"], "split": "dev" if c["id"] in dev else "test", "split_version": SPLIT_VERSION,
                     "analysis": a})
    return rows


def upload() -> None:
    from langfuse import get_client

    langfuse = get_client()
    langfuse.create_dataset(name=LANGFUSE_DATASET, description="E2 machine profiles -> explanations of the top 10 "
                            "(E4, docs/evidence-plan.md §9)", metadata={})
    for r in load_rows():
        # Langfuse item ids are unique per project and E2 already uses the case ids, hence the prefix.
        langfuse.create_dataset_item(dataset_name=LANGFUSE_DATASET, id=f"E4-{r['id']}", input={"id": r["id"]},
                                     metadata={"split": r["split"], "split_version": r["split_version"]})
    langfuse.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "upload"))
    if parser.parse_args().command == "upload":
        upload()
        return
    rows = build()
    DATASET.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{len(rows)} cases: dev {sum(r['split'] == 'dev' for r in rows)}, test {sum(r['split'] == 'test' for r in rows)}")


if __name__ == "__main__":
    main()
