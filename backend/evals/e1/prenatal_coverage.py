"""How well does each source of the prenatal flag cover HPO codes used in real prenatal reports?

Reference set: fetal-phenotype rows (status CÓ / NGHI NGỜ, with an HPO code) of the 234-case sheet from
the advisor. Its labels are silver (ChatGPT-assigned, not expert-reviewed): read results as indicative.
Runs locally only (DB + file); nothing is sent anywhere. Needs `make seed` first.

    python -m evals.e1.prenatal_coverage
"""

import csv
from collections import Counter
from pathlib import Path

import openpyxl

from core.db import connect
from kb.seed.hpo import PRENATAL_RULES, RAW

CASES = RAW / "gvhd" / "Data_train_HPO_prenatal_cases_1_234_completed.xlsx"


def reference_mentions(path: Path) -> list[str]:
    rows = openpyxl.load_workbook(path, read_only=True)["HPO_long"].iter_rows(values_only=True)
    header = next(rows)
    col = {name: header.index(name) for name in ("HPO_ID", "Loại bằng chứng")}
    return [str(r[col["HPO_ID"]]).strip() for r in rows
            if r[col["Loại bằng chứng"]] == "FETAL_PHENOTYPE" and r[col["HPO_ID"]]]


def main() -> None:
    with PRENATAL_RULES.open() as f:
        source_of_tag = {f"{r['kind']}:{r['hpo_id']}": r["source"] for r in csv.DictReader(f)}
    with connect() as conn:
        current = {h: (label, basis) for h, label, basis in conn.execute(
            "SELECT hpo_id, label_en, prenatal_basis FROM hpo_term WHERE NOT is_obsolete")}
        alt = dict(conn.execute("SELECT alt_id, hpo_id FROM hpo_alt_id"))

    sources_of = {h: {source_of_tag[t] for t in (basis or "").split(";") if t} for h, (_, basis) in current.items()}
    members = lambda wanted: {h for h, s in sources_of.items() if s & wanted}  # noqa: E731

    raw = reference_mentions(CASES)
    resolved = [alt.get(code, code) for code in raw]
    invalid = Counter(code for code, r in zip(raw, resolved) if r not in current)
    mentions = [r for r in resolved if r in current]
    codes = Counter(mentions)
    print(f"reference: {len(raw)} fetal-phenotype mentions with a code; {len(mentions)} valid in this release "
          f"({sum(c != r for c, r in zip(raw, resolved) if r in current)} via alt id), {len(codes)} distinct codes; "
          f"invalid: {dict(invalid)}\n")

    sets = {
        "A  hpo_prenatal_branch (HP:0001197)": {"hpo_prenatal_branch"},
        "B  hpoa_onset (antenatal onset)": {"hpoa_onset"},
        "A∪B  documented sources": {"hpo_prenatal_branch", "hpoa_onset"},
        "C  g14_assumption (morphology branches)": {"g14_assumption"},
        "A∪B∪C  current flag": {"hpo_prenatal_branch", "hpoa_onset", "g14_assumption"},
    }
    print(f"{'set':42} {'terms':>6} {'% of HPO':>8} {'mentions':>9} {'codes':>7}")
    for name, wanted in sets.items():
        s = members(wanted)
        hit_mentions = sum(n for h, n in codes.items() if h in s)
        hit_codes = sum(1 for h in codes if h in s)
        print(f"{name:42} {len(s):6} {len(s) / len(current):8.1%} "
              f"{hit_mentions / len(mentions):9.1%} {hit_codes / len(codes):7.1%}")

    documented = members(sets["A∪B  documented sources"])
    flagged = members(sets["A∪B∪C  current flag"])
    print("\nadded only by C (codes, mentions):")
    for h, n in codes.most_common():
        if h in flagged and h not in documented:
            print(f"  {h} {current[h][0]} ×{n}")
    print("\nnot covered by any source:")
    for h, n in codes.most_common():
        if h not in flagged:
            print(f"  {h} {current[h][0]} ×{n}")


if __name__ == "__main__":
    main()
