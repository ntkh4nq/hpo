"""E2: description -> Internal Phenotype Profile on the 40 locked test cases (docs/e2-plan.md §7-§8).

    python -m evals.e2.run                # run the graph on every case (40 DeepSeek + 40 Qwen requests), save, score
    python -m evals.e2.run --from <file>  # rescore a saved run, no model call

Model items are Analysis.findings, paired with gold items by evals.common.matching.match() (threshold in matching.toml).
Per case: split counts and assertion pairs; mapping on matched pairs (correct / near_correct = code equal to, or a
direct parent/child of, gold; gold hpo_id null is right only when UNMAPPED); profile sets G = {(hpo_id, assertion)}
of gold items with a code and P = {(term.hpo_id, assertion)} of MAPPED findings; seconds per node.
Totals are Σnum / Σden over cases, with 95% bootstrap intervals that resample whole cases (e2-plan §7.3).
"""

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import get_args

import numpy as np
from langfuse import Evaluation, get_client

from evals.common.provenance import git_version
from evals.common.tree import neighbours
from evals.e2.check import DATASET as CASES_FILE, load_cases
from evals.e2.dataset import DATASET
from evals.common.matching import CONFIG as MATCH_CONFIG, match, measurement_ok
from evals.split_check.score import STATUS_FIELDS
from evals.common.stats import bootstrap_ratio_ci
from phenotype import mapping, split
from phenotype.graph import graph, initial_state
from phenotype.schema import Assertion

RESULTS = Path(__file__).parent / "results"
ASSERTIONS = get_args(Assertion)
# metric -> (numerator, denominator) per case; these get a bootstrap interval (e2-plan §7.3)
RATIOS = {
    "split_recall": ("matched", "gold_items"),
    "split_precision": ("matched", "model_items"),
    "mapping_accuracy": ("correct", "matched"),
    "mapping_near_accuracy": ("near_correct", "matched"),
    "profile_recall": ("profile_hits", "profile_gold"),
    "profile_precision": ("profile_hits", "profile_pred"),
}
CASE_COUNTS = ("gold_items", "model_items", "matched", "status_correct", "correct", "near_correct", "ambiguous",
               "unmapped", "rejections", "profile_hits", "profile_gold", "profile_pred")


# ---------- running

def run_case(case: dict) -> dict:
    """Analysis (as JSON) and seconds per node; an exception is recorded so the other cases are kept."""
    seconds, analysis = {}, None
    start = last = time.perf_counter()
    try:
        for update in graph.stream(initial_state(case["text"]), stream_mode="updates"):
            (node, value), = update.items()
            now = time.perf_counter()
            seconds[node] = round(now - last, 3)
            last = now
            if node == "check_choices":
                analysis = value["analysis"].model_dump(mode="json")
    except Exception as e:  # noqa: BLE001 - keep the paid-for output of the other cases
        print(f"{case['id']}: {e!r}", flush=True)
        return {"id": case["id"], "error": repr(e), "analysis": None, "seconds": seconds}
    seconds["graph"] = round(last - start, 3)
    print(f"{case['id']}: {len(analysis['findings'])} findings, {seconds['graph']:.1f} s", flush=True)
    return {"id": case["id"], "error": None, "analysis": analysis, "seconds": seconds}


def run_model(cases: list[dict], concurrency: int) -> Path:
    print(f"{len(cases)} cases: {len(cases)} DeepSeek requests (split) + up to {len(cases)} Qwen (choose), "
          f"plus retries", flush=True)
    with ThreadPoolExecutor(concurrency) as pool:
        outputs = list(pool.map(run_case, cases))
    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "git": git_version(),
        "dataset_sha256": hashlib.sha256(CASES_FILE.read_bytes()).hexdigest(),
        "prompt_version": {"split": split.PROMPT_VERSION, "mapping": mapping.PROMPT_VERSION},
        "config": {"split": split.CONFIG, "mapping": mapping.CONFIG},
        "model": {"split": os.environ["LLM_MODEL"], "choose": mapping.CONFIG["model"]},
        "cases": outputs,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{record['time'].replace(':', '')}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {path}", flush=True)
    return path


# ---------- scoring

