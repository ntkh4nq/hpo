"""Case graph: routing, card actions, a full turn with the review pause (docs/agent-impl-plan.md task 4)."""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.graph import build_graph
from agent.state import ReviewDecision
from agent.tools import EXCLUDED_REASON, case_ranking
from ranking.schema import Ranking
from tests.fakes import FakeChat, call, cand, fake_phase3, item, row, seed_case

VSD, POLY, MICRO = "HP:0001629", "HP:0001561", "HP:0000252"
CFG = {"configurable": {"thread_id": "case-g"}}


def graph_with(*script):
    model = FakeChat(messages=iter(script))
    return build_graph(InMemorySaver(), model), model


def seeded(**extra):
    """A graph whose case already has findings and a ranking; the fake model must never be called."""
    graph, model = graph_with()
    state = seed_case([row("f1", "thông liên thất", VSD), row("f2", "đa ối", POLY),
                       row("f3", "mẹ tiểu đường", MICRO, subject="MOTHER"), row("f4", "khối lạ", None)], **extra)
    state["ranking"] = case_ranking(state).model_dump(mode="json")
    graph.update_state(CFG, state, as_node="apply_findings_action")
    return graph, model


def act(graph, edits=(), notes=None):
    asyncio.run(graph.ainvoke({"pending_action": {"type": "update_findings", "edits": list(edits),
                                                  "notes": notes or {}}}, CFG))
    return graph.get_state(CFG).values


def test_message_goes_to_supervisor():
    graph, model = graph_with(AIMessage("Chào bác sĩ."))
    asyncio.run(graph.ainvoke({"messages": [HumanMessage("Chào")]}, CFG))
    assert graph.get_state(CFG).values["messages"][-1].text == "Chào bác sĩ." and len(model.seen) == 1


def test_pending_action_goes_to_apply():
    graph, model = seeded()
    values = act(graph, notes={"f1": "xem lại"})
    assert values["notes"] == {"f1": "xem lại"} and values["pending_action"] is None
    assert model.seen == []   # no model request for a card action (T91)


def test_update_findings_reranks():
    graph, _ = seeded()
    before = graph.get_state(CFG).values["ranking"]
    values = act(graph, edits=[{"finding_id": "f1", "assertion": "SUSPECTED"}])
    after = Ranking.model_validate(values["ranking"])
    assert values["ranking"] != before
    assert [(t.hpo_id, t.weight) for t in after.terms] == [(VSD, 0.5), (POLY, 1.0)]


def test_notes_only_does_not_rerank():
    graph, _ = seeded()
    before = graph.get_state(CFG).values["ranking"]
    assert act(graph, notes={"f2": "đo lại chỉ số ối"})["ranking"] == before


def test_skipped_have_finding_ids():
    graph, _ = seeded()
    values = act(graph, edits=[{"finding_id": "f1", "excluded": True}])
    skipped = Ranking.model_validate(values["ranking"]).skipped
    assert [(s.finding_id, s.reason) for s in skipped] == [
        ("f1", EXCLUDED_REASON), ("f3", "không phải của thai"), ("f4", "chưa ánh xạ được")]


def test_full_turn_with_review(monkeypatch):
    fake_phase3(monkeypatch, [item("thông liên thất"), item("đa ối")],
                {"thông liên thất": [cand(VSD, "Ventricular septal defect")], "đa ối": [cand(POLY, "Polyhydramnios")]})
    graph, model = graph_with(call("analyze_findings", "c1"), AIMessage("Đã có gợi ý."))
    asyncio.run(graph.ainvoke({"messages": [HumanMessage("Thai có thông liên thất, đa ối.")]}, CFG))
    assert graph.get_state(CFG).interrupts[0].value["type"] == "review_findings"
    decision = ReviewDecision(action="continue", notes={"f2": "đo AFI 30 cm"})
    asyncio.run(graph.ainvoke(Command(resume=decision.model_dump(mode="json")), CFG))
    values = graph.get_state(CFG).values
    assert not graph.get_state(CFG).interrupts
    assert [r["finding_id"] for r in values["findings"]] == ["f1", "f2"] and values["notes"] == {"f2": "đo AFI 30 cm"}
    assert Ranking.model_validate(values["ranking"]).suggestions
    assert values["messages"][-1].text == "Đã có gợi ý." and len(model.seen) == 2   # ranking needs no model call
