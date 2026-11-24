import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agent.graph import build_graph
from api.cases import router as cases_router
from api.documents import router as documents_router
from core.db import connect
from ranking.index import load_index


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Case graph on the Postgres checkpointer: cases stay until the doctor deletes them (T116). `setup()` creates
    the checkpointer's own tables; the case list and turn record are in db/migrations/0005_cases.sql."""
    kwargs = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}   # what the saver requires
    async with AsyncConnectionPool(os.environ["DATABASE_URL"], kwargs=kwargs, open=False) as pool:
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
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
