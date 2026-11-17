"""Upload datasets/e2.jsonl (locked, decisions T45) as Langfuse dataset e2-description-to-profile (docs/e2-plan.md §5.2).

One item per case: id = case id, input = {text}, expected_output = {disease_id, items}. Every case is test
(e2-plan §1). Idempotent: items no longer in the file are archived.

    python -m eval.e2_dataset
"""

from langfuse import get_client

from eval.e2_check import load_cases

DATASET = "e2-description-to-profile"
SPLIT_VERSION = "v1"  # bump (and add a decisions.md row) whenever a case of e2.jsonl changes (T45)
DATASET_DESCRIPTION = "Vietnamese prenatal description -> gold items (phrase, status, subject, HPO code). Test only."


def main() -> None:
    cases = load_cases()
    langfuse = get_client()
    langfuse.create_dataset(name=DATASET, description=DATASET_DESCRIPTION, metadata={})
    for c in cases:
        langfuse.create_dataset_item(
            dataset_name=DATASET, id=c["id"], input={"text": c["text"]},
            expected_output={"disease_id": c["disease_id"], "items": c["items"]},
            metadata={"split": "test", "split_version": SPLIT_VERSION},
        )
    wanted = {c["id"] for c in cases}
    stale = [i for i in langfuse.get_dataset(DATASET).items if i.id not in wanted and i.status != "ARCHIVED"]
    for i in stale:
        langfuse.create_dataset_item(dataset_name=DATASET, id=i.id, input=i.input, status="ARCHIVED")
    langfuse.flush()
    print(f"{DATASET} ({SPLIT_VERSION}): {len(cases)} cases, {sum(len(c['items']) for c in cases)} items, "
          f"{len(stale)} archived")


if __name__ == "__main__":
    main()
