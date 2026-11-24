"""Calibrate the E5 judge against blind agent labels (docs/ingestion-plan.md §11; T7c: kappa ≥ 0.6 before use).

    python -m evals.e5.calibrate sample --from <dev results file>   # 40 rows -> calibration.jsonl (labels empty)
    python -m evals.e5.calibrate blind [--disagreements]            # the rows without any label (for the agents)
    python -m evals.e5.calibrate final                              # final = agreed label, else the tie-breaker's
    python -m evals.e5.calibrate kappa                              # agreement and Cohen's kappa, judge vs final
"""

import hashlib
import json
from pathlib import Path

from evals.common.calibration import cli, save
from evals.e5.dataset import load_rows

HERE = Path(__file__).parent
CALIBRATION = HERE / "calibration.jsonl"
PER_SIDE = 20
FIELDS = ("id", "disease_name", "hpo_label", "display_text")


def sample(source: Path) -> None:
    record = json.loads(source.read_text(encoding="utf-8"))
    if record["split"] != "dev":
        raise SystemExit("calibrate on dev output only")
    rows = {r["id"]: r for r in load_rows()}
    entries = [{"id": f"{pid}:{key[:8]}", "chunk_id": v["chunk_id"], "display_text": v["display_text"],
                "disease_name": rows[pid]["disease_name"], "hpo_label": rows[pid]["hpo_label"], "judge": v["supports"],
                "agent": None, "agent2": None, "tiebreak": None, "final": None}
               for pid, labels in record["judgements"].items() for key, v in labels.items()]
    entries.sort(key=lambda e: hashlib.sha1(e["id"].encode()).hexdigest())
    chosen = [e for e in entries if e["judge"]][:PER_SIDE] + [e for e in entries if not e["judge"]][:PER_SIDE]
    chosen.sort(key=lambda e: hashlib.sha1(e["id"].encode()).hexdigest())
    save(CALIBRATION, chosen)
    print(f"{CALIBRATION}: {len(chosen)} rows")


if __name__ == "__main__":
    cli(CALIBRATION, FIELDS, sample)
