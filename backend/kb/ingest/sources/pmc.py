"""Articles linked from the Fe2Gen collection (docs/ingestion-plan.md §2–§4, T70).

Full text (JATS XML + images from the PMC open-data bucket) when the licence is open, otherwise the
PubMed abstract. The source declares no disease codes (T75): an admin declares them (table declared_disease,
docs/evidence-plan.md §6) and the pipeline reads them from there.
"""

import hashlib
import io
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import openpyxl

from kb.ingest import fetch, jats
from kb.ingest.config import SOURCE_SETTINGS
from kb.ingest.model import DocRef, ManifestFile, ParsedDocument, absolute, rel
from kb.ingest.sources import pubmed

DECLARES_CODES = False  # codes come from declared_disease; never filtered by scope (T75, evidence-plan §6)
CFG = SOURCE_SETTINGS["pmc"]
PUBMED_LINK = re.compile(r"(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)(\d+)")
PMC_LINK = re.compile(r"ncbi\.nlm\.nih\.gov/(?:pmc/)?articles/(PMC\d+)")
URL = re.compile(r"https?://[^\s\"'<>]+")


# ---- download -----------------------------------------------------------------------------------
def fe2gen_links() -> list[dict]:
    """Every PubMed / PMC link in the Fe2Gen spreadsheets: cell text and cell hyperlinks (T70)."""
    urls = set()
    with zipfile.ZipFile(absolute(CFG["fe2gen_zip"])) as z:
        for name in sorted(n for n in z.namelist() if n.endswith(".xlsx")):
            book = openpyxl.load_workbook(io.BytesIO(z.read(name)))
            for sheet in book.worksheets:
                for row in sheet.iter_rows():
                    for cell in row:
                        for value in (cell.value if isinstance(cell.value, str) else None,
                                      cell.hyperlink.target if cell.hyperlink else None):
                            urls.update(URL.findall(value or ""))
    links = []
    for url in sorted(urls):
        if m := PMC_LINK.search(url):
            links.append({"url": url, "pmcid": m.group(1)})
        elif m := PUBMED_LINK.search(url):
            links.append({"url": url, "pmid": m.group(1)})
    return links


def _europepmc(query: str) -> dict | None:
    data = json.loads(fetch.get(CFG["europepmc_search_url"], params={"query": query, "format": "json"},
                                throttle="europepmc", per_second=3))
    results = data.get("resultList", {}).get("result", [])
    return results[0] if results else None


def _s3_latest(pmcid: str) -> str | None:
    """Latest '<PMCID>.<version>' prefix in the PMC open-data bucket, or None."""
    listing = fetch.get(CFG["s3_url"] + "/", params={"list-type": "2", "prefix": f"{pmcid}."}).decode()
    versions = {int(v) for v in re.findall(rf"<Key>{pmcid}\.(\d+)/", listing)}
    return f"{pmcid}.{max(versions)}" if versions else None


def _s3_https(s3_url: str) -> tuple[str, str | None]:
    """s3://pmc-oa-opendata/<key>?md5=<hex> -> (https URL, md5)."""
    parsed = urllib.parse.urlparse(s3_url)
    md5 = urllib.parse.parse_qs(parsed.query).get("md5", [None])[0]
    return f"{CFG['s3_url']}{parsed.path}", md5


