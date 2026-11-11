"""Run E1 (phrase -> HPO) on Langfuse, one experiment run per search profile.

    python -m eval.e1_run                         # every hpo_* profile in search_profiles.toml
    python -m eval.e1_run hpo_rrf hpo_rrf_prior   # chosen profiles

Per item: gold_rank (0 = not in top 20), hit@1/5/10/20, reciprocal_rank, near_hit@10 (gold or a direct
parent/child of it in the top 10). Per run: recall@k and MRR over all items and over the hard ones
(query_already_indexed = false), latency p50/p95. Selection rule: docs/retrieval-plan.md §6.
"""

import json
import statistics
import subprocess
import sys
import time

from langfuse import Evaluation, get_client

from eval.e1_dataset import DATASET
from kb.db import connect
from kb.query.hpo import search_hpo
from kb.query.retrieval import PROFILES, load_profile

K = 20
CUTOFFS = (1, 5, 10, 20)


def neighbours() -> dict[str, set[str]]:
    """Direct parents and children of every term (distance 1 either way)."""
    near: dict[str, set[str]] = {}
    with connect() as conn:
        for child, parent in conn.execute("SELECT child_id, parent_id FROM hpo_edge"):
            near.setdefault(child, set()).add(parent)
            near.setdefault(parent, set()).add(child)
    return near


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


def summary(*, item_results, **kwargs):
    rows = []  # (scores, is_hard, latency); a failed task counts as a miss
    for r in item_results:
        scores = {e.name: e.value for e in r.evaluations}
        hard = not (r.item.metadata or {}).get("query_already_indexed")
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
    latencies = sorted(l for _, _, l in rows if l is not None)
    out.append(Evaluation(name="latency_p50_s", value=statistics.median(latencies)))
    out.append(Evaluation(name="latency_p95_s", value=latencies[int(0.95 * (len(latencies) - 1))]))
    out.append(Evaluation(name="failed_items", value=sum(1 for _, _, l in rows if l is None)))
    return out


def _git_version() -> str:
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], capture_output=True, text=True).stdout.strip()
    return commit + ("+dirty" if dirty else "")


def main(profiles: list[str]) -> None:
    langfuse, near, version = get_client(), neighbours(), _git_version()
    with connect() as conn:
        release = conn.execute("SELECT release FROM kb_manifest WHERE source = 'hpo' LIMIT 1").fetchone()[0]
    dataset = langfuse.get_dataset(DATASET)
    for profile in profiles:
        result = dataset.run_experiment(
            name="E1 phrase -> HPO",
            run_name=f"{profile} @ {version}",
            description=f"search_hpo with profile {profile}",
            task=make_task(profile),
            evaluators=[make_item_evaluator(near)],
            run_evaluators=[summary],
            max_concurrency=4,
            metadata={"profile": profile, "profile_config": json.dumps(load_profile(profile)),
                      "git": version, "hpo_release": release, "k": K},
        )
        scores = {e.name: e.value for e in result.run_evaluations}
        print(f"{profile:24} " + "  ".join(
            f"{n}={scores[n]:.3f}" for n in ("hard_recall@1", "hard_recall@10", "hard_near_hit@10", "hard_mrr", "all_recall@10",
                                             "latency_p95_s", "failed_items")), flush=True)
        print(f"  {result.experiment_url}", flush=True)
    langfuse.flush()


if __name__ == "__main__":
    main(sys.argv[1:] or [n for n, v in PROFILES.items() if isinstance(v, dict) and n.startswith("hpo_")])