def score_case(gold: list[dict], analysis: dict | None, near: dict[str, set[str]]) -> dict:
    """e2-plan §7.1 for one case; a case whose run raised counts as having no findings."""
    findings = analysis["findings"] if analysis else []
    pairs = match(gold, findings, MATCH_CONFIG["match_threshold"])
    correct = near_correct = status_correct = measured = measurement_correct = 0
    for gi, pi, _ in pairs:
        g, f = gold[gi], findings[pi]
        status_correct += all(g[k] == f[k] for k in STATUS_FIELDS)
        if (ok := measurement_ok(g, f)) is not None:
            measured += 1
            measurement_correct += ok
        if g["hpo_id"] is None:
            right = near_right = f["mapping_status"] == "UNMAPPED"
        else:
            code = f["term"]["hpo_id"] if f["mapping_status"] == "MAPPED" else None
            right = code == g["hpo_id"]
            near_right = right or code in near.get(g["hpo_id"], set())
        correct += right
        near_correct += near_right
    statuses = Counter(findings[pi]["mapping_status"] for _, pi, _ in pairs)
    G = {(i["hpo_id"], i["assertion"]) for i in gold if i["hpo_id"]}
    P = {(f["term"]["hpo_id"], f["assertion"]) for f in findings if f["mapping_status"] == "MAPPED"}
    return {
        "gold_items": len(gold), "model_items": len(findings), "matched": len(pairs),
        "status_correct": status_correct, "measured": measured, "measurement_correct": measurement_correct,
        "assertion_pairs": [[gold[gi]["assertion"], findings[pi]["assertion"]] for gi, pi, _ in pairs],
        "correct": correct, "near_correct": near_correct,
        "ambiguous": statuses["AMBIGUOUS"], "unmapped": statuses["UNMAPPED"],
        "rejections": len(analysis["rejections"]) if analysis else 0,
        "profile_hits": len(G & P), "profile_gold": len(G), "profile_pred": len(P),
        "split_failed": bool(analysis and analysis["split_failed"]),
        "choose_failed": bool(analysis and analysis["choose_failed"]),
        "error": analysis is None,
    }


def ratio(scores: list[dict], num: str, den: str) -> float:
    d = sum(s[den] for s in scores)
    return sum(s[num] for s in scores) / d if d else 0.0


def f1(p: float, r: float) -> float:
    return 2 * p * r / (p + r) if p + r else 0.0


def totals(scores: list[dict], seconds: list[dict[str, float]]) -> dict:
    """e2-plan §7.2 over all cases; ratio metrics as (value, lo, hi)."""
    out = {name: bootstrap_ratio_ci([s[n] for s in scores], [s[d] for s in scores])
           if sum(s[d] for s in scores) else (0.0, 0.0, 0.0)  # e.g. no MAPPED finding at all: profile_precision
           for name, (n, d) in RATIOS.items()}
    out["split_f1"] = f1(out["split_precision"][0], out["split_recall"][0])
    out["profile_f1"] = f1(out["profile_precision"][0], out["profile_recall"][0])
    out["status_accuracy"] = ratio(scores, "status_correct", "matched")
    out["measurement_accuracy"] = ratio(scores, "measurement_correct", "measured")
    out["ambiguous_rate"] = ratio(scores, "ambiguous", "matched")
    out["unmapped_rate"] = ratio(scores, "unmapped", "matched")
    for k in ("rejections", "gold_items", "model_items", "matched", "split_failed", "choose_failed", "error"):
        out[k] = sum(s[k] for s in scores)
    out["confusion"] = Counter(tuple(p) for s in scores for p in s["assertion_pairs"])
    out["seconds"] = {}
    for node in dict.fromkeys(k for sec in seconds for k in sec):  # nodes that ran at least once, in order
        v = [sec[node] for sec in seconds if node in sec]
        out["seconds"][node] = (float(np.median(v)), float(np.percentile(v, 95)))
    return out


def print_totals(t: dict) -> None:
    print(f"\nE2 (test), {t['gold_items']} gold items, {t['model_items']} model items, {t['matched']} matched")
    for name in RATIOS:
        v, lo, hi = t[name]
        print(f"  {name:24} {v:.3f} [{lo:.3f}, {hi:.3f}]")
    for k in ("split_f1", "profile_f1", "status_accuracy", "measurement_accuracy", "ambiguous_rate", "unmapped_rate"):
        print(f"  {k:24} {t[k]:.3f}")
    for k in ("rejections", "split_failed", "choose_failed", "error"):
        print(f"  {k:24} {t[k]}")
    print("\n  assertion: gold (rows) × model (columns), matched pairs")
    print("  " + " " * 11 + "".join(f"{a:>11}" for a in ASSERTIONS))
    for g in ASSERTIONS:
        print(f"  {g:>11}" + "".join(f"{t['confusion'][(g, m)]:>11}" for m in ASSERTIONS))
    print("\n  seconds        median     p95")
    for node, (med, p95) in t["seconds"].items():
        print(f"  {node:15} {med:6.1f}  {p95:6.1f}")


