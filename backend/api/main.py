import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from agent.graph import build_graph
from agent.state import CONFIG
from api.cases import router as cases_router
from api.documents import router as documents_router
from core.db import connect
from ranking.index import load_index


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Case graph on the Redis checkpointer (T92); entering the saver runs its setup (T98)."""
    ttl = {"default_ttl": CONFIG["case_ttl_minutes"], "refresh_on_read": True}
    async with AsyncRedisSaver.from_conn_string(os.environ["REDIS_URL"], ttl=ttl) as saver:
        load_index()   # warm the ranking index (about 4 s, T51) before the first request
        app.state.graph = build_graph(saver)
        yield


app = FastAPI(title="HPO assistant", lifespan=lifespan)
app.include_router(documents_router)
app.include_router(cases_router)


@app.get("/health")
def health() -> dict:
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
