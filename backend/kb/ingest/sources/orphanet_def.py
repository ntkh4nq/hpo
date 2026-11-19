"""Orphanet definitions (Orphadata Science en_product1.xml, CC BY 4.0; docs/ingestion-plan.md §2–§4).

One document per <Disorder> that has a Definition text section, split to docs/ORPHA_<code>.xml.
Disease codes: the disorder's ORPHA code plus OMIM codes it maps with an Exact relation (T67).
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from kb.ingest import fetch
from kb.ingest.config import SOURCE_SETTINGS
from kb.ingest.model import DocRef, ManifestFile, Paragraph, ParsedDocument, Section, absolute, rel
from kb.ingest.scope import known_disease_ids

DECLARES_CODES = True  # documents without an in-scope code are not ingested (§4)
CFG = SOURCE_SETTINGS["orphanet_def"]


def norm(text: str | None) -> str:
    return " ".join((text or "").split())


def definition(disorder: ET.Element) -> str:
    for section in disorder.findall("SummaryInformationList/SummaryInformation/TextSectionList/TextSection"):
        kind = section.find("TextSectionType")
        if kind is not None and kind.get("id") == CFG["definition_section_type"]:
            return norm(section.findtext("Contents"))
    return ""


def download(release_dir: Path) -> list[ManifestFile]:
    """en_product1.xml, split into docs/ORPHA_<code>.xml for each top-level <Disorder> with a Definition."""
    raw = fetch.download(CFG["url"], release_dir / "en_product1.xml")
    docs = release_dir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    kept = 0
    for disorder in ET.parse(raw).getroot().find("DisorderList").findall("Disorder"):  # nested ones are associations
        if definition(disorder):
            (docs / f"ORPHA_{disorder.findtext('OrphaCode')}.xml").write_bytes(ET.tostring(disorder, encoding="utf-8"))
            kept += 1
    print(f"orphanet_def: {kept} disorders with a definition")
    return [ManifestFile("en_product1.xml", CFG["url"], CFG["license"])]


def list_docs(release_dir: Path) -> list[DocRef]:
    paths = (release_dir / "docs").glob("ORPHA_*.xml")
    return [DocRef(f"ORPHA:{p.stem.removeprefix('ORPHA_')}", rel(p))
            for p in sorted(paths, key=lambda p: int(p.stem.removeprefix("ORPHA_")))]


def parse(raw_path: Path) -> ParsedDocument:
    disorder = ET.parse(raw_path).getroot()
    code = disorder.findtext("OrphaCode")
    name = norm(disorder.findtext("Name"))
    return ParsedDocument(
        source="orphanet_def", source_ref=f"ORPHA:{code}", title=name,
        citation=f"Orphanet. {name} (ORPHA:{code}). Definition. Orphadata Science, en_product1.xml.",
        url=norm(disorder.findtext("ExpertLink")) or None, license=CFG["license"], language="en",
        doc_type="definition", raw_path=rel(raw_path),
        sections=[Section(["Definition"], [Paragraph(definition(disorder))])])


def declared_disease_ids(doc: ParsedDocument, conn) -> list[str]:
    disorder = ET.parse(absolute(doc.raw_path)).getroot()
    codes = [f"ORPHA:{disorder.findtext('OrphaCode')}"]
    for ref in disorder.findall("ExternalReferenceList/ExternalReference"):
        relation = norm(ref.findtext("DisorderMappingRelation/Name"))
        if ref.findtext("Source") == "OMIM" and relation.startswith(CFG["exact_relation_prefix"]):
            codes.append(f"OMIM:{norm(ref.findtext('Reference'))}")
    return known_disease_ids(conn, codes)
