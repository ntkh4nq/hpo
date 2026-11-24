"""Judge calibration shared by E4 and E5 (docs/evidence-impl-plan.md §Audit C21; T7c: kappa ≥ 0.6 before use).

Each row of a calibration.jsonl carries the judge's label and two blind labels from fresh agents with no context
(`agent`, `agent2`, different models); where they disagree a third fresh agent labels the row (`tiebreak`), and
`final` is the majority. The user has no medical background, so no row is labelled by the user (user, 2026-11-23).
"""

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from evals.common.stats import cohen_kappa

LABELS = ("agent", "agent2", "tiebreak", "final")


def disagreements(rows: list[dict]) -> list[dict]:
    return [e for e in rows if e["agent"] != e["agent2"]]


def finalize(rows: list[dict]) -> None:
    """final = the two agents' label when they agree, else the tie-breaker's."""
    for e in rows:
        if e["agent"] is None or e["agent2"] is None:
            raise SystemExit(f"{e['id']}: both agent labels are needed")
        if e["agent"] != e["agent2"] and e["tiebreak"] is None:
            raise SystemExit(f"{e['id']}: the agents disagree and there is no tiebreak label")
        e["final"] = e["agent"] if e["agent"] == e["agent2"] else e["tiebreak"]


def report(rows: list[dict]) -> float:
    """Print agreement and Cohen's kappa of the judge against `final`, and of the two agents; return the judge's kappa."""
    if any(e["final"] is None for e in rows):
        raise SystemExit("every row needs a final label (run the `final` command)")
    judge, final = [e["judge"] for e in rows], [e["final"] for e in rows]
    k = cohen_kappa(judge, final)
    print(f"{len(rows)} rows; judge vs final: agreement {sum(a == b for a, b in zip(judge, final)) / len(rows):.2f}, "
          f"Cohen's kappa {k:.2f}" + ("" if k >= 0.6 else "  < 0.6: stop and report (T7c)"))
    a, b = [e["agent"] for e in rows], [e["agent2"] for e in rows]
    print(f"agent vs agent2: agreement {sum(x == y for x, y in zip(a, b)) / len(rows):.2f}, kappa {cohen_kappa(a, b):.2f}"
          f"; {len(disagreements(rows))} rows went to the tie-breaker")
    return k


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in rows), encoding="utf-8")


def cli(path: Path, fields: tuple[str, ...], sample: Callable[[Path], None]) -> None:
    """The `sample|blind|final|kappa` commands of an eval's calibrate module; `fields` are what the blind agents see."""
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("sample", "blind", "final", "kappa"))
    parser.add_argument("--from", dest="source", type=Path)
    parser.add_argument("--disagreements", action="store_true", help="blind: only rows where the two agents disagree")
    args = parser.parse_args()
    if args.command == "sample":
        if not args.source:
            parser.error("sample needs --from")
        sample(args.source)
    elif args.command == "blind":
        rows = load(path)
        for e in disagreements(rows) if args.disagreements else rows:
            print(json.dumps({k: e[k] for k in fields}, ensure_ascii=False))
    elif args.command == "final":
        rows = load(path)
        finalize(rows)
        save(path, rows)
        print(f"{path}: final set on {len(rows)} rows")
    else:
        report(load(path))
