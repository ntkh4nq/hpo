"""E3: rank every simulated patient of one side with each scoring method (docs/ranking-plan.md §9.5, §10).

    python -m evals.e3.run --split dev                  # rank (ic, ppo, rrf), save, report, log to Langfuse
    python -m evals.e3.run --from <file>                # rescore a saved run (no Langfuse)
    python -m evals.e3.run --from <file> --langfuse     # rescore and log to Langfuse again

rank = mean rank of the right disease among every indexed disease (ties share their mean rank), not cut at top_n.
Pooled metrics resample whole diseases (its four patients are not independent): bootstrap_ratio_ci(num, den);
per-variant metrics (one patient per disease) use bootstrap_ci.

Langfuse (Hobby: 50k units a month, T50): one score per patient (`rank`; the others derive from it) and the
summary metrics with their intervals at run level. dev logs all three methods; test only the chosen method.
"""

import argparse
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from langfuse import Evaluation, get_client

from evals.common.provenance import git_version
from evals.common.stats import bootstrap_ci, bootstrap_ratio_ci, paired_bootstrap_ratio_diff
from evals.e3.dataset import DATASET as LANGFUSE_DATASET
from evals.e3.generate import DATASET, VARIANTS, load_rows
from ranking.index import CONFIG as RANKING_CONFIG, load_index
from ranking.schema import QueryTerm
from ranking.score import METHODS, order_all

# 2,000 items per run overflow the default OpenTelemetry span queue (2,048) of the Langfuse client: spans were
# dropped ("Queue full, dropping Span") and runs kept 896-1,152 of 2,000 items (2026-11-18). Must be set
# before the client is created.
os.environ.setdefault("OTEL_BSP_MAX_QUEUE_SIZE", "65536")

HERE = Path(__file__).parent
RESULTS = HERE / "results"
RANKING_CONFIG_FILE = HERE.parents[1] / "ranking" / "config.toml"
TOP_K = (1, 3, 5, 10)
CI_METRICS = tuple(f"top{k}" for k in TOP_K) + ("mrr",)


# ---------- ranking

def rank_patient(index, row: dict, method: str) -> tuple[float, bool, float]:
    """(rank of the right disease, whether a disease ranked above it shares a gene with it, seconds)."""
    terms = [QueryTerm(hpo_id=t, assertion="PRESENT", weight=1.0) for t in row["terms"]]
    start = time.perf_counter()
    ranked = order_all(index, terms, method)
    seconds = time.perf_counter() - start
    target = row["disease_id"]
    rank = next(r.rank for r in ranked if r.disease_id == target)
    genes = {g.ncbi_gene_id for g in index.genes[target]}
    above = any(genes & {g.ncbi_gene_id for g in index.genes[r.disease_id]}
                for r in ranked if r.rank < rank)  # ranked list is sorted, but ties make a scan simplest
    return rank, above, seconds


def run(split: str) -> Path:
    rows = [r for r in load_rows() if r["split"] == split]
    start = time.perf_counter()
    index = load_index()
    load_seconds = time.perf_counter() - start
    print(f"index loaded in {load_seconds:.1f} s; {len(rows)} patients on {split}", flush=True)
    patients = []
    for n, row in enumerate(rows, 1):
        out = {"id": row["id"], "disease_id": row["disease_id"], "variant": row["variant"],
               "rank": {}, "above_same_gene": {}, "seconds": {}}
        for m in METHODS:
            out["rank"][m], out["above_same_gene"][m], out["seconds"][m] = rank_patient(index, row, m)
        patients.append(out)
        if n % 400 == 0:
            print(f"  {n}/{len(rows)}", flush=True)
    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "split": split,
        "git": git_version(),
        "ranking_config": RANKING_CONFIG_FILE.read_text(),
        "hpo_release": index.hpo_release,
        "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
        "diseases_indexed": index.n,
        "load_seconds": round(load_seconds, 3),
        "patients": patients,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{record['time'].replace(':', '')}.json"
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"saved {path}", flush=True)
    return path


# ---------- scoring

def per_patient(rank: float) -> dict[str, float]:
    return {"rank": rank, "rr": 1 / rank, **{f"top{k}": float(rank <= k) for k in TOP_K}}


def by_disease(patients: list[dict], method: str, metric: str) -> tuple[list[float], list[float]]:
    """(Σ metric over a disease's patients, number of patients) per disease, in first-seen order."""
    num: dict[str, float] = {}
    den: dict[str, float] = {}
    for p in patients:
        value = per_patient(p["rank"][method])["rr" if metric == "mrr" else metric]
        num[p["disease_id"]] = num.get(p["disease_id"], 0.0) + value
        den[p["disease_id"]] = den.get(p["disease_id"], 0.0) + 1
    return list(num.values()), list(den.values())


def summary(patients: list[dict], method: str, pooled: bool) -> dict:
    """Top-k and MRR as (value, lo, hi); median rank; share of patients with a same-gene disease above."""
    out = {}
    for metric in CI_METRICS:
        if pooled:
            out[metric] = bootstrap_ratio_ci(*by_disease(patients, method, metric))
        else:
            out[metric] = bootstrap_ci([per_patient(p["rank"][method])["rr" if metric == "mrr" else metric]
                                        for p in patients])
    out["median_rank"] = float(np.median([p["rank"][method] for p in patients]))
    out["above_same_gene"] = float(np.mean([p["above_same_gene"][method] for p in patients]))
    return out


