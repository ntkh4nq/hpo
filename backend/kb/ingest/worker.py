"""Ingestion worker (docs/ingestion-plan.md §8, T63).

Assumes a single worker runs at a time: on start, jobs left 'running' by a stopped worker go back to 'queued'
(only the given run's jobs with --run).

    python -m kb.ingest.worker [--once] [--run RUN_ID]
"""

import argparse
import json
import time
import traceback

from core.db import connect
from kb.ingest import pipeline
from kb.ingest.config import INGEST
from kb.ingest.model import PermanentIngestError

TAKE = """UPDATE ingest_job SET state = 'running', attempts = attempts + 1, started_at = now()
          WHERE job_id = (SELECT job_id FROM ingest_job
                          WHERE state = 'queued' AND available_at <= now() {run_filter}
                          ORDER BY available_at, job_id FOR UPDATE SKIP LOCKED LIMIT 1)
          RETURNING job_id, run_id, kind, source, source_ref, raw_path, strategy, doc_id, attempts"""


def take(conn, run_id: int | None) -> pipeline.Job | None:
    row = conn.execute(TAKE.format(run_filter="AND run_id = %(run_id)s" if run_id else ""),
                       {"run_id": run_id}).fetchone()
    conn.commit()
    return pipeline.Job(*row) if row else None


def finish(conn, job: pipeline.Job, state: str, error: str | None = None) -> None:
    conn.execute("UPDATE ingest_job SET state = %s, last_error = coalesce(%s, last_error), finished_at = now()"
                 " WHERE job_id = %s", (state, error, job.job_id))
    if state == "failed" and job.kind == "ingest" and job.doc_id:
        conn.execute("UPDATE document SET status = 'FAILED' WHERE doc_id = %s", (job.doc_id,))
    close_run(conn, job.run_id)
    conn.commit()


def close_run(conn, run_id: int) -> None:
    """When a run has no queued or running job left, record finished_at and the counts by state."""
    if conn.execute("SELECT 1 FROM ingest_job WHERE run_id = %s AND state IN ('queued', 'running') LIMIT 1",
                    (run_id,)).fetchone():
        return
    counts = dict(conn.execute("SELECT state, count(*) FROM ingest_job WHERE run_id = %s GROUP BY state",
                               (run_id,)).fetchall())
    conn.execute("UPDATE ingest_run SET finished_at = now(), counts = %s::jsonb WHERE run_id = %s",
                 (json.dumps({k: counts.get(k, 0) for k in ("done", "skipped", "failed")}), run_id))


def run_one(conn, job: pipeline.Job) -> str:
    try:
        state = pipeline.run(conn, job)
    except PermanentIngestError as e:
        conn.rollback()
        finish(conn, job, "failed", str(e))
        return "failed"
    except Exception as e:  # noqa: BLE001 - any other error is retried
        conn.rollback()
        error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=5)}"
        if job.attempts < INGEST["max_attempts"]:
            conn.execute("""UPDATE ingest_job SET state = 'queued', last_error = %s,
                            available_at = now() + make_interval(secs => %s) WHERE job_id = %s""",
                         (error, INGEST["retry_delay_seconds"] * job.attempts, job.job_id))
            conn.commit()
            return "queued"
        finish(conn, job, "failed", error)
        return "failed"
    finish(conn, job, state)
    return state


def work(once: bool = False, run_id: int | None = None) -> None:
    with connect() as conn:
        # jobs left running by a stopped worker; with --run only that run's (tests share the dev DB)
        conn.execute("UPDATE ingest_job SET state = 'queued' WHERE state = 'running'"
                     + (" AND run_id = %s" if run_id else ""), (run_id,) if run_id else ())
        conn.commit()
        while True:
            job = take(conn, run_id)
            if job is None:
                if once:
                    pending = conn.execute(
                        "SELECT 1 FROM ingest_job WHERE state = 'queued'" + (" AND run_id = %s" if run_id else ""),
                        (run_id,) if run_id else ()).fetchone()
                    conn.commit()
                    if not pending:
                        return
                time.sleep(INGEST["idle_sleep_seconds"])
                continue
            state = run_one(conn, job)
            print(f"job {job.job_id} {job.source} {job.source_ref}: {state}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m kb.ingest.worker")
    parser.add_argument("--once", action="store_true", help="exit when the queue is empty")
    parser.add_argument("--run", type=int, help="only take jobs of this ingest_run")
    args = parser.parse_args()
    work(args.once, args.run)


if __name__ == "__main__":
    main()
