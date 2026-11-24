"""Real Postgres checkpointer (T116; was Redis, T92): a paused case resumes on a new saver and pool, as after an API
restart. Uses a test thread id and deletes it afterwards."""

import asyncio
import os
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

import pytest

from agent.graph import build_graph
from tests.fakes import FakeChat, call, cand, fake_phase3, item

KWARGS = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}


@pytest.fixture
def phase3(monkeypatch):
    return fake_phase3(monkeypatch, [item("thông liên thất")],
                       {"thông liên thất": [cand("HP:0001629", "Ventricular septal defect")]})


async def _with_saver(fn):
    async with AsyncConnectionPool(os.environ["DATABASE_URL"], kwargs=KWARGS, open=False) as pool:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
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
