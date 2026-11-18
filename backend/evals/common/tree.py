"""HPO tree helpers for scoring: a code counts as near when it is a direct parent or child of the gold code."""

from core.db import connect


def neighbours() -> dict[str, set[str]]:
    """Direct parents and children of every term (distance 1 either way)."""
    near: dict[str, set[str]] = {}
    with connect() as conn:
        for child, parent in conn.execute("SELECT child_id, parent_id FROM hpo_edge"):
            near.setdefault(child, set()).add(parent)
            near.setdefault(parent, set()).add(child)
    return near
