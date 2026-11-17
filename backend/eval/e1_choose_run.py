"""E1 for the choose step (docs/mapping-plan.md §9.2): steps 3-5 on E1 phrases, gate off, one request per 10 items.

    python -m eval.e1_choose_run --split dev                                   # call the model on ALL items, save, report dev
    python -m eval.e1_choose_run --from results/e1_choose/<f>.json --split dev  # rescore a saved run, no model call
    python -m eval.e1_choose_run --from <f> --threshold 0.62 --split test       # rescore with the gate at 0.62
    python -m eval.e1_choose_run --split dev --model vlm --no-thinking         # another model / thinking mode
    python -m eval.e1_choose_run --from <b> --compare <a> --split dev          # paired bootstrap b − a per item

One model run covers dev and test; decisions look only at dev, test is reported last (eval-strategy.md §2).
Each group of 10 items becomes a fake case: text = one "- <query>" line per item; every item is
PRESENT/ASSESSED/FETUS with phrase = source_text = query. find_candidates -> gate(0.0) -> choose_terms ->
check_choices, no split step. --threshold t counts every item with best_dense < t as UNMAPPED: an
approximation, since a real gate would also send the model fewer items per request.

Per item: correct (MAPPED, code in gold), near_correct (MAPPED, code in gold or a direct parent/child of it;
silver labels, so a lower bound, T25), gold_in_candidates. AMBIGUOUS and UNMAPPED count as wrong.
With --split dev the gate table of §4.3 is printed too.
"""

import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from langfuse import Evaluation, get_client

from eval.e1_dataset import DATASET
from eval.e1_run import _git_version, neighbours
from eval.stats import bootstrap_ci, paired_bootstrap_diff
from pipeline import mapping
from pipeline.mapping import best_dense, check_choices, choose_terms, find_candidates, gate
from pipeline.schema import Item

RESULTS = Path(__file__).parent / "results" / "e1_choose"
GROUP_SIZE = 10          # items per request (mapping-plan.md §9.2)
COST_RATIO = 2           # a wrong code costs 2 lost right codes: the team's choice, not measured (§4.3, T42)
MIN_WRONG = 3            # the gate needs at least this many wrong codes below t* as evidence (§4.3)
CI_METRICS = ("accuracy", "near_accuracy")


def dataset_items() -> list:
    items = [i for i in get_client().get_dataset(DATASET).items if i.status != "ARCHIVED"]
    return sorted(items, key=lambda i: i.id)


def run_group(group_no: int, group: list, model: str | None, thinking: bool | None) -> dict[str, dict]:
    queries = [i.input["query"] for i in group]
    text = "\n".join(f"- {q}" for q in queries)
    items = [Item(phrase=q, source_text=q, assertion="PRESENT", assessment="ASSESSED", temporality=None,
                  subject="FETUS") for q in queries]
    candidates = find_candidates(items)
    gated = gate(candidates, 0.0)
    choices, failed = choose_terms(text, items, candidates, gated, model=model, thinking=thinking)
    analysis = check_choices(text, items, candidates, gated, choices, failed, False)
    out = {}
    for n, (ds_item, finding) in enumerate(zip(group, analysis.findings)):
        gold = ds_item.expected_output["hpo_ids"]
        ids = [c.hpo_id for c in finding.candidates]
        out[ds_item.id] = {
            "split": ds_item.metadata["split"],
            "query": ds_item.input["query"],
            "gold": gold,
            "best_dense": best_dense(finding.candidates),
            "mapping_status": finding.mapping_status,
            "hpo_id": finding.term.hpo_id if finding.term else None,
            "options": [t.hpo_id for t in finding.options],
            "candidate_ids": ids,
            "gold_in_candidates": bool(set(gold) & set(ids)),
            "reasons": [r.reason for r in analysis.rejections if r.index == n],
            "group": group_no,
            "group_choose_failed": analysis.choose_failed,
        }
    return out


