"""search_disease: disease codes (OMIM, ORPHA) by approximate English name. Shared by the supervisor tool and
GET /diseases (docs/evidence-plan.md §6)."""

from core.db import connect


def search_disease(text: str, k: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT disease_id, name, db FROM disease WHERE name IS NOT NULL
               ORDER BY word_similarity(%s, name) DESC, disease_id LIMIT %s""", (text, k)).fetchall()
    return [{"disease_id": d, "name": n, "source": db} for d, n, db in rows]
