"""find_passages on hand-built layer 3 rows in the dev DB (docs/evidence-impl-plan.md task 1). No gateway.

Disease codes are fake (layer 3 has no foreign keys); HPO codes and names come from the seeded KB.
Every row made here hangs off a document with source_ref 'TEST:…', deleted at teardown.
"""

import pytest

from core.db import connect
from kb.search import engine, literature
from kb.search.literature import DOC_CHUNKS, PassageRequest, find_passages, find_passages_many
from tests.conftest import vec

NT = "HP:0010880"   # Increased nuchal translucency
D1, ORPHA, D3 = "OMIM:9900001", "ORPHA:9900002", "OMIM:9900003"
NT_TEXT = "Prenatal findings include increased nuchal translucency and cystic hygroma."


@pytest.fixture
def embeds(monkeypatch):
    """Every query embeds to e0; records the texts of each call."""
    calls = []

    def fake(texts, model=None):
        calls.append(list(texts))
        return [vec(0) for _ in texts]
    monkeypatch.setattr(literature, "embed_texts", fake)
    monkeypatch.setattr(engine, "embed_texts", fake)
    return calls


def ids(passages) -> set[int]:
    return {p.chunk_id for p in passages}


def commit(rows):
    rows.conn.commit()


def test_filters_active_strategy_and_disease(rows, embeds):
    a = rows.doc([D1])
    good = rows.chunk(a, NT_TEXT)
    rows.chunk(a, NT_TEXT + " Inactive.", active=False)
    rows.chunk(a, NT_TEXT + " Other strategy.", strategy="s200_ctx")
    rows.chunk(rows.doc([D3]), NT_TEXT + " Other disease.")
    commit(rows)
    assert ids(find_passages(D1, hpo_id=NT)) == {good}


def test_orpha_reaches_omim_equivalents(rows, embeds):
    rows.doc([ORPHA, D1], source="orphanet_def")
    target = rows.chunk(rows.doc([D1]), NT_TEXT)
    commit(rows)
    assert target in ids(find_passages(ORPHA, hpo_id=NT))


def test_omim_not_expanded(rows, embeds):
    rows.doc([ORPHA, D1], source="orphanet_def")
    orpha_only = rows.chunk(rows.doc([ORPHA]), NT_TEXT)
    commit(rows)
    assert orpha_only not in ids(find_passages(D1, hpo_id=NT))


def test_document_without_codes_never_returned(rows, embeds):
    undeclared = rows.chunk(rows.doc([], source="pmc"), NT_TEXT)
    rows.chunk(rows.doc([D1]), NT_TEXT + " Declared.")
    commit(rows)
    found = find_passages(D1, hpo_id=NT) + find_passages(D1, topic="increased nuchal translucency hygroma")
    assert undeclared not in ids(found)


def test_query_is_hpo_label_without_disease_name(rows, embeds):
    rows.chunk(rows.doc([D1]), NT_TEXT)
    commit(rows)
    find_passages(D1, hpo_id=NT)
    assert embeds[-1] == ["Increased nuchal translucency"]
    with connect() as conn:
        both = literature.query_text(conn, NT, ["label", "exact_syn"])
    assert both.startswith("Increased nuchal translucency")


def test_require_leg_drops_dense_only_candidates(rows, embeds):
    a = rows.doc([D1])
    lexical = rows.chunk(a, NT_TEXT, v=1)
    dense_only = rows.chunk(a, "Cardiac anomalies are common.", v=0)
    commit(rows)
    assert ids(find_passages(D1, hpo_id=NT, profile="doc_rrf")) == {lexical, dense_only}
    assert ids(find_passages(D1, hpo_id=NT, profile="doc_rrf_lexical")) == {lexical}


def test_query_vector_skips_embedding(rows, monkeypatch):
    rows.chunk(rows.doc([D1]), NT_TEXT)
    commit(rows)

    def boom(*a, **k):
        raise AssertionError("embed_texts called")
    monkeypatch.setattr(engine, "embed_texts", boom)
    found = engine.retrieve(DOC_CHUNKS, "increased nuchal translucency", "doc_rrf", 5,
                            params={"disease_ids": [D1], "strategy": "s400_ctx"}, query_vector=vec(0))
    assert len(found) == 1


