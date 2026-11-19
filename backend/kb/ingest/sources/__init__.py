"""Source adapters (docs/ingestion-plan.md §3): download, list_docs, parse, declared_disease_ids."""

from types import ModuleType

from kb.ingest.sources import genereviews, orphanet_def, pmc, pubmed, upload

SOURCES: dict[str, ModuleType] = {
    "orphanet_def": orphanet_def,
    "pubmed": pubmed,
    "genereviews": genereviews,
    "pmc": pmc,
    "upload": upload,
}
