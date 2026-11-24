"""Layer 3 commands (docs/ingestion-plan.md §1.4).

    python -m kb.ingest.cli download <source>     # §2: data/raw/<source>/<YYYY-MM-DD>/ + kb_manifest rows
    python -m kb.ingest.cli ingest <source>       # §8: one ingest job per document of the latest download
    python -m kb.ingest.cli rechunk --strategy <name> [--docs 1,2,3] [--replace]
    python -m kb.ingest.cli status
"""

import argparse
import shutil
import sys
from datetime import UTC, date, datetime

from core.db import connect
from kb.ingest.config import CHUNKING, resolve_strategy
from kb.ingest.fetch import sha256
from kb.ingest.model import REPO_ROOT
from kb.ingest.sources import SOURCES

RAW = REPO_ROOT / "data" / "raw"
PUBLIC = ["orphanet_def", "pubmed", "genereviews", "pmc", "medlineplus"]


def download(source: str) -> None:
    """Download and split one public source into today's release directory. A second run on the same day
    replaces that directory and its kb_manifest rows."""
    release = date.today().isoformat()
    release_dir = RAW / source / release
    with connect() as conn:
        conn.execute("DELETE FROM kb_manifest WHERE source = %s AND release = %s", (source, release))
    if release_dir.exists():
        shutil.rmtree(release_dir)
    files = SOURCES[source].download(release_dir)
    rows = [(source, release, f.file, f.url, sha256(release_dir / f.file),
             datetime.fromtimestamp((release_dir / f.file).stat().st_mtime, UTC), f.license) for f in files]
    with connect() as conn, conn.cursor() as cur:
        cur.executemany("INSERT INTO kb_manifest (source, release, file, url, sha256, downloaded_at, license)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s)", rows)
    print(f"{source}: {len(rows)} files recorded in kb_manifest (release {release})")


def new_run(conn, command: str, source: str) -> int:
    return conn.execute("INSERT INTO ingest_run (command, source) VALUES (%s, %s) RETURNING run_id",
                        (command, source)).fetchone()[0]


def ingest(source: str, command: str) -> int:
    """One ingest job per DocRef of the latest release; ACTIVE documents gone from it become RETIRED (§2)."""
    release_dir = max(p for p in (RAW / source).iterdir() if p.is_dir())
    refs = SOURCES[source].list_docs(release_dir)
    strategy = resolve_strategy(None)
    with connect() as conn:
        run_id = new_run(conn, command, source)
        gone = [r[0] for r in conn.execute(
            "SELECT doc_id FROM document WHERE source = %s AND status = 'ACTIVE' AND NOT source_ref = ANY(%s)",
            (source, [r.source_ref for r in refs]))]
        conn.execute("UPDATE document SET status = 'RETIRED' WHERE doc_id = ANY(%s)", (gone,))
        conn.execute("UPDATE chunk SET is_active = false WHERE doc_id = ANY(%s)", (gone,))
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO ingest_job (run_id, kind, source, source_ref, raw_path, strategy)"
                            " VALUES (%s, 'ingest', %s, %s, %s, %s)",
                            [(run_id, source, r.source_ref, r.raw_path, strategy) for r in refs])
    print(f"{source}: run {run_id}, {len(refs)} jobs from {release_dir.name}, {len(gone)} documents retired")
    return run_id


def rechunk(strategy: str, doc_ids: list[int] | None, command: str, replace: bool = False) -> int:
    """One rechunk job per document (given ones, or every ACTIVE/PENDING document). A job replaces the document's
    chunks of that strategy; without `replace`, documents that already have them are not queued, so re-running an
    interrupted rechunk only builds what is missing."""
    if strategy not in CHUNKING or not isinstance(CHUNKING[strategy], dict):
        raise SystemExit(f"unknown strategy {strategy!r}")
    with connect() as conn:
        rows = conn.execute(
            "SELECT doc_id, source, source_ref, raw_path FROM document WHERE status IN ('ACTIVE', 'PENDING')"
            + (" AND doc_id = ANY(%(docs)s)" if doc_ids else "")
            + ("" if replace else " AND NOT EXISTS (SELECT 1 FROM chunk c WHERE c.doc_id = document.doc_id"
                                  " AND c.strategy = %(strategy)s)")
            + " ORDER BY doc_id", {"docs": doc_ids, "strategy": strategy}).fetchall()
        run_id = new_run(conn, command, "rechunk")
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO ingest_job (run_id, kind, source, source_ref, raw_path, strategy, doc_id)"
                            " VALUES (%s, 'rechunk', %s, %s, %s, %s, %s)",
                            [(run_id, src, ref, path, strategy, doc_id) for doc_id, src, ref, path in rows])
    print(f"rechunk {strategy}: run {run_id}, {len(rows)} jobs")
    return run_id


def status() -> None:
    with connect() as conn:
        print("documents (source, status, n):")
        for r in conn.execute("SELECT source, status, count(*) FROM document GROUP BY 1, 2 ORDER BY 1, 2"):
            print("  ", *r)
        print("chunks (source, strategy, kind, n, with vector, with image vector, active):")
        for r in conn.execute("""SELECT d.source, c.strategy, c.kind, count(*), count(c.embedding),
                                        count(c.image_embedding), count(*) FILTER (WHERE c.is_active)
                                 FROM chunk c JOIN document d USING (doc_id) GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"""):
            print("  ", *r)
        print("jobs (state, n):")
        for r in conn.execute("SELECT state, count(*) FROM ingest_job GROUP BY 1 ORDER BY 1"):
            print("  ", *r)
        print("latest errors:")
        for job_id, ref, state, err in conn.execute(
                """SELECT job_id, source_ref, state, last_error FROM ingest_job WHERE last_error IS NOT NULL
                   ORDER BY coalesce(finished_at, available_at) DESC LIMIT 5"""):
            print(f"   job {job_id} {ref} ({state}): {err.splitlines()[0][:200]}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m kb.ingest.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("download", help="download and split a public source (§2)")
    p.add_argument("source", choices=PUBLIC)
    p = sub.add_parser("ingest", help="queue one ingest job per document of the latest download (§8)")
    p.add_argument("source", choices=PUBLIC)
    p = sub.add_parser("rechunk", help="queue rechunk jobs for another strategy (§8)")
    p.add_argument("--strategy", required=True)
    p.add_argument("--docs", help="comma-separated doc_id list (default: every ACTIVE/PENDING document)")
    p.add_argument("--replace", action="store_true", help="also rebuild documents that already have this strategy")
    sub.add_parser("status", help="documents, chunks and jobs by state; latest errors")
    args = parser.parse_args()
    command = "python -m kb.ingest.cli " + " ".join(sys.argv[1:])
    if args.command == "download":
        download(args.source)
    elif args.command == "ingest":
        ingest(args.source, command)
    elif args.command == "rechunk":
        rechunk(args.strategy, [int(x) for x in args.docs.split(",")] if args.docs else None, command, args.replace)
    else:
        status()


if __name__ == "__main__":
    main()
