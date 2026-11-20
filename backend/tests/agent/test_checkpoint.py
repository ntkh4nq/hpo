"""Real Redis checkpointer (T92; docs/agent-impl-plan.md task 4). Skipped when Redis at REDIS_URL does not answer."""

import asyncio
import os
import uuid

import pytest
import redis
import redis.asyncio as aredis
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.types import Command

from agent.graph import build_graph
from agent.state import CONFIG
from tests.fakes import FakeChat, call, cand, fake_phase3, item

URL = os.environ.get("REDIS_URL", "redis://localhost:6380")
TTL = {"default_ttl": CONFIG["case_ttl_minutes"], "refresh_on_read": True}


def redis_up() -> bool:
    try:
        return redis.Redis.from_url(URL, socket_connect_timeout=1).ping()
    except redis.exceptions.RedisError:
        return False


pytestmark = pytest.mark.skipif(not redis_up(), reason=f"no Redis at {URL} (make up)")


@pytest.fixture
def phase3(monkeypatch):
    return fake_phase3(monkeypatch, [item("thông liên thất")],
                       {"thông liên thất": [cand("HP:0001629", "Ventricular septal defect")]})


def test_resume_with_new_saver_instance_and_ttl(phase3):
    case_id = f"test-{uuid.uuid4()}"
    cfg = {"configurable": {"thread_id": case_id}}

    async def first_request():
        async with AsyncRedisSaver.from_conn_string(URL, ttl=TTL) as saver:
            graph = build_graph(saver, FakeChat(messages=iter([call("analyze_findings", "c1")])))
            await graph.ainvoke({"messages": [HumanMessage("Thai có thông liên thất.")]}, cfg)
            return (await graph.aget_state(cfg)).interrupts

    async def second_request():
        async with AsyncRedisSaver.from_conn_string(URL, ttl=TTL) as saver:
            graph = build_graph(saver, FakeChat(messages=iter([AIMessage("Xong.")])))
            await graph.ainvoke(Command(resume={"action": "continue"}), cfg)
            return (await graph.aget_state(cfg)).values

    async def ttls():
        r = aredis.from_url(URL)
        try:
            return [await r.ttl(k) async for k in r.scan_iter(f"*{case_id}*")]
        finally:
            await r.aclose()

    interrupts = asyncio.run(first_request())
    assert interrupts and interrupts[0].value["type"] == "review_findings"
    values = asyncio.run(second_request())
    assert values["findings"][0]["phrase"] == "thông liên thất" and values["messages"][-1].text == "Xong."
    assert phase3.calls["split"] == 1
    found = asyncio.run(ttls())
    assert found and all(0 < t <= CONFIG["case_ttl_minutes"] * 60 for t in found)
