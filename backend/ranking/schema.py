"""Data types of phase 4 (profile -> ranked diseases and genes) (docs/ranking-plan.md §7).

Every disease, gene and HPO code here is read from the KB. `score` only orders diseases: it is never a
probability or a "% risk" (principle #2). Frequencies are P(phenotype | disease).
"""

from typing import Literal

from pydantic import BaseModel

QueryAssertion = Literal["PRESENT", "SUSPECTED"]
MatchKind = Literal["excluded", "exact", "disease_more_specific", "disease_more_general", "related", "none"]


class QueryTerm(BaseModel):
    hpo_id: str
    assertion: QueryAssertion
    weight: float
    from_options: list[str] = []       # the AMBIGUOUS options this common ancestor stands for; empty otherwise
    finding_ids: list[str] = []        # ids of the findings behind this code (phase 5); empty when none given


class Skipped(BaseModel):
    phrase: str | None                 # None when the code came without a finding (rank_terms)
    hpo_id: str | None
    reason: str
    finding_id: str | None = None      # id of the skipped finding (phase 5); None for codes skipped by rank_terms


class Match(BaseModel):                # how one query term relates to the disease
    query_hpo_id: str
    assertion: QueryAssertion
    weight: float
    kind: MatchKind
    disease_hpo_id: str | None         # the disease's annotation behind `kind`; None for "none"
    common_ancestor_id: str | None     # "related" only
    # one entry per source row of that annotation (phenotype.hpoa), in row order (principle #6)
    frequency_raw: list[str | None] = []
    evidence: list[str | None] = []
    references: list[list[str]] = []


class Unmentioned(BaseModel):          # a frequent prenatal phenotype of the disease the description did not mention
    hpo_id: str
    label_en: str
    label_vi: str | None
    frequency_raw: list[str | None]


class Gene(BaseModel):
    ncbi_gene_id: str
    symbol: str
    association_type: str | None
    source: str | None


class Suggestion(BaseModel):
    rank: float                        # mean rank of its tie group
    disease_id: str
    disease_name: str | None
    source_db: str
    score: float
    matches: list[Match]
    unmentioned: list[Unmentioned]
    genes: list[Gene]


class GeneSuggestion(BaseModel):       # genes link to phenotypes only through a disease (principle #3)
    ncbi_gene_id: str
    symbol: str
    best_rank: float
    disease_ids: list[str]


class Ranking(BaseModel):
    hpo_release: str
    method: str
    terms: list[QueryTerm]
    skipped: list[Skipped]
    suggestions: list[Suggestion]
    genes: list[GeneSuggestion]
