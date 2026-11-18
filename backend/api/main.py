from fastapi import FastAPI

from core.db import connect

app = FastAPI(title="HPO assistant")


@app.get("/health")
def health() -> dict:
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}
