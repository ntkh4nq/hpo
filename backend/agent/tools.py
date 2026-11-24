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
from kb.search import disease, literature
from kb.search import hpo as hpo_search
from phenotype.schema import Finding
from ranking.index import CONFIG as RANK_CONFIG
from ranking.index import load_index
from ranking.rank import absent_terms, conflicts, explain_match, query_terms, rank_terms, unmentioned, weight_of
from ranking.schema import QueryTerm, Ranking, Skipped
from ranking.score import excluded_mask, order_all

NO_FINDINGS = "Lỗi: chưa có biểu hiện của ca; cần đọc mô tả trước (analyze_findings)."
NO_TERMS = "Lỗi: ca chưa có biểu hiện nào dùng được để xếp hạng (của thai, có hoặc nghi có, đã ánh xạ)."


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def case_terms(state, remove: set[str] = frozenset()) -> tuple[list[QueryTerm], list[Skipped]]:
    """Query terms of the effective findings, without doctor-excluded ones and `remove` (finding ids)."""
    views = [v for v in effective_findings(state) if v.finding_id not in remove]
    used = [v for v in views if not v.excluded]
    terms, skipped = query_terms([Finding.model_validate(v.model_dump()) for v in used],
                                 ids=[v.finding_id for v in used])
    doctor = [Skipped(phrase=v.phrase, hpo_id=v.term.hpo_id if v.term else None, reason="excluded_by_doctor",
                      finding_id=v.finding_id, assertion=v.assertion) for v in views if v.excluded]
    return terms, doctor + skipped


def case_ranking(state) -> Ranking:
    """Ranking of the case's effective findings; skipped findings and codes are reported together.
    Written by analyze_findings (Tiếp tục) and by card edits, so a case with findings always has a ranking (T99)."""
    terms, skipped = case_terms(state)
    result = rank_terms(terms, absent=absent_terms(skipped))
    return result.model_copy(update={"skipped": skipped + result.skipped})


def top_rows(ranking: Ranking) -> list[dict]:
    return [{"rank": s.rank, "disease_id": s.disease_id, "name": s.disease_name, "source": s.source_db,
             "matched": matched_count(s.model_dump())} for s in ranking.suggestions]


def _disease_row(conn, disease_id: str):
    return conn.execute("SELECT name, db FROM disease WHERE disease_id = %s", (disease_id,)).fetchone()


def _labels(index, code: str) -> dict:
    return {"hpo_id": code, "label_en": index.labels.get(code, (code, None))[0]}


def list_status(state, disease_id: str) -> str | None:
    """Whether the disease is on the list the doctor sees now (a suggestion matching at least one finding), in words;
    None when the case has no suggestions yet. Lets an answer tell the current list from older turns (T128)."""
    ranking = (state or {}).get("ranking")
    if not ranking:
        return None
    listed = {s["disease_id"] for s in ranking["suggestions"] if matched_count(s) > 0}
    return CONFIG["case"]["list_words"]["listed" if disease_id in listed else "not_listed"]


def _not_ranked(disease_id: str) -> str:
    with connect() as conn:
        known = _disease_row(conn, disease_id) is not None
    return (f"Lỗi: {disease_id} không có biểu hiện ghi nhận trong KB nên không được xếp hạng." if known
            else f"Lỗi: {disease_id} không có trong KB.")


@tool
def explain_disease(disease_id: str, runtime: ToolRuntime) -> str:
    """Hạng và căn cứ của một bệnh với các biểu hiện hiện tại của ca, kể cả khi bệnh nằm ngoài top 10.

    Trả về hạng, từng biểu hiện của ca khớp với bệnh thế nào (kiểu khớp, tần suất, nguồn), các biểu hiện thường gặp
    của bệnh mà mô tả chưa nhắc tới, các biểu hiện thai được ghi là không có mà bệnh thường có ("conflicts"), và
    gene. disease_id dạng OMIM:… hoặc ORPHA:… (tìm bằng search_disease).
    """
    state = runtime.state
    if not state.get("findings"):
        return NO_FINDINGS
    index = load_index()
    if disease_id not in index.position:
        return _not_ranked(disease_id)
    terms, skipped = case_terms(state)
    kept = [t for t in terms if t.hpo_id in index.ancestors]
    if not kept:
        return NO_TERMS
    absent = [a for a in absent_terms(skipped) if a.hpo_id in index.ancestors]
    ranked = order_all(index, kept, RANK_CONFIG["method"])
    rank = next(r.rank for r in ranked if r.disease_id == disease_id)
    pos = index.position[disease_id]
    matches = [{**explain_match(index, disease_id, t, bool(excluded_mask(index, t.hpo_id)[pos])).model_dump(mode="json"),
                "finding_ids": t.finding_ids} for t in kept]
    return dump({
        "disease_id": disease_id, "name": index.disease_name[disease_id], "source": index.disease_db[disease_id],
        "current_list": list_status(state, disease_id), "rank": rank, "total": index.n, "matches": matches,
        "unmentioned": [u.model_dump(mode="json") for u in unmentioned(index, disease_id, kept, absent=absent)],
        "conflicts": [c.model_dump(mode="json") for c in conflicts(index, disease_id, absent)],
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
        "diseases": [{"disease_id": d, "name": index.disease_name[d], "source": index.disease_db[d],
                      "current_list": list_status(runtime.state, d)} for d in ids],
        "shared": top(shared),
        "differs": top(positive - shared),
        "excluded_vs_present": [{**r, "excluded_in": [d for d in ids if r["hpo_id"] in neg[d]]} for r in top(excluded)],
    })


