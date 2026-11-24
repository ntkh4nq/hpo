"""Run the split check: the split step on every description, `runs` times (config.toml).

    python -m evals.split_check.run            # report, save evals/split_check/results/<time>.json
    python -m evals.split_check.run --model vlm
    python -m evals.split_check.run --thinking     # override phenotype/split.toml
    python -m evals.split_check.run --ids S01,S05  # only these descriptions (re-check after a prompt fix)

The first saved run becomes evals/split_check/results/baseline.json; later runs print their totals next to it.
Report per description: missed / extra / wrong-status items, and fuzzy pairs (similarity < 1) to eyeball.
source_text_found: share of the model's items whose source_text occurs verbatim in the preprocessed text (T33);
source_text is not scored against the gold items.
"""

import argparse
import json
import os
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from langfuse import get_client

from evals.split_check.score import CONFIG, load_cases, score_case, totals
from phenotype.split import CONFIG as SPLIT_CONFIG, PROMPT_VERSION, locate, preprocess, split

RESULTS = Path(__file__).parent / "results"
METRICS = ("recall", "precision", "status_accuracy", "missed", "extra", "wrong_status", "measurement_accuracy",
           "wrong_measurement", "source_text_found")


def run_once(cases: list[dict], model: str, thinking: bool) -> dict:
    def one(case):
        t0 = time.perf_counter()
        items = split(case["text"], model=model, thinking=thinking)
        return case, items, time.perf_counter() - t0

    with ThreadPoolExecutor(CONFIG["concurrency"]) as pool:
        outputs = list(pool.map(one, cases))
    per_case, scores, gold_n, pred_n, found = [], [], [], [], 0
    for case, items, seconds in outputs:
        pred = [i.model_dump() for i in items] if items is not None else []
        text = preprocess(case["text"])
        found += sum(locate(text, p["source_text"]) is not None for p in pred)
        s = score_case(case["items"], pred)
        scores.append(s)
        gold_n.append(len(case["items"]))
        pred_n.append(len(pred))
        per_case.append({"id": case["id"], "failed": items is None, "seconds": round(seconds, 1), "pred": pred, **s})
    result = {"totals": totals(scores, gold_n, pred_n), "cases": per_case}
    result["totals"]["source_text_found"] = found / sum(pred_n) if sum(pred_n) else 0.0
    return result


def print_case(c: dict) -> None:
    if c["failed"]:
        print(f"{c['id']}: model failed, description not analysed")
        return
    if not (c["missed"] or c["extra"] or c["wrong_status"] or c["fuzzy"] or c["wrong_measurement"]):
        return
    print(f"{c['id']}:")
    for g in c["missed"]:
        print(f"   - sót    {g['phrase']}  ({g['assertion']}, {g['assessment']}, {g['subject']})")
    for p in c["extra"]:
        print(f"   + thừa   {p['phrase']}  ({p['assertion']}, {p['assessment']}, {p['subject']})")
    for w in c["wrong_status"]:
        diff = ", ".join(f"{f}: {w['gold'][f]} → {w['pred'].get(f)}" for f in w["fields"])
        print(f"   ≠ sai    {w['gold']['phrase']}  ({diff})")
    for w in c["wrong_measurement"]:
        print(f"   # số đo  {w['gold']['phrase']}  ({w['gold']['measurement']['value']} → "
              f"{(w['pred'].get('measurement') or {}).get('value')})")
    for g, p, s in c["fuzzy"]:
        print(f"   ~ ghép   {g}  ≈  {p}  ({s})")


def fmt(t: dict) -> str:
    return (f"recall {t['recall']:.3f}  precision {t['precision']:.3f}  status_acc {t['status_accuracy']:.3f}  "
            f"sót {t['missed']}  thừa {t['extra']}  sai {t['wrong_status']}  số đo sai {t['wrong_measurement']}  source_text_found {t['source_text_found']:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.environ["LLM_MODEL"])
    parser.add_argument("--runs", type=int, default=CONFIG["runs"])
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=SPLIT_CONFIG["thinking"])
    parser.add_argument("--ids", help="comma-separated description ids; default: all")
    args = parser.parse_args()

    cases = load_cases()
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
        assert len(cases) == len(wanted), f"unknown ids: {wanted - {c['id'] for c in cases}}"
    runs = []
    for r in range(args.runs):
        result = run_once(cases, args.model, args.thinking)
        runs.append(result)
        print(f"\n===== run {r + 1}/{args.runs}: {fmt(result['totals'])}")
        for c in result["cases"]:
            print_case(c)
    get_client().flush()

    summary = {m: [run["totals"][m] for run in runs] for m in METRICS}
    print("\n===== tổng hợp (trung bình ± độ lệch chuẩn qua các lần chạy)")
    for m, values in summary.items():
        sd = statistics.stdev(values) if len(values) > 1 else 0.0
        print(f"  {m:17} {statistics.mean(values):.3f} ± {sd:.3f}   {values}")

    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        "thinking": args.thinking,
        "git": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "config": CONFIG,
        "ids": [c["id"] for c in cases],
        "summary": summary,
        "runs": runs,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{record['time'].replace(':', '')}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    baseline = RESULTS / "baseline.json"
    if args.ids:
        print(f"\nSaved {path.name} (subset: not compared with the baseline).")
    elif not baseline.exists():
        baseline.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\nSaved {path.name}; it is the baseline.")
    else:
        base = json.loads(baseline.read_text(encoding="utf-8"))
        print(f"\nSaved {path.name}. Baseline ({base['time']}, {base['model']}, prompt {base['prompt_version']}):")
        for m in METRICS:
            old = f"{statistics.mean(base['summary'][m]):.3f}" if m in base["summary"] else "—"
            print(f"  {m:17} baseline {old:>5}   now {statistics.mean(summary[m]):.3f}")


if __name__ == "__main__":
    main()
