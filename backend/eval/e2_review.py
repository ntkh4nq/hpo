"""Generate docs/e2-review.md from datasets/e2.jsonl, for the user to review (docs/e2-plan.md §6).

    python -m eval.e2_review
"""

from pathlib import Path

from eval.e2_check import load_cases
from eval.e2_pool import disease_genes, usable_phenotypes
from kb.db import connect
from kb.query.retrieval import PROFILES

OUT = Path(__file__).parents[2] / "docs" / "e2-review.md"
ASSERTION = {"PRESENT": "có", "ABSENT": "không có", "SUSPECTED": "nghi", "UNCERTAIN": "chưa rõ"}
ASSESSMENT = {"ASSESSED": "đã đánh giá", "LIMITED": "hạn chế", "NOT_ASSESSABLE": "không đánh giá được",
              "NOT_ASSESSED": "chưa khảo sát"}
SUBJECT = {"FETUS": "thai", "MOTHER": "mẹ", "FAMILY": "gia đình"}


def cell(text) -> str:
    return "—" if text is None else str(text).replace("|", "\\|").replace("\n", " ")


def main() -> None:
    cases = load_cases()
    diseases = [c["disease_id"] for c in cases]
    codes = sorted({i["hpo_id"] for c in cases for i in c["items"] if i["hpo_id"]})
    with connect() as conn:
        usable = usable_phenotypes(conn, diseases)
        genes = disease_genes(conn, diseases)
        names = dict(conn.execute("SELECT disease_id, name FROM disease WHERE disease_id = ANY(%s)", (diseases,)))
        labels = {h: (en, vi) for h, en, vi in conn.execute(
            """SELECT t.hpo_id, t.label_en, (SELECT n.text FROM hpo_name n WHERE n.hpo_id = t.hpo_id AND n.lang = 'vi'
                   ORDER BY array_position(%s::text[], n.source) LIMIT 1)
               FROM hpo_term t WHERE t.hpo_id = ANY(%s)""", (PROFILES["hpo_vi_label_sources"], codes))}

    n_items = sum(len(c["items"]) for c in cases)
    lines = [
        f"# E2: {len(cases)} ca, {n_items} mục (đã duyệt và khoá, T45)", "",
        "Sinh bằng `python -m eval.e2_review` từ `backend/eval/datasets/e2.jsonl`; sửa file jsonl rồi sinh lại, không sửa tay file này.",
        "Cách soạn: `docs/e2-plan.md` §3–§4. Đáp án theo các quy ước của T31 (`docs/split-check-review.md`).",
        "Trạng thái viết tắt: có = PRESENT, không có = ABSENT, nghi = SUSPECTED, chưa rõ = UNCERTAIN; "
        "đã đánh giá = ASSESSED, hạn chế = LIMITED, không đánh giá được = NOT_ASSESSABLE, chưa khảo sát = NOT_ASSESSED. "
        "Cột \"của bệnh\" đánh dấu mục lấy từ annotation của bệnh; mã `—` nghĩa là mục phải nhận UNMAPPED.", "",
    ]
    for c in cases:
        own = {p["hpo_id"] for p in usable.get(c["disease_id"], [])}
        lines += [f"## {c['id']} · {c['disease_id']} · {cell(names.get(c['disease_id']))}", "",
                  f"Gene: {', '.join(genes.get(c['disease_id'], [])) or '—'}", "", "> " + c["text"].replace("\n", "\n> "), "",
                  "| # | phrase | trạng thái | chủ thể | mã | nhãn tiếng Anh | tên tiếng Việt | của bệnh |",
                  "|---|---|---|---|---|---|---|---|"]
        for n, i in enumerate(c["items"], 1):
            en, vi = labels.get(i["hpo_id"], (None, None))
            lines.append(f"| {n} | {cell(i['phrase'])} | {ASSERTION[i['assertion']]}, {ASSESSMENT[i['assessment']]} "
                         f"| {SUBJECT[i['subject']]} | {cell(i['hpo_id'])} | {cell(en)} | {cell(vi)} "
                         f"| {'✓' if i['hpo_id'] in own else ''} |")
        lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
