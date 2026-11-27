"""Upload evals/split_check/dataset.jsonl as Langfuse dataset split-check (docs/eval-strategy.md §3, §8).

One item per description: id = description id (S01…), input = {text}, expected_output = {items}. The whole set is
dev (it was used to write the split prompt). Idempotent: items no longer in the file are archived.

    python -m evals.split_check.dataset
"""

from langfuse import get_client

from evals.split_check.score import load_cases

DATASET = "split-check"
DATASET_DESCRIPTION = "Vietnamese prenatal description -> gold items (phrase, status, subject, measurement). Dev only."


def main() -> None:
    cases = load_cases()
    langfuse = get_client()
    langfuse.create_dataset(name=DATASET, description=DATASET_DESCRIPTION, metadata={})
    for c in cases:
        langfuse.create_dataset_item(dataset_name=DATASET, id=c["id"], input={"text": c["text"]},
                                     expected_output={"items": c["items"]}, metadata={"split": "dev"})
    wanted = {c["id"] for c in cases}
    stale = [i for i in langfuse.get_dataset(DATASET).items if i.id not in wanted and i.status != "ARCHIVED"]
    for i in stale:
        langfuse.create_dataset_item(dataset_name=DATASET, id=i.id, input=i.input, status="ARCHIVED")
    langfuse.flush()
    print(f"{DATASET}: {len(cases)} descriptions, {sum(len(c['items']) for c in cases)} items, {len(stale)} archived")


if __name__ == "__main__":
    main()
