"""Source adapters on small real files (fixtures/, fetched once) and self-made DOCX/PDF (§3, §4, Appendix C).

Needs the seeded dev DB for declared_disease_ids; never calls the gateway.
"""

import shutil
import uuid
from pathlib import Path

import pytest

from core.db import connect
from kb.ingest.model import REPO_ROOT, Figure, Paragraph, Table
from kb.ingest.scope import in_scope, known_disease_ids
from kb.ingest.sources import SOURCES, genereviews, medlineplus, orphanet_def, pmc, pubmed, upload

FIX = Path(__file__).parent / "fixtures"
GR = FIX / "genereviews/docs/gene_NBK1116/sca20.nxml"
PMC = FIX / "pmc/docs/PMC10568385/PMC10568385.1.xml"


@pytest.fixture(scope="module")
def conn():
    with connect() as c:
        yield c


def blocks(doc, kind=None):
    return [b for s in doc.sections for b in s.blocks if kind is None or isinstance(b, kind)]


def paths(doc):
    return [s.heading_path for s in doc.sections]


# ---- orphanet_def -------------------------------------------------------------------------------
def test_orphanet_definition_and_exact_omim_only(conn):
    doc = orphanet_def.parse(FIX / "orphanet_def/docs/ORPHA_166282.xml")
    assert (doc.source_ref, doc.title, doc.doc_type) == ("ORPHA:166282", "Hereditary sick sinus syndrome", "definition")
    assert paths(doc) == [["Definition"]] and len(blocks(doc)) == 1
    assert blocks(doc)[0].text.startswith("A rare cardiac rhythm")
    # 608567 is an Exact mapping; 163800, 182190, 614090, 619464 are BTNT and must not be attached (T67)
    assert orphanet_def.declared_disease_ids(doc, conn) == ["OMIM:608567", "ORPHA:166282"]


def test_orphanet_list_docs():
    refs = orphanet_def.list_docs(FIX / "orphanet_def")
    assert [r.source_ref for r in refs] == ["ORPHA:648", "ORPHA:166282"]


# ---- medlineplus --------------------------------------------------------------------------------
def test_medlineplus_summary_and_known_omim_only(conn):
    doc = medlineplus.parse(FIX / "medlineplus/docs/MEDLINEPLUS_21263.xml")
    assert (doc.source_ref, doc.title, doc.doc_type) == ("MEDLINEPLUS:21263", "Abdominal wall defect", "summary")
    assert doc.url == "https://medlineplus.gov/genetics/condition/abdominal-wall-defect"
    assert doc.citation == "MedlinePlus Genetics. Abdominal wall defect. National Library of Medicine."
    assert doc.license == "MedlinePlus.gov (NLM); credit required"
    assert paths(doc) == [["Description"]] and len(blocks(doc)) > 1
    assert blocks(doc)[0].text.startswith("An abdominal wall defect is an opening")
    # 230750 and 310980 are OMIM keys absent from our disease table: dropped
    assert medlineplus.declared_disease_ids(doc, conn) == ["OMIM:164750"]


def test_medlineplus_list_docs():
    refs = medlineplus.list_docs(FIX / "medlineplus")
    assert [r.source_ref for r in refs] == ["MEDLINEPLUS:20351", "MEDLINEPLUS:21263"]


# ---- pubmed -------------------------------------------------------------------------------------
def test_pubmed_labelled_abstract_one_section_per_label(conn):
    doc = pubmed.parse(FIX / "pubmed/docs/PMID_32164556.xml")
    assert doc.source_ref == "PMID:32164556" and doc.url == "https://pubmed.ncbi.nlm.nih.gov/32164556/"
    assert paths(doc) == [["BACKGROUND"], ["METHODS"], ["RESULTS"], ["CONCLUSION"]]
    assert doc.citation.endswith("PMID:32164556") and "BMC Med Genet" in doc.citation
    assert pubmed.declared_disease_ids(doc, conn) == ["OMIM:163950"]


def test_pubmed_unlabelled_abstract():
    doc = pubmed.parse(FIX / "pubmed/docs/PMID_12634870.xml")
    assert paths(doc) == [["Abstract"]] and blocks(doc)[0].text.startswith("Noonan syndrome (NS)")


# ---- genereviews --------------------------------------------------------------------------------
def test_genereviews_metadata_and_codes(conn):
    doc = genereviews.parse(GR)
    assert (doc.source_ref, doc.title) == ("NBK1471", "Spinocerebellar Ataxia Type 20")
    assert doc.url == "https://www.ncbi.nlm.nih.gov/books/NBK1471/"
    assert doc.citation.startswith("Storey E, Gardner R. Spinocerebellar Ataxia Type 20. 2007 Feb 27 [Updated 2019 Apr 18]")
    assert genereviews.declared_disease_ids(doc, conn) == ["OMIM:608687"]
    assert [r.source_ref for r in genereviews.list_docs(FIX / "genereviews")] == ["NBK1471"]


def test_genereviews_sections_and_skips():
    doc = genereviews.parse(GR)
    assert paths(doc)[0] == ["Abstract"]
    assert blocks(doc)[0].text.startswith("Clinical characteristics: ")
    assert ["Diagnosis", "Suggestive Findings"] in paths(doc)
    flat = {h.casefold() for p in paths(doc) for h in p}
    assert not flat & {"references", "chapter notes", "literature cited", "revision history"}


