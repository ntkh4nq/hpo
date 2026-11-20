"""Case API and SSE (docs/agent-impl-plan.md task 5). TestClient without `with`, so the Redis lifespan does not
run; each test puts a graph on InMemorySaver with a fake model on app.state."""

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import build_graph
from agent.tools import case_ranking
from api import cases
from api.main import app
from tests.fakes import FakeChat, call, cand, fake_phase3, item, row, seed_case

VSD, ASD, POLY = "HP:0001629", "HP:0001631", "HP:0001561"
client = TestClient(app)


@pytest.fixture
def use(monkeypatch):
    """use(*script) -> the fake model; the app's graph now runs it."""
    cases.RUNNING.clear()
    fake_phase3(monkeypatch, [item("thông liên thất"), item("đa ối")],
                {"thông liên thất": [cand(VSD, "Ventricular septal defect"), cand(ASD, "Atrial septal defect")],
                 "đa ối": [cand(POLY, "Polyhydramnios")]})

    def install(*script):
        model = FakeChat(messages=iter(script))
        app.state.graph = build_graph(InMemorySaver(), model)
        return model
    yield install
    cases.RUNNING.clear()


def events(resp) -> list[tuple[str, dict]]:
    assert resp.status_code == 200, resp.text
    out = []
    for block in resp.text.split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if name:
            out.append((name, data))
    return out


def of(evs, name) -> list[dict]:
    return [d for n, d in evs if n == name]


def start_review(use, *after_review) -> tuple[str, list]:
    use(call("analyze_findings", "c1"), *after_review)
    evs = events(client.post("/cases", json={"text": "Thai 22 tuần có thông liên thất, đa ối."}))
    return of(evs, "case")[0]["case_id"], evs


def seeded_case(use) -> tuple[str, FakeChat]:
    model = use()
    state = seed_case([row("f1", "thông liên thất", VSD, candidates=[cand(VSD, "VSD"), cand(ASD, "ASD")]),
                       row("f2", "đa ối", POLY)])
    state["ranking"] = case_ranking(state).model_dump(mode="json")
    app.state.graph.update_state(cases.config("case-s"), state, as_node="apply_findings_action")
    return "case-s", model


def test_new_case_description_pauses_for_review(use):
    case_id, evs = start_review(use)
    assert evs[0][0] == "case" and evs[-1][0] == "review"
    steps = {(s["key"], s["status"]): s for s in of(evs, "step")}
    tool = steps[("tool:analyze_findings", "running")]
    assert tool["id"] == "c1" and tool["kind"] == "tool"
    assert steps[("extract", "done")]["parent_id"] == "c1" and steps[("split", "done")]["summary"] == {"items": 2}
    assert steps[("check_choices", "done")]["summary"] == {"mapped": 2, "ambiguous": 0, "unmapped": 0}
    assert [f["finding_id"] for f in of(evs, "review")[0]["findings"]] == ["f1", "f2"]
    assert client.get(f"/cases/{case_id}").json()["status"] == "review"


def test_review_continue_ranks_and_answers(use):
    case_id, _ = start_review(use, AIMessage("Thai có thông liên nhĩ [HP:0001631]."))
    evs = events(client.post(f"/cases/{case_id}/review",
                             json={"action": "continue", "edits": [{"finding_id": "f1", "hpo_id": ASD}],
                                   "notes": {"f2": "AFI 28 cm"}}))
    kinds = [d["kind"] for d in of(evs, "artifact")]
    assert kinds == ["findings", "ranking"] and evs[-1][0] == "done"
    findings = of(evs, "artifact")[0]["data"]
    assert findings[0]["term"]["hpo_id"] == ASD and findings[0]["machine"]["term"]["hpo_id"] == VSD
    assert "".join(d["delta"] for d in of(evs, "text")) == "Thai có thông liên nhĩ [HP:0001631]."
    done = of(evs, "done")[0]
    assert [c["id"] for c in done["citations"]] == [ASD] and done["citations"][0]["label"]
    assert done["view"]["status"] == "idle" and done["view"]["ranking"]["suggestions"]
    assert [m["role"] for m in done["view"]["messages"]] == ["user", "assistant"]
    tool = next(s for s in of(evs, "step") if s["key"] == "tool:analyze_findings" and s["status"] == "done")
    assert tool["summary"] == {"findings": 2, "suggestions": 10}


