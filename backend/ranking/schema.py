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
    label_en: str | None = None        # filled by rank_terms from the KB, so a reader never has to look the code up
    from_options: list[str] = []       # the AMBIGUOUS options this common ancestor stands for; empty otherwise
    finding_ids: list[str] = []        # ids of the findings behind this code (phase 5); empty when none given


# Why a finding or code is not a query term (T129). Keys, never text: words for them are data of each reader
# (agent/config.toml [case.skip_words], frontend locales "skipReason.*"). `Skipped.reason` stays a plain str so cases
# saved before T129 (reason written as Vietnamese text) still load; readers show an unknown key as it is.
SKIP_REASONS = ("not_fetal", "state_not_used", "absent_checked", "unmapped", "ambiguous_no_common_ancestor",
                "not_in_kb", "excluded_by_doctor")


class Skipped(BaseModel):
    phrase: str | None                 # None when the code came without a finding (rank_terms)
    hpo_id: str | None
    reason: str                        # one of SKIP_REASONS
    finding_id: str | None = None      # id of the skipped finding (phase 5); None for codes skipped by rank_terms
    assertion: str | None = None       # the finding's state, so "bố mẹ cùng huyết thống" is never read without "ABSENT"


class AbsentTerm(BaseModel):           # a fetal finding assessed as absent; checked against each suggestion (T130)
    hpo_id: str
    label_en: str | None = None
    finding_ids: list[str] = []


class Conflict(BaseModel):             # the disease records frequently what the fetus was assessed not to have (T130)
    query_hpo_id: str                  # the absent finding's term
    finding_ids: list[str] = []
    disease_hpo_id: str                # the disease's annotation at or below it
    disease_label_en: str | None = None
    frequency_raw: list[str | None] = []   # one entry per source row, as in Match (principle #6)
    evidence: list[str | None] = []
    references: list[list[str]] = []


class Match(BaseModel):                # how one query term relates to the disease
    query_hpo_id: str
    assertion: QueryAssertion
    weight: float
    kind: MatchKind
    disease_hpo_id: str | None         # the disease's annotation behind `kind`; None for "none"
    disease_label_en: str | None = None  # its name, so a summary says what the disease records (T138)
    common_ancestor_id: str | None     # "related" only
    # one entry per source row of that annotation (phenotype.hpoa), in row order (principle #6)
    frequency_raw: list[str | None] = []
    evidence: list[str | None] = []
    references: list[list[str]] = []


class Unmentioned(BaseModel):          # a frequent prenatal phenotype of the disease the description did not mention
    hpo_id: str
    label_en: str
    frequency_raw: list[str | None]
    evidence: list[str | None] = []    # one entry per source row, as in Match (principle #6)
    references: list[list[str]] = []
    # other suggestions in the same list that record this phenotype (or a more specific one) / record it as absent
    also_in: list[str] = []
    excluded_in: list[str] = []


class Trait(BaseModel):                # a non-phenotype annotation of the disease: inheritance (aspect I), course (C)
    hpo_id: str
    label_en: str
    frequency_raw: list[str | None]
    references: list[list[str]]


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
    inheritance: list[Trait] = []
    clinical_course: list[Trait] = []
    conflicts: list[Conflict] = []     # never changes rank or score (T130)


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
    absent: list[AbsentTerm] = []      # absent findings checked for conflicts; not query terms (T130)
    suggestions: list[Suggestion]
    genes: list[GeneSuggestion]
