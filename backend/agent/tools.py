"""Code capabilities of the supervisor (docs/agent-impl-plan.md task 3; agent-plan §3.4).

Every tool only reads the KB and checks every code against it before use (principle #8). Bad input or missing
case data returns a Vietnamese error string starting with "Lỗi: " so the supervisor can react; nothing raises.
Scores never leave the ranking code (principle #5); tools report ranks, matches and frequencies only.
"""

import json
from typing import Literal

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel

from agent.state import CONFIG, effective_findings, matched_count
from core.db import connect
from kb.search import hpo as hpo_search
from phenotype.schema import Finding
from ranking.index import CONFIG as RANK_CONFIG
from ranking.index import load_index
from ranking.rank import explain_match, query_terms, rank_terms, unmentioned, weight_of
from ranking.schema import QueryTerm, Ranking, Skipped
from ranking.score import excluded_mask, order_all

NO_FINDINGS = "Lỗi: chưa có biểu hiện của ca; cần đọc mô tả trước (analyze_findings)."
NO_TERMS = "Lỗi: ca chưa có biểu hiện nào dùng được để xếp hạng (của thai, có hoặc nghi có, đã ánh xạ)."
EXCLUDED_REASON = "bác sĩ bỏ khỏi xếp hạng"


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def case_terms(state, remove: set[str] = frozenset()) -> tuple[list[QueryTerm], list[Skipped]]:
    """Query terms of the effective findings, without doctor-excluded ones and `remove` (finding ids)."""
    views = [v for v in effective_findings(state) if v.finding_id not in remove]
    used = [v for v in views if not v.excluded]
    terms, skipped = query_terms([Finding.model_validate(v.model_dump()) for v in used],
                                 ids=[v.finding_id for v in used])
    doctor = [Skipped(phrase=v.phrase, hpo_id=v.term.hpo_id if v.term else None, reason=EXCLUDED_REASON,
                      finding_id=v.finding_id) for v in views if v.excluded]
    return terms, doctor + skipped


def case_ranking(state) -> Ranking:
    """Ranking of the case's effective findings; skipped findings and codes are reported together.
    Written by analyze_findings (Tiếp tục) and by card edits, so a case with findings always has a ranking (T99)."""
    terms, skipped = case_terms(state)
    result = rank_terms(terms)
    return result.model_copy(update={"skipped": skipped + result.skipped})


def top_rows(ranking: Ranking) -> list[dict]:
    return [{"rank": s.rank, "disease_id": s.disease_id, "name": s.disease_name, "source": s.source_db,
             "matched": matched_count(s.model_dump())} for s in ranking.suggestions]


def _disease_row(conn, disease_id: str):
    return conn.execute("SELECT name, db FROM disease WHERE disease_id = %s", (disease_id,)).fetchone()


def _labels(index, code: str) -> dict:
    en, vi = index.labels.get(code, (code, None))
    return {"hpo_id": code, "label_vi": vi, "label_en": en}


def _not_ranked(disease_id: str) -> str:
    with connect() as conn:
        known = _disease_row(conn, disease_id) is not None
    return (f"Lỗi: {disease_id} không có biểu hiện ghi nhận trong KB nên không được xếp hạng." if known
            else f"Lỗi: {disease_id} không có trong KB.")


@tool
def explain_disease(disease_id: str, runtime: ToolRuntime) -> str:
    """Hạng và căn cứ của một bệnh với các biểu hiện hiện tại của ca, kể cả khi bệnh nằm ngoài top 10.

    Trả về hạng, từng biểu hiện của ca khớp với bệnh thế nào (kiểu khớp, tần suất, nguồn), các biểu hiện thường gặp
    của bệnh mà mô tả chưa nhắc tới, và gene. disease_id dạng OMIM:… hoặc ORPHA:… (tìm bằng search_disease).
    """
    state = runtime.state
    if not state.get("findings"):
        return NO_FINDINGS
    index = load_index()
    if disease_id not in index.position:
        return _not_ranked(disease_id)
    terms, _ = case_terms(state)
    kept = [t for t in terms if t.hpo_id in index.ancestors]
    if not kept:
        return NO_TERMS
    ranked = order_all(index, kept, RANK_CONFIG["method"])
    rank = next(r.rank for r in ranked if r.disease_id == disease_id)
    pos = index.position[disease_id]
    matches = [{**explain_match(index, disease_id, t, bool(excluded_mask(index, t.hpo_id)[pos])).model_dump(mode="json"),
                "finding_ids": t.finding_ids} for t in kept]
    return dump({
        "disease_id": disease_id, "name": index.disease_name[disease_id], "source": index.disease_db[disease_id],
        "rank": rank, "total": index.n, "matches": matches,
        "unmentioned": [u.model_dump(mode="json") for u in unmentioned(index, disease_id, kept)],
        "genes": [g.symbol for g in index.genes[disease_id]],
    })


