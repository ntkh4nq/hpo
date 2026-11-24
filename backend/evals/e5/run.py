"""E5: which configuration of find_passages finds passages proving "disease D has phenotype P"
(docs/evidence-plan.md §8, docs/ingestion-plan.md §11, criteria T83). Only Qwen (judge) and embeddings are called.

    python -m evals.e5.run --split dev                 # build missing chunks, search, judge, choose (staged), Langfuse
    python -m evals.e5.run --split test                # the configuration now in chunking.toml / profiles.toml only
    python -m evals.e5.run --from <file> [--langfuse]  # rescore a saved run without any model call
    python -m evals.e5.run --split dev --current       # only the configuration in use, no stages, no Langfuse:
                                                       # coverage before/after adding a source (docs/sources-plan.md §3)

Every distinct (pair, passage text) is judged once and its label reused by every configuration, so paired
differences are not blurred by judge noise (docs/evidence-impl-plan.md §Audit C10). Metrics are averaged per group
(diseases sharing a GeneReviews chapter) and compared with a paired bootstrap over groups.
"""

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from langfuse import Evaluation, get_client
from pydantic import BaseModel

from agent.state import CONFIG as AGENT_CONFIG
from core.db import connect
from core.llm import invoke_json
from evals.common.provenance import git_version
from evals.common.stats import bootstrap_ci, paired_bootstrap_diff
from evals.e5.dataset import CONFIG, DATASET, LANGFUSE_DATASET, load_rows, sha1
from kb.ingest import cli, worker
from kb.ingest.chunking import count_tokens
from kb.ingest.config import CHUNKING
from kb.search.engine import PROFILES
from kb.search.literature import find_passages, matched_ids

HERE = Path(__file__).parent
RESULTS = HERE / "results"
JUDGE_PROMPT = (HERE / "prompts" / "judge.md").read_text(encoding="utf-8")
JUDGE_VERSION = hashlib.sha256(JUDGE_PROMPT.encode()).hexdigest()[:12]
METRICS = ("hit@5", "precision@5", "tokens@5")


class Judgement(BaseModel):
    index: int
    supports: bool


class Judgements(BaseModel):
    judgements: list[Judgement]


def config_name(c: dict) -> str:
    return f"{c['strategy']}-{c['profile']}-{'+'.join(c['query_kinds'])}"


# ---------- chunks, search, judge

def ensure_chunks(rows: list[dict], strategies: list[str]) -> None:
    """Chunks of the other strategies for the documents of these diseases (only missing ones, cli.rechunk)."""
    with connect() as conn:
        ids = sorted({i for r in rows for i in matched_ids(conn, r["disease_id"])})
        docs = [d for (d,) in conn.execute("SELECT doc_id FROM document WHERE status = 'ACTIVE' AND disease_ids && %s"
                                           " ORDER BY doc_id", (ids,))]
    for strategy in strategies:
        if strategy == CONFIG["baseline_strategy"] or not docs:   # an empty list would mean every document
            continue
        run_id = cli.rechunk(strategy, docs, f"python -m evals.e5.run ({len(docs)} documents)")
        worker.work(once=True, run_id=run_id)


def search(rows: list[dict], c: dict) -> dict:
    items = {}
    for r in rows:
        t0 = time.perf_counter()
        found = find_passages(r["disease_id"], hpo_id=r["hpo_id"], k=CONFIG["k"], strategy=c["strategy"],
                              profile=c["profile"], query_kinds=list(c["query_kinds"]))
        items[r["id"]] = {"latency_s": round(time.perf_counter() - t0, 3),
                          "hits": [{"chunk_id": p.chunk_id, "doc_id": p.doc_id, "kind": p.kind,
                                    "display_text": p.display_text, "tokens": count_tokens(p.display_text)}
                                   for p in found]}
    return items


