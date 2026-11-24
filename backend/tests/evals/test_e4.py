"""E4 (docs/evidence-impl-plan.md task 6): split and metrics on a hand-made record. No gateway."""

import hashlib

from evals.e4 import dataset
from evals.e4.run import case_counts, claims_of, sha1


def test_dataset_split_deterministic():
    rows = dataset.load_rows()
    dev = sorted((r["id"] for r in rows), key=lambda i: hashlib.sha1(i.encode()).hexdigest())[:20]
    assert {r["id"] for r in rows if r["split"] == "dev"} == set(dev) and len(rows) == 40
    assert all("candidates" not in f for r in rows for f in r["analysis"]["findings"])


def case() -> dict:
    claim = {"text": "Tài liệu ghi nhận.", "refs": ["CHUNK:1"]}
    return {"id": "E2-99", "trace": {"first_claims": 4, "first_invalid": 1},
            "explanations": {"usage": {}, "passages": {"CHUNK:1": {"display_text": "A"}}, "items": [
                {"disease_id": "D1", "pairs": [
                    {"query_hpo_id": "H1", "disease_hpo_id": "H1", "status": "cited", "claims": [claim]},
                    {"query_hpo_id": "H9", "disease_hpo_id": "H1", "status": "cited", "claims": [claim]},
                    {"query_hpo_id": "H2", "disease_hpo_id": "H2", "status": "no_supporting_passage", "claims": []},
                    {"query_hpo_id": "H3", "disease_hpo_id": "H3", "status": "failed", "claims": []},
                    {"query_hpo_id": "H4", "disease_hpo_id": "H4", "status": "no_passage", "claims": []}]}]},
            "sent": {pk: {"disease_name": "D", "hpo_label": "P", "passages": [{"chunk_id": 1, "display_text": t}]}
                     for pk, t in (("D1|H1", "A"), ("D1|H2", "B"), ("D1|H3", "C"))}}


def test_metrics_from_saved_record():
    c = case()
    (claim,) = claims_of(c)                       # the shared pair's claim counts once
    record = {"faithfulness": {"E2-99": {claim["key"]: True}},
              "evidence": {"E2-99|D1|H1": {sha1("A"): {"supports": True}}, "E2-99|D1|H2": {sha1("B"): {"supports": True}},
                           "E2-99|D1|H3": {sha1("C"): {"supports": False}}}}   # the shape e5.judge writes
    counts = case_counts(c, record)
    assert counts["invalid_claim_rate"] == (1, 4)
    assert counts["failed_pair_rate"] == (1, 3)
    assert counts["faithfulness"] == (1, 1)
    assert counts["missed_evidence"] == (1, 2)    # H2 had a supporting passage but no claim
    assert not counts["unjudged"]
