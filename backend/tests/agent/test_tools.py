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
                tools.get_disease.func("OMIM:000000"), tools.get_gene.func("NOPE1"), tools.get_hpo.func("HP:9999999")]:
        assert out.startswith("Lỗi: "), out


def test_tools_need_findings():
    for out in [tools.explain_disease.func(CHARGE, rt()), tools.rank_what_if.func(rt())]:
        assert out == tools.NO_FINDINGS


def test_case_ranking_reports_skipped():
    state = case(edits={"f2": {"finding_id": "f2", "excluded": True}})
    ranking = tools.case_ranking(state)
    assert [t.hpo_id for t in ranking.terms] == [VSD] and ranking.terms[0].finding_ids == ["f1"]
    assert [(s.finding_id, s.reason) for s in ranking.skipped] == [
        ("f2", tools.EXCLUDED_REASON), ("f3", "không phải của thai"), ("f4", "chưa ánh xạ được")]
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
    assert [r["disease_id"] for r in out] == [s.disease_id for s in expected.suggestions]
    current = {s.disease_id: s.rank for s in tools.case_ranking(state).suggestions}
    assert all(r["current_rank"] == current.get(r["disease_id"], "mới vào") for r in out)
    assert state == before


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
    out = json.loads(tools.get_disease.func(CHARGE))
    flags = [p["prenatal"] for p in out["phenotypes"]]
    assert flags == sorted(flags, reverse=True) and out["genes"]


def test_get_hpo():
    out = json.loads(tools.get_hpo.func(VSD))
    assert out["label_en"] == "Ventricular septal defect" and out["parents"]


def test_search_hpo_uses_engine(monkeypatch):
    fake_search_hpo(monkeypatch, [{"hpo_id": VSD, "label_vi": "Thông liên thất", "label_en": "Ventricular septal defect",
                                   "matched_text": "x", "scores": {}}])
    assert json.loads(tools.search_hpo.func("thông liên thất")) == [
        {"hpo_id": VSD, "label_vi": "Thông liên thất", "label_en": "Ventricular septal defect"}]


@pytest.mark.parametrize("text", ["không có mã", "[HP:0001629] và [OMIM:214800], [ORPHA:138]"])
def test_citations_from_guard(text):
    from agent.guard import citations
    out = citations([text])
    assert [c["id"] for c in out] == [c for c in ("HP:0001629", "OMIM:214800", "ORPHA:138") if c in text]
    assert all(c["label"] for c in out)
