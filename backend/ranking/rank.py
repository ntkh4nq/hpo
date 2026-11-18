"""Phase 4: phenotype profile -> ranked diseases and genes with their evidence (docs/ranking-plan.md §2, §7).

    python -m ranking.rank HP:0001629 HP:0001561?     # suffix ? = SUSPECTED

Deterministic code over KB layers 1-2, no LLM (principle #5).
"""

import sys

from langfuse import observe

from phenotype.schema import Finding
from ranking.index import CONFIG, Index, SourceRow, load_index
from ranking.schema import (GeneSuggestion, Match, QueryTerm, Ranking, Skipped, Suggestion, Unmentioned)
from ranking.score import excluded_mask, order_all

ROOT = "HP:0000118"  # Phenotypic abnormality


def weight_of(assertion: str, config: dict = CONFIG) -> float:
    return 1.0 if assertion == "PRESENT" else config["suspected_weight"]


def common_ancestor(index: Index, codes: list[str]) -> tuple[str, float] | None:
    """The shared ancestor (itself included) with the largest IC, ties to the smaller hpo_id; None if none has an IC."""
    shared = frozenset.intersection(*(index.ancestors.get(c, frozenset()) for c in codes))
    best = min((a for a in shared if a in index.ic), key=lambda a: (-index.ic[a], a), default=None)
    return None if best is None else (best, index.ic[best])


def query_terms(findings: list[Finding], config: dict = CONFIG, index: Index | None = None
                ) -> tuple[list[QueryTerm], list[Skipped]]:
    """Fetal PRESENT/SUSPECTED findings that are MAPPED, or AMBIGUOUS with a specific enough common ancestor (§2)."""
    index = index or load_index()
    terms: dict[str, QueryTerm] = {}
    skipped: list[Skipped] = []
    for f in findings:
        code = f.term.hpo_id if f.term else None
        if f.subject != "FETUS":
            skipped.append(Skipped(phrase=f.phrase, hpo_id=code, reason="không phải của thai"))
        elif f.assertion not in ("PRESENT", "SUSPECTED"):
            skipped.append(Skipped(phrase=f.phrase, hpo_id=code,
                                   reason=f"trạng thái {f.assertion} không dùng để xếp hạng"))
        elif f.mapping_status == "UNMAPPED":
            skipped.append(Skipped(phrase=f.phrase, hpo_id=None, reason="chưa ánh xạ được"))
        else:
            options = [o.hpo_id for o in f.options] if f.mapping_status == "AMBIGUOUS" else []
            if options:
                found = common_ancestor(index, options)
                if found is None or found[1] <= 0:
                    skipped.append(Skipped(phrase=f.phrase, hpo_id=None,
                                           reason="các mã phân vân không có tổ tiên chung đủ cụ thể"))
                    continue
                code = found[0]
            term = QueryTerm(hpo_id=code, assertion=f.assertion, weight=weight_of(f.assertion, config),
                             from_options=options)
            if code not in terms or term.weight > terms[code].weight:
                terms[code] = term
    return list(terms.values()), skipped


# ---------- explanation (§7)

def _best(candidates: dict[str, float | None]) -> str:
    """Largest frequency first (None last), then the smaller hpo_id."""
    return min(candidates, key=lambda t: (candidates[t] is None, -(candidates[t] or 0.0), t))


def _sources(rows: list[SourceRow]) -> dict:
    return {"frequency_raw": [r.freq_raw for r in rows], "evidence": [r.evidence for r in rows],
            "references": [r.references for r in rows]}


def explain_match(index: Index, disease_id: str, term: QueryTerm, excluded: bool) -> Match:
    q, direct = term.hpo_id, index.direct[disease_id]
    anc = index.ancestors[q]
    base = {"query_hpo_id": q, "assertion": term.assertion, "weight": term.weight}
    if excluded:
        cands = {t: f for t, f in index.excluded_terms[disease_id].items() if t in anc}
        t = _best(cands)
        return Match(**base, kind="excluded", disease_hpo_id=t, common_ancestor_id=None,
                     **_sources(index.excluded_rows[(disease_id, t)]))
    kinds = (
        ("exact", {t: f for t, f in direct.items() if t == q}),
        ("disease_more_specific", {t: f for t, f in direct.items() if t != q and q in index.ancestors[t]}),
        ("disease_more_general", {t: f for t, f in direct.items() if t != q and t in anc}),
    )
    for kind, cands in kinds:
        if cands:
            t = _best(cands)
            return Match(**base, kind=kind, disease_hpo_id=t, common_ancestor_id=None,
                         **_sources(index.rows[(disease_id, t)]))
    # related: the shared ancestor with the largest IC over all positive annotations (= the IC score of q)
    shared = {a for t in direct for a in index.ancestors[t] & anc if a in index.ic}
    a = min(shared, key=lambda x: (-index.ic[x], x), default=None)
    if a is None or index.ic[a] <= 0:
        return Match(**base, kind="none", disease_hpo_id=None, common_ancestor_id=None)
    t = _best({t: f for t, f in direct.items() if a in index.ancestors[t]})
    return Match(**base, kind="related", disease_hpo_id=t, common_ancestor_id=a,
                 **_sources(index.rows[(disease_id, t)]))