def _judge_batch(row: dict, batch: list[dict]) -> dict[str, bool]:
    human = (f"Disease: {row['disease_name']} ({row['disease_id']})\nPhenotype: {row['hpo_label']} ({row['hpo_id']})\n\n"
             + "\n\n".join(f"[{i + 1}] {h['display_text']}" for i, h in enumerate(batch)))
    out = invoke_json(JUDGE_PROMPT, human, Judgements, thinking=False, timeout=CONFIG["judge_timeout"],
                      model=CONFIG["judge_model"])
    if out is None:
        return {}
    by_index = {j.index: j.supports for j in out.judgements}
    return {sha1(h["display_text"]): by_index[i + 1] for i, h in enumerate(batch) if i + 1 in by_index}


def judge(rows: list[dict], runs: list[dict], table: dict) -> None:
    """Fill table[pair id][sha1(text)] = {"supports", "chunk_id", "display_text"} for every passage not judged yet."""
    by_id = {r["id"]: r for r in rows}
    todo: dict[str, dict[str, dict]] = {}
    for run in runs:
        for pid, item in run["items"].items():
            for h in item["hits"]:
                if sha1(h["display_text"]) not in table.get(pid, {}):
                    todo.setdefault(pid, {})[sha1(h["display_text"])] = h
    jobs = [(pid, list(hits.values())[i:i + CONFIG["judge_batch"]]) for pid, hits in todo.items()
            for i in range(0, len(hits), CONFIG["judge_batch"])]
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda job: (job, _judge_batch(by_id[job[0]], job[1])), jobs))
    for (pid, batch), labels in results:
        for h in batch:
            if sha1(h["display_text"]) in labels:
                table.setdefault(pid, {})[sha1(h["display_text"])] = {
                    "supports": labels[sha1(h["display_text"])], "chunk_id": h["chunk_id"], "display_text": h["display_text"]}
    print(f"  judged {sum(len(b) for _, b in jobs)} passages in {len(jobs)} requests", flush=True)


# ---------- scoring and choice

def score(run: dict, table: dict) -> dict:
    """Per pair metrics; a pair with an unjudged passage gets None (excluded from every comparison)."""
    out = {}
    for pid, item in run["items"].items():
        labels = [table.get(pid, {}).get(sha1(h["display_text"]), {}).get("supports") for h in item["hits"]]
        if any(v is None for v in labels):
            out[pid] = None
            continue
        out[pid] = {"hit@5": float(any(labels)), "precision@5": sum(labels) / len(labels) if labels else 0.0,
                    "tokens@5": float(sum(h["tokens"] for h in item["hits"]))}
    return out


def group_means(scored: list[dict], rows: list[dict], metric: str, keep: set[str]) -> list[float]:
    """Mean of the metric per group (sorted by group key), over the pairs in `keep`."""
    groups: dict[str, list[float]] = {}
    by_id = {r["id"]: r for r in rows}
    for pid in sorted(keep):
        groups.setdefault(by_id[pid]["group"], []).append(scored[pid][metric])
    return [sum(v) / len(v) for _, v in sorted(groups.items())]


def comparable(*scored: dict) -> set[str]:
    return set.intersection(*[{pid for pid, s in sc.items() if s is not None} for sc in scored])


def summary(scored: dict, rows: list[dict]) -> dict:
    keep = comparable(scored)
    return {m: bootstrap_ci(group_means(scored, rows, m, keep)) for m in METRICS} | {"pairs": len(keep)}


def diff(a: dict, b: dict, rows: list[dict], metric: str) -> tuple[float, float, float]:
    """b − a over groups, on the pairs both configurations could score."""
    keep = comparable(a, b)
    return paired_bootstrap_diff(group_means(a, rows, metric, keep), group_means(b, rows, metric, keep))


def fmt(t) -> str:
    return f"{t[0]:+.3f} [{t[1]:+.3f}, {t[2]:+.3f}]"