def _full_text(pmcid: str, release_dir: Path) -> tuple[list[ManifestFile], str | None, bool]:
    """Fetch XML + images when the article is open (licence, not retracted).
    Returns (manifest files, pmid from the metadata, fetched?)."""
    version = _s3_latest(pmcid)
    if version is None:
        return [], None, False
    meta_url = f"{CFG['s3_url']}/{version}/{version}.json"
    meta = json.loads(fetch.get(meta_url))
    pmid = str(meta["pmid"]) if meta.get("pmid") else None
    if meta.get("license_code") not in CFG["open_licenses"] or meta.get("is_retracted") is True:
        return [], pmid, False
    out_dir = release_dir / "docs" / pmcid
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{version}.json").write_bytes(json.dumps(meta, indent=1).encode())
    files = [ManifestFile(f"docs/{pmcid}/{version}.json", meta_url, meta["license_code"])]
    xml_url = f"{CFG['s3_url']}/{version}/{version}.xml"
    fetch.download(xml_url, out_dir / f"{version}.xml")
    files.append(ManifestFile(f"docs/{pmcid}/{version}.xml", xml_url, meta["license_code"]))
    for media in meta.get("media_urls", []):
        url, md5 = _s3_https(media)
        name = Path(urllib.parse.urlparse(url).path).name
        if Path(name).suffix.lower() not in CFG["image_extensions"]:
            continue
        fetch.download(url, out_dir / name)
        if md5 and hashlib.md5((out_dir / name).read_bytes()).hexdigest() != md5:
            raise ValueError(f"md5 mismatch for {url}")
        files.append(ManifestFile(f"docs/{pmcid}/{name}", url, meta["license_code"]))
    return files, pmid, True


def download(release_dir: Path) -> list[ManifestFile]:
    """§2 `pmc`: links → PMCID → full text from the PMC open-data bucket, otherwise the PubMed abstract."""
    release_dir.mkdir(parents=True, exist_ok=True)
    links = fe2gen_links()
    (release_dir / "links.json").write_text(json.dumps(links, indent=1))
    pmcids = {x["pmcid"] for x in links if "pmcid" in x}
    pmids = {x["pmid"] for x in links if "pmid" in x}
    for pmid in sorted(pmids, key=int):  # PMID -> PMCID (Europe PMC)
        hit = _europepmc(f"EXT_ID:{pmid} AND SRC:MED")
        if hit and hit.get("pmcid"):
            pmcids.add(hit["pmcid"])
    files: list[ManifestFile] = []
    abstract_pmids, full = set(), set()
    for pmcid in sorted(pmcids, key=lambda x: int(x[3:])):
        got, pmid, fetched = _full_text(pmcid, release_dir)
        files += got
        if fetched:
            full.add(pmid)
            continue
        if pmid is None:
            hit = _europepmc(f"PMCID:{pmcid}")
            pmid = hit.get("pmid") if hit else None
        if pmid:
            abstract_pmids.add(pmid)
    abstract_pmids |= pmids
    abstract_pmids -= full
    abstract_files, missing = pubmed.fetch_abstracts(sorted(abstract_pmids, key=int), release_dir)
    files += abstract_files
    print(f"pmc: {len(links)} links ({len(pmcids)} PMCIDs after PMID lookup); {len(full)} full texts, "
          f"{len(abstract_pmids) - len(missing)} abstracts, {len(missing)} PMIDs without abstract")
    return files


def list_docs(release_dir: Path) -> list[DocRef]:
    docs = release_dir / "docs"
    out = []
    for d in sorted(p for p in docs.glob("PMC*") if p.is_dir()):
        xml = next(iter(sorted(d.glob("PMC*.xml"))), None)
        if xml is not None:
            out.append(DocRef(d.name, rel(xml)))
    for p in sorted(docs.glob("PMID_*.xml")):
        out.append(DocRef(f"PMID:{p.stem.removeprefix('PMID_')}", rel(p)))
    return out


def parse(raw_path: Path) -> ParsedDocument:
    if raw_path.name.startswith("PMID_"):
        return pubmed.parse_article(raw_path, "pmc", SOURCE_SETTINGS["pubmed"]["license"])
    meta_json = json.loads(raw_path.with_suffix(".json").read_text())
    root = ET.parse(raw_path).getroot()
    meta = root.find("front/article-meta")
    pmcid = meta_json["pmcid"]
    return ParsedDocument(
        source="pmc", source_ref=pmcid, title=jats.text(meta.find("title-group/article-title")),
        citation=meta_json.get("citation"), url=CFG["article_url"].format(pmcid=pmcid),
        license=meta_json["license_code"], language="en", doc_type="article", raw_path=rel(raw_path),
        sections=jats.read_sections(meta, root.find("body"), raw_path.parent, CFG["skip_sections"],
                                    root.find("floats-group")))