def run_model(concurrency: int, model: str, thinking: bool) -> Path:
    items = dataset_items()
    groups = [items[i:i + GROUP_SIZE] for i in range(0, len(items), GROUP_SIZE)]
    print(f"{len(items)} items -> {len(groups)} groups (one {model} request each, plus retries)", flush=True)
    with ThreadPoolExecutor(concurrency) as pool:
        parts = list(pool.map(lambda n, g: run_group(n, g, model, thinking), range(len(groups)), groups))
    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "prompt_version": mapping.PROMPT_VERSION,
        "model": model,
        "mapping_config": {**mapping.CONFIG, "thinking": thinking},
        "git": _git_version(),
        "items": {k: v for part in parts for k, v in part.items()},
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{record['time'].replace(':', '')}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {path}", flush=True)
    return path


# ---------- scoring

def score(rec: dict, near: dict[str, set[str]], threshold: float) -> dict:
    """Per-item scores; best_dense < threshold counts as UNMAPPED (gate applied after the fact)."""
    gated = rec["best_dense"] < threshold
    status = "UNMAPPED" if gated else rec["mapping_status"]
    gold = set(rec["gold"])
    close = gold.union(*(near.get(g, set()) for g in gold))
    mapped = status == "MAPPED"
    return {
        "status": status, "gated": gated,
        "correct": float(mapped and rec["hpo_id"] in gold),
        "near_correct": float(mapped and rec["hpo_id"] in close),
        "gold_in_candidates": float(rec["gold_in_candidates"]),
    }


def side_metrics(recs: list[dict], scores: list[dict]) -> dict:
    n = len(scores)
    given = [s["correct"] for s in scores if s["gold_in_candidates"]]
    return {
        "items": n,
        "accuracy": bootstrap_ci([s["correct"] for s in scores]),
        "near_accuracy": bootstrap_ci([s["near_correct"] for s in scores]),
        "accuracy_given_candidates": sum(given) / len(given) if given else 0.0,
        "gold_in_candidates": sum(s["gold_in_candidates"] for s in scores) / n,
        "ambiguous_rate": sum(s["status"] == "AMBIGUOUS" for s in scores) / n,
        "unmapped_rate": sum(s["status"] == "UNMAPPED" for s in scores) / n,
        "gated_rate": sum(s["gated"] for s in scores) / n,
        "rejections": sum(len(r["reasons"]) for r in recs),
        "choose_failed_groups": len({r["group"] for r in recs if r["group_choose_failed"]}),
    }


def print_metrics(side: str, m: dict) -> None:
    print(f"\nE1-{side}: {m['items']} items")
    for k in CI_METRICS:
        mean, lo, hi = m[k]
        print(f"  {k:26} {mean:.3f} [{lo:.3f}, {hi:.3f}]")
    for k in ("accuracy_given_candidates", "gold_in_candidates", "ambiguous_rate", "unmapped_rate", "gated_rate"):
        print(f"  {k:26} {m[k]:.3f}")
    print(f"  {'rejections':26} {m['rejections']}\n  {'choose_failed_groups':26} {m['choose_failed_groups']}")


def gate_table(recs: list[dict], scores: list[dict]) -> None:
    """mapping-plan.md §4.3 on dev items of a gate-off run."""
    rows = []
    for t in sorted({r["best_dense"] for r in recs}):
        below = [(r, s) for r, s in zip(recs, scores) if r["best_dense"] < t]
        w = sum(1 for r, s in below if r["mapping_status"] == "MAPPED" and not s["near_correct"])
        c = sum(1 for _, s in below if s["near_correct"])
        rows.append((t, w, c, w - COST_RATIO * c))
    print(f"\ngate rule (§4.3), {len(recs)} dev items: gain(t) = W(t) − {COST_RATIO}·C(t)")
    print(f"  {'t':>8} {'W':>4} {'C':>4} {'gain':>5}")
    for t, w, c, g in rows[:20]:
        print(f"  {t:8.4f} {w:4} {c:4} {g:5}")
    t_star, w, c, g = max(rows, key=lambda r: (r[3], -r[0]))
    on = g > 0 and w >= MIN_WRONG
    print(f"  t* = {t_star:.4f}: W = {w}, C = {c}, gain = {g} -> "
          + (f"gate ON, unmapped_dense_threshold = {math.floor(t_star * 100) / 100:.2f}" if on
             else "gate OFF, unmapped_dense_threshold = 0.0"))