def staged_dev(rows: list[dict], record: dict, run_config) -> dict:
    """The criteria of T83, in order; each stage runs (or re-reads) its configurations, then decides."""
    table, log = record["judgements"], []

    def get(c: dict) -> dict:
        return score(run_config(c), table)

    base = {"profile": "doc_rrf_lexical", "query_kinds": ["label"]}
    stage1 = {s: get({"strategy": s, **base}) for s in CONFIG["strategies"]}
    hits = {s: summary(sc, rows)["hit@5"][0] for s, sc in stage1.items()}
    best, baseline = max(CONFIG["strategies"], key=lambda s: hits[s]), CONFIG["baseline_strategy"]
    d = diff(stage1[baseline], stage1[best], rows, "hit@5")
    tokens = {s: summary(sc, rows)["tokens@5"][0] for s, sc in stage1.items()}
    strategy = best if d[1] > 0 else min((best, baseline), key=lambda s: tokens[s])
    log.append(f"(1) strategy: best hit@5 {best} vs {baseline}: {fmt(d)} -> {strategy}"
               f"; s400_ctx − s400_noctx hit@5 {fmt(diff(stage1['s400_noctx'], stage1['s400_ctx'], rows, 'hit@5'))}"
               f"; tokens@5 × {CONFIG['explain_pairs']} = {tokens[strategy] * CONFIG['explain_pairs']:.0f}"
               f" vs max_context_tokens {AGENT_CONFIG['explain']['max_context_tokens']}")
    current = {"strategy": strategy, **base}
    lexical = stage1[strategy]

    rrf = get({**current, "profile": "doc_rrf"})
    d = diff(rrf, lexical, rows, "hit@5")
    profile = "doc_rrf" if d[2] < 0 else "doc_rrf_lexical"
    log.append(f"(2) doc_rrf_lexical − doc_rrf hit@5 {fmt(d)}, precision@5 {fmt(diff(rrf, lexical, rows, 'precision@5'))}"
               f", tokens@5 {fmt(diff(rrf, lexical, rows, 'tokens@5'))} -> {profile}")
    current["profile"] = profile
    chosen = lexical if profile == "doc_rrf_lexical" else rrf

    syn = get({**current, "query_kinds": ["label", "exact_syn"]})
    d = diff(chosen, syn, rows, "hit@5")
    if d[1] > 0:
        current["query_kinds"], chosen = ["label", "exact_syn"], syn
    log.append(f"(3) [label, exact_syn] − [label] hit@5 {fmt(d)} -> {current['query_kinds']}")

    if profile == "doc_rrf_lexical":
        for variant in ("doc_rrf_lexical_visual", "doc_rrf_lexical_colbert"):
            other = get({**current, "profile": variant})
            d = diff(chosen, other, rows, "hit@5")
            if d[1] > 0:
                current["profile"], chosen = variant, other
            log.append(f"(4) {variant} − {current['profile'] if current['profile'] != variant else 'previous'} "
                       f"hit@5 {fmt(d)} -> {current['profile']}")
    else:
        log.append("(4) skipped: the visual and rerank profiles are built on doc_rrf_lexical")
    return {"choice": current, "log": log}


# ---------- runs, files, Langfuse