def test_genereviews_floats_follow_first_citation():
    doc = genereviews.parse(GR)
    section = next(s for s in doc.sections if s.heading_path == ["Diagnosis", "Suggestive Findings"])
    kinds = [type(b).__name__ for b in section.blocks]
    i = kinds.index("Figure")
    assert "Figure 1" in section.blocks[i - 1].text  # right after the block that cites it
    f1, f2 = section.blocks[i], section.blocks[i + 1]
    assert f1.caption.startswith("Figure 1") and f1.image_path.endswith("sca20-Image001.jpg")
    assert f2.caption.startswith("Figure 2") and f2.image_path is None  # image not in the fixture


def test_genereviews_table_rowspan_and_footnotes():
    doc = genereviews.parse(GR)
    table = next(b for b in blocks(doc, Table) if b.caption.startswith("Table 1."))
    assert table.header == ["Duplication 1", "Method",
                            "Proportion of Probands with a Pathogenic Variant Detectable by Method"]
    assert table.rows[0][0] == table.rows[1][0] == "Heterozygous duplication at 11q12.2-11q12.3 2"
    assert table.rows[1][1] == "Targeted duplication analysis 4"
    assert table.footnotes.startswith("1. See Molecular Genetics")
    no_head = next(b for b in blocks(doc, Table) if b.caption.startswith("Table B."))
    assert no_head.header == ["Column 1", "Column 2"]


# ---- pmc ----------------------------------------------------------------------------------------
def test_pmc_full_text(conn):
    doc = pmc.parse(PMC)
    assert (doc.source, doc.source_ref, doc.license, doc.doc_type) == ("pmc", "PMC10568385", "CC BY-NC-ND", "article")
    assert paths(doc)[0] == ["Abstract"] and blocks(doc)[0].text.startswith("Background: ")
    figures = blocks(doc, Figure)
    assert len(figures) == 5
    assert [f.image_path is not None for f in figures] == [False, False, False, False, True]
    skip = {s.casefold() for s in pmc.CFG["skip_sections"]}
    assert not any(h.casefold() in skip for p in paths(doc) for h in p)


def test_pmc_abstract_fallback(conn):
    doc = pmc.parse(FIX / "pmc/docs/PMID_12634870.xml")
    assert (doc.source, doc.source_ref) == ("pmc", "PMID:12634870")
    assert [r.source_ref for r in pmc.list_docs(FIX / "pmc")] == ["PMC10568385", "PMID:12634870"]


# ---- upload (Docling) ---------------------------------------------------------------------------
@pytest.fixture
def uploaded():
    """Copy a fixture into data/uploads (figures are written next to it) and clean up afterwards."""
    made = []

    def put(name: str) -> Path:
        dst = REPO_ROOT / "data/uploads" / f"TEST_{uuid.uuid4().hex}{Path(name).suffix}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIX / "upload" / name, dst)
        made.append(dst)
        return dst

    yield put
    for p in made:
        p.unlink(missing_ok=True)
        shutil.rmtree(upload.figures_dir(p), ignore_errors=True)


def test_upload_docx(uploaded):
    raw = uploaded("guide_vi.docx")
    doc = upload.parse(raw)
    assert (doc.title, doc.language, doc.doc_type) == ("Hướng dẫn siêu âm thai quý một", "vi", "upload")
    assert paths(doc) == [["Đặc điểm lâm sàng"], ["Đặc điểm lâm sàng", "Dấu hiệu trước sinh"]]
    assert "- Nang bạch huyết vùng cổ\n- Phù thai" in [b.text for b in blocks(doc, Paragraph)]
    (table,) = blocks(doc, Table)
    assert table.header == ["Dấu hiệu", "Tần suất"] and table.rows == [["Nang bạch huyết", "Thường gặp"], ["Phù thai", "Hiếm"]]
    (figure,) = blocks(doc, Figure)
    assert (REPO_ROOT / figure.image_path).is_file()


def test_upload_pdf(uploaded):
    raw = uploaded("guide_en.pdf")
    doc = upload.parse(raw)
    assert doc.language == "en" and doc.title == raw.stem  # Docling gives no `title` item for this PDF
    assert ["Clinical Characteristics"] in paths(doc)
    (table,) = blocks(doc, Table)
    assert table.header == ["Column 1", "Column 2"]  # no header row detected (T76)
    (figure,) = blocks(doc, Figure)
    assert (REPO_ROOT / figure.image_path).is_file()


def test_language():
    assert upload.language("Tăng khoảng sáng sau gáy") == "vi"
    assert upload.language("Increased nuchal translucency") == "en"


# ---- determinism (rechunk relies on it, §3) -----------------------------------------------------
@pytest.mark.parametrize("source,path", [
    ("orphanet_def", FIX / "orphanet_def/docs/ORPHA_648.xml"),
    ("medlineplus", FIX / "medlineplus/docs/MEDLINEPLUS_20351.xml"),
    ("pubmed", FIX / "pubmed/docs/PMID_32164556.xml"),
    ("genereviews", GR),
    ("pmc", PMC),
    ("pmc", FIX / "pmc/docs/PMID_12634870.xml"),
])
def test_parse_is_deterministic(source, path):
    assert SOURCES[source].parse(path) == SOURCES[source].parse(path)


@pytest.mark.parametrize("name", ["guide_vi.docx", "guide_en.pdf"])
def test_upload_parse_is_deterministic(uploaded, name):
    raw = uploaded(name)
    assert upload.parse(raw) == upload.parse(raw)


# ---- scope --------------------------------------------------------------------------------------
def test_scope(conn):
    assert in_scope(conn, ["OMIM:163950", "OMIM:608687"]) == ["OMIM:163950"]
    assert known_disease_ids(conn, ["OMIM:176876", "OMIM:163950", "OMIM:163950"]) == ["OMIM:163950"]  # gene MIM dropped
