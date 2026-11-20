"""Capability analyze_findings + subgraph `analysis` (docs/agent-impl-plan.md task 2). No gateway: phase 3's model
and search steps are fakes; check_choices and hpo_release read the real KB."""

import asyncio

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.analysis import analyze_findings
from agent.state import CaseState, FindingEdit, ReviewDecision, check_edits, effective_findings
from phenotype.schema import Term
from ranking.schema import Ranking
from tests.fakes import FakeChat, call, cand, fake_phase3, item

VSD, ASD, POLY, OLIGO, MICRO = "HP:0001629", "HP:0001631", "HP:0001561", "HP:0001562", "HP:0000252"
ITEMS = [item("thông liên thất"), item("dư ối"), item("khối lạ")]
CANDIDATES = {
    "thông liên thất": [cand(VSD, "Ventricular septal defect"), cand(ASD, "Atrial septal defect")],
    "dư ối": [cand(POLY, "Polyhydramnios"), cand(OLIGO, "Oligohydramnios")],
    "khối lạ": [cand(MICRO, "Microcephaly")],
    "đầu nhỏ": [cand(MICRO, "Microcephaly"), cand(VSD, "Ventricular septal defect")],
}
DECISIONS = {"dư ối": ("AMBIGUOUS", [POLY, OLIGO]), "khối lạ": ("UNMAPPED",)}
CFG = {"configurable": {"thread_id": "case-1"}}


@pytest.fixture
def phase3(monkeypatch):
    return fake_phase3(monkeypatch, ITEMS, CANDIDATES, DECISIONS)


def make_agent(*replies: AIMessage):
    script = [call("analyze_findings", "c1"), *replies]
    return create_agent(model=FakeChat(messages=iter(script)), tools=[analyze_findings], state_schema=CaseState,
                        checkpointer=InMemorySaver())


def run(agent, graph_input):
    return asyncio.run(agent.ainvoke(graph_input, CFG))


def pending(agent) -> dict:
    (intr,) = agent.get_state(CFG).interrupts
    return intr.value


def resume(agent, **decision):
    return run(agent, Command(resume=ReviewDecision(**decision).model_dump(mode="json")))


def test_uses_last_doctor_message(phase3):
    agent = make_agent(AIMessage("xong"))
    run(agent, {"messages": [HumanMessage("mô tả cũ"), AIMessage("đã đọc"), HumanMessage("thai có thông liên thất"),
                             HumanMessage("viết lại", name="answer_guard")]})
    assert phase3.split_texts == ["thai có thông liên thất"]


def test_pauses_at_review_and_resume_does_not_rerun_phase3(phase3):
    agent = make_agent(AIMessage("xong"))
    run(agent, {"messages": [HumanMessage("mô tả")]})
    value = pending(agent)
    assert value["type"] == "review_findings"
    assert [(f["finding_id"], f["mapping_status"]) for f in value["findings"]] == [
        ("f1", "MAPPED"), ("f2", "AMBIGUOUS"), ("f3", "UNMAPPED")]
    resume(agent, action="continue", edits=[FindingEdit(finding_id="f1", assertion="SUSPECTED")], notes={"f2": "đo lại"})
    state = agent.get_state(CFG).values
    assert phase3.calls["split"] == 1 and phase3.calls["choose_terms"] == 1
    assert [r["finding_id"] for r in state["findings"]] == ["f1", "f2", "f3"]
    assert state["edits"] == {"f1": {"finding_id": "f1", "assertion": "SUSPECTED"}}
    assert state["notes"] == {"f2": "đo lại"} and state["description"] == "mô tả"
    ranking = Ranking.model_validate(state["ranking"])   # ranked in code on continue (T99)
    assert [(t.hpo_id, t.weight, t.finding_ids) for t in ranking.terms] == [(VSD, 0.5, ["f1"]), ("HP:0001560", 1.0, ["f2"])]   # f2 AMBIGUOUS → shared ancestor (T48 (4))
    assert state["messages"][-2].content.startswith("Đã ghi 3 biểu hiện vào ca và xếp hạng: 10 bệnh gợi ý")


