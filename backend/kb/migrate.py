"""Apply db/migrations/*.sql in filename order, each file exactly once."""

from pathlib import Path

from core.db import connect

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


def migrate() -> list[str]:
    applied = []
    # ponytail: no lock against two concurrent runners; add pg_advisory_lock if migrations ever run from several processes
    with connect(autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


if __name__ == "__main__":
    print("\n".join(migrate()) or "up to date")
