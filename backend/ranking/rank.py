"""Phase 4: phenotype profile -> ranked diseases and genes with their evidence (docs/ranking-plan.md §2, §7).

    python -m ranking.rank HP:0001629 HP:0001561?     # suffix ? = SUSPECTED

Deterministic code over KB layers 1-2, no LLM (principle #5).
"""

import sys

from langfuse import observe

from phenotype.schema import Finding
from ranking.index import CONFIG, Index, SourceRow, load_index
from ranking.schema import (AbsentTerm, Conflict, GeneSuggestion, Match, QueryTerm, Ranking, Skipped, Suggestion, Trait,
                            Unmentioned)
from ranking.score import excluded_mask, order_all

ROOT = "HP:0000118"  # Phenotypic abnormality


def weight_of(assertion: str, config: dict = CONFIG) -> float:
    return 1.0 if assertion == "PRESENT" else config["suspected_weight"]


def common_ancestor(index: Index, codes: list[str]) -> tuple[str, float] | None:
    """The shared ancestor (itself included) with the largest IC, ties to the smaller hpo_id; None if none has an IC."""
    shared = frozenset.intersection(*(index.ancestors.get(c, frozenset()) for c in codes))
    best = min((a for a in shared if a in index.ic), key=lambda a: (-index.ic[a], a), default=None)
    return None if best is None else (best, index.ic[best])


def query_terms(findings: list[Finding], config: dict = CONFIG, index: Index | None = None,
                ids: list[str] | None = None) -> tuple[list[QueryTerm], list[Skipped]]:
    """Fetal PRESENT/SUSPECTED findings that are MAPPED, or AMBIGUOUS with a specific enough common ancestor (§2).
    Fetal ABSENT findings that are ASSESSED and MAPPED are skipped with reason `absent_checked` (absent_terms reads
    them, T130), unless their term is a query term or an ancestor of one (a contradiction within the case), a grouping
    node or above one (T131), or not in the KB: then `state_not_used`.

    `ids` (one per finding) are copied into QueryTerm.finding_ids / Skipped.finding_id (phase 5)."""
    index = index or load_index()
    terms: dict[str, QueryTerm] = {}
    skipped: list[Skipped] = []
    checked: list[int] = []            # positions in `skipped` of absent findings to check once all terms are known
    for i, f in enumerate(findings):
        fid = ids[i] if ids else None
        code = f.term.hpo_id if f.term else None
        def skip(reason: str, hpo_id: str | None = code) -> None:
            skipped.append(Skipped(phrase=f.phrase, hpo_id=hpo_id, reason=reason, finding_id=fid, assertion=f.assertion))
        if f.subject != "FETUS":
            skip("not_fetal")
        elif f.assertion == "ABSENT" and f.assessment == "ASSESSED" and f.mapping_status == "MAPPED":
            checked.append(len(skipped))
            skip("absent_checked")
        elif f.assertion not in ("PRESENT", "SUSPECTED"):
            skip("state_not_used")
        elif f.mapping_status == "UNMAPPED":
            skip("unmapped", None)
        else:
            options = [o.hpo_id for o in f.options] if f.mapping_status == "AMBIGUOUS" else []
            if options:
                found = common_ancestor(index, options)
                if found is None or found[1] <= 0:
                    skip("ambiguous_no_common_ancestor", None)
                    continue
                code = found[0]
            term = QueryTerm(hpo_id=code, assertion=f.assertion, weight=weight_of(f.assertion, config),
                             from_options=options, finding_ids=[fid] if fid else [])
            if code not in terms:
                terms[code] = term
            else:
                old = terms[code]
                kept = term if term.weight > old.weight else old
                terms[code] = kept.model_copy(update={"finding_ids": old.finding_ids + term.finding_ids})
    blocked = grouping_ancestors(index, config)
    for i in checked:
        code = skipped[i].hpo_id
        if code not in index.ancestors or code in blocked or any(code in index.ancestors.get(t, ()) for t in terms):
            skipped[i] = skipped[i].model_copy(update={"reason": "state_not_used"})
    return list(terms.values()), skipped


def absent_terms(skipped: list[Skipped]) -> list[AbsentTerm]:
    """The absent findings query_terms marked for checking, one entry per term (T130)."""
    out: dict[str, AbsentTerm] = {}
    for s in skipped:
        if s.reason == "absent_checked" and s.hpo_id:
            entry = out.setdefault(s.hpo_id, AbsentTerm(hpo_id=s.hpo_id))
            if s.finding_id:
                entry.finding_ids.append(s.finding_id)
    return list(out.values())


