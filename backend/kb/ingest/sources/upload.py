"""User uploads: PDF with a text layer or DOCX, read with Docling (docs/ingestion-plan.md §10, Appendix C.2).

Docling runs with its defaults except: no OCR, keep picture images, images at `docling_images_scale` (T65, T76).
Uploads declare no disease codes (T75): the admin gives them with the upload (table declared_disease,
docs/evidence-plan.md §6) and the pipeline reads them from there.
"""

import unicodedata
from functools import lru_cache
from pathlib import Path

from kb.ingest.config import INGEST, SOURCE_SETTINGS
from kb.ingest.model import Block, Figure, Paragraph, ParsedDocument, PermanentIngestError, Section, Table, rel

DECLARES_CODES = False  # codes come from declared_disease; never filtered by scope (T75, evidence-plan §6)
CFG = SOURCE_SETTINGS["upload"]
VI_LETTERS = set(unicodedata.normalize("NFC", "àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ"))
VI_LETTERS |= {c.upper() for c in VI_LETTERS}


def language(text: str) -> str:
    """'vi' when Vietnamese-diacritic letters make up at least `vi_char_ratio` of all letters (§7)."""
    letters = [c for c in unicodedata.normalize("NFC", text) if c.isalpha()]
    if not letters:
        return "en"
    return "vi" if sum(c in VI_LETTERS for c in letters) / len(letters) >= INGEST["vi_char_ratio"] else "en"


@lru_cache(maxsize=1)
def _converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(do_ocr=False, generate_picture_images=True,
                                 images_scale=INGEST["docling_images_scale"])
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


def figures_dir(raw_path: Path) -> Path:
    return raw_path.with_name(f"{raw_path.stem}_figures")


def _table(item, doc) -> Table:
    frame = item.export_to_dataframe(doc)
    columns = [str(c) for c in frame.columns]
    if all(isinstance(c, int) for c in frame.columns):  # Docling found no header row (T76)
        columns = [f"Column {i}" for i in range(1, len(columns) + 1)]
    rows = [["" if v is None else " ".join(str(v).split()) for v in row] for row in frame.itertuples(index=False)]
    return Table(item.caption_text(doc), columns, rows, "")


def parse(raw_path: Path) -> ParsedDocument:
    from docling.exceptions import ConversionError

    try:
        doc = _converter().convert(raw_path).document
    except ConversionError as e:
        raise PermanentIngestError(f"unreadable document: {e}") from e

    title = None
    path: list[str] | None = None  # None until the first section header
    sections: list[Section] = []
    pending_list: list[str] = []
    n_figures = 0

    def add(block: Block) -> None:
        current = path if path is not None else [title or raw_path.stem]
        if not sections or sections[-1].heading_path != current:
            sections.append(Section(list(current)))
        sections[-1].blocks.append(block)

    def flush_list() -> None:
        if pending_list:
            add(Paragraph("\n".join(f"- {t}" for t in pending_list)))
            pending_list.clear()

    for item, _ in doc.iterate_items():
        label = item.label.value
        if label != "list_item":
            flush_list()
        if label == "title":
            title = title or " ".join(item.text.split())
        elif label == "section_header":
            level = max(getattr(item, "level", 1), 1)
            path = (path or [])[:level - 1] + [" ".join(item.text.split())]
        elif label in ("text", "paragraph"):
            if item.text.strip():
                add(Paragraph(" ".join(item.text.split())))
        elif label == "list_item":
            if item.text.strip():
                pending_list.append(" ".join(item.text.split()))
        elif label == "table":
            add(_table(item, doc))
        elif label in ("picture", "chart"):
            image = item.get_image(doc)
            image_path = None
            if image is not None:
                n_figures += 1
                out = figures_dir(raw_path) / f"figure_{n_figures:03d}.png"
                out.parent.mkdir(parents=True, exist_ok=True)
                image.save(out, format="PNG")
                image_path = rel(out)
            add(Figure(item.caption_text(doc), image_path))
    flush_list()

    all_text = " ".join(b.text if isinstance(b, Paragraph) else "" for s in sections for b in s.blocks)
    if not all_text.strip() and not any(isinstance(b, Table) for s in sections for b in s.blocks):
        raise PermanentIngestError("no text layer: scanned documents are not supported")
    return ParsedDocument(
        source="upload", source_ref=raw_path.stem, title=title or raw_path.stem, citation=None, url=None,
        license=CFG["license"], language=language(all_text), doc_type="upload", raw_path=rel(raw_path),
        sections=sections)