class WhatIfTerm(BaseModel):
    hpo_id: str
    assertion: Literal["PRESENT", "SUSPECTED"]


class WhatIfChange(BaseModel):          # a new state for a finding already in the case (T133)
    finding_id: str
    assertion: Literal["PRESENT", "SUSPECTED"]


@tool
def rank_what_if(runtime: ToolRuntime, add: list[WhatIfTerm] | None = None, remove: list[str] | None = None,
                 change: list[WhatIfChange] | None = None) -> str:
    """Xếp hạng thử khi thêm biểu hiện (mã HPO, có hoặc nghi có), bỏ biểu hiện của ca (finding_id) hoặc đổi trạng
    thái một biểu hiện đã có (change: finding_id + PRESENT/SUSPECTED), không đổi ca.

    Dùng cho câu hỏi kiểu "nếu thai có thêm X thì thứ hạng đổi thế nào". Bác sĩ nói một biểu hiện đã có chỉ là nghi
    ngờ (hoặc là chắc chắn có) thì dùng change, không bỏ biểu hiện đó. Mã HPO cần thêm thì tìm bằng search_hpo trước.
    Trả về các thay đổi đã thử (bằng lời) và top bệnh thử, mỗi bệnh kèm hạng hiện tại (hoặc "mới vào").
    """
    state = runtime.state
    if not state.get("findings"):
        return NO_FINDINGS
    remove = remove or []
    changes = [WhatIfChange.model_validate(c) for c in change or []]
    views = {v.finding_id: v for v in effective_findings(state)}
    if unknown := [r for r in remove + [c.finding_id for c in changes] if r not in views]:
        return f"Lỗi: không có biểu hiện {', '.join(unknown)} trong ca."
    index = load_index()
    adds = [WhatIfTerm.model_validate(a) for a in add or []]
    if bad := [a.hpo_id for a in adds if a.hpo_id not in index.ancestors]:
        return f"Lỗi: {', '.join(bad)} không có trong KB hoặc đã obsolete."
    edits = dict(state.get("edits") or {})
    for c in changes:   # the same path as a card edit of the assertion, on a copy of the case
        edits[c.finding_id] = {**edits.get(c.finding_id, {}), "finding_id": c.finding_id, "assertion": c.assertion}
    terms, _ = case_terms({**state, "edits": edits}, remove=set(remove))
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
    words = CONFIG["case"]["assertion_words"]
    # In words only: an id here ends up in the answer the doctor reads (E2E 2026-11-24).
    tried = ([f'"{views[c.finding_id].phrase}": {words[views[c.finding_id].assertion]} → {words[c.assertion]}'
              for c in changes]
             + [f'bỏ "{views[r].phrase}"' for r in remove]
             + [f"thêm {a.hpo_id} ({words[a.assertion]})" for a in adds])
    return dump({"tried": tried,
                 "suggestions": [{**r, "current_rank": now.get(r["disease_id"], "mới vào")} for r in top_rows(trial)]})


@tool
def search_disease(text: str) -> str:
    """Tìm mã bệnh (OMIM, ORPHA) theo tên, gần đúng. Tên bệnh trong KB là tiếng Anh, ví dụ "CHARGE syndrome"."""
    return dump(disease.search_disease(text, CONFIG["search_k"]))


