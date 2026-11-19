"""PubMed abstracts of the PMIDs that HPO annotations cite (docs/ingestion-plan.md §2–§4).

The reader is shared with the abstract fallback of the `pmc` source.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from core.db import connect
from kb.ingest import fetch
from kb.ingest.config import SOURCE_SETTINGS
from kb.ingest.model import DocRef, ManifestFile, Paragraph, ParsedDocument, Section, rel
from kb.ingest.scope import POSITIVE, known_disease_ids, scope_disease_ids

DECLARES_CODES = True  # documents without an in-scope code are not ingested (§4)
CFG = SOURCE_SETTINGS["pubmed"]


def norm(text: str) -> str:
    return " ".join(text.split())


def text_of(el: ET.Element | None) -> str:
    return norm("".join(el.itertext())) if el is not None else ""


def _citation(article: ET.Element, pmid: str) -> str:
    authors = []
    for a in article.findall(".//AuthorList/Author"):
        name = a.findtext("CollectiveName") or " ".join(x for x in (a.findtext("LastName"), a.findtext("Initials")) if x)
        if name:
            authors.append(name)
    who = ", ".join(authors[:3]) + (", et al" if len(authors) > 3 else "")
    journal = article.findtext(".//Journal/ISOAbbreviation") or article.findtext(".//Journal/Title") or ""
    date = article.find(".//JournalIssue/PubDate")
    year = (date.findtext("Year") or date.findtext("MedlineDate") or "") if date is not None else ""
    parts = [who, text_of(article.find(".//ArticleTitle")), journal, year]
    return ". ".join(p.rstrip(".") for p in parts if p) + f". PMID:{pmid}"


def parse_article(path: Path, source: str, license_: str) -> ParsedDocument:
    """One <PubmedArticle> file. Labelled AbstractText → one section per label; unlabelled → ["Abstract"]."""
    article = ET.parse(path).getroot()
    pmid = article.findtext(".//MedlineCitation/PMID")
    doc = ParsedDocument(
        source=source, source_ref=f"PMID:{pmid}", title=text_of(article.find(".//ArticleTitle")),
        citation=_citation(article, pmid), url=CFG["article_url"].format(pmid=pmid), license=license_,
        language="en", doc_type="abstract", raw_path=rel(path))
    for part in article.findall(".//Abstract/AbstractText"):
        text = text_of(part)
        if not text:
            continue
        path_ = [part.get("Label")] if part.get("Label") else ["Abstract"]
        if not doc.sections or doc.sections[-1].heading_path != path_:
            doc.sections.append(Section(path_))
        doc.sections[-1].blocks.append(Paragraph(text))
    return doc


def pmid_of(doc: ParsedDocument) -> str:
    return doc.source_ref.removeprefix("PMID:")


def cited_disease_ids(conn, pmid: str) -> list[str]:
    """Diseases with a positive annotation whose `references` cite PMID:<pmid> (T75)."""
    rows = conn.execute(f"""SELECT DISTINCT dp.disease_id FROM disease_phenotype dp
                            WHERE %s = ANY(dp."references") AND {POSITIVE}""", (f"PMID:{pmid}",))
    return known_disease_ids(conn, [r[0] for r in rows])


def cited_pmids(conn) -> list[str]:
    """Every PMID cited by a positive annotation of an in-scope disease, as digits, sorted numerically."""
    rows = conn.execute(f"""SELECT DISTINCT r FROM disease_phenotype dp, unnest(dp."references") r
                            WHERE r LIKE 'PMID:%%' AND {POSITIVE} AND dp.disease_id = ANY(%s)""",
                        (scope_disease_ids(conn),))
    return sorted({r[0].removeprefix("PMID:") for r in rows if r[0].removeprefix("PMID:").isdigit()}, key=int)


def fetch_abstracts(pmids: list[str], release_dir: Path) -> tuple[list[ManifestFile], list[str]]:
    """efetch in batches into efetch/batch_NNN.xml; each article with an <Abstract> to docs/PMID_<n>.xml.
    Returns the manifest files and the PMIDs that have no abstract (or were not returned)."""
    (release_dir / "efetch").mkdir(parents=True, exist_ok=True)
    (release_dir / "docs").mkdir(parents=True, exist_ok=True)
    files, written = [], set()
    size = CFG["batch_size"]
    for i in range(0, len(pmids), size):
        batch = pmids[i:i + size]
        name = f"efetch/batch_{i // size + 1:03d}.xml"
        (release_dir / name).write_bytes(fetch.eutils(CFG["efetch_url"], db="pubmed", retmode="xml", id=",".join(batch)))
        files.append(ManifestFile(name, CFG["efetch_url"], CFG["license"]))
        for article in ET.parse(release_dir / name).getroot().findall("PubmedArticle"):
            pmid = article.findtext("MedlineCitation/PMID")
            if article.find(".//Abstract") is not None and pmid in batch:
                (release_dir / "docs" / f"PMID_{pmid}.xml").write_bytes(ET.tostring(article, encoding="utf-8"))
                written.add(pmid)
    return files, [p for p in pmids if p not in written]


def download(release_dir: Path) -> list[ManifestFile]:
    with connect() as conn:
        pmids = cited_pmids(conn)
    files, missing = fetch_abstracts(pmids, release_dir)
    print(f"pubmed: {len(pmids)} PMIDs, {len(pmids) - len(missing)} with an abstract, {len(missing)} without (skipped)")
    return files


def list_docs(release_dir: Path) -> list[DocRef]:
    paths = sorted((release_dir / "docs").glob("PMID_*.xml"), key=lambda p: int(p.stem.removeprefix("PMID_")))
    return [DocRef(f"PMID:{p.stem.removeprefix('PMID_')}", rel(p)) for p in paths]


def parse(raw_path: Path) -> ParsedDocument:
    return parse_article(raw_path, "pubmed", CFG["license"])


def declared_disease_ids(doc: ParsedDocument, conn) -> list[str]:
    return cited_disease_ids(conn, pmid_of(doc))
