"""Code tools of the supervisor (docs/agent-impl-plan.md task 3). Real Postgres KB; only search_hpo is faked."""

import copy
import json
from types import SimpleNamespace

import pytest

from agent import tools
from ranking.index import CONFIG as RANK_CONFIG
from ranking.index import load_index
from ranking.rank import rank_terms
from ranking.schema import QueryTerm, Ranking
from ranking.score import order_all
from tests.fakes import fake_search_hpo, row, seed_case

VSD, POLY, MICRO, CHARGE, CHARGE_ORPHA = "HP:0001629", "HP:0001561", "HP:0000252", "OMIM:214800", "ORPHA:138"


def rt(state=None) -> SimpleNamespace:
    """What a tool reads from ToolRuntime."""
    return SimpleNamespace(state=state or {"messages": []}, tool_call_id="call-1")


def case(**extra) -> dict:
    return {"messages": [], **seed_case([
        row("f1", "thông liên thất", VSD), row("f2", "đa ối", POLY), row("f3", "mẹ tiểu đường", MICRO, subject="MOTHER"),
        row("f4", "khối lạ", None)], **extra)}


def test_unknown_codes_return_error_text():
    state = case()
    for out in [tools.explain_disease.func("OMIM:000000", rt(state)),
                tools.compare_diseases.func(["OMIM:000000", CHARGE], rt(state)),
                tools.compare_diseases.func([CHARGE, CHARGE], rt(state)),
                tools.rank_what_if.func(rt(state), add=[{"hpo_id": "HP:9999999", "assertion": "PRESENT"}]),
                tools.rank_what_if.func(rt(state), remove=["f9"]),
                tools.get_disease.func("OMIM:000000", rt(state)), tools.get_gene.func("NOPE1"), tools.get_hpo.func("HP:9999999")]:
        assert out.startswith("Lỗi: "), out


def test_tools_need_findings():
    for out in [tools.explain_disease.func(CHARGE, rt()), tools.rank_what_if.func(rt())]:
        assert out == tools.NO_FINDINGS


def test_case_ranking_reports_skipped():
    state = case(edits={"f2": {"finding_id": "f2", "excluded": True}})
    ranking = tools.case_ranking(state)
    assert [t.hpo_id for t in ranking.terms] == [VSD] and ranking.terms[0].finding_ids == ["f1"]
    assert [(s.finding_id, s.reason, s.assertion) for s in ranking.skipped] == [
        ("f2", "excluded_by_doctor", "PRESENT"), ("f3", "not_fetal", "PRESENT"), ("f4", "unmapped", "PRESENT")]
    assert ranking.terms[0].label_en == "Ventricular septal defect"
    assert len(ranking.suggestions) == RANK_CONFIG["top_n"]


def test_explain_disease_outside_top10():
    state = case()
    terms, _ = tools.case_terms(state)
    target = order_all(load_index(), terms, RANK_CONFIG["method"])[19]
    out = json.loads(tools.explain_disease.func(target.disease_id, rt(state)))
    assert out["rank"] == target.rank > RANK_CONFIG["top_n"]
    assert [m["query_hpo_id"] for m in out["matches"]] == [VSD, POLY]
    assert out["matches"][0]["finding_ids"] == ["f1"]


def test_rank_what_if_matches_rank_terms_and_keeps_state():
    state = case()
    before = copy.deepcopy(state)
    out = json.loads(tools.rank_what_if.func(rt(state), add=[{"hpo_id": MICRO, "assertion": "PRESENT"}], remove=["f2"]))
    expected = rank_terms([QueryTerm(hpo_id=VSD, assertion="PRESENT", weight=1.0),
                           QueryTerm(hpo_id=MICRO, assertion="PRESENT", weight=1.0)])
    rows = out["suggestions"]
    assert [r["disease_id"] for r in rows] == [s.disease_id for s in expected.suggestions]
    current = {s.disease_id: s.rank for s in tools.case_ranking(state).suggestions}
    assert all(r["current_rank"] == current.get(r["disease_id"], "mới vào") for r in rows)
    assert out["tried"] == ['bỏ "đa ối"', "thêm HP:0000252 (có biểu hiện)"]
    assert state == before


