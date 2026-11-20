"""Supervisor wiring and the answer guard (docs/agent-impl-plan.md task 3). Fake model; real KB for tools."""

import asyncio

import pytest
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent.state import CONFIG, render_case
from agent.supervisor import TOOLS, build_supervisor
from tests.fakes import FakeChat, call, row, seed_case

VSD, MICRO = "HP:0001629", "HP:0000252"
CFG = {"configurable": {"thread_id": "sup"}}


def run(*script, state: dict | None = None, messages=None):
    """Run one supervisor turn; return (final state, model, custom stream events)."""
    model = FakeChat(messages=iter(script))
    sup = build_supervisor(model)
    graph_input = {**(state or {}), "messages": messages or [HumanMessage("Câu hỏi của bác sĩ")]}

    async def go():
        events = [d async for d in sup.astream(graph_input, CFG, stream_mode="custom")]
        return sup.get_state(CFG).values if sup.checkpointer else None, events

    sup.checkpointer = InMemorySaver()
    values, events = asyncio.run(go())
    return values, model, events


def ai_texts(values) -> list[str]:
    return [m.text for m in values["messages"] if m.type == "ai" and m.text]


def test_tool_list():
    _, model, _ = run(AIMessage("Chào bác sĩ."))
    assert model.bound_tools == sorted([t.name for t in TOOLS] + ["write_todos"])


def test_write_todos_updates_todos():
    todos = [{"content": "Đọc biểu hiện", "status": "in_progress"}, {"content": "Xếp hạng", "status": "pending"}]
    values, _, _ = run(call("write_todos", "c1", todos=todos), AIMessage("Xong."))
    assert values["todos"] == todos


def test_case_prompt_has_latest_case():
    state = seed_case([row("f1", "thông liên thất", VSD)])
    _, model, _ = run(AIMessage("Xong."), state=state)
    system = model.seen[0][0]
    assert system.type == "system" and render_case(state) in system.text


def test_guard_unknown_code_retries_once():
    values, model, events = run(AIMessage("Có thể là [HP:9999999]."), AIMessage("Thai có thông liên thất [HP:0001629]."),
                                state=seed_case([row("f1", "thông liên thất", VSD)]))
    assert [e["passed"] for e in events if e["type"] == "check_answer"] == [False, True]
    assert ai_texts(values) == ["Thai có thông liên thất [HP:0001629]."]
    guard = [m for m in values["messages"] if m.type == "human" and m.name == "answer_guard"]
    assert len(guard) == 1 and "HP:9999999" in guard[0].text
    assert len(model.seen) == 2


def test_guard_second_failure_uses_fallback():
    values, _, events = run(AIMessage("[HP:9999999]"), AIMessage("Vẫn [HP:9999998]."))
    checks = [e for e in events if e["type"] == "check_answer"]
    assert [e["passed"] for e in checks] == [False, False] and checks[-1]["replacement"] == CONFIG["fallback_answer"]
    last = values["messages"][-1]
    assert last.type == "ai" and last.text == CONFIG["fallback_answer"] and not last.tool_calls


def test_guard_banned_phrase():
    values, _, events = run(AIMessage("Thai có khoảng 30% nguy cơ mắc bệnh."), AIMessage("Hạng 1 là bệnh có nhiều biểu hiện khớp nhất."))
    assert [e["passed"] for e in events if e["type"] == "check_answer"] == [False, True]
    assert ai_texts(values) == ["Hạng 1 là bệnh có nhiều biểu hiện khớp nhất."]


def test_guard_allows_tool_and_earlier_codes():
    values, _, events = run(call("get_hpo", "c1", hpo_id=MICRO), AIMessage("Đầu nhỏ [HP:0000252], như đã nói [OMIM:214800]."),
                            messages=[HumanMessage("CHARGE?"), AIMessage("CHARGE là [OMIM:214800]."), HumanMessage("Đầu nhỏ là gì?")])
    assert all(e["passed"] for e in events if e["type"] == "check_answer")
    assert ai_texts(values)[-1] == "Đầu nhỏ [HP:0000252], như đã nói [OMIM:214800]."


def test_guard_rejects_code_only_typed_by_doctor():
    _, _, events = run(AIMessage("Đó là [HP:0000252]."), AIMessage("Cần tra trước."),
                       messages=[HumanMessage("HP:0000252 là gì?")])
    assert [e["passed"] for e in events if e["type"] == "check_answer"] == [False, True]


def test_model_call_limit_raises():
    script = [call("get_hpo", f"c{i}", hpo_id=VSD) for i in range(CONFIG["max_model_calls"] + 1)]
    with pytest.raises(ModelCallLimitExceededError):
        run(*script)