@tool
def get_disease(disease_id: str, runtime: ToolRuntime, focus_hpo_ids: list[str] | None = None) -> str:
    """Thông tin một bệnh: tên, nguồn, gene, các biểu hiện ghi nhận (biểu hiện thấy được trước sinh đứng trước, rồi
    theo tần suất; kèm tần suất gốc và tài liệu nguồn) và các biểu hiện bị ghi là KHÔNG có ở bệnh này.
    disease_id dạng OMIM:… hoặc ORPHA:…, lấy từ ca hoặc từ search_disease.

    "phenotypes" chỉ liệt kê tối đa một số biểu hiện đầu (phenotypes_total là tổng số): một biểu hiện không có trong
    danh sách KHÔNG có nghĩa là bệnh không có nó. Muốn biết bệnh có ghi một biểu hiện cụ thể không (ví dụ co giật),
    truyền mã HPO của biểu hiện đó vào focus_hpo_ids (lấy từ search_hpo): kết quả "focus" ghi các dòng có biểu hiện đó
    hoặc dạng cụ thể hơn của nó ("recorded") và các dòng ghi KHÔNG có ("recorded_absent"). Một biểu hiện có thể vừa
    có dòng ghi có vừa có dòng ghi không có (các bài báo khác nhau); khi đó phải nói cả hai, không kết luận "không có"."""
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

    def absent(t: str) -> dict:   # T114: a term recorded absent by one source may be recorded present by another
        return {**phenotype(t, index.excluded_rows[(disease_id, t)]), "also_recorded_present": t in direct}

    order = sorted(direct, key=lambda t: (t not in index.prenatal, direct[t] is None, -(direct[t] or 0.0), t))
    limit = CONFIG["list_limit"]
    out = {
        "disease_id": disease_id, "name": row[0], "source": row[1], "current_list": list_status(runtime.state, disease_id),
        "genes": [{"symbol": s, "association_type": a} for s, a in genes],
        "phenotypes_total": len(direct),
        "phenotypes": [phenotype(t, index.rows[(disease_id, t)]) for t in order[:limit]],
        "excluded": [absent(t) for t in sorted(excluded)[:limit]],
    }
    if focus_hpo_ids:
        focus = []
        for q in dict.fromkeys(focus_hpo_ids):
            if q not in index.ancestors:
                focus.append({"hpo_id": q, "error": "mã không có trong KB hoặc đã obsolete"})
                continue
            focus.append({**_labels(index, q),
                          "recorded": [phenotype(t, index.rows[(disease_id, t)]) for t in sorted(direct)
                                       if q in index.ancestors.get(t, ())],
                          "recorded_absent": [absent(t) for t in sorted(excluded) if q in index.ancestors.get(t, ())]})
        out["focus"] = focus
    return dump(out)


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
    """Tìm thuật ngữ HPO cho một cụm biểu hiện (tiếng Việt hoặc tiếng Anh): mã và tên tiếng Anh."""
    return dump([{"hpo_id": c["hpo_id"], "label_en": c["label_en"]}
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


@tool
def find_passages(disease_id: str, hpo_id: str | None = None, topic: str | None = None) -> str:
    """Các đoạn tài liệu (KB lớp 3) nói về một bệnh. Dùng khi bác sĩ hỏi tài liệu nói gì về một bệnh.

    disease_id lấy từ mục 'Ca hiện tại' hoặc search_disease. Hỏi về một biểu hiện ở bệnh đó thì đưa hpo_id (lấy từ
    ca hoặc search_hpo). Chủ đề khác (chẩn đoán trước sinh, di truyền, tiên lượng…) thì đưa topic, viết BẰNG TIẾNG
    ANH. Đưa đúng một trong hpo_id, topic. Trích đoạn bằng mã ref trong ngoặc vuông, ví dụ [CHUNK:123]. Kết quả []
    nghĩa là chưa có đoạn tài liệu.
    """
    if (hpo_id is None) == (topic is None) or (topic is not None and not topic.strip()):
        return "Lỗi: cần đúng một trong hpo_id, topic."
    with connect() as conn:
        if _disease_row(conn, disease_id) is None:
            return f"Lỗi: {disease_id} không có trong KB."
        if hpo_id is not None:
            row = conn.execute("SELECT is_obsolete FROM hpo_term WHERE hpo_id = %s", (hpo_id,)).fetchone()
            if row is None or row[0]:
                return f"Lỗi: {hpo_id} không có trong KB hoặc đã obsolete."
    found = literature.find_passages(disease_id, hpo_id=hpo_id, topic=topic)
    return dump([{"ref": p.ref, "title": p.title, "section": " › ".join(p.section_path), "source": p.source,
                  "url": p.url, "text": p.display_text} for p in found])


CODE_TOOLS = [explain_disease, compare_diseases, rank_what_if, search_disease, get_disease, get_gene,
              search_hpo, get_hpo, find_passages]
