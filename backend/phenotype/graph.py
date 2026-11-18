"""Phase 3 graph (decisions T2, docs/mapping-plan.md §7): description -> Internal Phenotype Profile.

preprocess -> split -> find_candidates (+ gate) -> choose_terms -> check_choices; when split yields no items
(failure or nothing to report) it goes straight to check_choices. No checkpointer, no interrupt (T28).

    python -m phenotype.graph "<mô tả>"       # or: make analyze TEXT="..."
"""

import sys
from typing import TypedDict

from langfuse import get_client, observe
from langgraph.graph import END, START, StateGraph

from phenotype import mapping
from phenotype.schema import Analysis, Candidate, ChoiceSet, Item
from phenotype.split import preprocess, split


class State(TypedDict):
    text: str
    items: list[Item] | None
    candidates: dict[int, list[Candidate]]
    gated: set[int]
    choices: ChoiceSet | None
    split_failed: bool
    choose_failed: bool
    analysis: Analysis | None


def preprocess_node(state: State) -> dict:
    return {"text": preprocess(state["text"])}


def split_node(state: State) -> dict:
    items = split(state["text"])
    return {"items": items or [], "split_failed": items is None}


def after_split(state: State) -> str:
    return "find_candidates" if state["items"] else "check_choices"


def find_candidates_node(state: State) -> dict:
    candidates = mapping.find_candidates(state["items"])
    return {"candidates": candidates, "gated": mapping.gate(candidates, mapping.CONFIG["unmapped_dense_threshold"])}


def choose_terms_node(state: State) -> dict:
    choices, failed = mapping.choose_terms(state["text"], state["items"], state["candidates"], state["gated"])
    return {"choices": choices, "choose_failed": failed}


def check_choices_node(state: State) -> dict:
    return {"analysis": mapping.check_choices(
        state["text"], state["items"], state["candidates"], state["gated"], state["choices"],
        state["choose_failed"], state["split_failed"])}


graph = (
    StateGraph(State)
    .add_node("preprocess", preprocess_node)
    .add_node("split", split_node)
    .add_node("find_candidates", find_candidates_node)
    .add_node("choose_terms", choose_terms_node)
    .add_node("check_choices", check_choices_node)
    .add_edge(START, "preprocess")
    .add_edge("preprocess", "split")
    .add_conditional_edges("split", after_split, ["find_candidates", "check_choices"])
    .add_edge("find_candidates", "choose_terms")
    .add_edge("choose_terms", "check_choices")
    .add_edge("check_choices", END)
    .compile()
)


def initial_state(text: str) -> State:
    return {"text": text, "items": None, "candidates": {}, "gated": set(), "choices": None,
            "split_failed": False, "choose_failed": False, "analysis": None}


@observe(name="analyze")
def analyze(text: str) -> Analysis:
    return graph.invoke(initial_state(text))["analysis"]


def print_analysis(a: Analysis) -> None:
    for f in a.findings:
        if f.mapping_status == "MAPPED":
            mapped = f"{f.term.hpo_id} {f.term.label_vi or f.term.label_en}"
        elif f.mapping_status == "AMBIGUOUS":
            mapped = ", ".join(f"{t.hpo_id} {t.label_vi or t.label_en}" for t in f.options)
        else:
            mapped = "—"
        print(f"{f.phrase} | {f.source_text} | {f.assertion} | {f.assessment} | {f.subject} | {f.mapping_status} | {mapped}")
    print(f"rejections: {[r.model_dump() for r in a.rejections]}")
    print(f"split_failed: {a.split_failed}  choose_failed: {a.choose_failed}")


if __name__ == "__main__":
    print_analysis(analyze(sys.argv[1]))
    get_client().flush()