def unmentioned(index: Index, disease_id: str, terms: list[QueryTerm], config: dict = CONFIG) -> list[Unmentioned]:
    """Frequent prenatal phenotypes of the disease unrelated to every query term: "not mentioned", not "absent"."""
    query = [(t.hpo_id, index.ancestors[t.hpo_id]) for t in terms]
    found = sorted(
        (t, f) for t, f in index.direct[disease_id].items()
        if t in index.prenatal and f is not None and f >= config["unmentioned_min_frequency"]
        and not any(t in anc or q in index.ancestors[t] for q, anc in query)
    )
    found.sort(key=lambda tf: -tf[1])  # stable: ties stay in hpo_id order
    return [Unmentioned(hpo_id=t, label_en=index.labels[t][0], label_vi=index.labels[t][1],
                        frequency_raw=[r.freq_raw for r in index.rows[(disease_id, t)]])
            for t, _ in found[:config["unmentioned_limit"]]]


@observe(name="rank")
def rank_terms(terms: list[QueryTerm], method: str | None = None, top_n: int | None = None,
               config: dict = CONFIG, index: Index | None = None) -> Ranking:
    index = index or load_index()
    method = method or config["method"]
    top_n = config["top_n"] if top_n is None else top_n
    kept = [t for t in terms if t.hpo_id in index.ancestors]  # current KB terms only (principle #8)
    skipped = [Skipped(phrase=None, hpo_id=t.hpo_id, reason="mã không có trong KB hoặc đã obsolete")
               for t in terms if t.hpo_id not in index.ancestors]
    if not kept:
        return Ranking(hpo_release=index.hpo_release, method=method, terms=[], skipped=skipped,
                       suggestions=[], genes=[])
    top = order_all(index, kept, method, config)[:top_n]
    masks = {t.hpo_id: excluded_mask(index, t.hpo_id) for t in kept}
    suggestions = [Suggestion(
        rank=r.rank, disease_id=r.disease_id, disease_name=index.disease_name[r.disease_id],
        source_db=index.disease_db[r.disease_id], score=r.score,
        matches=[explain_match(index, r.disease_id, t, bool(masks[t.hpo_id][index.position[r.disease_id]]))
                 for t in kept],
        unmentioned=unmentioned(index, r.disease_id, kept, config),
        genes=index.genes[r.disease_id],
    ) for r in top]
    genes: dict[str, GeneSuggestion] = {}
    for s in suggestions:
        for g in s.genes:
            if g.ncbi_gene_id not in genes:
                genes[g.ncbi_gene_id] = GeneSuggestion(ncbi_gene_id=g.ncbi_gene_id, symbol=g.symbol,
                                                       best_rank=s.rank, disease_ids=[])
            genes[g.ncbi_gene_id].disease_ids.append(s.disease_id)
    return Ranking(hpo_release=index.hpo_release, method=method, terms=kept, skipped=skipped,
                   suggestions=suggestions, genes=sorted(genes.values(), key=lambda g: (g.best_rank, g.symbol)))


def rank(analysis) -> Ranking:
    """Phase 3 output -> ranking; skipped findings and skipped codes are reported together."""
    terms, skipped = query_terms(analysis.findings)
    result = rank_terms(terms)
    return result.model_copy(update={"skipped": skipped + result.skipped})


# ---------- command line

def _label(index: Index, code: str | None) -> str:
    if code is None:
        return "—"
    en, vi = index.labels.get(code, (code, None))
    return f"{code} {en}" + (f" ({vi})" if vi else "")


def main(args: list[str]) -> None:
    if not args:
        raise SystemExit(__doc__)
    terms = [QueryTerm(hpo_id=a.rstrip("?"), assertion="SUSPECTED" if a.endswith("?") else "PRESENT",
                       weight=weight_of("SUSPECTED" if a.endswith("?") else "PRESENT")) for a in args]
    index = load_index()
    result = rank_terms(terms, index=index)
    print(f"HPO {result.hpo_release} · method {result.method} · {index.n} diseases ranked")
    for s in result.skipped:
        print(f"  bỏ {s.hpo_id}: {s.reason}")
    for s in result.suggestions:
        print(f"\n#{s.rank:g} {s.disease_id} {s.disease_name} [{s.source_db}] · score {s.score:.3f}")
        for m in s.matches:
            via = f" qua {_label(index, m.common_ancestor_id)}" if m.common_ancestor_id else ""
            print(f"   {m.query_hpo_id} ({m.assertion}) → {m.kind}: {_label(index, m.disease_hpo_id)}{via}"
                  f" · tần suất {', '.join(f or '—' for f in m.frequency_raw) or '—'}")
        for u in s.unmentioned:
            print(f"   chưa nhắc tới: {_label(index, u.hpo_id)} · tần suất {', '.join(f or '—' for f in u.frequency_raw)}")
        print(f"   gene: {', '.join(g.symbol for g in s.genes) or '—'}")
    print(f"\ngene: {', '.join(f'{g.symbol} (#{g.best_rank:g})' for g in result.genes) or '—'}")


if __name__ == "__main__":
    main(sys.argv[1:])
