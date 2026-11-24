"""Case API and SSE (docs/agent-impl-plan.md task 5; persistence docs/followup-plan.md §A). TestClient without
`with`, so the lifespan does not run; each test puts a graph on InMemorySaver with a fake model on app.state. The
case list, turns and explanations cache use the real Postgres tables; every case a test creates is deleted after it."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent.graph import build_graph
from agent.state import CONFIG
from agent.tools import case_ranking
from api import case_store, cases
from api.main import app
from tests.fakes import FakeChat, call, cand, fake_phase3, item, row, seed_case

VSD, ASD, POLY = "HP:0001629", "HP:0001631", "HP:0001561"
client = TestClient(app)


CREATED: list[str] = []


@pytest.fixture(autouse=True)
def cleanup(monkeypatch):
    """Record every case created through the store; delete them after the test."""
    real = case_store.create

    def create(case_id, title):
        CREATED.append(case_id)
        real(case_id, title)
    monkeypatch.setattr(case_store, "create", create)
    yield
    while CREATED:
        case_store.delete(CREATED.pop())


def put_case(state: dict, case_id: str | None = None) -> str:
    """A listed case with this graph state, as if its turns had run."""
    case_id = case_id or str(uuid.uuid4())
    case_store.create(case_id, "test")
    app.state.graph.update_state(cases.config(case_id), state, as_node="apply_findings_action")
    return case_id


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
    return put_case(state), model


def test_new_case_description_pauses_for_review(use):
    case_id, evs = start_review(use)
    assert evs[0][0] == "case" and evs[-1][0] == "review"
    steps = {(s["key"], s["status"]): s for s in of(evs, "step")}
    tool = steps[("tool:analyze_findings", "running")]
    assert tool["id"] == "c1" and tool["kind"] == "tool"
    assert steps[("extract", "done")]["parent_id"] == "c1" and steps[("split", "done")]["summary"] == {"items": 2}
    assert {steps[(k, "done")]["parent_id"] for k in ("split", "check_choices")} == {steps[("extract", "done")]["id"]}
    assert steps[("check_choices", "done")]["summary"] == {"mapped": 2, "ambiguous": 0, "unmapped": 0}
    assert [f["finding_id"] for f in of(evs, "review")[0]["findings"]] == ["f1", "f2"]
    assert client.get(f"/cases/{case_id}").json()["status"] == "review"


def test_case_view_hides_case_events(use):
    """T128: the notice written after a card edit is not a doctor message."""
    case_id, _ = seeded_case(use)
    client.post(f"/cases/{case_id}/findings", json={"edits": [{"finding_id": "f1", "assertion": "SUSPECTED"}], "notes": {}})
    values = app.state.graph.get_state(cases.config(case_id)).values
    assert any(m.name == "case_event" for m in values["messages"])
    assert client.get(f"/cases/{case_id}").json()["messages"] == []


def test_case_waiting_review_returns_its_findings(use):
    """T127: a reopened case paused at review shows the findings of the paused analysis, not []."""
    case_id, evs = start_review(use)
    got = client.get(f"/cases/{case_id}").json()
    assert got["status"] == "review"
    assert got["findings"] == of(evs, "review")[0]["findings"] and len(got["findings"]) == 2


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
    no_rank = put_case(seed_case([row("f1", "thông liên thất", VSD)]))
    assert code(client.post(f"/cases/{no_rank}/findings", json={"edits": []}), 409) == "no_ranking"
    assert code(client.get(f"/cases/{uuid.uuid4()}"), 404) == "case_not_found"


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


# ---- explanations (docs/evidence-impl-plan.md task 3) -------------------------------------------
@pytest.fixture
def explain(monkeypatch):
    """Real prepare/assemble on fake passages (one per pair); run_model counted and scripted."""
    import time

    from agent import explanations
    from tests.agent.test_explanations import passage

    calls = {"prepare": 0, "model": 0, "sleep": 0.0, "failed": False}
    real_prepare = explanations.prepare

    def prepare(values):
        calls["prepare"] += 1
        return real_prepare(values)

    def run_model(prepared):
        calls["model"] += 1
        time.sleep(calls["sleep"])
        return explanations.ModelResult(request_failed=calls["failed"], created_at=explanations.now(),
                                        usage=explanations.Usage(requests=1))

    monkeypatch.setattr(explanations, "find_passages_many",
                        lambda reqs, k=5, **kw: [[passage(100 + i)] for i, _ in enumerate(reqs)])
    monkeypatch.setattr(explanations, "find_overviews", lambda ids, sources, **kw: {})
    monkeypatch.setattr(explanations, "prepare", prepare)
    monkeypatch.setattr(explanations, "run_model", run_model)
    return calls


def test_explanations_errors(use, explain):
    assert client.post("/cases/nope/explanations").json()["detail"]["code"] == "case_not_found"
    case_id, _ = start_review(use)
    r = client.post(f"/cases/{case_id}/explanations")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "case_waiting_review"
    seeded, _ = seeded_case(use)
    cases.RUNNING.add(seeded)
    assert client.post(f"/cases/{seeded}/explanations").json()["detail"]["code"] == "case_busy"
    cases.RUNNING.discard(seeded)
    empty = put_case(seed_case([row("f1", "đa ối", POLY)]))
    r = client.post(f"/cases/{empty}/explanations")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_ranking"


def test_explanations_cache_hit_skips_model(use, explain):
    case_id, _ = seeded_case(use)
    first = client.post(f"/cases/{case_id}/explanations").json()
    second = client.post(f"/cases/{case_id}/explanations").json()
    assert explain["prepare"] == 2 and explain["model"] == 1
    assert first["cached"] is False and first["usage"]["requests"] == 1
    assert second["cached"] is True and second["usage"]["requests"] == 0 and second["items"] == first["items"]
    assert first["explanation_key"] == second["explanation_key"]
    assert case_store.cache_get(case_id, first["explanation_key"]) is not None


def test_explanations_concurrent_identical_calls_share_one_request(use, explain):
    import asyncio

    import httpx

    case_id, _ = seeded_case(use)
    explain["sleep"] = 0.3

    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            return await asyncio.gather(*[c.post(f"/cases/{case_id}/explanations") for _ in range(3)])

    responses = asyncio.run(main())
    assert explain["model"] == 1 and {r.status_code for r in responses} == {200}
    assert len({r.json()["explanation_key"] for r in responses}) == 1 and not cases.EXPLAINING


def test_explanations_not_cacheable_not_stored(use, explain):
    case_id, _ = seeded_case(use)
    explain["failed"] = True
    first = client.post(f"/cases/{case_id}/explanations").json()
    assert {p["status"] for i in first["items"] for p in i["pairs"]} == {"failed"}
    assert case_store.cache_get(case_id, first["explanation_key"]) is None
    client.post(f"/cases/{case_id}/explanations")
    assert explain["model"] == 2


def test_note_change_gives_new_explanations(use, explain):
    case_id, _ = seeded_case(use)
    before = client.post(f"/cases/{case_id}/explanations").json()["explanation_key"]
    assert client.post(f"/cases/{case_id}/findings", json={"edits": [], "notes": {"f1": "Cần xem lại."}}).status_code == 200
    after = client.post(f"/cases/{case_id}/explanations").json()["explanation_key"]
    assert after != before and explain["model"] == 2


def test_case_view_has_no_explanation_key(use):
    case_id, _ = seeded_case(use)
    assert "explanation_key" not in client.get(f"/cases/{case_id}").json()


# ---- persistence (docs/followup-plan.md §A, T116) ------------------------------------------------
def test_new_case_is_listed_with_default_title_and_renamed(use):
    case_id, _ = start_review(use)
    listed = {c["case_id"]: c for c in client.get("/cases").json()}
    assert listed[case_id]["title"] == "Thai 22 tuần có thông liên thất, đa ối." and listed[case_id]["status"] == "review"
    view = client.patch(f"/cases/{case_id}", json={"title": "  Ca   tim  "}).json()
    assert view["title"] == "Ca tim" and client.get(f"/cases/{case_id}").json()["title"] == "Ca tim"
    assert client.patch(f"/cases/{case_id}", json={"title": "x" * (CONFIG["case_title_chars"] + 1)}).status_code == 422


def test_default_title_cuts_at_word():
    assert case_store.default_title("\n  Thai 22 tuần, thông liên thất lớn\nDòng hai", 20) == "Thai 22 tuần, thông…"
    assert case_store.default_title("ngắn", 20) == "ngắn"


def test_turns_record_what_the_doctor_saw(use):
    case_id, _ = start_review(use, AIMessage("Thai có thông liên nhĩ [HP:0001631]."))
    evs = events(client.post(f"/cases/{case_id}/review", json={"action": "continue", "notes": {},
                                                                 "edits": [{"finding_id": "f1", "hpo_id": ASD}]}))
    assert evs[-1][0] == "done", evs[-1]
    turns = client.get(f"/cases/{case_id}").json()["turns"]
    assert [(t["route"], t["outcome"]) for t in turns] == [("message", "review"), ("review", "done")]
    assert turns[0]["input"] == {"text": "Thai 22 tuần có thông liên thất, đa ối."}
    assert turns[1]["input"]["action"] == "continue"
    first_steps = [i["step"] for i in turns[0]["items"] if i["kind"] == "step"]
    assert first_steps[0]["key"] == "tool:analyze_findings" and first_steps[0]["status"] == "running"  # paused
    texts = [i["text"] for i in turns[1]["items"] if i["kind"] == "text"]
    assert texts == ["Thai có thông liên nhĩ [HP:0001631]."] and turns[1]["citations"][0]["id"] == ASD


def test_guard_reset_and_failed_turn_are_recorded(use):
    bad = "Thai có khoảng 30% nguy cơ mắc bệnh này, đây là con số tính từ nhiều nguồn khác nhau trong dữ liệu."
    use(AIMessage(bad), AIMessage("Chưa đủ căn cứ."))
    case_id = of(events(client.post("/cases", json={"text": "Thai có nguy cơ không?"})), "case")[0]["case_id"]
    turn = client.get(f"/cases/{case_id}").json()["turns"][0]
    assert [i["text"] for i in turn["items"] if i["kind"] == "text"] == ["Chưa đủ căn cứ."]

    def broken():
        raise RuntimeError("hỏng")
        yield  # noqa: unreachable - makes this a generator
    app.state.graph = build_graph(InMemorySaver(), FakeChat(messages=broken()))
    failed = of(events(client.post("/cases", json={"text": "Chào"})), "case")[0]["case_id"]
    view = client.get(f"/cases/{failed}").json()
    assert view["status"] == "idle" and view["findings"] == [] and view["turns"][0]["outcome"] == "error"


def test_delete_removes_everything(use, explain):
    case_id, _ = seeded_case(use)
    key = client.post(f"/cases/{case_id}/explanations").json()["explanation_key"]
    cases.RUNNING.add(case_id)
    assert client.delete(f"/cases/{case_id}").status_code == 409
    cases.RUNNING.discard(case_id)
    assert client.delete(f"/cases/{case_id}").status_code == 204
    assert client.get(f"/cases/{case_id}").status_code == 404
    assert case_store.cache_get(case_id, key) is None and case_store.turns(case_id) == []
    assert not app.state.graph.get_state(cases.config(case_id)).values
    assert case_id not in {c["case_id"] for c in client.get("/cases").json()}


def test_reanalysis_carries_matching_notes_and_archives_the_rest(use, monkeypatch):
    """T132: the same sentence keeps its note on re-analysis; a note whose sentence is gone goes to the archive (T116)."""
    case_id, _ = start_review(use, AIMessage("Đã xếp hạng."), call("analyze_findings", "c2"), AIMessage("Xong."),
                              call("analyze_findings", "c3"), AIMessage("Xong."))
    events(client.post(f"/cases/{case_id}/review", json={"action": "continue", "edits": [], "notes": {"f2": "AFI 28"}}))
    assert client.get(f"/cases/{case_id}").json()["notes_archive"] == []
    evs = events(client.post(f"/cases/{case_id}/messages", json={"text": "Thai 22 tuần có thông liên thất, đa ối. Thêm câu."}))
    review = of(evs, "review")[0]
    assert review["findings"][1]["note"] == "AFI 28" and review["carry_over"] == {"applied": 1, "not_applied": 0}
    events(client.post(f"/cases/{case_id}/review", json={"action": "continue", "edits": [], "notes": {"f2": "AFI 28"}}))
    assert client.get(f"/cases/{case_id}").json()["notes_archive"] == []
    fake_phase3(monkeypatch, [item("thông liên nhĩ")], {"thông liên nhĩ": [cand(ASD, "Atrial septal defect")]})
    events(client.post(f"/cases/{case_id}/messages", json={"text": "Mô tả mới: thông liên nhĩ."}))
    events(client.post(f"/cases/{case_id}/review", json={"action": "continue", "edits": [], "notes": {}}))
    archive = client.get(f"/cases/{case_id}").json()["notes_archive"]
    assert len(archive) == 1 and [f["note"] for f in archive[0]["findings"]] == ["AFI 28"]
    assert archive[0]["description"]


def test_card_edit_is_recorded_with_ranking_change(use):
    case_id, _ = seeded_case(use)
    client.post(f"/cases/{case_id}/findings", json={"edits": [], "notes": {"f1": "xem"}})
    client.post(f"/cases/{case_id}/findings", json={"edits": [{"finding_id": "f2", "excluded": True}], "notes": {}})
    turns = client.get(f"/cases/{case_id}").json()["turns"]
    assert [(t["route"], t["input"]["ranking_changed"]) for t in turns] == [("findings", False), ("findings", True)]


def test_remap_can_carry_the_doctors_conclusion(use):
    """T118: "Bất thường → đa ối" on a measurement is one remap with the assertion."""
    case_id, _ = start_review(use)
    evs = events(client.post(f"/cases/{case_id}/review",
                             json={"action": "remap", "finding_id": "f1", "phrase": "đa ối", "assertion": "SUSPECTED"}))
    f1 = of(evs, "review")[0]["findings"][0]
    assert (f1["phrase"], f1["assertion"], f1["machine"]["assertion"]) == ("đa ối", "SUSPECTED", "PRESENT")