def test_rank_what_if_changes_a_state_like_a_card_edit():
    """T133: "coi như chỉ nghi ngờ" lowers the finding's weight instead of removing it."""
    state = case()
    out = json.loads(tools.rank_what_if.func(rt(state), change=[{"finding_id": "f1", "assertion": "SUSPECTED"}]))
    edited = tools.case_ranking({**state, "edits": {"f1": {"finding_id": "f1", "assertion": "SUSPECTED"}}})
    assert [r["disease_id"] for r in out["suggestions"]] == [s.disease_id for s in edited.suggestions]
    assert out["tried"] == ['"thông liên thất": có biểu hiện → nghi ngờ có biểu hiện']
    assert tools.rank_what_if.func(rt(state), change=[{"finding_id": "f9", "assertion": "SUSPECTED"}]).startswith("Lỗi: ")


def test_compare_diseases_groups():
    index = load_index()
    out = json.loads(tools.compare_diseases.func([CHARGE, CHARGE_ORPHA], rt(case())))
    pos = {d: {t for t in index.direct[d] if t in index.prenatal} for d in (CHARGE, CHARGE_ORPHA)}
    assert out["shared"] and all(r["hpo_id"] in pos[CHARGE] & pos[CHARGE_ORPHA] for r in out["shared"])
    assert all(r["hpo_id"] in pos[CHARGE] ^ pos[CHARGE_ORPHA] for r in out["differs"])
    for r in out["excluded_vs_present"]:
        assert r["excluded_in"] and any(r["hpo_id"] in pos[d] for d in (CHARGE, CHARGE_ORPHA))
    assert all(r["in_case"] == (r["hpo_id"] in (VSD, POLY)) for g in ("shared", "differs") for r in out[g])


def test_search_disease_fuzzy():
    ids = [r["disease_id"] for r in json.loads(tools.search_disease.func("charge"))]
    assert CHARGE in ids or CHARGE_ORPHA in ids


def test_get_gene_returns_diseases_only():
    text = tools.get_gene.func("chd7")
    out = json.loads(text)
    assert out["symbol"] == "CHD7" and CHARGE in [d["disease_id"] for d in out["diseases"]]
    assert "HP:" not in text   # gene–phenotype only through a disease (#3)


def test_get_disease_prenatal_first():
    out = json.loads(tools.get_disease.func(CHARGE, rt()))
    flags = [p["prenatal"] for p in out["phenotypes"]]
    assert flags == sorted(flags, reverse=True) and out["genes"]


def test_get_hpo():
    out = json.loads(tools.get_hpo.func(VSD))
    assert out["label_en"] == "Ventricular septal defect" and out["parents"]


def test_search_hpo_uses_engine(monkeypatch):
    fake_search_hpo(monkeypatch, [{"hpo_id": VSD, "label_vi": "Thông liên thất", "label_en": "Ventricular septal defect",
                                   "matched_text": "x", "scores": {}}])
    assert json.loads(tools.search_hpo.func("thông liên thất")) == [
        {"hpo_id": VSD, "label_en": "Ventricular septal defect"}]


@pytest.mark.parametrize("text", ["không có mã", "[HP:0001629] và [OMIM:214800], [ORPHA:138]"])
def test_citations_from_guard(text):
    from agent.guard import citations
    out = citations([text])
    assert [c["id"] for c in out] == [c for c in ("HP:0001629", "OMIM:214800", "ORPHA:138") if c in text]
    assert all(c["label"] for c in out)


def test_find_passages_tool_checks_input(monkeypatch):
    monkeypatch.setattr(tools.literature, "find_passages", lambda *a, **k: pytest.fail("searched"))
    for kwargs in ({"disease_id": CHARGE}, {"disease_id": CHARGE, "hpo_id": VSD, "topic": "genetics"},
                   {"disease_id": CHARGE, "topic": "  "}, {"disease_id": "OMIM:000000", "hpo_id": VSD},
                   {"disease_id": CHARGE, "hpo_id": "HP:9999999"}):
        assert tools.find_passages.func(**kwargs).startswith("Lỗi: ")


def test_find_passages_tool_returns_refs(monkeypatch):
    from tests.agent.test_explanations import passage

    seen = {}

    def fake(disease_id, hpo_id=None, topic=None, **kw):
        seen.update(disease_id=disease_id, hpo_id=hpo_id, topic=topic)
        return [passage(7, "Coloboma is common.")]
    monkeypatch.setattr(tools.literature, "find_passages", fake)
    out = json.loads(tools.find_passages.func(CHARGE, topic="prenatal diagnosis"))
    assert seen == {"disease_id": CHARGE, "hpo_id": None, "topic": "prenatal diagnosis"}
    assert out == [{"ref": "CHUNK:7", "title": "Chapter", "section": "Clinical Description", "source": "genereviews",
                    "url": None, "text": "Coloboma is common."}]