@tool
def compare_diseases(disease_ids: list[str], runtime: ToolRuntime) -> str:
    """So sánh 2 bệnh trở lên theo các biểu hiện tiền sản: biểu hiện chung, biểu hiện chỉ một số bệnh có (kèm tần
    suất từng bệnh), và biểu hiện bị ghi là KHÔNG có ở bệnh này mà có ở bệnh kia.

    Dùng để trả lời bệnh nào khác bệnh nào ở đâu, lần khám sau nên xem gì để phân biệt. Tần suất là tỉ lệ bệnh
    nhân mắc bệnh có biểu hiện đó (null = bệnh không ghi nhận biểu hiện này). in_case = biểu hiện đã có trong ca.
    """
    ids = list(dict.fromkeys(disease_ids))
    if len(ids) < 2:
        return "Lỗi: cần ít nhất 2 mã bệnh khác nhau."
    index = load_index()
    for d in ids:
        if d not in index.position:
            return _not_ranked(d)
    in_case = {t.hpo_id for t in case_terms(runtime.state)[0]} if runtime.state.get("findings") else set()
    pos = {d: {t: f for t, f in index.direct[d].items() if t in index.prenatal} for d in ids}
    neg = {d: {t for t in index.excluded_terms.get(d, {}) if t in index.prenatal} for d in ids}
    positive = set().union(*pos.values())

    def row(t: str) -> dict:
        return {**_labels(index, t), "freq": {d: pos[d].get(t) for d in ids}, "in_case": t in in_case}

    def spread(t: str) -> float:
        values = [pos[d].get(t) or 0.0 for d in ids]
        return max(values) - min(values)

    def top(codes) -> list[dict]:
        return [row(t) for t in sorted(codes, key=lambda t: (-spread(t), t))[:CONFIG["list_limit"]]]

    shared = {t for t in positive if all(t in pos[d] for d in ids)}
    excluded = {t for t in set().union(*neg.values()) if t in positive}
    return dump({
        "diseases": [{"disease_id": d, "name": index.disease_name[d], "source": index.disease_db[d]} for d in ids],
        "shared": top(shared),
        "differs": top(positive - shared),
        "excluded_vs_present": [{**r, "excluded_in": [d for d in ids if r["hpo_id"] in neg[d]]} for r in top(excluded)],
    })


class WhatIfTerm(BaseModel):
    hpo_id: str
    assertion: Literal["PRESENT", "SUSPECTED"]


@tool
def rank_what_if(runtime: ToolRuntime, add: list[WhatIfTerm] | None = None, remove: list[str] | None = None) -> str:
    """Xếp hạng thử khi thêm biểu hiện (mã HPO, có hoặc nghi có) hoặc bỏ biểu hiện của ca (finding_id), không đổi ca.

    Dùng cho câu hỏi kiểu "nếu thai có thêm X thì thứ hạng đổi thế nào". Mã HPO cần thêm thì tìm bằng search_hpo
    trước. Trả về top bệnh thử, mỗi bệnh kèm hạng hiện tại (hoặc "mới vào").
    """
    state = runtime.state
    if not state.get("findings"):
        return NO_FINDINGS
    remove = remove or []
    known = {v.finding_id for v in effective_findings(state)}
    if unknown := [r for r in remove if r not in known]:
        return f"Lỗi: không có biểu hiện {', '.join(unknown)} trong ca."
    index = load_index()
    adds = [WhatIfTerm.model_validate(a) for a in add or []]
    if bad := [a.hpo_id for a in adds if a.hpo_id not in index.ancestors]:
        return f"Lỗi: {', '.join(bad)} không có trong KB hoặc đã obsolete."
    terms, _ = case_terms(state, remove=set(remove))
    merged = {t.hpo_id: t for t in terms}
    for a in adds:
        q = QueryTerm(hpo_id=a.hpo_id, assertion=a.assertion, weight=weight_of(a.assertion))
        if a.hpo_id not in merged or q.weight > merged[a.hpo_id].weight:
            merged[a.hpo_id] = q
    if not merged:
        return NO_TERMS
    trial = rank_terms(list(merged.values()), index=index)
    current = Ranking.model_validate(state["ranking"]) if state.get("ranking") else case_ranking(state)
    now = {s.disease_id: s.rank for s in current.suggestions}
    return dump([{**r, "current_rank": now.get(r["disease_id"], "mới vào")} for r in top_rows(trial)])


@tool
def search_disease(text: str) -> str:
    """Tìm mã bệnh (OMIM, ORPHA) theo tên, gần đúng. Tên bệnh trong KB là tiếng Anh, ví dụ "CHARGE syndrome"."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT disease_id, name, db FROM disease WHERE name IS NOT NULL
               ORDER BY word_similarity(%s, name) DESC, disease_id LIMIT %s""", (text, CONFIG["search_k"])).fetchall()
    return dump([{"disease_id": d, "name": n, "source": db} for d, n, db in rows])


