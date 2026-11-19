"""GeneReviews chapters (BITS XML from the NCBI litarch tar; docs/ingestion-plan.md §2–§4).

Documents are the current chapters listed in GRtitle_shortname_NBKid.txt; retired chapters, appendices and
author lists in the tar are not ingested (T77). Disease codes: the chapter's OMIM numbers in
NBKid_shortname_OMIM.txt (gene MIM numbers in that file drop out because they are not in `disease`).
"""

import csv
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path

from kb.ingest import fetch, jats
from kb.ingest.config import SOURCE_SETTINGS
from kb.ingest.model import DocRef, ManifestFile, ParsedDocument, absolute, rel
from kb.ingest.scope import known_disease_ids

DECLARES_CODES = True  # documents without an in-scope code are not ingested (§4)
CFG = SOURCE_SETTINGS["genereviews"]
TITLES_FILE = "GRtitle_shortname_NBKid.txt"   # ISO-8859-1
OMIM_FILE = "NBKid_shortname_OMIM.txt"
CHAPTER_DIR = "docs/gene_NBK1116"


def release_dir_of(raw_path: Path) -> Path:
    return raw_path.resolve().parents[2]  # <release>/docs/gene_NBK1116/<shortname>.nxml


def _rows(path: Path) -> list[list[str]]:
    with path.open(encoding="latin-1", newline="") as f:
        return [r for r in csv.reader(f, delimiter="\t") if r and not r[0].startswith("#")]


def nbk_by_shortname(release_dir: Path) -> dict[str, str]:
    return {r[0]: r[2] for r in _rows(release_dir / TITLES_FILE)}


def download(release_dir: Path) -> list[ManifestFile]:
    """The litarch tar (path looked up in file_list.txt) + the two NCBI index files; tar extracted to docs/
    without its PDFs."""
    release_dir.mkdir(parents=True, exist_ok=True)
    listing = release_dir / "file_list.txt"
    fetch.download(CFG["file_list_url"], listing)
    tar_path = next(line.split("\t")[0] for line in listing.read_text(encoding="latin-1").splitlines()
                    if CFG["book_id"] in line.split("\t")[1:])
    tar_url = CFG["litarch_url"] + tar_path
    tar = fetch.download(tar_url, release_dir / Path(tar_path).name)
    fetch.download(CFG["omim_map_url"], release_dir / OMIM_FILE)
    fetch.download(CFG["titles_url"], release_dir / TITLES_FILE)
    with tarfile.open(tar) as t:
        members = [m for m in t.getmembers() if m.isfile() and not m.name.endswith(".pdf")]
        t.extractall(release_dir / "docs", members=members, filter="data")
    print(f"genereviews: {tar_path}, {len(list_docs(release_dir))} current chapters")
    return [ManifestFile("file_list.txt", CFG["file_list_url"], None),
            ManifestFile(tar.name, tar_url, CFG["license"]),
            ManifestFile(OMIM_FILE, CFG["omim_map_url"], None),
            ManifestFile(TITLES_FILE, CFG["titles_url"], None)]


def list_docs(release_dir: Path) -> list[DocRef]:
    out = []
    for short, nbk in sorted(nbk_by_shortname(release_dir).items(), key=lambda kv: kv[1]):
        path = release_dir / CHAPTER_DIR / f"{short}.nxml"
        if path.is_file():
            out.append(DocRef(nbk, rel(path)))
    return out


def _name(contrib: ET.Element) -> str:
    surname = contrib.findtext(".//surname") or ""
    initials = "".join(p[0] for p in (contrib.findtext(".//given-names") or "").replace("-", " ").split())
    return f"{surname} {initials}".strip() or jats.text(contrib.find(".//collab"))


def _date(el: ET.Element | None) -> str:
    if el is None:
        return ""
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    month = el.findtext("month")
    parts = [el.findtext("year"), months[int(month) - 1] if month else None, el.findtext("day")]
    return " ".join(p for p in parts if p)


def _citation(root: ET.Element, meta: ET.Element, title: str) -> str:
    authors = [_name(c) for c in meta.findall("contrib-group/contrib[@contrib-type='author']")]
    who = ", ".join(authors[:3]) + (", et al" if len(authors) > 3 else "")
    history = meta.find("pub-history")
    created = _date(history.find("date[@date-type='created']")) if history is not None else ""
    latest = None
    if history is not None:
        dates = [d for d in history.findall("date") if d.get("date-type") in ("updated", "revised")]
        latest = max(dates, key=lambda d: tuple(int(d.findtext(k) or 0) for k in ("year", "month", "day")),
                     default=None)
    when = created + (f" [Updated {_date(latest)}]" if latest is not None else "")
    years = [y.text for y in root.findall("book-meta/pub-date/year")]
    span = f"{years[0]}-{years[-1]}" if years else ""
    parts = [who, title, when, f"In: GeneReviews® [Internet]. Seattle (WA): University of Washington, Seattle; {span}"]
    return ". ".join(p for p in parts if p) + "."


def parse(raw_path: Path) -> ParsedDocument:
    root = ET.parse(raw_path).getroot()
    part = root.find("book-part")
    meta = part.find("book-part-meta")
    title = jats.text(meta.find("title-group/title"))
    nbk = nbk_by_shortname(release_dir_of(raw_path))[root.get("id")]
    return ParsedDocument(
        source="genereviews", source_ref=nbk, title=title, citation=_citation(root, meta, title),
        url=CFG["chapter_url"].format(nbk=nbk), license=CFG["license"], language="en", doc_type="review_chapter",
        raw_path=rel(raw_path),
        sections=jats.read_sections(meta, part.find("body"), raw_path.parent, CFG["skip_sections"]))


def declared_disease_ids(doc: ParsedDocument, conn) -> list[str]:
    release_dir = release_dir_of(absolute(doc.raw_path))
    codes = [f"OMIM:{r[2]}" for r in _rows(release_dir / OMIM_FILE) if r[0] == doc.source_ref]
    return known_disease_ids(conn, codes)
