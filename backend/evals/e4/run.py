"""E4: measure the explanations of the suggestions (docs/evidence-plan.md §9, docs/evidence-impl-plan.md task 6).

    python -m evals.e4.run --split dev                   # explain every case (1 DeepSeek request each, +1 per repair),
                                                         # judge with Qwen, report, log to Langfuse
    python -m evals.e4.run --from <file> [--langfuse]    # re-judge only when a judge prompt changed (Qwen), report;
                                                         # never calls DeepSeek

Per case: invalid_claim_rate (claims the check caught in the first output), failed_pair_rate, faithfulness (claims
the judge finds supported by their cited passages), missed_evidence (pairs with a supporting passage, by the E5
judge, that did not end `cited`), time, tokens. Rates are pooled over cases with bootstrap_ratio_ci.
"""

import argparse
import hashlib
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
from langfuse import Evaluation, get_client
from pydantic import BaseModel

from evals.common import langfuse_run as lr  # first: sends this process's traces to env 'eval'
from agent.explanations import EXPLAIN, PROMPT_VERSION, ModelResult, assemble, now, pair_key, prepare, run_model
from agent.state import finding_rows
from agent.tools import case_ranking
from core.llm import invoke_json
from evals.common.provenance import git_version
from evals.common.stats import bootstrap_ratio_ci
from evals.e4.dataset import DATASET, LANGFUSE_DATASET, load_rows
from evals.e5 import run as e5
from evals.e5.dataset import sha1
from phenotype.schema import Analysis
from ranking.index import load_index

HERE = Path(__file__).parent
RESULTS = HERE / "results"
FAITH_PROMPT = (HERE / "prompts" / "faithfulness.md").read_text(encoding="utf-8")
FAITH_VERSION = hashlib.sha256(FAITH_PROMPT.encode()).hexdigest()[:12]
FAITH_BATCH = 8
RATES = ("invalid_claim_rate", "failed_pair_rate", "faithfulness", "missed_evidence")


class Faith(BaseModel):
    index: int
    supported: bool


class FaithOut(BaseModel):
    judgements: list[Faith]


# ---------- explain

def explain(row: dict) -> dict:
    a = Analysis.model_validate(row["analysis"])
    values = {"description": a.text, "findings": finding_rows(a), "remaps": {}, "edits": {}, "notes": {}}
    values["ranking"] = case_ranking(values).model_dump(mode="json")
    t0 = time.perf_counter()
    prepared = prepare(values)
    result = run_model(prepared) if prepared.context is not None else ModelResult(created_at=now())
    seconds = time.perf_counter() - t0
    labels = load_index().labels
    names = {s.disease_id: s.disease_name or s.disease_id for s in prepared.ranking.suggestions}
    return {"id": row["id"], "seconds": round(seconds, 2), "trace": result.trace,
            "explanations": assemble(prepared, result).model_dump(mode="json"),
            "sent": {pair_key(p): {"disease_name": names[p[0]], "hpo_label": labels.get(p[1], (p[1], None))[0],
                                   "passages": [{"chunk_id": x.chunk_id, "display_text": x.display_text} for x in ps]}
                     for p, ps in prepared.sent.items()}}


# ---------- judges

def claims_of(case: dict) -> list[dict]:
    """Distinct claims of a case (shared pairs carry identical claims), with their cited passages."""
    e, seen, out = case["explanations"], set(), []
    for item in e["items"]:
        for p in item["pairs"]:
            pk = f"{item['disease_id']}|{p['disease_hpo_id']}"
            for c in p["claims"]:
                if (pk, c["text"]) in seen:
                    continue
                seen.add((pk, c["text"]))
                out.append({"key": sha1(pk + c["text"]), "pair": pk, "text": c["text"],
                            "passages": [e["passages"][r]["display_text"] for r in c["refs"]]})
    return out


def _faith_batch(case: dict, batch: list[dict]) -> dict[str, bool]:
    parts = []
    for i, c in enumerate(batch):
        info = case["sent"][c["pair"]]
        passages = "\n".join(f"  - {t}" for t in c["passages"])
        parts.append(f"[{i + 1}] Disease: {info['disease_name']}\nPhenotype: {info['hpo_label']}\n"
                     f"Sentence: {c['text']}\nCited passages:\n{passages}")
    out = invoke_json(FAITH_PROMPT, "\n\n".join(parts), FaithOut, thinking=False, timeout=e5.CONFIG["judge_timeout"],
                      model=e5.CONFIG["judge_model"], name="judge_e4_faithfulness")
    if out is None:
        return {}
    by_index = {j.index: j.supported for j in out.judgements}
    return {c["key"]: by_index[i + 1] for i, c in enumerate(batch) if i + 1 in by_index}