@tool
def get_disease(disease_id: str) -> str:
    """Thông tin một bệnh: tên, nguồn, gene, các biểu hiện ghi nhận (biểu hiện thấy được trước sinh đứng trước, rồi
    theo tần suất; kèm tần suất gốc và tài liệu nguồn) và các biểu hiện bị ghi là KHÔNG có ở bệnh này.
    disease_id dạng OMIM:… hoặc ORPHA:…, lấy từ ca hoặc từ search_disease."""
    with connect() as conn:
        row = _disease_row(conn, disease_id)
        if row is None:
            return f"Lỗi: {disease_id} không có trong KB."
        genes = conn.execute(
            """SELECT g.symbol, gd.association_type FROM gene_disease gd JOIN gene g USING (ncbi_gene_id)
               WHERE gd.disease_id = %s ORDER BY g.symbol""", (disease_id,)).fetchall()
    index = load_index()
    direct = index.direct.get(disease_id, {})
    excluded = index.excluded_terms.get(disease_id, {})

    def phenotype(t: str, rows) -> dict:
        return {**_labels(index, t), "prenatal": t in index.prenatal,
                "frequency_raw": [r.freq_raw for r in rows],
                "references": sorted({ref for r in rows for ref in r.references})}

    order = sorted(direct, key=lambda t: (t not in index.prenatal, direct[t] is None, -(direct[t] or 0.0), t))
    limit = CONFIG["list_limit"]
    return dump({
        "disease_id": disease_id, "name": row[0], "source": row[1],
        "genes": [{"symbol": s, "association_type": a} for s, a in genes],
        "phenotypes_total": len(direct),
        "phenotypes": [phenotype(t, index.rows[(disease_id, t)]) for t in order[:limit]],
        "excluded": [phenotype(t, index.excluded_rows[(disease_id, t)]) for t in sorted(excluded)[:limit]],
    })


@tool
def get_gene(symbol: str) -> str:
    """Một gene và các bệnh của nó (mã bệnh, tên, kiểu liên kết). Gene chỉ nối với biểu hiện qua bệnh."""
    with connect() as conn:
        gene = conn.execute("SELECT ncbi_gene_id, symbol FROM gene WHERE upper(symbol) = upper(%s) ORDER BY ncbi_gene_id",
                            (symbol.strip(),)).fetchone()
        if gene is None:
            return f"Lỗi: gene {symbol} không có trong KB."
        diseases = conn.execute(
            """SELECT gd.disease_id, d.name, gd.association_type FROM gene_disease gd JOIN disease d USING (disease_id)
               WHERE gd.ncbi_gene_id = %s ORDER BY gd.disease_id""", (gene[0],)).fetchall()
    return dump({"symbol": gene[1], "ncbi_gene_id": gene[0], "diseases_total": len(diseases),
                 "diseases": [{"disease_id": d, "name": n, "association_type": a}
                              for d, n, a in diseases[:CONFIG["list_limit"]]]})


@tool
def search_hpo(text: str) -> str:
    """Tìm thuật ngữ HPO cho một cụm biểu hiện (tiếng Việt hoặc tiếng Anh): mã, tên tiếng Việt, tên tiếng Anh."""
    return dump([{"hpo_id": c["hpo_id"], "label_vi": c["label_vi"], "label_en": c["label_en"]}
                 for c in hpo_search.search_hpo(text, k=CONFIG["search_k"])])


@tool
def get_hpo(hpo_id: str) -> str:
    """Nghĩa của một mã HPO: tên tiếng Anh và tiếng Việt, định nghĩa, mã cha, mã con, có thấy được trước sinh không."""
    with connect() as conn:
        row = conn.execute("SELECT definition_en, prenatal_relevant, is_obsolete, replaced_by FROM hpo_term "
                           "WHERE hpo_id = %s", (hpo_id,)).fetchone()
        if row is None:
            return f"Lỗi: {hpo_id} không có trong KB."
        if row[2]:
            return f"Lỗi: {hpo_id} đã obsolete" + (f", thay bằng {row[3]}." if row[3] else ".")
        parents = [p for (p,) in conn.execute("SELECT parent_id FROM hpo_edge WHERE child_id = %s ORDER BY parent_id",
                                              (hpo_id,))]
        children = [c for (c,) in conn.execute("SELECT child_id FROM hpo_edge WHERE parent_id = %s ORDER BY child_id",
                                               (hpo_id,))]
    index = load_index()
    return dump({**_labels(index, hpo_id), "definition_en": row[0], "prenatal_relevant": row[1],
                 "parents": [_labels(index, p) for p in parents], "children_total": len(children),
                 "children": [_labels(index, c) for c in children[:CONFIG["list_limit"]]]})


CODE_TOOLS = [explain_disease, compare_diseases, rank_what_if, search_disease, get_disease, get_gene,
              search_hpo, get_hpo]
