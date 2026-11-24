"""Explanations with fake passages and a scripted model (docs/evidence-impl-plan.md task 3). No gateway."""

import pytest

from agent import explanations
from agent.explanations import ClaimOut, ExplainOut, ItemOut, ModelResult, assemble, explain_case, prepare, run_model
from kb.search.literature import Passage
from ranking.schema import Match, Ranking, Suggestion
from tests.fakes import row, seed_case

NT, VSD, ASD, MIC = "HP:0010880", "HP:0001629", "HP:0001631", "HP:0000252"
D1, D2, D3 = "OMIM:163950", "OMIM:214800", "ORPHA:648"


def match(query: str, disease_term: str, kind: str = "exact") -> Match:
    return Match(query_hpo_id=query, assertion="PRESENT", weight=1.0, kind=kind, disease_hpo_id=disease_term,
                 common_ancestor_id=None, frequency_raw=["7/10"], evidence=["PCS"], references=[["PMID:1"]])


def suggestion(rank: int, disease_id: str, matches: list[Match]) -> Suggestion:
    return Suggestion(rank=rank, disease_id=disease_id, disease_name=f"Disease {rank}", source_db="OMIM", score=1.0,
                      matches=matches, unmentioned=[], genes=[])


def ranking(*suggestions) -> dict:
    return Ranking(hpo_release="test", method="ppo", terms=[], skipped=[], suggestions=list(suggestions),
                   genes=[]).model_dump(mode="json")


def passage(chunk_id: int, text: str | None = None) -> Passage:
    return Passage(chunk_id=chunk_id, ref=f"CHUNK:{chunk_id}", kind="text", display_text=text or f"Passage {chunk_id}.",
                   figure_path=None, section_path=["Clinical Description"], doc_id=1, source="genereviews",
                   title="Chapter", citation=None, url=None, license="test", disease_ids=[], active=True)


def case(*suggestions, notes=None, description="Thai có tăng khoảng sáng sau gáy, thông liên thất.") -> dict:
    rows = [row("f1", "tăng khoảng sáng sau gáy", NT), row("f2", "thông liên thất", VSD)]
    return seed_case(rows, ranking=ranking(*suggestions), notes=notes or {}, description=description)


@pytest.fixture
def world(monkeypatch):
    """passages: (disease, term) -> passages; replies: scripted invoke_json results (None = failed request)."""
    state = {"passages": {}, "replies": [], "asked": []}

    def find(requests, k=5, **kw):
        return [list(state["passages"].get((r.disease_id, r.hpo_id), []))[:k] for r in requests]

    def ask(system, human, schema, **kw):
        state["asked"].append(human)
        usage = kw.get("usage")
        if usage is not None:
            usage["requests"] = usage.get("requests", 0) + 1
            usage["input_tokens"] = usage.get("input_tokens", 0) + 100
        return state["replies"].pop(0)

    monkeypatch.setattr(explanations, "find_passages_many", find)
    monkeypatch.setattr(explanations, "invoke_json", ask)
    monkeypatch.setattr(explanations, "find_overviews", lambda ids, sources, **kw: dict(state.get("overviews", {})))
    return state


def out(*items) -> ExplainOut:
    return ExplainOut(items=[ItemOut(disease_id=d, claims=[ClaimOut(hpo_id=h, text=t, chunks=c) for h, t, c in claims])
                             for d, claims in items])


def statuses(e) -> dict:
    return {(i.disease_id, p.disease_hpo_id): p.status for i in e.items for p in i.pairs}


def test_pairs_and_statuses(world):
    world["passages"] = {(D1, NT): [passage(1)], (D1, VSD): [passage(2)]}
    world["replies"] = [out((D1, [(NT, "Tài liệu ghi nhận độ mờ da gáy tăng.", [1])]))]
    e = explain_case(case(suggestion(1, D1, [match(NT, NT), match(VSD, VSD)]),
                          suggestion(2, D2, [match(VSD, ASD, "related"), match(NT, None, "none")])))
    assert statuses(e) == {(D1, NT): "cited", (D1, VSD): "no_supporting_passage", (D2, ASD): "no_passage"}
    assert [i.disease_id for i in e.items] == [D1, D2] and e.usage.requests == 1 and not e.cached


def test_disease_without_passages_not_sent(world):
    world["passages"] = {(D1, NT): [passage(1)]}
    world["replies"] = [out()]
    explain_case(case(suggestion(1, D1, [match(NT, NT)]), suggestion(2, D2, [match(VSD, VSD)])))
    assert f"# {D1}" in world["asked"][0] and f"# {D2}" not in world["asked"][0]


