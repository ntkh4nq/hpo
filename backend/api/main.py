from fastapi import FastAPI

from api.documents import router as documents_router
from core.db import connect

app = FastAPI(title="HPO assistant")
app.include_router(documents_router)


@app.get("/health")
def health() -> dict:
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