def report(record: dict) -> dict:
    """Summaries per method (pooled and per variant), pairwise MRR differences (§10) and timing; printed."""
    patients = record["patients"]
    groups = {"all": patients} | {v: [p for p in patients if p["variant"] == v] for v in VARIANTS}
    result = {m: {g: summary(ps, m, pooled=g == "all") for g, ps in groups.items()} for m in METHODS}
    print(f"\nE3 {record['split']}: {len(patients)} patients, {len({p['disease_id'] for p in patients})} diseases, "
          f"{record['diseases_indexed']} diseases ranked · {record['hpo_release']} · git {record['git']}")
    for g in groups:
        print(f"\n  {g}")
        print(f"  {'method':6} " + "".join(f"{m:>22}" for m in CI_METRICS) + f"{'median':>8}{'same_gene':>10}")
        for m in METHODS:
            s = result[m][g]
            cells = "".join(f"{s[k][0]:>8.3f} [{s[k][1]:.3f},{s[k][2]:.3f}]" for k in CI_METRICS)
            print(f"  {m:6} {cells}{s['median_rank']:>8g}{s['above_same_gene']:>10.3f}")

    diffs = {}
    for a, b in (("ic", "ppo"), ("ic", "rrf"), ("ppo", "rrf")):
        num_a, den_a = by_disease(patients, a, "mrr")
        num_b, den_b = by_disease(patients, b, "mrr")
        diffs[f"{b}-{a}"] = paired_bootstrap_ratio_diff(num_a, den_a, num_b, den_b)
    print("\n  MRR difference (pooled, paired bootstrap over diseases)")
    for name, (d, lo, hi) in diffs.items():
        print(f"    {name:8} {d:+.3f} [{lo:+.3f}, {hi:+.3f}]")

    timing = {m: (float(np.median([p["seconds"][m] for p in patients])),
                  float(np.percentile([p["seconds"][m] for p in patients], 95))) for m in METHODS}
    print(f"\n  index load {record['load_seconds']:.1f} s; order_all p50 / p95 (ms): "
          + ", ".join(f"{m} {p50 * 1000:.0f} / {p95 * 1000:.0f}" for m, (p50, p95) in timing.items()))
    return {"summary": result, "mrr_diff": diffs, "timing": timing}


# ---------- Langfuse

def log_langfuse(record: dict, scored: dict, path: Path, methods: tuple[str, ...]) -> None:
    by_id = {p["id"]: p for p in record["patients"]}
    data = [i for i in get_client().get_dataset(LANGFUSE_DATASET).items
            if i.status != "ARCHIVED" and (i.metadata or {}).get("split") == record["split"]]
    for m in methods:
        def task(*, item, **kwargs):
            p = by_id[item.id]
            return {"disease_id": p["disease_id"], "rank": p["rank"][m], "above_same_gene": p["above_same_gene"][m]}

        def item_evaluator(*, output, **kwargs):
            return [Evaluation(name="rank", value=output["rank"])]

        def run_evaluator(**kwargs):
            out = []
            for group, s in scored["summary"][m].items():
                for metric in CI_METRICS:
                    v, lo, hi = s[metric]
                    out += [Evaluation(name=f"{group}_{metric}", value=v),
                            Evaluation(name=f"{group}_{metric}_ci_lo", value=lo),
                            Evaluation(name=f"{group}_{metric}_ci_hi", value=hi)]
                out += [Evaluation(name=f"{group}_median_rank", value=s["median_rank"]),
                        Evaluation(name=f"{group}_above_same_gene", value=s["above_same_gene"])]
            p50, p95 = scored["timing"][m]
            out += [Evaluation(name="order_all_p50_s", value=p50), Evaluation(name="order_all_p95_s", value=p95),
                    Evaluation(name="index_load_s", value=record["load_seconds"])]
            return out

        result = get_client().run_experiment(
            name="E3 simulated patients", run_name=f"{record['split']} · {m} @ {record['git']}",
            description=f"saved output {path.name}", data=data, task=task, evaluators=[item_evaluator],
            run_evaluators=[run_evaluator], max_concurrency=16,
            metadata={"split": record["split"], "method": m, "git": record["git"],
                      "ranking_config": record["ranking_config"], "hpo_release": record["hpo_release"],
                      "dataset_sha256": record["dataset_sha256"], "output_file": path.name},
        )
        print(f"  {m}: {result.experiment_url}", flush=True)
    get_client().flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("dev", "test"))
    parser.add_argument("--from", dest="source", type=Path, help="rescore this saved run")
    parser.add_argument("--langfuse", action="store_true", help="with --from: log to Langfuse again")
    args = parser.parse_args()
    if bool(args.split) == bool(args.source):
        parser.error("give exactly one of --split, --from")
    path = args.source or run(args.split)
    record = json.loads(path.read_text(encoding="utf-8"))
    if record["dataset_sha256"] != hashlib.sha256(DATASET.read_bytes()).hexdigest():
        raise SystemExit(f"{path.name} was run on another version of {DATASET.name}")
    scored = report(record)
    if args.split or args.langfuse:
        chosen = (RANKING_CONFIG["method"],)
        log_langfuse(record, scored, path, METHODS if record["split"] == "dev" else chosen)


if __name__ == "__main__":
    main()
