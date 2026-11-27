"""Real Redis checkpointer (T139, core/redis_saver.py): a paused case resumes on a new saver, as after an API restart;
the fast history read equals the default one. Uses test thread ids and deletes them afterwards."""

import asyncio
import os
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

import pytest

from agent.graph import build_graph
from core.redis_saver import RedisSaver
from tests.fakes import FakeChat, call, cand, fake_phase3, item


@pytest.fixture
def phase3(monkeypatch):
    return fake_phase3(monkeypatch, [item("thông liên thất")],
                       {"thông liên thất": [cand("HP:0001629", "Ventricular septal defect")]})


async def _with_saver(fn):
    async with RedisSaver.from_conn_string(os.environ["REDIS_URL"]) as saver:
        return await fn(saver)


def test_resume_with_new_saver_instance(phase3):
    case_id = str(uuid.uuid4())
    cfg = {"configurable": {"thread_id": case_id}}

    async def first(saver):
        graph = build_graph(saver, FakeChat(messages=iter([call("analyze_findings", "c1")])))
        await graph.ainvoke({"messages": [HumanMessage("Thai có thông liên thất.")]}, cfg)
        return (await graph.aget_state(cfg)).interrupts

    async def second(saver):
        graph = build_graph(saver, FakeChat(messages=iter([AIMessage("Xong.")])))
        await graph.ainvoke(Command(resume={"action": "continue"}), cfg)
        return (await graph.aget_state(cfg)).values

    async def delete(saver):
        await saver.adelete_thread(case_id)
        return (await build_graph(saver).aget_state(cfg)).values

    try:
        interrupts = asyncio.run(_with_saver(first))
        assert interrupts and interrupts[0].value["type"] == "review_findings"
        values = asyncio.run(_with_saver(second))
        assert values["findings"][0]["phrase"] == "thông liên thất" and values["messages"][-1].text == "Xong."
        assert phase3.calls["split"] == 1
    finally:
        assert not asyncio.run(_with_saver(delete))


def test_fast_delta_history_equals_default():
    """core/redis_saver.py reads DeltaChannel history in a few round trips; it must return what the default
    parent-chain walk of BaseCheckpointSaver returns (messages snapshot only every 50 updates, files never written)."""
    case_id = str(uuid.uuid4())
    cfg = {"configurable": {"thread_id": case_id}}

    async def run(saver):
        for i in range(3):
            graph = build_graph(saver, FakeChat(messages=iter([AIMessage(f"answer {i}")])))
            await graph.ainvoke({"messages": [HumanMessage(f"question {i}")]}, cfg)
        tup = await saver.aget_tuple({"configurable": {"thread_id": case_id, "checkpoint_ns": ""}})
        args = {"config": tup.config, "channels": ["messages", "files"]}
        fast = await saver.aget_delta_channel_history(**args)
        slow = await BaseCheckpointSaver.aget_delta_channel_history(saver, **args)
        await saver.adelete_thread(case_id)
        return fast, slow

    def norm(history):
        return {ch: ([(w[0], w[1], repr(w[2])) for w in h["writes"]], repr(h.get("seed"))) for ch, h in history.items()}

    fast, slow = asyncio.run(_with_saver(run))
    assert len(fast["messages"]["writes"]) >= 6 and norm(fast) == norm(slow)