def judge(record: dict) -> None:
    """Fill record["faithfulness"][case][claim key] and record["evidence"][case|pair][sha1(text)] where missing."""
    if record.get("judge_versions") != {"faithfulness": FAITH_VERSION, "evidence": e5.JUDGE_VERSION}:
        record["faithfulness"], record["evidence"] = {}, {}
        record["judge_versions"] = {"faithfulness": FAITH_VERSION, "evidence": e5.JUDGE_VERSION}
    faith = record["faithfulness"]
    jobs = []
    for case in record["cases"]:
        todo = [c for c in claims_of(case) if c["key"] not in faith.get(case["id"], {})]
        jobs += [(case, todo[i:i + FAITH_BATCH]) for i in range(0, len(todo), FAITH_BATCH)]
    with ThreadPoolExecutor(4) as pool:
        for (case, _), labels in zip(jobs, pool.map(lambda j: _faith_batch(*j), jobs)):
            faith.setdefault(case["id"], {}).update(labels)
    rows, items = [], {}
    for case in record["cases"]:
        for pk, info in case["sent"].items():
            pid = f"{case['id']}|{pk}"
            d, h = pk.split("|", 1)
            rows.append({"id": pid, "disease_id": d, "disease_name": info["disease_name"], "hpo_id": h,
                         "hpo_label": info["hpo_label"]})
            items[pid] = {"hits": info["passages"]}
    e5.judge(rows, [{"items": items}], record["evidence"])
    print(f"  faithfulness: {sum(len(b) for _, b in jobs)} claims in {len(jobs)} requests", flush=True)


# ---------- scoring

def case_counts(case: dict, record: dict) -> dict:
    """Numerator/denominator of each rate for one case (None numerator when a judgement is missing)."""
    e, trace = case["explanations"], case["trace"]
    pairs = {f"{i['disease_id']}|{p['disease_hpo_id']}": p["status"] for i in e["items"] for p in i["pairs"]}
    sent = [pk for pk in pairs if pk in case["sent"]]
    claims = claims_of(case)
    faith = record["faithfulness"].get(case["id"], {})
    evidence = {pk: [record["evidence"].get(f"{case['id']}|{pk}", {}).get(sha1(x["display_text"]), {}).get("supports")
                     for x in case["sent"][pk]["passages"]] for pk in sent}   # e5.judge stores {"supports", ...}
    supported_pairs = [pk for pk in sent if any(v for v in evidence[pk])]
    unjudged = any(c["key"] not in faith for c in claims) or any(v is None for vs in evidence.values() for v in vs)
    return {
        "invalid_claim_rate": (trace.get("first_invalid", 0), trace.get("first_claims", 0)),
        "failed_pair_rate": (sum(pairs[pk] == "failed" for pk in sent), len(sent)),
        "faithfulness": (sum(faith.get(c["key"], False) for c in claims), len(claims)),
        "missed_evidence": (sum(pairs[pk] != "cited" for pk in supported_pairs), len(supported_pairs)),
        "unjudged": unjudged,
    }


