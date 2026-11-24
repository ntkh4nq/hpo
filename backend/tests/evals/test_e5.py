"""E5 (docs/evidence-impl-plan.md task 5): dataset rules, metrics, judge reuse. No gateway."""

from evals.e5 import dataset, run
from evals.e5.run import Judgement, Judgements, score, sha1


def test_dataset_deterministic_and_grouped():
    rows = dataset.build()
    assert rows == dataset.build()
    assert len({r["id"] for r in rows}) == len(rows) == dataset.CONFIG["pairs"]
    sides = {}
    for r in rows:
        sides.setdefault(r["group"], set()).add(r["split"])
    assert all(len(s) == 1 for s in sides.values())          # a group never spans dev and test
    assert {r["split"] for r in rows} == {"dev", "test"}


def test_components_join_diseases_sharing_a_document():
    groups = dataset.components([{"B", "C"}, {"C", "D"}, {"X"}], ["A", "B", "C", "D"])
    assert groups == {"A": "A", "B": "B", "C": "B", "D": "B"}


def hit(text: str) -> dict:
    return {"chunk_id": 1, "doc_id": 1, "kind": "text", "display_text": text, "tokens": len(text.split())}


def test_metrics():
    table = {"p1": {sha1("yes a"): {"supports": True}, sha1("no b"): {"supports": False}}, "p2": {}}
    out = score({"items": {"p1": {"hits": [hit("yes a"), hit("no b")]}, "p2": {"hits": []},
                           "p3": {"hits": [hit("unjudged")]}}}, table)
    assert out["p1"] == {"hit@5": 1.0, "precision@5": 0.5, "tokens@5": 4.0}
    assert out["p2"] == {"hit@5": 0.0, "precision@5": 0.0, "tokens@5": 0.0}
    assert out["p3"] is None


def test_judge_table_reused_across_configs(monkeypatch):
    calls = []

    def fake(system, human, schema, **kw):
        calls.append(human)
        return Judgements(judgements=[Judgement(index=i + 1, supports=True) for i in range(human.count("\n[") + 1)])
    monkeypatch.setattr(run, "invoke_json", fake)
    rows = [{"id": "p1", "disease_id": "OMIM:1", "disease_name": "D", "hpo_id": "HP:1", "hpo_label": "P"}]
    table: dict = {}
    run.judge(rows, [{"items": {"p1": {"hits": [hit("same"), hit("other")]}}}], table)
    run.judge(rows, [{"items": {"p1": {"hits": [hit("same")]}}}], table)
    assert len(calls) == 1 and set(table["p1"]) == {sha1("same"), sha1("other")}