def test_holdback_never_leaks_banned_phrase(use):
    bad = ("Thai có thông liên thất và đa ối, đây là hai biểu hiện thường gặp trong nhiều hội chứng khác nhau "
           "nên cần xem thêm; thai có khoảng 30% nguy cơ mắc bệnh này.")
    use(AIMessage(bad), AIMessage("Chưa đủ căn cứ để nói về khả năng mắc bệnh."))
    evs = events(client.post("/cases", json={"text": "Thai có nguy cơ không?"}))
    texts = of(evs, "text")
    first = texts[0]["message_id"]
    shown = "".join(d["delta"] for d in texts if d["message_id"] == first)
    assert shown and "30%" not in shown and "nguy cơ" not in shown
    assert of(evs, "answer_reset") == [{"message_id": first}]
    assert "".join(d["delta"] for d in texts if d["message_id"] != first) == "Chưa đủ căn cứ để nói về khả năng mắc bệnh."


def test_findings_action_returns_view_without_model(use):
    case_id, model = seeded_case(use)
    before = client.get(f"/cases/{case_id}").json()["ranking"]
    view = client.post(f"/cases/{case_id}/findings", json={"edits": [{"finding_id": "f2", "excluded": True}],
                                                           "notes": {"f1": "xem lại"}}).json()
    assert view["ranking"] != before and view["findings"][1]["excluded"] and view["findings"][0]["note"] == "xem lại"
    assert model.seen == []


def test_errors(use):
    def code(resp, status):
        assert resp.status_code == status, resp.text
        return resp.json()["detail"]["code"] if isinstance(resp.json()["detail"], dict) else "validation"

    assert code(client.get("/cases/nope"), 404) == "case_not_found"
    assert code(client.post("/cases", json={"text": "   "}), 422) == "validation"
    case_id, _ = start_review(use)
    assert code(client.post(f"/cases/{case_id}/messages", json={"text": "hỏi"}), 409) == "case_waiting_review"
    assert code(client.post(f"/cases/{case_id}/review", json={
        "action": "continue", "edits": [{"finding_id": "f2", "hpo_id": VSD}]}), 422) == "invalid_request"
    assert code(client.post(f"/cases/{case_id}/review", json={"action": "remap", "finding_id": "f9",
                                                              "phrase": "x"}), 422) == "invalid_request"
    seeded, _ = seeded_case(use)
    assert code(client.post(f"/cases/{seeded}/review", json={"action": "cancel"}), 409) == "case_not_waiting_review"
    state = seed_case([row("f1", "thông liên thất", VSD)])
    app.state.graph.update_state(cases.config("no-rank"), state, as_node="apply_findings_action")
    assert code(client.post("/cases/no-rank/findings", json={"edits": []}), 409) == "no_ranking"


def test_busy_and_status(use):
    case_id, _ = seeded_case(use)
    assert client.get(f"/cases/{case_id}").json()["status"] == "idle"
    cases.RUNNING.add(case_id)
    assert client.get(f"/cases/{case_id}").json()["status"] == "running"
    resp = client.post(f"/cases/{case_id}/messages", json={"text": "hỏi"})
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "case_busy"


def test_node_error_emits_error_event(use):
    def broken():
        raise RuntimeError("hỏng")
        yield  # noqa: unreachable - makes this a generator
    use()
    app.state.graph = build_graph(InMemorySaver(), FakeChat(messages=broken()))
    evs = events(client.post("/cases", json={"text": "Chào"}))
    assert evs[-1] == ("error", {"code": "internal_error", "message": "hỏng"})
    assert client.get(f"/cases/{evs[0][1]['case_id']}").json()["status"] == "idle"
