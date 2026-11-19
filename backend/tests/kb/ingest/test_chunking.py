"""Chunking rules (docs/ingestion-plan.md §5). Pure functions: no DB, no gateway."""

from pathlib import Path

import pytest
from PIL import Image

from core import embedding, llm
from core.image import data_url
from kb.ingest.chunking import PARAGRAPH_JOIN, chunk_document, count_tokens, strategy
from kb.ingest.model import Figure, Paragraph, ParsedDocument, Section, Table
from kb.ingest.sources import genereviews

GR = Path(__file__).parent / "fixtures/genereviews/docs/gene_NBK1116/sca20.nxml"


def words(text: str) -> int:
    """A simple counter for hand-made cases: one token per word."""
    return len(text.split())


def make_doc(*sections: Section) -> ParsedDocument:
    return ParsedDocument("upload", "TEST:x", "Doc", None, None, "l", "en", "upload", "x", list(sections))


DOC = make_doc(
    Section(["A"], [Paragraph("one two three. Four five six."), Paragraph("seven eight"),
                    Table("Table 1.", ["Gene", "Freq"], [["G1", "50%"], ["G2", ""], ["G3", "10%"]], "Note."),
                    Paragraph("nine ten"),
                    Figure("Figure 1. Caption.", "data/x.png")]),
    Section(["A", "B"], [Paragraph("eleven twelve thirteen fourteen fifteen sixteen. Seventeen.")]),
)
FLAT = {"max_tokens": 12, "context_line": True}


