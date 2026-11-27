"""Redis checkpointer whose DeltaChannel history read takes a constant number of round trips (T139).

The default `BaseCheckpointSaver.aget_delta_channel_history` walks the parent chain one `aget_tuple` at a time.
Deep Agents keeps `messages` and `files` in DeltaChannels (snapshot every 50 updates; `files` is never written),
so every state read walked the whole thread: about 5 s for a 10-turn case over Railway's public proxy.
Taken unchanged from DBIZ Autonomous (src/platform/runtime/memory/checkpoints.py, commits 3b8c11ab, c8f7cb74):
page the checkpoint index for parent links, pipeline the channel checks, load the writes in one batch.
"""
import asyncio
import json
from collections.abc import Mapping, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import DeltaChannelHistory
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.checkpoint.redis.base import aexpire_with_retry, apersist_with_retry
from langgraph.checkpoint.redis.util import (
    from_storage_safe_id,
    to_storage_safe_id,
    to_storage_safe_str,
)
from redisvl.query import FilterQuery


class RedisSaver(AsyncRedisSaver):
    def get_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        if self.loop is None:
            raise RuntimeError(
                "RedisSaver must be set up before calling synchronous methods. "
                "Call `await saver.asetup()` or use `async with saver:` first."
            )
        try:
            if asyncio.get_running_loop() is self.loop:
                raise asyncio.InvalidStateError(
                    "Synchronous calls to RedisSaver are only allowed from a different thread. "
                    "Use `await saver.aget_delta_channel_history(...)` on its event loop."
                )
        except RuntimeError:
            pass
        return asyncio.run_coroutine_threadsafe(
            self.aget_delta_channel_history(config=config, channels=channels), self.loop
        ).result()

    async def aget_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        result: dict[str, DeltaChannelHistory] = {ch: {"writes": []} for ch in channels}
        if not channels:
            return result
        target = await self.aget_tuple(config)
        if target is None or target.parent_config is None:
            return result
        thread_id = target.config["configurable"]["thread_id"]
        checkpoint_ns = target.config["configurable"]["checkpoint_ns"]
        query = FilterQuery(
            filter_expression="@thread_id:{$thread_id} @checkpoint_ns:{$checkpoint_ns}",
            return_fields=["thread_id", "checkpoint_ns", "checkpoint_id", "parent_checkpoint_id"],
            num_results=64,
            sort_by=("checkpoint_id", "DESC"),
        )
        remaining = set(result)
        channels_by_checkpoint: dict[str, set[str]] = {}
        seeds: dict[str, str] = {}
        refresh = bool(self.ttl_config and self.ttl_config.get("refresh_on_read"))
        ttl_by_key: dict[str | bytes, int | None] = {}
        ttl_minutes = self.ttl_config.get("default_ttl") if self.ttl_config else None
        cursor = target.parent_config["configurable"]["checkpoint_id"]
        offset = 0
        exhausted = False
        while cursor and remaining:
            parents: dict[str, str] = {}
            if not exhausted:
                page = await self.checkpoints_index.search(
                    query.paging(offset, 64),
                    query_params={
                        "thread_id": to_storage_safe_id(thread_id),
                        "checkpoint_ns": to_storage_safe_str(checkpoint_ns),
                    },
                )
                for doc in page.docs:
                    if doc.thread_id != to_storage_safe_id(thread_id) or doc.checkpoint_ns != to_storage_safe_str(checkpoint_ns):
                        continue
                    parents[from_storage_safe_id(doc.checkpoint_id)] = from_storage_safe_id(
                        getattr(doc, "parent_checkpoint_id", "")
                    )
                offset += len(page.docs)
                exhausted = not page.docs or offset >= page.total
            chain: list[str] = []
            while cursor and cursor in parents and len(chain) < 64:
                chain.append(cursor)
                cursor = parents[cursor]
            if not chain:
                exhausted = True
                key = self._make_redis_checkpoint_key(thread_id, checkpoint_ns, cursor)
                header = await self._redis.json().get(key, "$.thread_id", "$.checkpoint_ns", "$.parent_checkpoint_id")
                if not header or header["$.thread_id"] != [to_storage_safe_id(thread_id)] or header["$.checkpoint_ns"] != [to_storage_safe_str(checkpoint_ns)]:
                    break
                chain.append(cursor)
                cursor = from_storage_safe_id(header["$.parent_checkpoint_id"][0])
            channels_to_check = list(remaining)
            pipeline = self._redis.pipeline(transaction=False)
            for checkpoint_id in chain:
                key = self._make_redis_checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
                for ch in channels_to_check:
                    pipeline.json().type(key, f"$.checkpoint.channel_values[{json.dumps(ch)}]")
            types = await pipeline.execute()
            width = len(channels_to_check)
            for i, checkpoint_id in enumerate(chain):
                row = types[i * width:(i + 1) * width]
                if row[0] is None:
                    cursor = ""
                    break
                channels_by_checkpoint[checkpoint_id] = remaining.copy()
                for ch, value_type in zip(channels_to_check, row):
                    if ch in remaining and value_type:
                        seeds[ch] = checkpoint_id
                        remaining.remove(ch)
                if not remaining:
                    break
        pipeline = self._redis.pipeline(transaction=False)
        for ch, checkpoint_id in seeds.items():
            key = self._make_redis_checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
            pipeline.json().get(key, f"$.checkpoint.channel_values[{json.dumps(ch)}]")
        for ch, value in zip(seeds, await pipeline.execute()):
            if value:
                result[ch]["seed"] = self._recursive_deserialize(value[0])
        if refresh and ttl_minutes is not None:
            pipeline = self._redis.pipeline(transaction=False)
            for checkpoint_id in channels_by_checkpoint:
                key = self._make_redis_checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
                pipeline.ttl(key)
                if self._key_registry:
                    pipeline.zrange(
                        self._key_registry.make_write_keys_zset_key(thread_id, checkpoint_ns, checkpoint_id), 0, -1
                    )
            ttl_values = await pipeline.execute()
            width = 1 + bool(self._key_registry)
            for i, checkpoint_id in enumerate(channels_by_checkpoint):
                row = ttl_values[i * width:(i + 1) * width]
                if row[0] <= 0:
                    continue
                key = self._make_redis_checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
                for key in [key, *(row[-1] if self._key_registry else [])]:
                    ttl_by_key[key] = None if ttl_minutes == -1 else int(ttl_minutes * 60)
                if self._key_registry:
                    ttl_by_key[self._key_registry.make_write_keys_zset_key(thread_id, checkpoint_ns, checkpoint_id)] = int(
                        ttl_minutes * 60
                    )
        pipeline = self._redis.pipeline(transaction=False)
        for key, ttl_seconds in ttl_by_key.items():
            if ttl_seconds is None:
                pipeline.persist(key)
            else:
                pipeline.expire(key, ttl_seconds)
        try:
            ttl_results = await pipeline.execute(raise_on_error=False)
        except Exception as exc:
            ttl_results = [exc] * len(ttl_by_key)
        for (key, ttl_seconds), ttl_result in zip(ttl_by_key.items(), ttl_results):
            if isinstance(ttl_result, Exception):
                if ttl_seconds is None:
                    await apersist_with_retry(self._redis, key)
                else:
                    await aexpire_with_retry(self._redis, key, ttl_seconds)
        writes = await self._abatch_load_pending_writes(
            [(thread_id, checkpoint_ns, checkpoint_id) for checkpoint_id in channels_by_checkpoint]
        )
        for checkpoint_id, checkpoint_channels in reversed(channels_by_checkpoint.items()):
            for write in writes.get((thread_id, checkpoint_ns, checkpoint_id), []):
                if write[1] in checkpoint_channels:
                    result[write[1]]["writes"].append(write)
        return result