def test_remap_maps_one_item_and_returns_to_review(phase3):
    agent = make_agent(AIMessage("xong"))
    run(agent, {"messages": [HumanMessage("mô tả")]})
    resume(agent, action="remap", finding_id="f3", phrase="đầu nhỏ")
    assert phase3.calls["find_candidates"] == 2 and phase3.calls["choose_terms"] == 2 and phase3.calls["split"] == 1
    f3 = pending(agent)["findings"][2]
    assert f3["phrase"] == "đầu nhỏ" and f3["term"]["hpo_id"] == MICRO and f3["mapping_status"] == "MAPPED"
    assert f3["machine"]["phrase"] == "khối lạ" and f3["machine"]["mapping_status"] == "UNMAPPED"
    assert [c["hpo_id"] for c in f3["candidates"]] == [MICRO, VSD]
    resume(agent, action="continue")
    state = agent.get_state(CFG).values
    assert state["remaps"]["f3"]["phrase"] == "đầu nhỏ"
    assert state["findings"][2]["phrase"] == "khối lạ"   # the machine's row is kept (#6)


def test_cancel_leaves_case_unchanged(phase3):
    agent = make_agent(AIMessage("xong"))
    run(agent, {"messages": [HumanMessage("mô tả")]})
    resume(agent, action="cancel")
    state = agent.get_state(CFG).values
    assert "findings" not in state
    assert state["messages"][-2].content == "Bác sĩ dừng phân tích; ca không đổi."


def test_remap_requires_finding_and_phrase():
    with pytest.raises(ValueError):
        ReviewDecision(action="remap", finding_id="f1", phrase="  ")


# ---------- effective findings and edit checks (pure)

def as_term(c) -> dict:
    return Term.model_validate(c.model_dump()).model_dump()


def case_state(**extra) -> dict:
    rows = []
    for i, it in enumerate(ITEMS):
        status, term, options = "UNMAPPED", None, []
        c = CANDIDATES[it.phrase]
        if i == 0:
            status, term = "MAPPED", as_term(c[0])
        elif i == 1:
            status, options = "AMBIGUOUS", [as_term(x) for x in c]
        rows.append({**it.model_dump(), "source_span": None, "mapping_status": status, "term": term,
                     "options": options, "candidates": [x.model_dump() for x in c], "finding_id": f"f{i + 1}"})
    return {"findings": rows, **extra}


def views(state):
    return {v.finding_id: v for v in effective_findings(state)}


def test_effective_findings_without_edits_has_no_machine():
    assert all(not v.machine and not v.excluded for v in effective_findings(case_state()))


@pytest.mark.parametrize("edit, field, new, old", [
    ({"excluded": True}, "excluded", True, None),
    ({"assertion": "SUSPECTED"}, "assertion", "SUSPECTED", "PRESENT"),
    ({"assessment": "LIMITED"}, "assessment", "LIMITED", "ASSESSED"),
    ({"subject": "MOTHER"}, "subject", "MOTHER", "FETUS"),
])
def test_effective_findings_applies_simple_edits(edit, field, new, old):
    v = views(case_state(edits={"f1": {"finding_id": "f1", **edit}}))["f1"]
    assert getattr(v, field) == new
    assert v.machine == ({} if old is None else {field: old})


def test_hpo_id_edit_on_ambiguous_becomes_mapped():
    v = views(case_state(edits={"f2": {"finding_id": "f2", "hpo_id": OLIGO}}))["f2"]
    assert v.mapping_status == "MAPPED" and v.term.hpo_id == OLIGO and v.options == []
    assert set(v.machine) == {"term", "mapping_status", "options"} and v.machine["mapping_status"] == "AMBIGUOUS"


def test_note_is_attached_and_not_a_change():
    v = views(case_state(notes={"f3": "hỏi lại bác sĩ siêu âm"}))["f3"]
    assert v.note == "hỏi lại bác sĩ siêu âm" and not v.machine


def test_check_edits():
    vs = effective_findings(case_state())
    check_edits(vs, [FindingEdit(finding_id="f1", hpo_id=ASD)], {"f1": "ok"})
    for edits, notes in [([FindingEdit(finding_id="f1", hpo_id=POLY)], {}),      # outside its candidates
                         ([FindingEdit(finding_id="f9", excluded=True)], {}),    # unknown finding
                         ([FindingEdit(finding_id="f1"), FindingEdit(finding_id="f1")], {}),
                         ([], {"f9": "x"}),
                         ([], {"f1": "x" * 5000})]:
        with pytest.raises(ValueError):
            check_edits(vs, edits, notes)


def test_check_edits_after_remap_uses_new_candidates():
    remapped = {**case_state()["findings"][2], "phrase": "đầu nhỏ", "mapping_status": "MAPPED",
                "term": as_term(CANDIDATES["đầu nhỏ"][0]), "candidates": [x.model_dump() for x in CANDIDATES["đầu nhỏ"]]}
    vs = effective_findings(case_state(remaps={"f3": remapped}))
    check_edits(vs, [FindingEdit(finding_id="f3", hpo_id=VSD)], {})
