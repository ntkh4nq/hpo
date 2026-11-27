"""Run E1 (phrase -> HPO) on Langfuse, one experiment run per search profile.

    python -m evals.e1.search_run --split dev                         # every hpo_* profile in kb/search/profiles.toml
    python -m evals.e1.search_run --split test hpo_rrf_prior          # chosen profiles

--split keeps only the items of that side (metadata.split, docs/eval-strategy.md §4); test runs only at milestones.
Per item: gold_rank (0 = not in top 20), hit@1/5/10/20, reciprocal_rank, near_hit@10 (gold or a direct
parent/child of it in the top 10). Per run: recall@k and MRR over all items and over the hard ones
(query_already_indexed = false), 95% bootstrap CI for the hard metrics, latency p50/p95.
Afterwards: paired bootstrap of per-item hit@10 on hard items, each profile vs DEFAULT_PROFILE.
Selection rule: docs/retrieval-plan.md §6, then docs/eval-strategy.md §6.
"""

import argparse
import statistics
import subprocess
import time

from langfuse import Evaluation, get_client

from evals.common import langfuse_run as lr  # first: sends this process's traces to env 'eval'
from evals.e1.dataset import DATASET
from evals.common.stats import bootstrap_ci, paired_bootstrap_diff
from core.db import connect
from kb.search.hpo import search_hpo
from kb.search.engine import PROFILES, load_profile

K = 20
CUTOFFS = (1, 5, 10, 20)
DEFAULT_PROFILE = "hpo_rrf_prior"  # search_hpo's default (T24); other profiles are compared against it
CI_METRICS = {"hard_recall@10": "hit@10", "hard_near_hit@10": "near_hit@10", "hard_mrr": "reciprocal_rank"}


def make_task(profile: str):
    def task(*, item, **kwargs):
        t0 = time.perf_counter()
        found = search_hpo(item.input["query"], k=K, profile=profile)
        return {"hpo_ids": [c["hpo_id"] for c in found], "latency_s": round(time.perf_counter() - t0, 3)}
    return task


def make_item_evaluator(near: dict[str, set[str]]):
    def rank_evaluator(*, output, expected_output, **kwargs):
        gold, ids = set(expected_output["hpo_ids"]), output["hpo_ids"]
        rank = next((i for i, h in enumerate(ids, 1) if h in gold), 0)
        close = gold.union(*(near.get(g, set()) for g in gold))
        return [
            Evaluation(name="gold_rank", value=rank),
            Evaluation(name="reciprocal_rank", value=1 / rank if rank else 0.0),
            *[Evaluation(name=f"hit@{c}", value=float(0 < rank <= c)) for c in CUTOFFS],
            Evaluation(name="near_hit@10", value=float(bool(close & set(ids[:10])))),
        ]
    return rank_evaluator


def is_hard(item) -> bool:
    return not (item.metadata or {}).get("query_already_indexed")


def summary(*, item_results, **kwargs):
    rows = []  # (scores, is_hard, latency); a failed task counts as a miss
    for r in item_results:
        scores = {e.name: e.value for e in r.evaluations}
        hard = is_hard(r.item)
        rows.append((scores, hard, (r.output or {}).get("latency_s")))
    out = []
    for scope, subset in (("all", rows), ("hard", [r for r in rows if r[1]])):
        for c in CUTOFFS:
            out.append(Evaluation(name=f"{scope}_recall@{c}",
                                  value=sum(s.get(f"hit@{c}", 0) for s, _, _ in subset) / len(subset)))
        out.append(Evaluation(name=f"{scope}_near_hit@10",
                              value=sum(s.get("near_hit@10", 0) for s, _, _ in subset) / len(subset)))
        out.append(Evaluation(name=f"{scope}_mrr", value=sum(s.get("reciprocal_rank", 0) for s, _, _ in subset) / len(subset),
                              comment=f"{len(subset)} items"))
    hard_rows = [s for s, hard, _ in rows if hard]
    for name, item_metric in CI_METRICS.items():
        _, lo, hi = bootstrap_ci([s.get(item_metric, 0) for s in hard_rows])
        out += [Evaluation(name=f"{name}_ci_lo", value=lo), Evaluation(name=f"{name}_ci_hi", value=hi)]
    latencies = sorted(l for _, _, l in rows if l is not None)
    out.append(Evaluation(name="latency_p50_s", value=statistics.median(latencies)))
    out.append(Evaluation(name="latency_p95_s", value=latencies[int(0.95 * (len(latencies) - 1))]))
    out.append(Evaluation(name="failed_items", value=sum(1 for _, _, l in rows if l is None)))
    return out


def hard_hits(result) -> dict[str, float]:
    """item id -> hit@10, hard items only; a failed task counts as a miss."""
    return {r.item.id: next((e.value for e in r.evaluations if e.name == "hit@10"), 0.0)
            for r in result.item_results if is_hard(r.item)}


def main(split: str, profiles: list[str]) -> None:
    langfuse, near, version = get_client(), neighbours(), git_version()
    with connect() as conn:
        release = conn.execute("SELECT release FROM kb_manifest WHERE source = 'hpo' LIMIT 1").fetchone()[0]
    items = [i for i in langfuse.get_dataset(DATASET).items
             if i.status != "ARCHIVED" and (i.metadata or {}).get("split") == split]
    print(f"{DATASET} [{split}]: {len(items)} items, {sum(map(is_hard, items))} hard", flush=True)
    hits: dict[str, dict[str, float]] = {}
    for profile in profiles:
        result = langfuse.run_experiment(
            name=lr.experiment("E1-search"),
            run_name=lr.run_name("E1-search", split, profile, None, version),
            description=lr.description(f"search_hpo with profile {profile} on E1-{split}.", None),
            data=items,
            task=make_task(profile),
            evaluators=[make_item_evaluator(near)],
            run_evaluators=[summary],
            max_concurrency=4,
            metadata=lr.metadata("E1-search", split, None, version, profile=profile,
                                 profile_config=load_profile(profile), hpo_release=release, k=str(K)),
        )
        scores = {e.name: e.value for e in result.run_evaluations}
        print(f"{profile:24} " + "  ".join(
            f"{n}={scores[n]:.3f}" for n in ("hard_recall@1", "hard_recall@10", "hard_near_hit@10", "hard_mrr", "all_recall@10",
                                             "latency_p95_s", "failed_items")), flush=True)
        print(" " * 25 + "  ".join(f"{n} [{scores[n + '_ci_lo']:.3f}, {scores[n + '_ci_hi']:.3f}]" for n in CI_METRICS))
        print(f"  {result.experiment_url}", flush=True)
        hits[profile] = hard_hits(result)
    langfuse.flush()

    if DEFAULT_PROFILE in hits and len(hits) > 1:
        print(f"\nhard hit@10 vs {DEFAULT_PROFILE} (paired bootstrap, b − a, 95% CI):")
        base = hits[DEFAULT_PROFILE]
        ids = sorted(base)
        for profile, h in hits.items():
            if profile != DEFAULT_PROFILE:
                diff, lo, hi = paired_bootstrap_diff([base[i] for i in ids], [h.get(i, 0.0) for i in ids])
                print(f"  {profile:24} {diff:+.3f} [{lo:+.3f}, {hi:+.3f}]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", required=True, choices=("dev", "test"))
    parser.add_argument("profiles", nargs="*")
    args = parser.parse_args()
    main(args.split, args.profiles or [n for n, v in PROFILES.items() if isinstance(v, dict) and n.startswith("hpo_")])
