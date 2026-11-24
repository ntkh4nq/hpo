"""Calibrate the E4 faithfulness judge against blind agent labels (docs/evidence-plan.md §9; T7c: kappa ≥ 0.6 before use).

    python -m evals.e4.calibrate sample --from <dev results file>   # 40 claims -> calibration.jsonl (labels empty)
    python -m evals.e4.calibrate blind [--disagreements]            # the rows without any label (for the agents)
    python -m evals.e4.calibrate final                              # final = agreed label, else the tie-breaker's
    python -m evals.e4.calibrate kappa                              # agreement and Cohen's kappa, judge vs final
"""

import hashlib
import json
from pathlib import Path

from evals.common.calibration import cli, save
from evals.e4.run import claims_of

HERE = Path(__file__).parent
CALIBRATION = HERE / "calibration.jsonl"
PER_SIDE = 20
FIELDS = ("id", "disease_name", "hpo_label", "text", "passages")


def sample(source: Path) -> None:
    record = json.loads(source.read_text(encoding="utf-8"))
    if record["split"] != "dev":
        raise SystemExit("calibrate on dev output only")
    entries = []
    for case in record["cases"]:
        judged = record["faithfulness"].get(case["id"], {})
        for c in claims_of(case):
            if c["key"] in judged:
                info = case["sent"][c["pair"]]
                entries.append({"id": f"{case['id']}:{c['key'][:8]}", "disease_name": info["disease_name"],
                                "hpo_label": info["hpo_label"], "text": c["text"], "passages": c["passages"],
                                "judge": judged[c["key"]], "agent": None, "agent2": None, "tiebreak": None, "final": None})
    entries.sort(key=lambda e: hashlib.sha1(e["id"].encode()).hexdigest())
    chosen = [e for e in entries if e["judge"]][:PER_SIDE] + [e for e in entries if not e["judge"]][:PER_SIDE]
    chosen.sort(key=lambda e: hashlib.sha1(e["id"].encode()).hexdigest())
    save(CALIBRATION, chosen)
    print(f"{CALIBRATION}: {len(chosen)} rows ({sum(e['judge'] for e in chosen)} judged supported)")


if __name__ == "__main__":
    cli(CALIBRATION, FIELDS, sample)