def report(record: dict) -> dict:
    counts = [case_counts(c, record) for c in record["cases"]]
    summary = {}
    for rate in RATES:
        num, den = [c[rate][0] for c in counts], [c[rate][1] for c in counts]
        summary[rate] = bootstrap_ratio_ci(num, den) if sum(den) else (None, None, None)
    usage = [c["explanations"]["usage"] for c in record["cases"]]
    seconds = [c["seconds"] for c in record["cases"]]
    summary |= {"seconds_p50": float(np.median(seconds)), "seconds_max": float(max(seconds)),
                "input_tokens_mean": float(np.mean([u["input_tokens"] for u in usage])),
                "output_tokens_mean": float(np.mean([u["output_tokens"] for u in usage])),
                "requests": int(sum(u["requests"] for u in usage)),
                "cases_unjudged": sum(c["unjudged"] for c in counts)}
    statuses = dict(Counter(p["status"] for c in record["cases"]
                            for i in c["explanations"]["items"] for p in i["pairs"]))
    summary["statuses"] = statuses
    print(f"\nE4 {record['split']}: {len(record['cases'])} cases · {record['model']} prompt {record['prompt_version']}"
          f" · judges {record['judge_versions']} · git {record['git']}")
    for rate in RATES:
        v, lo, hi = summary[rate]
        den = sum(c[rate][1] for c in counts)
        print(f"  {rate:20} " + (f"{v:.3f} [{lo:.3f}, {hi:.3f}]" if v is not None else "—") + f"  (n = {den})")
    print(f"  pair statuses {statuses}")
    print(f"  time p50 {summary['seconds_p50']:.1f} s, max {summary['seconds_max']:.1f} s; tokens in/out (mean) "
          f"{summary['input_tokens_mean']:.0f} / {summary['output_tokens_mean']:.0f}; DeepSeek requests {summary['requests']}"
          f"; cases with an unjudged item {summary['cases_unjudged']}")
    return summary


def log_langfuse(record: dict, summary: dict, path: Path) -> None:
    by_id = {c["id"]: case_counts(c, record) for c in record["cases"]}
    items = [i for i in get_client().get_dataset(LANGFUSE_DATASET).items
             if i.status != "ARCHIVED" and (i.metadata or {}).get("split") == record["split"]]

    def task(*, item, **kwargs):
        return by_id[item.input["id"]]

    def item_evaluator(*, output, **kwargs):
        return [Evaluation(name=r, value=output[r][0] / output[r][1]) for r in RATES if output[r][1]]

    def run_evaluator(**kwargs):
        out = []
        for r in RATES:
            if summary[r][0] is not None:
                v, lo, hi = summary[r]
                out += [Evaluation(name=r, value=v), Evaluation(name=f"{r}_ci_lo", value=lo),
                        Evaluation(name=f"{r}_ci_hi", value=hi)]
        return out + [Evaluation(name="seconds_p50", value=summary["seconds_p50"]),
                      Evaluation(name="input_tokens_mean", value=summary["input_tokens_mean"])]

    get_client().run_experiment(
        name=lr.experiment("E4"),
        run_name=lr.run_name("E4", record["split"], f"prompt {record['prompt_version']}", path, record["git"]),
        description=lr.description("Explanations of the top 10 for each case, judged for faithfulness.", path),
        data=items, task=task, evaluators=[item_evaluator],
        run_evaluators=[run_evaluator], max_concurrency=16,
        metadata=lr.metadata("E4", record["split"], path, record["git"], prompt_version=record["prompt_version"],
                             judges=record["judge_versions"]))
    get_client().flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("dev", "test"))
    parser.add_argument("--from", dest="source", type=Path, help="re-judge and report a saved run (no DeepSeek)")
    parser.add_argument("--langfuse", action="store_true", help="with --from: log to Langfuse again")
    args = parser.parse_args()
    if bool(args.split) == bool(args.source):
        parser.error("give exactly one of --split, --from")
    if args.source:
        path, record = args.source, json.loads(args.source.read_text(encoding="utf-8"))
        if record["dataset_sha256"] != hashlib.sha256(DATASET.read_bytes()).hexdigest():
            raise SystemExit("the saved run used another version of dataset.jsonl")
    else:
        rows = [r for r in load_rows() if r["split"] == args.split]
        record = {"split": args.split, "created_at": datetime.now().isoformat(timespec="seconds"), "git": git_version(),
                  "model": EXPLAIN["model"], "prompt_version": PROMPT_VERSION,
                  "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "cases": []}
        for n, row in enumerate(rows, 1):
            record["cases"].append(explain(row))
            c = record["cases"][-1]
            print(f"  {n}/{len(rows)} {row['id']}: {c['seconds']} s, {c['explanations']['usage']}", flush=True)
        RESULTS.mkdir(parents=True, exist_ok=True)
        path = RESULTS / f"{record['created_at'].replace(':', '')}.json"
        path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")   # outputs first, judges next
    judge(record)
    record["summary"] = report(record)
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"saved {path}")
    if args.split or args.langfuse:
        log_langfuse(record, record["summary"], path)


if __name__ == "__main__":
    main()