def test_no_passage_anywhere_sends_no_request(world):
    p = prepare(case(suggestion(1, D1, [match(NT, NT)])))
    assert p.context is None
    e = explain_case(case(suggestion(1, D1, [match(NT, NT)])))
    assert world["asked"] == [] and e.usage.requests == 0 and statuses(e) == {(D1, NT): "no_passage"}


def test_context_limit_drops_last_passage_of_largest_pair(world, monkeypatch):
    world["passages"] = {(D1, NT): [passage(1, "a b"), passage(2, "c d"), passage(3, "e f")],
                         (D1, VSD): [passage(4, "g h"), passage(5, "i j"), passage(6, "k l")]}
    monkeypatch.setattr(explanations, "count_tokens", lambda text: len(text.split()))
    monkeypatch.setitem(explanations.EXPLAIN, "max_context_tokens", 8)
    p = prepare(case(suggestion(1, D1, [match(NT, NT), match(VSD, VSD)])))
    assert [x.chunk_id for x in p.sent[(D1, NT)]] == [1, 2] and [x.chunk_id for x in p.sent[(D1, VSD)]] == [4, 5]


def test_notes_section_uses_phrase_and_no_disease(world):
    world["passages"] = {(D1, NT): [passage(1)]}
    with_note = prepare(case(suggestion(1, D1, [match(NT, NT)]), notes={"f2": "Cần siêu âm tim lại."}))
    text = with_note.context
    assert '- "thông liên thất": Cần siêu âm tim lại.' in text
    assert text.index("# Nhận xét của bác sĩ") < text.index(f"# {D1}")
    assert "Nhận xét" not in prepare(case(suggestion(1, D1, [match(NT, NT)]))).context


@pytest.mark.parametrize("claim, reason", [
    ((D2, NT, "Câu đúng.", [1]), "không nằm trong các bệnh"),
    ((D1, ASD, "Câu đúng.", [1]), "không phải một cặp"),
    ((D1, NT, "Câu đúng.", []), "không có đoạn nguồn"),
    ((D1, NT, "Câu đúng.", [2]), "không thuộc cặp này"),
    ((D1, NT, "Bệnh OMIM:163950 có biểu hiện này.", [1]), "chứa mã"),
    ((D1, NT, "Xem CHUNK:1.", [1]), "chứa mã"),
    ((D1, NT, "Tăng xác suất mắc bệnh.", [1]), "cụm cấm"),
])
def test_check_rules(world, claim, reason):
    world["passages"] = {(D1, NT): [passage(1)], (D1, VSD): [passage(2)]}
    p = prepare(case(suggestion(1, D1, [match(NT, NT), match(VSD, VSD)])))
    d, h, text, chunks = claim
    valid, rejected = explanations.check_claims(out((d, [(h, text, chunks)])), p.sent)
    assert not valid and any(reason in r for rs in rejected.values() for _, reasons in rs for r in reasons)


def test_repair_covers_only_bad_pairs(world):
    world["passages"] = {(D1, NT): [passage(1)], (D1, VSD): [passage(2)], (D2, ASD): [passage(3)]}
    world["replies"] = [
        out((D1, [(NT, "Đúng về da gáy.", [1]), (VSD, "Sai đoạn.", [1])]), (D2, [(ASD, "Đúng về vách.", [3])])),
        out((D1, [(VSD, "Đã sửa về vách liên thất.", [2]), (NT, "Thay ý khác.", [1])]), (D2, [(ASD, "Bỏ qua.", [3])])),
    ]
    p = prepare(case(suggestion(1, D1, [match(NT, NT), match(VSD, VSD)]), suggestion(2, D2, [match(VSD, ASD)])))
    r = run_model(p)
    repair = world["asked"][1]
    assert f"## Cặp {VSD}" in repair and f"## Cặp {NT}" not in repair and f"# {D2}" not in repair
    assert "Sai đoạn." in repair and explanations.REPAIR_PROMPT.strip() in repair
    texts = {k: [c.text for c in v] for k, v in r.claims.items()}
    assert texts == {f"{D1}|{NT}": ["Đúng về da gáy."], f"{D1}|{VSD}": ["Đã sửa về vách liên thất."],
                     f"{D2}|{ASD}": ["Đúng về vách."]}
    assert r.usage.requests == 2 and r.trace == {"first_claims": 3, "first_invalid": 1, "repaired": True}
    assert r.cacheable


def test_still_invalid_after_repair_is_dropped(world):
    world["passages"] = {(D1, NT): [passage(1)]}
    world["replies"] = [out((D1, [(NT, "Sai.", [9])])), out((D1, [(NT, "Vẫn sai.", [8])]))]
    p = prepare(case(suggestion(1, D1, [match(NT, NT)])))
    r = run_model(p)
    e = assemble(p, r)
    assert statuses(e) == {(D1, NT): "failed"} and e.items[0].pairs[0].claims == [] and not r.cacheable