def print_cases(cases: list[dict], record: dict, scores: list[dict], near: dict[str, set[str]]) -> None:
    """Per case: unmatched gold/model items and matched pairs with a wrong status or code."""
    outputs = {o["id"]: o for o in record["cases"]}
    for case, s in zip(cases, scores):
        out = outputs[case["id"]]
        if out["error"]:
            print(f"{case['id']}: error {out['error']}")
            continue
        if s["split_failed"]:
            print(f"{case['id']}: split failed, description not analysed")
            continue
        gold, findings = case["items"], out["analysis"]["findings"]
        pairs = match(gold, findings, MATCH_CONFIG["match_threshold"])
        lines = [f"   - sót    {gold[i]['phrase']}" for i in range(len(gold)) if i not in {g for g, _, _ in pairs}]
        lines += [f"   + thừa   {findings[i]['phrase']}  ({findings[i]['assertion']})"
                  for i in range(len(findings)) if i not in {p for _, p, _ in pairs}]
        for gi, pi, _ in pairs:
            g, f = gold[gi], findings[pi]
            wrong = [f"{k}: {g[k]} → {f[k]}" for k in STATUS_FIELDS if g[k] != f[k]]
            if measurement_ok(g, f) is False:
                wrong.append(f"số đo: {g['measurement']['value']} → {(f.get('measurement') or {}).get('value')}")
            code = f["term"]["hpo_id"] if f["mapping_status"] == "MAPPED" else f["mapping_status"]
            if code != (g["hpo_id"] or "UNMAPPED"):
                near_mark = " (cha/con)" if code in near.get(g["hpo_id"], set()) else ""
                wrong.append(f"mã: {g['hpo_id']} → {code}{near_mark}")
            if wrong:
                lines.append(f"   ≠ sai    {g['phrase']} ≈ {f['phrase']}  ({'; '.join(wrong)})")
        if lines:
            print(f"{case['id']}:")
            print("\n".join(lines))


# ---------- Langfuse

def report(cases: list[dict], record: dict, path: Path, near: dict[str, set[str]]) -> None:
    outputs = {o["id"]: o for o in record["cases"]}
    scores = [score_case(c["items"], outputs[c["id"]]["analysis"], near) for c in cases]
    t = totals(scores, [outputs[c["id"]]["seconds"] for c in cases if not outputs[c["id"]]["error"]])
    print_cases(cases, record, scores, near)
    print_totals(t)

    by_id = {c["id"]: s for c, s in zip(cases, scores)}
    data = [i for i in get_client().get_dataset(DATASET).items if i.status != "ARCHIVED"]

    def task(*, item, **kwargs):
        return {**outputs[item.id], "scores": by_id[item.id]}

    def item_evaluator(*, output, **kwargs):
        s = output["scores"]
        return ([Evaluation(name=k, value=float(s[k])) for k in CASE_COUNTS]
                + [Evaluation(name="seconds_graph", value=output["seconds"].get("graph", 0.0))])

    def run_evaluator(**kwargs):
        out = []
        for name in RATIOS:
            v, lo, hi = t[name]
            out += [Evaluation(name=name, value=v), Evaluation(name=f"{name}_ci_lo", value=lo),
                    Evaluation(name=f"{name}_ci_hi", value=hi)]
        out += [Evaluation(name=k, value=float(t[k])) for k in
                ("split_f1", "profile_f1", "status_accuracy", "ambiguous_rate", "unmapped_rate", "rejections",
                 "split_failed", "choose_failed", "error")]
        for node, (med, p95) in t["seconds"].items():
            out += [Evaluation(name=f"seconds_{node}_median", value=med),
                    Evaluation(name=f"seconds_{node}_p95", value=p95)]
        return out

    pv = record["prompt_version"]
    result = get_client().run_experiment(
        name="E2 description -> profile",
        run_name=f"split {pv['split']} · choose {pv['mapping']} @ {record['git']}",
        description=f"graph on the 40 E2 test cases, saved output {path.name}",
        data=data, task=task, evaluators=[item_evaluator], run_evaluators=[run_evaluator], max_concurrency=8,
        metadata={"split": "test", "prompt_version_split": pv["split"], "prompt_version_mapping": pv["mapping"],
                  "model_split": record["model"]["split"], "model_choose": record["model"]["choose"],
                  "config": json.dumps(record["config"]), "git": record["git"],
                  "dataset_sha256": record["dataset_sha256"], "output_file": path.name},
    )
    print(f"\n  {result.experiment_url}")
    get_client().flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="source", type=Path, help="rescore this saved run instead of calling the model")
    parser.add_argument("--concurrency", type=int, default=4, help="cases run in parallel")
    args = parser.parse_args()
    cases = load_cases()
    path = args.source or run_model(cases, args.concurrency)
    record = json.loads(path.read_text(encoding="utf-8"))
    if record["dataset_sha256"] != hashlib.sha256(CASES_FILE.read_bytes()).hexdigest():
        raise SystemExit(f"{path.name} was run on another version of {CASES_FILE.name}")
    report(cases, record, path, neighbours())


if __name__ == "__main__":
    main()
