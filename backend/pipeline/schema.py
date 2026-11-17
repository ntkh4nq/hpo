"""Data types of the phase 3 pipeline (docs/mapping-plan.md §2).

Item/Split are the split step's model output; Choice/ChoiceSet the choose step's. Finding/Analysis are the
graph's output (Internal Phenotype Profile, docs/domain-context.md §2). Term values are always copied from
a Candidate read from the KB, never from model output (principle #8).
"""

from typing import Literal

from pydantic import BaseModel, Field

Assertion = Literal["PRESENT", "ABSENT", "SUSPECTED", "UNCERTAIN"]
Assessment = Literal["ASSESSED", "LIMITED", "NOT_ASSESSABLE", "NOT_ASSESSED"]
Subject = Literal["FETUS", "MOTHER", "FAMILY"]
MappingStatus = Literal["MAPPED", "AMBIGUOUS", "UNMAPPED"]


class Item(BaseModel):
    phrase: str = Field(description="Cụm tự đủ nghĩa nêu một biểu hiện, dùng đúng chữ bác sĩ, không chứa chữ kết luận")
    source_text: str = Field(description="Câu trong mô tả chứa biểu hiện, chép nguyên văn từng chữ")
    assertion: Assertion
    assessment: Assessment
    temporality: str | None = Field(description="Tuổi thai lúc quan sát nếu mô tả có nêu, ví dụ '22 tuần'")
    subject: Subject


class Split(BaseModel):
    items: list[Item]


class Candidate(BaseModel):            # one search_hpo result shown to the choose step
    hpo_id: str
    label_en: str
    label_vi: str | None
    label_vi_reviewed: bool            # True when a Vietnamese name with source 'doctor' exists (plan.md phase 7)
    definition_en: str | None
    matched_text: str
    dense_score: float | None          # search_hpo scores["dense_score"]; None when absent


class Term(BaseModel):                 # a chosen HPO term; values copied from its Candidate, never from model output
    hpo_id: str
    label_en: str
    label_vi: str | None
    label_vi_reviewed: bool
    definition_en: str | None


class Finding(BaseModel):              # one row of the Internal Phenotype Profile
    phrase: str
    source_text: str
    source_span: tuple[int, int] | None = None   # [start, end) in Analysis.text; None if not found verbatim
    assertion: Assertion
    assessment: Assessment
    temporality: str | None
    subject: Subject
    mapping_status: MappingStatus
    term: Term | None = None           # MAPPED only
    options: list[Term] = []           # AMBIGUOUS only, >= 2
    candidates: list[Candidate] = []   # what step 4 saw, or what the gate dropped


class Rejection(BaseModel):            # one thing step 5 discarded
    index: int | None                  # item index; None for an answer whose index matches no item
    reason: str                        # wording fixed in mapping-plan.md §6


class Analysis(BaseModel):             # output of the graph
    text: str                          # preprocessed description
    hpo_release: str                   # kb_manifest.release where source = 'hpo' (principle #6)
    findings: list[Finding] = []
    split_failed: bool = False         # split failed twice -> no findings ("mô tả chưa được phân tích")
    choose_failed: bool = False        # choose failed twice -> every item sent to it is UNMAPPED
    rejections: list[Rejection] = []


class Choice(BaseModel):               # model output of step 4, one per item
    index: int
    decision: MappingStatus
    hpo_id: str | None = None          # MAPPED
    options: list[str] = []            # AMBIGUOUS


class ChoiceSet(BaseModel):
    choices: list[Choice]
