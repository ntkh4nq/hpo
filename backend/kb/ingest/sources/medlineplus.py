"""MedlinePlus Genetics condition summaries (NLM; docs/sources-plan.md §2.1, T122).

One document per <health-condition-summary> of ghr-summaries.xml, split to docs/MEDLINEPLUS_<id>.xml.
Disease codes: the condition's OMIM db-keys that exist in our disease table (1,282 of 1,305 conditions have one).
The text lists the condition's features, so it is evidence for (disease, finding) pairs, not an overview (T110).
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from kb.ingest import fetch
from kb.ingest.config import SOURCE_SETTINGS
from kb.ingest.model import DocRef, ManifestFile, Paragraph, ParsedDocument, Section, absolute, rel
from kb.ingest.scope import known_disease_ids

DECLARES_CODES = True  # documents without an in-scope code are not ingested (ingestion-plan §4)
CFG = SOURCE_SETTINGS["medlineplus"]
NS = {"m": CFG["namespace"], "html": "http://www.w3.org/1999/xhtml"}


def norm(text: str | None) -> str:
    return " ".join((text or "").split())


def download(release_dir: Path) -> list[ManifestFile]:
    """ghr-summaries.xml, split into docs/MEDLINEPLUS_<id>.xml, one per condition."""
    raw = fetch.download(CFG["url"], release_dir / "ghr-summaries.xml")
    docs = release_dir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    conditions = ET.parse(raw).getroot().findall("m:health-condition-summary", NS)
    for c in conditions:
        (docs / f"MEDLINEPLUS_{c.get('id')}.xml").write_bytes(ET.tostring(c, encoding="utf-8"))
    print(f"medlineplus: {len(conditions)} conditions")
    return [ManifestFile("ghr-summaries.xml", CFG["url"], CFG["license"])]


def list_docs(release_dir: Path) -> list[DocRef]:
    paths = (release_dir / "docs").glob("MEDLINEPLUS_*.xml")
    return [DocRef(f"MEDLINEPLUS:{p.stem.removeprefix('MEDLINEPLUS_')}", rel(p))
            for p in sorted(paths, key=lambda p: int(p.stem.removeprefix("MEDLINEPLUS_")))]


def parse(raw_path: Path) -> ParsedDocument:
    c = ET.parse(raw_path).getroot()
    name = norm(c.findtext("m:name", namespaces=NS))
    sections = []
    for text in c.findall("m:text-list/m:text", NS):
        role = norm(text.findtext("m:text-role", namespaces=NS)) or "description"
        paragraphs = [Paragraph(norm("".join(p.itertext()))) for p in text.iter(f"{{{NS['html']}}}p")]
        sections.append(Section([role.capitalize()], [p for p in paragraphs if p.text]))
    return ParsedDocument(
        source="medlineplus", source_ref=f"MEDLINEPLUS:{c.get('id')}", title=name,
        citation=f"MedlinePlus Genetics. {name}. National Library of Medicine.",
        url=norm(c.findtext("m:ghr-page", namespaces=NS)) or None, license=CFG["license"], language="en",
        doc_type="summary", raw_path=rel(raw_path), sections=[s for s in sections if s.blocks])


def declared_disease_ids(doc: ParsedDocument, conn) -> list[str]:
    c = ET.parse(absolute(doc.raw_path)).getroot()
    codes = [f"OMIM:{norm(k.findtext('m:key', namespaces=NS))}" for k in c.findall("m:db-key-list/m:db-key", NS)
             if norm(k.findtext("m:db", namespaces=NS)) == "OMIM"]
    return known_disease_ids(conn, codes)
