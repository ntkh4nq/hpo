"""E2 -> E3: how far phase 3 errors move the right disease (docs/ranking-plan.md §12). No model call.

    python -m evals.e3.impact --from evals/e2/results/2026-11-17T144801.json

For each of the 40 E2 cases, rank with the gold profile (gold items with a code, FETUS, PRESENT/SUSPECTED) and
with the machine profile (query_terms of the saved Analysis), using order_all and the configured method.
E2 is a test set: nothing in phase 3 or 4 is tuned on these numbers.
"""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
from langfuse import Evaluation, get_client

from evals.common.provenance import git_version
from evals.common.stats import bootstrap_ci, paired_bootstrap_diff
from evals.e2.check import DATASET as E2_CASES_FILE, load_cases
from evals.e2.dataset import DATASET as E2_LANGFUSE_DATASET
from phenotype.schema import Analysis
from ranking.index import CONFIG as RANKING_CONFIG, load_index
from ranking.rank import query_terms, weight_of
from ranking.schema import QueryTerm
from ranking.score import excluded_mask, order_all

RESULTS = Path(__file__).parent / "results"
TOP_K = (1, 5, 10)


def gold_terms(items: list[dict]) -> list[QueryTerm]:
    terms: dict[str, QueryTerm] = {}
    for i in items:
        if i["hpo_id"] and i["subject"] == "FETUS" and i["assertion"] in ("PRESENT", "SUSPECTED"):
            t = QueryTerm(hpo_id=i["hpo_id"], assertion=i["assertion"], weight=weight_of(i["assertion"]))
            if i["hpo_id"] not in terms or t.weight > terms[i["hpo_id"]].weight:
                terms[i["hpo_id"]] = t
    return list(terms.values())


def rank_of(index, terms: list[QueryTerm], disease_id: str, method: str) -> float:
    return next(r.rank for r in order_all(index, terms, method) if r.disease_id == disease_id)


def evaluate(e2_record: dict, method: str) -> list[dict]:
    index = load_index()
    analyses = {c["id"]: c["analysis"] for c in e2_record["cases"]}
    out = []
    for case in load_cases():
        gold = gold_terms(case["items"])
        analysis = analyses[case["id"]]
        machine = query_terms(Analysis.model_validate(analysis).findings, index=index)[0] if analysis else []
        d = case["disease_id"]
        g_ids, m_ids = {t.hpo_id for t in gold}, {t.hpo_id for t in machine}
        out.append({
            "id": case["id"], "disease_id": d,
            "gold_rank": rank_of(index, gold, d, method), "machine_rank": rank_of(index, machine, d, method),
            "gold_terms": [t.model_dump() for t in gold], "machine_terms": [t.model_dump() for t in machine],
            "missing": sorted(g_ids - m_ids), "extra": sorted(m_ids - g_ids),
            "fetal_codes": sorted(h for h in m_ids if index.labels[h][0].startswith("Fetal ")),
            # gold codes the disease is recorded as not having (§3.4): the ranker scores them against it
            "gold_excluded": sorted(t.hpo_id for t in gold if excluded_mask(index, t.hpo_id)[index.position[d]]),
        })
    return out


def profile_summary(ranks: list[float]) -> dict:
    return {"median_rank": float(np.median(ranks)), "mrr": bootstrap_ci([1 / r for r in ranks]),
            **{f"top{k}": bootstrap_ci([float(r <= k) for r in ranks]) for k in TOP_K}}