def new_record(split: str) -> dict:
    return {"split": split, "created_at": datetime.now().isoformat(timespec="seconds"), "git": git_version(),
            "judge_model": CONFIG["judge_model"], "judge_prompt_version": JUDGE_VERSION,
            "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "runs": [], "judgements": {}}


def runner(rows: list[dict], record: dict, rescore: bool):
    saved = {config_name(r["config"]): r for r in record["runs"]}

    def run_config(c: dict) -> dict:
        name = config_name(c)
        if name not in saved:
            if rescore:
                raise SystemExit(f"{name} is not in the saved file")
            print(f"  {name}", flush=True)
            saved[name] = {"config": c, "items": search(rows, c)}
            record["runs"].append(saved[name])
            judge(rows, [saved[name]], record["judgements"])
        return saved[name]
    return run_config


def report(record: dict, rows: list[dict]) -> dict:
    table = record["judgements"]
    print(f"\nE5 {record['split']}: {len(rows)} pairs · judge {record['judge_model']} {record['judge_prompt_version']}"
          f" · git {record['git']}")
    for run in record["runs"]:
        s = summary(score(run, table), rows)
        lat = sorted(i["latency_s"] for i in run["items"].values())
        print(f"  {config_name(run['config']):48} " + "  ".join(f"{m} {s[m][0]:.3f} [{s[m][1]:.3f},{s[m][2]:.3f}]"
                                                              for m in METRICS)
              + f"  pairs {s['pairs']}  p50 {lat[len(lat) // 2]:.2f}s")
    return {config_name(r["config"]): summary(score(r, table), rows) for r in record["runs"]}


def log_langfuse(record: dict, rows: list[dict], path: Path) -> None:
    items = [i for i in get_client().get_dataset(LANGFUSE_DATASET).items
             if i.status != "ARCHIVED" and (i.metadata or {}).get("split") == record["split"]]
    date = record["created_at"].replace(":", "")   # with the time: two runs of one day must not share a name
    for run in record["runs"]:
        scored, summ = score(run, record["judgements"]), summary(score(run, record["judgements"]), rows)

        def task(*, item, **kwargs):
            return scored.get(item.id)

        def item_evaluator(*, output, **kwargs):
            return [Evaluation(name=m, value=output[m]) for m in METRICS] if output else []

        def run_evaluator(**kwargs):
            out = []
            for m in METRICS:
                v, lo, hi = summ[m]
                out += [Evaluation(name=m, value=v), Evaluation(name=f"{m}_ci_lo", value=lo),
                        Evaluation(name=f"{m}_ci_hi", value=hi)]
            return out

        c = run["config"]
        get_client().run_experiment(
            name="E5 literature search", run_name=f"e5-{record['split']}-{config_name(c)}-{date}",
            description=f"saved output {path.name}", data=items, task=task, evaluators=[item_evaluator],
            run_evaluators=[run_evaluator], max_concurrency=16,
            metadata={**c, "split": record["split"], "git": record["git"], "judge": record["judge_prompt_version"]})
    get_client().flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("dev", "test"))
    parser.add_argument("--from", dest="source", type=Path, help="rescore this saved run (no model call)")
    parser.add_argument("--langfuse", action="store_true", help="with --from: log to Langfuse again")
    parser.add_argument("--current", action="store_true", help="only the configuration in use; no stages, no Langfuse")
    args = parser.parse_args()
    if bool(args.split) == bool(args.source):
        parser.error("give exactly one of --split, --from")
    record = json.loads(args.source.read_text(encoding="utf-8")) if args.source else new_record(args.split)
    if record["dataset_sha256"] != hashlib.sha256(DATASET.read_bytes()).hexdigest():
        raise SystemExit("the saved run used another version of dataset.jsonl")
    rows = [r for r in load_rows() if r["split"] == record["split"]]
    run_config = runner(rows, record, rescore=bool(args.source))
    if args.current:
        record["current"] = True
    if record["split"] == "dev" and not record.get("current"):
        if not args.source:
            ensure_chunks(rows, CONFIG["strategies"])
        record["decision"] = staged_dev(rows, record, run_config)
    else:
        final = {"strategy": CHUNKING["default"], "profile": PROFILES["literature_profile"],
                 "query_kinds": PROFILES["literature_query_kinds"]}
        if not args.source:
            ensure_chunks(rows, [final["strategy"]])
        run_config(final)
    record["summary"] = report(record, rows)
    if "decision" in record:
        print("\n  " + "\n  ".join(record["decision"]["log"]) + f"\n  choice: {record['decision']['choice']}")
    path = args.source or RESULTS / f"{record['created_at'].replace(':', '')}.json"
    if not args.source:
        RESULTS.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"saved {path}")
    if (args.split and not args.current) or args.langfuse:
        log_langfuse(record, rows, path)


if __name__ == "__main__":
    main()
