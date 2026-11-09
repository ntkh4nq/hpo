from kb.db import connect
from kb.migrate import migrate


def test_migrate_is_idempotent():
    migrate()
    assert migrate() == []
    with connect() as conn:
        extensions = {row[0] for row in conn.execute("SELECT extname FROM pg_extension")}
    assert {"vector", "pg_trgm"} <= extensions
