"""Opt-in replay of model answers for test runs: `LLM_CACHE=<file>` stores every chat model answer on disk, keyed by
its exact input (LangChain's cache key: the messages without their ids, plus the model and its parameters).

A rerun whose model inputs did not change costs no model request, e.g. `make e2e` after a change to storage or the
API. Off unless the variable is set: the app and the evals always call the models. A hit reports the token usage
of the recorded answer.
"""

import sqlite3
import threading
from pathlib import Path

from langchain_core.caches import BaseCache
from langchain_core.load import dumps, loads


class SQLiteCache(BaseCache):
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()   # the async API runs lookups in worker threads
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS answer (prompt TEXT, llm TEXT, value TEXT, PRIMARY KEY (prompt, llm))")

    def lookup(self, prompt: str, llm_string: str):
        with self.lock:
            row = self.conn.execute("SELECT value FROM answer WHERE prompt = ? AND llm = ?", (prompt, llm_string)).fetchone()
        return loads(row[0]) if row else None

    def update(self, prompt: str, llm_string: str, return_val) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO answer VALUES (?, ?, ?)", (prompt, llm_string, dumps(return_val)))

    def clear(self, **kwargs) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM answer")