def conflicts(index: Index, disease_id: str, absent: list[AbsentTerm], config: dict = CONFIG) -> list[Conflict]:
    """Absent findings the disease records (at or below their term) frequently or without frequency (T130)."""
    out = []
    for a in absent:
        cands = {t: f for t, f in index.direct[disease_id].items() if a.hpo_id in index.ancestors.get(t, ())
                 and (f is None or f >= config["conflict_min_frequency"])}
        if cands:
            t = _best(cands)
            out.append(Conflict(query_hpo_id=a.hpo_id, finding_ids=a.finding_ids, disease_hpo_id=t,
                                disease_label_en=_label_en(index, t) or t, **_sources(index.rows[(disease_id, t)])))
    return out


# ---------- explanation (§7)

def _best(candidates: dict[str, float | None]) -> str:
    """Largest frequency first (None last), then the smaller hpo_id."""
    return min(candidates, key=lambda t: (candidates[t] is None, -(candidates[t] or 0.0), t))


def _sources(rows: list[SourceRow]) -> dict:
    return {"frequency_raw": [r.freq_raw for r in rows], "evidence": [r.evidence for r in rows],
            "references": [r.references for r in rows]}


def grouping_ancestors(index: Index, config: dict = CONFIG) -> frozenset[str]:
    """The grouping nodes of config and all their ancestors: never evidence that two findings are related (T131)."""
    return frozenset().union(*(index.ancestors.get(g, frozenset()) for g in config["grouping_nodes"]))


def _label_en(index: Index, hpo_id: str) -> str | None:
    return index.labels.get(hpo_id, (None,))[0]


def explain_match(index: Index, disease_id: str, term: QueryTerm, excluded: bool, config: dict = CONFIG) -> Match:
    q, direct = term.hpo_id, index.direct[disease_id]
    anc = index.ancestors[q]
    base = {"query_hpo_id": q, "assertion": term.assertion, "weight": term.weight}
    if excluded:
        cands = {t: f for t, f in index.excluded_terms[disease_id].items() if t in anc}
        t = _best(cands)
        return Match(**base, kind="excluded", disease_hpo_id=t, disease_label_en=_label_en(index, t), common_ancestor_id=None,
                     **_sources(index.excluded_rows[(disease_id, t)]))
    kinds = (
        ("exact", {t: f for t, f in direct.items() if t == q}),
        ("disease_more_specific", {t: f for t, f in direct.items() if t != q and q in index.ancestors[t]}),
        ("disease_more_general", {t: f for t, f in direct.items() if t != q and t in anc}),
    )
    for kind, cands in kinds:
        if cands:
            t = _best(cands)
            return Match(**base, kind=kind, disease_hpo_id=t, disease_label_en=_label_en(index, t), common_ancestor_id=None,
                         **_sources(index.rows[(disease_id, t)]))
    # related: the shared ancestor with the largest IC over all positive annotations, grouping nodes excluded (T131)
    blocked = grouping_ancestors(index, config)
    shared = {a for t in direct for a in index.ancestors[t] & anc if a in index.ic and a not in blocked}
    a = min(shared, key=lambda x: (-index.ic[x], x), default=None)
    if a is None or index.ic[a] <= 0:
        return Match(**base, kind="none", disease_hpo_id=None, common_ancestor_id=None)
    t = _best({t: f for t, f in direct.items() if a in index.ancestors[t]})
    return Match(**base, kind="related", disease_hpo_id=t, disease_label_en=_label_en(index, t), common_ancestor_id=a,
                 **_sources(index.rows[(disease_id, t)]))


def unmentioned(index: Index, disease_id: str, terms: list[QueryTerm], config: dict = CONFIG,
                absent: list[AbsentTerm] = ()) -> list[Unmentioned]:
    """Frequent prenatal phenotypes of the disease unrelated to every query term and every checked absent finding:
    "not mentioned", not "absent". A phenotype the fetus was assessed not to have is a conflict instead (T130)."""
    query = [(c, index.ancestors[c]) for c in [t.hpo_id for t in terms] + [a.hpo_id for a in absent]]
    found = sorted(
        (t, f) for t, f in index.direct[disease_id].items()
        if t in index.prenatal and f is not None and f >= config["unmentioned_min_frequency"]
        and not any(t in anc or q in index.ancestors[t] for q, anc in query)
    )
    # same frequency: the one resting on more reported patients first (37/37 before 1/1); stable: then hpo_id order
    found.sort(key=lambda tf: (-tf[1], -sum(r.patients for r in index.rows[(disease_id, tf[0])])))
    return [Unmentioned(hpo_id=t, label_en=index.labels[t][0],
                        **_sources(index.rows[(disease_id, t)]))
            for t, _ in found[:config["unmentioned_limit"]]]


