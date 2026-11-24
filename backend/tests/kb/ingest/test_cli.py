"""cli rechunk queues only what is missing unless --replace (docs/evidence-impl-plan.md task 2)."""

from core.db import connect
from kb.ingest import cli

NOONAN = "orphanet_def/docs/ORPHA_648.xml"


def queued(run_id: int) -> int:
    with connect() as conn:
        return conn.execute("SELECT count(*) FROM ingest_job WHERE run_id = %s", (run_id,)).fetchone()[0]


def test_rechunk_skips_existing_unless_replace(env, fake_models):
    env.run([("orphanet_def", "TEST:noonan", env.copy(NOONAN))])
    (doc,) = env.documents("TEST:noonan")
    skip = cli.rechunk("s400_ctx", [doc["doc_id"]], "test")
    replace = cli.rechunk("s400_ctx", [doc["doc_id"]], "test", replace=True)
    env.runs += [skip, replace]   # removed at teardown
    assert queued(skip) == 0 and queued(replace) == 1