def test_citations_chunk_label(rows):
    from agent.guard import citations

    chunk = rows.chunk(rows.doc([], title="CHARGE Syndrome"), "t")
    rows.conn.commit()
    assert citations([f"Đoạn [CHUNK:{chunk}] và [CHUNK:999999999]."]) == [
        {"id": f"CHUNK:{chunk}", "kind": "chunk", "label": "CHARGE Syndrome"},
        {"id": "CHUNK:999999999", "kind": "chunk", "label": None}]


def test_get_disease_focus_reaches_past_the_list_limit():
    # T114: a phenotype cut off by list_limit is still answered when asked for, with its more specific forms
    index = load_index()
    seizure = "HP:0001250"
    disease = next(d for d, terms in index.direct.items()
                   if len(terms) > 40 and any(seizure in index.ancestors.get(t, ()) for t in terms))
    out = json.loads(tools.get_disease.func(disease, rt(), [seizure, "HP:9999999"]))
    assert out["focus"][0]["recorded"] and all("also_recorded_present" in e for e in out["excluded"])
    assert out["focus"][1]["error"]


def test_case_summary_and_tool_results_show_no_vietnamese_term_name(monkeypatch):
    """T120: the supervisor sees English term names only, so it never repeats a machine-translated name."""
    from agent.state import render_case
    state = seed_case([row("f1", "thông liên thất", "HP:0001629")])
    state["ranking"] = tools.case_ranking(state).model_dump(mode="json")
    assert "label_vi" not in json.dumps(state["ranking"])
    text = render_case(state)
    assert "Thông liên thất" not in text and "[HP:0001629]" in text


def test_case_summary_writes_states_in_words_and_never_a_phrase_alone():
    """T129: no assertion/assessment/subject code reaches the supervisor; a skipped finding always carries its state."""
    import re
    from agent.state import render_case
    state = seed_case([row("f1", "thông liên thất", VSD), row("f2", "bố mẹ cùng huyết thống", None, assertion="ABSENT",
                                                              subject="FAMILY"),
                       row("f3", "đa ối", POLY, assertion="SUSPECTED")])
    state["ranking"] = tools.case_ranking(state).model_dump(mode="json")
    text = render_case(state)
    assert not re.search(r"\b(PRESENT|ABSENT|SUSPECTED|UNCERTAIN|ASSESSED|LIMITED|NOT_ASSESSED|FETUS|MOTHER|FAMILY)\b", text)
    assert '"bố mẹ cùng huyết thống" — không có biểu hiện — tiền sử gia đình' in text
    skipped = text.split("Biểu hiện không dùng để tìm bệnh")[1]
    assert '"bố mẹ cùng huyết thống" — không có biểu hiện — tiền sử gia đình — không phải biểu hiện của thai' in skipped
    used = text.split("Biểu hiện của thai dùng để tìm bệnh")[1].split("Gợi ý hiện tại")[0]
    assert '- Ventricular septal defect [HP:0001629] ← "thông liên thất" (f1)' in used
    assert "Polyhydramnios [HP:0001561]" in used and "ở thai mới nghi ngờ" in used


def test_disease_tools_say_whether_the_disease_is_on_the_current_list():
    """T128: a tool result names whether the disease is on the list the doctor sees now."""
    state = case()
    state["ranking"] = tools.case_ranking(state).model_dump(mode="json")
    words = tools.CONFIG["case"]["list_words"]
    listed = next(s["disease_id"] for s in state["ranking"]["suggestions"] if tools.matched_count(s) > 0)
    assert json.loads(tools.get_disease.func(listed, rt(state)))["current_list"] == words["listed"]
    assert json.loads(tools.get_disease.func(CHARGE_ORPHA, rt(state)))["current_list"] == words["not_listed"]
    assert json.loads(tools.get_disease.func(CHARGE, rt()))["current_list"] is None
    pair = json.loads(tools.compare_diseases.func([listed, CHARGE_ORPHA], rt(state)))["diseases"]
    assert [d["current_list"] for d in pair] == [words["listed"], words["not_listed"]]