def cross_reference(index: Index, suggestions: list[Suggestion]) -> None:
    """For each "not mentioned" phenotype, which other listed diseases record it (true-path rule) or record it as
    absent, so a summary can compare diseases without reading their whole annotation sets."""
    ids = [s.disease_id for s in suggestions]
    for s in suggestions:
        for u in s.unmentioned:
            positions = set(index.under[u.hpo_id][0].tolist()) if u.hpo_id in index.under else set()
            u.also_in = [d for d in ids if d != s.disease_id and index.position[d] in positions]
            u.excluded_in = [d for d in ids if d != s.disease_id and u.hpo_id in index.excluded_terms.get(d, {})]


def traits(index: Index, disease_id: str, aspect: str) -> list[Trait]:
    """Inheritance (I) or clinical course (C) annotations of the disease, one entry per term, sources kept."""
    rows: dict[str, list] = {}
    for r in index.traits.get(disease_id, []):
        if r.aspect == aspect:
            rows.setdefault(r.hpo_id, []).append(r)
    return [Trait(hpo_id=t, label_en=index.labels.get(t, (t, None))[0],
                  frequency_raw=[r.freq_raw for r in rs], references=[list(r.references or []) for r in rs])
            for t, rs in rows.items()]


@observe(name="rank")
def rank_terms(terms: list[QueryTerm], method: str | None = None, top_n: int | None = None,
               config: dict = CONFIG, index: Index | None = None, absent: list[AbsentTerm] = ()) -> Ranking:
    """Order diseases by `terms`; `absent` (checked absent findings) only adds conflicts, never changes ranks."""
    index = index or load_index()
    absent = [a.model_copy(update={"label_en": index.labels[a.hpo_id][0]}) for a in absent if a.hpo_id in index.ancestors]
    method = method or config["method"]
    top_n = config["top_n"] if top_n is None else top_n
    kept = [t for t in terms if t.hpo_id in index.ancestors]  # current KB terms only (principle #8)
    kept = [t.model_copy(update={"label_en": index.labels[t.hpo_id][0]}) for t in kept]
    skipped = [Skipped(phrase=None, hpo_id=t.hpo_id, reason="not_in_kb", assertion=t.assertion)
               for t in terms if t.hpo_id not in index.ancestors]
    if not kept:
        return Ranking(hpo_release=index.hpo_release, method=method, terms=[], skipped=skipped,
                       suggestions=[], genes=[], absent=absent)
    top = order_all(index, kept, method, config)[:top_n]
    masks = {t.hpo_id: excluded_mask(index, t.hpo_id) for t in kept}
    suggestions = [Suggestion(
        rank=r.rank, disease_id=r.disease_id, disease_name=index.disease_name[r.disease_id],
        source_db=index.disease_db[r.disease_id], score=r.score,
        matches=[explain_match(index, r.disease_id, t, bool(masks[t.hpo_id][index.position[r.disease_id]]))
                 for t in kept],
        unmentioned=unmentioned(index, r.disease_id, kept, config, absent),
        genes=index.genes[r.disease_id],
        inheritance=traits(index, r.disease_id, "I"), clinical_course=traits(index, r.disease_id, "C"),
        conflicts=conflicts(index, r.disease_id, absent, config),
    ) for r in top]
    cross_reference(index, suggestions)
    genes: dict[str, GeneSuggestion] = {}
    for s in suggestions:
        for g in s.genes:
            if g.ncbi_gene_id not in genes:
                genes[g.ncbi_gene_id] = GeneSuggestion(ncbi_gene_id=g.ncbi_gene_id, symbol=g.symbol,
                                                       best_rank=s.rank, disease_ids=[])
            genes[g.ncbi_gene_id].disease_ids.append(s.disease_id)
    return Ranking(hpo_release=index.hpo_release, method=method, terms=kept, skipped=skipped, absent=absent,
                   suggestions=suggestions, genes=sorted(genes.values(), key=lambda g: (g.best_rank, g.symbol)))


def rank(analysis) -> Ranking:
    """Phase 3 output -> ranking; skipped findings and skipped codes are reported together."""
    terms, skipped = query_terms(analysis.findings)
    result = rank_terms(terms, absent=absent_terms(skipped))
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
