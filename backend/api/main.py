import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis import exceptions as redis_errors

from core import tracing

tracing.setup("app")   # before any other module uses Langfuse (core/tracing.py)

from agent.graph import build_graph  # noqa: E402
from agent.state import CONFIG  # noqa: E402
from api.cases import router as cases_router  # noqa: E402
from api.documents import router as documents_router  # noqa: E402
from core.db import connect  # noqa: E402
from core.redis_saver import RedisSaver  # noqa: E402
from ranking.index import load_index  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Case graph on the Redis checkpointer with a TTL; the case data stays in Postgres and an expired thread is
    rebuilt from it (T139, agent/rehydrate.py). Entering the saver runs its setup (indexes)."""
    ttl = {"default_ttl": CONFIG["case_ttl_minutes"], "refresh_on_read": CONFIG["case_ttl_refresh_on_read"]}
    async with RedisSaver.from_conn_string(os.environ["REDIS_URL"], ttl=ttl, connection_args=redis_args()) as saver:
        load_index()   # warm the ranking index (about 4 s, T51) before the first request
        app.state.graph = build_graph(saver)
        yield


def redis_args() -> dict:
    """Client options for Redis behind Railway's public proxy (agent/config.toml)."""
    return {"health_check_interval": CONFIG["redis_health_check_seconds"], "socket_keepalive": True,
            "retry": Retry(ExponentialBackoff(), CONFIG["redis_retries"]),
            "retry_on_error": [redis_errors.ConnectionError, redis_errors.TimeoutError]}


app = FastAPI(title="HPO assistant", lifespan=lifespan)
app.include_router(documents_router)
app.include_router(cases_router)


@app.get("/health")
def health() -> dict:
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