def test_repair_withdrawing_claim_is_no_supporting_passage(world):
    world["passages"] = {(D1, NT): [passage(1)]}
    world["replies"] = [out((D1, [(NT, "Sai.", [9])])), out()]
    p = prepare(case(suggestion(1, D1, [match(NT, NT)])))
    r = run_model(p)
    assert statuses(assemble(p, r)) == {(D1, NT): "no_supporting_passage"} and r.cacheable


def test_request_failure_marks_sent_pairs_failed(world):
    world["passages"] = {(D1, NT): [passage(1)]}
    world["replies"] = [None]
    p = prepare(case(suggestion(1, D1, [match(NT, NT), match(VSD, VSD)])))
    r = run_model(p)
    assert statuses(assemble(p, r)) == {(D1, NT): "failed", (D1, VSD): "no_passage"} and not r.cacheable


def test_passages_only_cited(world):
    world["passages"] = {(D1, NT): [passage(1), passage(2)]}
    world["replies"] = [out((D1, [(NT, "Câu.", [2])]))]
    e = explain_case(case(suggestion(1, D1, [match(NT, NT)])))
    assert list(e.passages) == ["CHUNK:2"] and e.items[0].pairs[0].claims[0].refs == ["CHUNK:2"]


def test_shared_pair_claims_copied(world):
    world["passages"] = {(D1, NT): [passage(1)]}
    world["replies"] = [out((D1, [(NT, "Câu.", [1])]))]
    e = explain_case(case(suggestion(1, D1, [match(NT, NT), match(VSD, NT, "related")])))
    a, b = e.items[0].pairs
    assert (a.query_hpo_id, b.query_hpo_id) == (NT, VSD) and a.claims == b.claims and a.status == b.status == "cited"


def test_assemble_uses_current_ranking(world):
    world["passages"] = {(D1, NT): [passage(1)]}
    cached = ModelResult(claims={f"{D1}|{NT}": [explanations.Claim(text="Câu.", refs=["CHUNK:1"])]},
                         written=[f"{D1}|{NT}"], created_at="2026-11-21T00:00:00+00:00",
                         usage=explanations.Usage(requests=1))
    p = prepare(case(suggestion(1, D1, [match(MIC, NT, "disease_more_specific")]), suggestion(2, D2, [match(VSD, VSD)])))
    e = assemble(p, cached, cached=True)
    assert e.items[0].pairs[0].query_hpo_id == MIC and [i.disease_id for i in e.items] == [D1, D2]
    assert e.cached and e.usage.requests == 0 and statuses(e)[(D2, VSD)] == "no_passage"


def test_key_is_input(world, monkeypatch):
    world["passages"] = {(D1, NT): [passage(1)]}
    s = suggestion(1, D1, [match(NT, NT)])
    base = prepare(case(s)).key
    assert prepare(case(s)).key == base
    assert prepare(case(s, notes={"f1": "ghi chú"})).key != base
    assert prepare(case(s, description="Mô tả khác.")).key != base
    world["passages"] = {(D1, NT): [passage(1, "Other text.")]}
    assert prepare(case(s)).key != base
    world["passages"] = {(D1, NT): [passage(1)]}
    monkeypatch.setattr(explanations, "PROMPT", "khác")
    assert prepare(case(s)).key != base
    monkeypatch.undo()
    monkeypatch.setattr(explanations, "find_passages_many", lambda reqs, k=5, **kw: [[passage(1)] for _ in reqs])
    monkeypatch.setattr(explanations, "REPAIR_PROMPT", "khác")
    assert prepare(case(s)).key != base
    monkeypatch.setitem(explanations.EXPLAIN, "model", "vlm")
    assert prepare(case(s)).key != base


def test_overview_attached_not_sent(world, monkeypatch):
    world["overviews"] = {D1: passage(9, "A rare disorder.")}
    seen = {}

    def find(requests, k=5, **kw):
        seen.update(kw)
        return [[] for _ in requests]
    monkeypatch.setattr(explanations, "find_passages_many", find)
    e = explain_case(case(suggestion(1, D1, [match(NT, NT)]), suggestion(2, D2, [match(VSD, VSD)])))
    assert seen["exclude_sources"] == explanations.EXPLAIN["overview_sources"] and world["asked"] == []
    assert [(i.disease_id, i.overview) for i in e.items] == [(D1, "CHUNK:9"), (D2, None)]
    assert set(e.passages) == {"CHUNK:9"}
