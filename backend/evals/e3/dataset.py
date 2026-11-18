"""Upload evals/e3/dataset.jsonl as Langfuse dataset e3-simulated-patients (docs/ranking-plan.md §9.4).

One item per simulated patient: id, input = {terms}, expected_output = {disease_id},
metadata = {split, variant, split_version}. Idempotent: items no longer in the file are archived.

    python -m evals.e3.dataset
"""

from langfuse import get_client

from evals.e3.generate import CONFIG, load_rows

DATASET = "e3-simulated-patients"
DATASET_DESCRIPTION = ("Simulated patients (Köhler 2009 protocol: optimal, imprecision, noise, noise_imprecision) "
                       "-> the disease they were drawn from.")


def main() -> None:
    rows = load_rows()
    langfuse = get_client()
    langfuse.create_dataset(name=DATASET, description=DATASET_DESCRIPTION, metadata={})
    for n, r in enumerate(rows, 1):
        langfuse.create_dataset_item(
            dataset_name=DATASET, id=r["id"], input={"terms": r["terms"]},
            expected_output={"disease_id": r["disease_id"]},
            metadata={"split": r["split"], "variant": r["variant"], "split_version": CONFIG["split_version"]},
        )
        if n % 500 == 0:
            print(f"  {n}/{len(rows)}", flush=True)
    wanted = {r["id"] for r in rows}
    stale = [i for i in langfuse.get_dataset(DATASET).items if i.id not in wanted and i.status != "ARCHIVED"]
    for i in stale:
        langfuse.create_dataset_item(dataset_name=DATASET, id=i.id, input=i.input, status="ARCHIVED")
    langfuse.flush()
    sides = {s: sum(r["split"] == s for r in rows) for s in ("dev", "test")}
    print(f"{DATASET} ({CONFIG['split_version']}): {len(rows)} patients ({sides}), {len(stale)} archived")


if __name__ == "__main__":
    main()
