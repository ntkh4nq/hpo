"""Source adapters (docs/ingestion-plan.md §3): download, list_docs, parse, and declared_disease_ids for sources with
DECLARES_CODES = True (the others get their codes from table declared_disease, docs/evidence-plan.md §6)."""

from types import ModuleType

from kb.ingest.sources import genereviews, medlineplus, orphanet_def, pmc, pubmed, upload

SOURCES: dict[str, ModuleType] = {
    "orphanet_def": orphanet_def,
    "pubmed": pubmed,
    "genereviews": genereviews,
    "pmc": pmc,
    "medlineplus": medlineplus,
    "upload": upload,
}