# ---------- Langfuse

def report(side: str, record: dict, path: Path, threshold: float, near: dict[str, set[str]]) -> None:
    recs = {k: v for k, v in record["items"].items() if v["split"] == side}
    scores = {k: score(v, near, threshold) for k, v in recs.items()}
    metrics = side_metrics(list(recs.values()), list(scores.values()))
    print_metrics(side, metrics)
    if side == "dev" and threshold == 0.0 and record["mapping_config"]["unmapped_dense_threshold"] == 0.0:
        gate_table(list(recs.values()), list(scores.values()))

    data = [i for i in dataset_items() if i.id in recs]

    def task(*, item, **kwargs):
        return {**recs[item.id], **scores[item.id]}

    def item_evaluator(*, output, **kwargs):
        return [Evaluation(name=k, value=output[k]) for k in ("correct", "near_correct", "gold_in_candidates")]

    def run_evaluator(**kwargs):
        out = []
        for k in CI_METRICS:
            mean, lo, hi = metrics[k]
            out += [Evaluation(name=k, value=mean), Evaluation(name=f"{k}_ci_lo", value=lo),
                    Evaluation(name=f"{k}_ci_hi", value=hi)]
        out += [Evaluation(name=k, value=float(metrics[k])) for k in
                ("accuracy_given_candidates", "gold_in_candidates", "ambiguous_rate", "unmapped_rate", "gated_rate",
                 "rejections", "choose_failed_groups")]
        return out

    result = get_client().run_experiment(
        name="E1 choose term",
        run_name=f"choose [{side}] t={threshold} prompt {record['prompt_version']} @ {record['git']}",
        description=f"steps 3-5 on E1-{side}, saved output {path.name}, gate applied at {threshold} when rescoring",
        data=data, task=task, evaluators=[item_evaluator], run_evaluators=[run_evaluator], max_concurrency=8,
        metadata={"split": side, "threshold": str(threshold), "prompt_version": record["prompt_version"],
                  "model": record["model"], "mapping_config": json.dumps(record["mapping_config"]),
                  "git": record["git"], "output_file": path.name},
    )
    print(f"  {result.experiment_url}")
    get_client().flush()


def compare(side: str, a: dict, b: dict, threshold: float, near: dict[str, set[str]]) -> None:
    ids = sorted(k for k, v in b["items"].items() if v["split"] == side)
    print(f"\nE1-{side}, {len(ids)} items: b ({b['model']}, prompt {b['prompt_version']}) − "
          f"a ({a['model']}, prompt {a['prompt_version']}), paired bootstrap 95% CI")
    for metric in ("correct", "near_correct"):
        sa = [score(a["items"][k], near, threshold)[metric] for k in ids]
        sb = [score(b["items"][k], near, threshold)[metric] for k in ids]
        diff, lo, hi = paired_bootstrap_diff(sa, sb)
        print(f"  {metric:13} a {sum(sa) / len(ids):.3f}  b {sum(sb) / len(ids):.3f}  b − a {diff:+.3f} [{lo:+.3f}, {hi:+.3f}]")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", required=True, choices=("dev", "test"))
    parser.add_argument("--from", dest="source", type=Path, help="rescore this saved run instead of calling the model")
    parser.add_argument("--threshold", type=float, default=0.0)
    parser.add_argument("--concurrency", type=int, default=4, help="parallel model requests")
    parser.add_argument("--model", default=mapping.CONFIG["model"])
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=mapping.CONFIG["thinking"])
    parser.add_argument("--compare", type=Path, help="another saved run (a): print paired bootstrap of this run (b) − a")
    args = parser.parse_args()
    path = args.source or run_model(args.concurrency, args.model, args.thinking)
    record = json.loads(path.read_text(encoding="utf-8"))
    near = neighbours()
    report(args.split, record, path, args.threshold, near)
    if args.compare:
        compare(args.split, json.loads(args.compare.read_text(encoding="utf-8")), record, args.threshold, near)


if __name__ == "__main__":
    main()