def report(record: dict) -> dict:
    cases = record["cases"]
    out = {p: profile_summary([c[f"{p}_rank"] for c in cases]) for p in ("gold", "machine")}
    out["rr_diff"] = paired_bootstrap_diff([1 / c["gold_rank"] for c in cases], [1 / c["machine_rank"] for c in cases])
    print(f"\nE2 -> E3 · method {record['method']} · {len(cases)} cases · E2 output {record['e2_output']}")
    print("\n  case    disease          gold  machine   missing (gold not in machine) | extra (machine not in gold)")
    for c in cases:
        flag = f"  ! gold excluded at disease: {', '.join(c['gold_excluded'])}" if c["gold_excluded"] else ""
        print(f"  {c['id']:7} {c['disease_id']:15} {c['gold_rank']:>6g} {c['machine_rank']:>8g}   "
              f"{' '.join(c['missing']) or '—'} | {' '.join(c['extra']) or '—'}{flag}")
    print(f"\n  {'profile':8} {'median':>7} " + "".join(f"{k:>22}" for k in [f'top{k}' for k in TOP_K] + ['mrr']))
    for p in ("gold", "machine"):
        s = out[p]
        print(f"  {p:8} {s['median_rank']:>7g} " + "".join(
            f"{s[k][0]:>8.3f} [{s[k][1]:.3f},{s[k][2]:.3f}]" for k in [f'top{k}' for k in TOP_K] + ['mrr']))
    d, lo, hi = out["rr_diff"]
    print(f"\n  reciprocal rank, machine − gold: {d:+.3f} [{lo:+.3f}, {hi:+.3f}]")
    fetal = sum(len(c["fetal_codes"]) for c in cases)
    print(f"  'Fetal …' codes in machine profiles: {fetal} in {sum(bool(c['fetal_codes']) for c in cases)} cases")
    flagged = [c for c in cases if c["gold_excluded"]]
    print(f"  cases with a gold code excluded at their disease: {', '.join(c['id'] for c in flagged) or '—'}")
    out["fetal_codes"] = fetal
    return out


def log_langfuse(record: dict, scored: dict, path: Path) -> None:
    by_id = {c["id"]: c for c in record["cases"]}
    data = [i for i in get_client().get_dataset(E2_LANGFUSE_DATASET).items if i.status != "ARCHIVED"]

    def task(*, item, **kwargs):
        c = by_id[item.id]
        return {k: c[k] for k in ("disease_id", "gold_rank", "machine_rank", "missing", "extra", "gold_excluded")}

    def item_evaluator(*, output, **kwargs):
        return [Evaluation(name="gold_rank", value=output["gold_rank"]),
                Evaluation(name="machine_rank", value=output["machine_rank"])]

    def run_evaluator(**kwargs):
        out = []
        for p in ("gold", "machine"):
            s = scored[p]
            out.append(Evaluation(name=f"{p}_median_rank", value=s["median_rank"]))
            for k in [f"top{k}" for k in TOP_K] + ["mrr"]:
                v, lo, hi = s[k]
                out += [Evaluation(name=f"{p}_{k}", value=v), Evaluation(name=f"{p}_{k}_ci_lo", value=lo),
                        Evaluation(name=f"{p}_{k}_ci_hi", value=hi)]
        d, lo, hi = scored["rr_diff"]
        out += [Evaluation(name="rr_diff", value=d), Evaluation(name="rr_diff_ci_lo", value=lo),
                Evaluation(name="rr_diff_ci_hi", value=hi),
                Evaluation(name="fetal_codes", value=float(scored["fetal_codes"]))]
        return out

    result = get_client().run_experiment(
        name="E2 -> E3 impact", run_name=f"{record['method']} @ {record['git']}",
        description=f"ranking with gold vs machine profile; saved output {path.name}", data=data, task=task,
        evaluators=[item_evaluator], run_evaluators=[run_evaluator], max_concurrency=8,
        metadata={"method": record["method"], "git": record["git"], "ranking_config": record["ranking_config"],
                  "hpo_release": record["hpo_release"], "e2_output": record["e2_output"]},
    )
    print(f"\n  {result.experiment_url}")
    get_client().flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="source", type=Path, required=True, help="saved E2 output (evals/e2/results/)")
    args = parser.parse_args()
    e2_record = json.loads(args.source.read_text(encoding="utf-8"))
    if e2_record["dataset_sha256"] != hashlib.sha256(E2_CASES_FILE.read_bytes()).hexdigest():
        raise SystemExit(f"{args.source.name} was run on another version of {E2_CASES_FILE.name}")
    method = RANKING_CONFIG["method"]
    record = {
        "time": datetime.now().isoformat(timespec="seconds"), "git": git_version(), "method": method,
        "ranking_config": (Path(__file__).parents[2] / "ranking" / "config.toml").read_text(),
        "hpo_release": load_index().hpo_release, "e2_output": args.source.name,
        "cases": evaluate(e2_record, method),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"impact-{record['time'].replace(':', '')}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"saved {path}")
    log_langfuse(record, report(record), path)


if __name__ == "__main__":
    main()
