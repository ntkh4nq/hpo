"""Case list, case data, turn record and explanations cache in Postgres (T116, T139; db/migrations/0005, 0006).

Plain synchronous psycopg: each call is one short query; async routes run them with asyncio.to_thread.
The graph checkpoint lives in Redis with a TTL (api/main.py); `state` keeps what is needed to rebuild it.
"""

import uuid
from collections.abc import Mapping
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from agent.state import SAVED_KEYS
from core.db import connect


def case_uuid(case_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(case_id)
    except (ValueError, TypeError):
        return None


def default_title(text: str, limit: int) -> str:
    """First line of the doctor's first message, cut at a word boundary (the doctor renames it)."""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = " ".join(line.split())
    if len(line) <= limit:
        return line
    cut = line[:limit].rsplit(" ", 1)[0] or line[:limit]
    return cut.rstrip(" ,;:.") + "…"


def create(case_id: str, title: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO case_record (case_id, title) VALUES (%s, %s)", (case_id, title))


def get(case_id: str) -> dict | None:
    if case_uuid(case_id) is None:
        return None
    with connect(row_factory=dict_row) as conn:
        return conn.execute("SELECT case_id::text, title, created_at, updated_at FROM case_record WHERE case_id = %s",
                            (case_id,)).fetchone()


def list_cases() -> list[dict]:
    """Every case with the outcome of its last turn (None: no turn finished), read without touching Redis."""
    with connect(row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT c.case_id::text, c.title, c.created_at, c.updated_at, "
            "(SELECT t.outcome FROM case_turn t WHERE t.case_id = c.case_id ORDER BY t.turn_no DESC LIMIT 1) "
            "AS last_outcome FROM case_record c ORDER BY c.updated_at DESC").fetchall()


def save_state(case_id: str, values: Mapping) -> None:
    with connect() as conn:
        conn.execute("UPDATE case_record SET state = %s WHERE case_id = %s",
                     (Jsonb({k: values[k] for k in SAVED_KEYS if k in values}), case_id))


def get_state(case_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT state FROM case_record WHERE case_id = %s", (case_id,)).fetchone()
    return (row[0] if row else None) or {}


def rename(case_id: str, title: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE case_record SET title = %s, updated_at = now() WHERE case_id = %s", (title, case_id))


def delete(case_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM case_record WHERE case_id = %s", (case_id,))


def next_turn_no(case_id: str) -> int:
    """The number the case's next turn will get (a case runs one request at a time, so it cannot change meanwhile)."""
    with connect() as conn:
        return conn.execute("SELECT coalesce(max(turn_no), 0) + 1 FROM case_turn WHERE case_id = %s",
                            (case_id,)).fetchone()[0]


def start_turn(case_id: str, route: str, input_: dict) -> int:
    with connect() as conn:
        return conn.execute(
            "INSERT INTO case_turn (case_id, turn_no, route, input) "
            "SELECT %s, coalesce(max(turn_no), 0) + 1, %s, %s FROM case_turn WHERE case_id = %s RETURNING turn_no",
            (case_id, route, Jsonb(input_), case_id)).fetchone()[0]


def finish_turn(case_id: str, turn_no: int, items: list[dict], outcome: str, citations: list[dict]) -> None:
    with connect() as conn:
        conn.execute("UPDATE case_turn SET items = %s, outcome = %s, citations = %s, finished_at = now() "
                     "WHERE case_id = %s AND turn_no = %s",
                     (Jsonb(items), outcome, Jsonb(citations), case_id, turn_no))
        conn.execute("UPDATE case_record SET updated_at = now() WHERE case_id = %s", (case_id,))


def turns(case_id: str) -> list[dict[str, Any]]:
    with connect(row_factory=dict_row) as conn:
        return conn.execute("SELECT turn_no, route, input, items, outcome, citations FROM case_turn "
                            "WHERE case_id = %s ORDER BY turn_no", (case_id,)).fetchall()


def cache_get(case_id: str, key: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT result FROM explanation_cache WHERE case_id = %s AND input_key = %s",
                           (case_id, key)).fetchone()
    return row[0] if row else None


def cache_put(case_id: str, key: str, result: dict) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO explanation_cache (case_id, input_key, result) VALUES (%s, %s, %s) "
                     "ON CONFLICT (case_id, input_key) DO UPDATE SET result = EXCLUDED.result", (case_id, key, Jsonb(result)))