def test_many_embeds_once_one_connection(rows, embeds, monkeypatch):
    rows.chunk(rows.doc([D1]), NT_TEXT)
    commit(rows)
    opened = []
    real = literature.connect

    def counting(*a, **k):
        opened.append(1)
        return real(*a, **k)
    monkeypatch.setattr(literature, "connect", counting)
    monkeypatch.setattr(engine, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("engine connected")))
    out = find_passages_many([PassageRequest(D1, hpo_id=NT), PassageRequest(D1, hpo_id="HP:0001629"),
                              PassageRequest(D1, topic="cystic hygroma")])
    assert len(out) == 3 and len(embeds) == 1 and len(embeds[0]) == 3 and len(opened) == 1


def test_passage_fields_no_embed_text(rows, embeds):
    a = rows.doc([D1])
    cid = rows.chunk(a, NT_TEXT)
    commit(rows)
    (p,) = find_passages(D1, hpo_id=NT)
    assert p.chunk_id == cid and p.ref == f"CHUNK:{cid}" and p.doc_id == a and p.active
    assert p.section_path[-1] == "Clinical Description" and p.disease_ids == [D1] and p.source == "genereviews"
    assert "embed_text" not in p.model_dump() and p.scores["fulltext_rank"] == 1


def test_dedupe_by_display_text(rows, embeds):
    rows.chunk(rows.doc([D1]), NT_TEXT)
    rows.chunk(rows.doc([D1]), NT_TEXT)
    commit(rows)
    assert len(find_passages(D1, hpo_id=NT)) == 1


def test_parent_child_returns_parent_text(rows, embeds):
    a = rows.doc([D1])
    parent = rows.chunk(a, "PARENT TEXT about the prenatal period.", strategy="parent_child", role="parent",
                        embedded=False)
    rows.chunk(a, NT_TEXT, strategy="parent_child", role="child", parent=parent)
    commit(rows)
    (p,) = find_passages(D1, hpo_id=NT, strategy="parent_child")
    assert p.chunk_id == parent and p.display_text.startswith("PARENT TEXT")


def test_visual_leg_only_image_chunks(rows, embeds, monkeypatch):
    a = rows.doc([D1])
    with_image = rows.chunk(a, NT_TEXT + " Figure 1.", image=True)
    without = rows.chunk(a, NT_TEXT)
    commit(rows)
    monkeypatch.setattr(engine, "embed_visual_query", lambda text, **kw: vec(0, 2048))
    found = {p.chunk_id: p for p in find_passages(D1, hpo_id=NT, profile="doc_rrf_lexical_visual")}
    assert "visual_rank" in found[with_image].scores and "visual_rank" not in found[without].scores


@pytest.mark.parametrize("request_", [PassageRequest(D1), PassageRequest(D1, hpo_id=NT, topic="x")])
def test_exactly_one_of_hpo_id_topic(request_):
    with pytest.raises(ValueError):
        find_passages_many([request_])


def test_exclude_sources(rows, embeds):
    keep = rows.chunk(rows.doc([D1]), NT_TEXT)
    rows.chunk(rows.doc([D1], source="orphanet_def"), NT_TEXT + " Definition.")
    commit(rows)
    assert keep in ids(find_passages(D1, hpo_id=NT)) and len(find_passages(D1, hpo_id=NT)) == 2
    (only,) = find_passages_many([PassageRequest(D1, hpo_id=NT)], exclude_sources=["orphanet_def"])
    assert ids(only) == {keep}


def test_find_overviews(rows):
    definition = rows.chunk(rows.doc([ORPHA, D1], source="orphanet_def"), "A rare disorder characterized by X.")
    rows.chunk(rows.doc([D1]), NT_TEXT)
    rows.chunk(rows.doc([D3], source="orphanet_def", status="PENDING"), "Not active.")
    commit(rows)
    found = literature.find_overviews([ORPHA, D1, D3], ["orphanet_def"])
    assert {d: p.chunk_id for d, p in found.items()} == {ORPHA: definition, D1: definition}
    assert literature.find_overviews([ORPHA], []) == {}