def test_sections_never_crossed_and_order():
    chunks = chunk_document(DOC, {"max_tokens": 30, "context_line": True}, ["Noonan syndrome 1"], {}, count=words)
    assert [c.ord for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        if c.kind == "text":
            section_text = " ".join(b.text for b in DOC.sections[c.section].blocks if isinstance(b, Paragraph))
            assert all(piece in section_text for piece in c.display_text.split(PARAGRAPH_JOIN))
    # the table and the figure break runs of paragraphs, and the reading order is kept
    assert [c.kind for c in chunks if c.section == 0] == ["text", "table", "text", "figure"]


def test_context_line_and_disease_line():
    chunks = chunk_document(DOC, FLAT, ["D1", "D2", "D3", "D4"], {}, count=words)
    first, last = chunks[0], chunks[-1]
    assert first.embed_text.startswith("Doc › A\nDiseases: D1; D2; D3\n")
    assert last.embed_text.startswith("Doc › A › B\n")
    assert not first.display_text.startswith("Doc")
    no_codes = chunk_document(DOC, FLAT, [], {}, count=words)
    assert "Diseases:" not in no_codes[0].embed_text
    no_ctx = chunk_document(DOC, {"max_tokens": 12, "context_line": False}, ["D1"], {}, count=words)
    assert all(c.embed_text == c.display_text for c in no_ctx if c.kind == "text")


def test_paragraph_split_by_sentence_when_too_long():
    chunks = chunk_document(DOC, {"max_tokens": 6, "context_line": False}, [], {}, count=words)
    texts = [c.display_text for c in chunks if c.section == 1]
    assert texts == ["eleven twelve thirteen fourteen fifteen sixteen.", "Seventeen."]


def test_single_long_sentence_stands_alone():
    doc = make_doc(Section(["A"], [Paragraph("a b c d e f g h"), Paragraph("i j")]))
    chunks = chunk_document(doc, {"max_tokens": 4, "context_line": False}, [], {}, count=words)
    assert [c.display_text for c in chunks] == ["a b c d e f g h", "i j"]


def test_table_rows_header_repeated_and_empty_cells_skipped():
    chunks = chunk_document(DOC, {"max_tokens": 9, "context_line": False}, [], {}, count=words)
    parts = [c for c in chunks if c.kind == "table"]
    assert len(parts) == 2
    for p in parts:
        assert p.display_text.startswith("Table 1.\n\n| Gene | Freq |\n|---|---|") and p.display_text.endswith("Note.")
        assert p.embed_text.startswith("Table 1.\nNote.\n")
    assert parts[0].embed_text.endswith("Gene: G1; Freq: 50%\nGene: G2")  # empty cell left out
    assert parts[1].embed_text.endswith("Gene: G3; Freq: 10%")


def test_figure_description_only_in_embed_text():
    chunks = chunk_document(DOC, FLAT, [], {"data/x.png": "An ultrasound image."}, count=words)
    (fig,) = [c for c in chunks if c.kind == "figure"]
    assert fig.display_text == "Figure 1. Caption." and fig.figure_path == "data/x.png"
    assert fig.embed_text.endswith("Figure 1. Caption.\nFigure description: An ultrasound image.")
    assert "ultrasound" not in fig.display_text
    without = [c for c in chunk_document(DOC, FLAT, [], {}, count=words) if c.kind == "figure"][0]
    assert "Figure description" not in without.embed_text


def test_parent_child():
    settings = {"max_tokens": 8, "child_max_tokens": 6, "context_line": True}
    chunks = chunk_document(DOC, settings, ["D1"], {}, count=words)
    by_ord = {c.ord: c for c in chunks}
    parents = [c for c in chunks if c.role == "parent"]
    children = [c for c in chunks if c.role == "child"]
    assert parents and children
    assert all(c.embed_text is None for c in parents)
    assert all(words(c.display_text) <= 8 or "." not in c.display_text for c in parents)
    for child in children:
        parent = by_ord[child.parent_ord]
        assert parent.role == "parent" and parent.ord < child.ord and parent.section == child.section
        assert child.display_text in parent.display_text
        assert child.embed_text.startswith("Doc › ")
    for c in chunks:
        if c.kind in ("table", "figure"):
            assert c.role == "leaf" and c.parent_ord is None
    # each parent is directly followed by its children
    for p in parents:
        kids = [c.ord for c in children if c.parent_ord == p.ord]
        assert kids == list(range(p.ord + 1, p.ord + 1 + len(kids)))


@pytest.mark.parametrize("name", ["s200_ctx", "s400_ctx", "s800_ctx", "s400_noctx", "parent_child"])
def test_real_chapter_respects_max_tokens(name):
    """bge-m3 tokens on a real GeneReviews chapter. Allowed over the limit: a single sentence, or a table
    part holding a single row (caption and footnotes repeat in every part, rule 4)."""
    settings = strategy(name)
    limit = settings.get("child_max_tokens", settings["max_tokens"])
    doc = genereviews.parse(GR.resolve())
    chunks = chunk_document(doc, settings, ["Spinocerebellar ataxia 20"], {})
    for c in chunks:
        if c.role == "parent":
            n = count_tokens(c.display_text)
            assert n <= settings["max_tokens"] or PARAGRAPH_JOIN not in c.display_text
            continue
        if c.kind == "figure" or count_tokens(c.embed_text) <= limit:
            continue
        if c.kind == "table":
            assert c.display_text.count("\n| ") == 2, c.display_text  # header line + one row
        else:
            assert PARAGRAPH_JOIN not in c.display_text and "\n" not in c.display_text, c.display_text


# ---- image helpers (no gateway: the clients are replaced) ---------------------------------------
def test_data_url_converts_gif_and_tiff(tmp_path):
    for suffix in (".gif", ".tif"):
        path = tmp_path / f"x{suffix}"
        Image.new("RGB", (4, 4), "red").save(path)
        assert data_url(path).startswith("data:image/png;base64,")
    jpg = tmp_path / "x.jpg"
    Image.new("RGB", (4, 4)).save(jpg)
    assert data_url(jpg).startswith("data:image/jpeg;base64,")


def test_describe_image_returns_none_after_failures(monkeypatch, tmp_path):
    jpg = tmp_path / "x.jpg"
    Image.new("RGB", (4, 4)).save(jpg)
    calls = []

    class Broken:
        def invoke(self, *a, **k):
            calls.append(1)
            raise TimeoutError

    monkeypatch.setattr(llm, "chat", lambda *a, **k: Broken())
    assert llm.describe_image(jpg, "describe", model="vlm", timeout=1) is None
    assert len(calls) == llm.ATTEMPTS


def test_visual_embedding_request_body(monkeypatch, tmp_path):
    jpg = tmp_path / "x.jpg"
    Image.new("RGB", (4, 4)).save(jpg)
    sent = {}

    class Embeddings:
        def create(self, **kw):
            sent.update(kw)
            return type("R", (), {"data": [type("D", (), {"embedding": [0.1] * 3})()]})()

    class Client:
        embeddings = Embeddings()

        def with_options(self, **kw):
            return self

    monkeypatch.setattr(embedding, "_client", lambda: Client())
    assert embedding.embed_image(jpg, model="vl", instruction="doc", timeout=1) == [0.1] * 3
    messages = sent["extra_body"]["messages"]
    assert sent["input"] == [""] and messages[0] == {"role": "system", "content": "doc"}
    assert messages[1]["content"][0]["type"] == "image_url"
    embedding.embed_visual_query("q", model="vl", instruction="query", timeout=1)
    assert sent["extra_body"]["messages"][1]["content"] == [{"type": "text", "text": "q"}]
