"""Phase 3 steps 3-5 (docs/mapping-plan.md §4-§6): find HPO candidates, choose a term, check the choice.

Step 3 is code (search_hpo per item, plus the UNMAPPED gate, T29). Step 4 is one LLM call for the whole case,
choosing only among each item's own candidates. Step 5 is code: every code the model returns must be one of
that item's candidates and a current KB term (principle #8); Term values come from the Candidate, never from
model output.
"""

import hashlib
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from kb.db import connect
from kb.query.hpo import search_hpo
from pipeline.llm import invoke_json
from pipeline.schema import Analysis, Candidate, ChoiceSet, Finding, Item, Rejection, Term
from pipeline.split import locate

HERE = Path(__file__).parent
CONFIG = tomllib.loads((HERE / "mapping.toml").read_text())
PROMPT = (HERE / "prompts" / "choose_term.md").read_text(encoding="utf-8")
PROMPT_VERSION = hashlib.sha256(PROMPT.encode()).hexdigest()[:12]


# ---------- step 3: candidates and gate

def find_candidates(items: list[Item]) -> dict[int, list[Candidate]]:
    """item index -> its search_hpo results (default profile), with definition and reviewed-name flag from the KB."""
    with ThreadPoolExecutor(CONFIG["search_concurrency"]) as pool:
        found = list(pool.map(lambda item: search_hpo(item.phrase, k=CONFIG["candidates_k"]), items))
    ids = sorted({c["hpo_id"] for results in found for c in results})
    with connect() as conn:
        extra = {h: (d, r) for h, d, r in conn.execute(
            """SELECT t.hpo_id, t.definition_en,
                      EXISTS (SELECT 1 FROM hpo_name n WHERE n.hpo_id = t.hpo_id AND n.lang = 'vi' AND n.source = 'doctor')
               FROM hpo_term t WHERE t.hpo_id = ANY(%s)""", (ids,))}
    return {i: [Candidate(
        hpo_id=c["hpo_id"], label_en=c["label_en"], label_vi=c["label_vi"],
        label_vi_reviewed=extra[c["hpo_id"]][1], definition_en=extra[c["hpo_id"]][0],
        matched_text=c["matched_text"], dense_score=c["scores"].get("dense_score"),
    ) for c in results] for i, results in enumerate(found)}


def best_dense(candidates: list[Candidate]) -> float:
    return max((c.dense_score for c in candidates if c.dense_score is not None), default=0.0)


def gate(candidates: dict[int, list[Candidate]], threshold: float) -> set[int]:
    """Items whose best dense score is below the threshold: UNMAPPED without asking the model."""
    return {i for i, cands in candidates.items() if best_dense(cands) < threshold}


# ---------- step 4: choose

def _v(value) -> str:
    return "—" if value is None else str(value)


def human_message(text: str, items: list[Item], candidates: dict[int, list[Candidate]], gated: set[int]) -> str:
    lines = ["Mô tả:", text, "", "Các mục:"]
    for i, item in enumerate(items):
        if i in gated:
            continue
        lines.append(f"[{i}] phrase: {item.phrase} | assertion: {item.assertion} | subject: {item.subject} "
                     f"| câu nguồn: {item.source_text}")
        lines += [f"  - {c.hpo_id} | {c.label_en} | {_v(c.label_vi)} | khớp tên: {c.matched_text} "
                  f"| định nghĩa: {_v(c.definition_en)}" for c in candidates.get(i, [])]
    return "\n".join(lines)


def choose_terms(text: str, items: list[Item], candidates: dict[int, list[Candidate]], gated: set[int],
                 model: str | None = None, thinking: bool | None = None) -> tuple[ChoiceSet | None, bool]:
    """(choices, choose_failed). No call when every item is gated. model/thinking override the config (eval only)."""
    if all(i in gated for i in range(len(items))):
        return None, False
    thinking = CONFIG["thinking"] if thinking is None else thinking
    choices = invoke_json(PROMPT, human_message(text, items, candidates, gated), ChoiceSet,
                          thinking=thinking, timeout=CONFIG["timeout"], model=model or CONFIG["model"])
    return (None, True) if choices is None else (choices, False)


# ---------- step 5: check and build the profile

def hpo_release() -> str:
    with connect() as conn:
        return conn.execute("SELECT release FROM kb_manifest WHERE source = 'hpo' "
                            "ORDER BY downloaded_at DESC LIMIT 1").fetchone()[0]


def _current_terms(ids: set[str]) -> set[str]:
    with connect() as conn:
        return {h for (h,) in conn.execute(
            "SELECT hpo_id FROM hpo_term WHERE hpo_id = ANY(%s) AND NOT is_obsolete", (sorted(ids),))}


def _term(c: Candidate) -> Term:
    return Term(hpo_id=c.hpo_id, label_en=c.label_en, label_vi=c.label_vi,
                label_vi_reviewed=c.label_vi_reviewed, definition_en=c.definition_en)


def check_choices(text: str, items: list[Item], candidates: dict[int, list[Candidate]], gated: set[int],
                  choices: ChoiceSet | None, choose_failed: bool, split_failed: bool) -> Analysis:
    sent = [i for i in range(len(items)) if i not in gated]
    rejections: list[Rejection] = []
    answer = {}
    for c in (choices.choices if choices else []):
        if c.index not in sent:
            rejections.append(Rejection(index=None, reason=f"unknown index {c.index}"))
        elif c.index in answer:
            rejections.append(Rejection(index=c.index, reason="duplicate answer"))
        else:
            answer[c.index] = c
    chosen = {h for c in answer.values() for h in ([c.hpo_id] if c.hpo_id else []) + c.options}
    current = _current_terms(chosen) if chosen else set()

    def valid(i: int, hpo_id: str) -> Candidate | None:
        """The item's candidate for this code, or None after logging why it is rejected."""
        cand = next((c for c in candidates.get(i, []) if c.hpo_id == hpo_id), None)
        if cand is None:
            rejections.append(Rejection(index=i, reason=f"{hpo_id} not in its candidates"))
        elif hpo_id not in current:
            rejections.append(Rejection(index=i, reason=f"{hpo_id} obsolete or not in KB"))
            cand = None
        return cand

    findings = []
    for i, item in enumerate(items):
        status, term, options = "UNMAPPED", None, []
        c = answer.get(i)
        if i in gated or choose_failed:
            pass
        elif c is None:
            rejections.append(Rejection(index=i, reason="no answer"))
        elif c.decision == "MAPPED":
            if not c.hpo_id:
                rejections.append(Rejection(index=i, reason="MAPPED without hpo_id"))
            elif cand := valid(i, c.hpo_id):
                status, term = "MAPPED", _term(cand)
        elif c.decision == "AMBIGUOUS":
            kept = [cand for h in dict.fromkeys(c.options) if (cand := valid(i, h))]
            if len(kept) >= 2:
                status, options = "AMBIGUOUS", [_term(k) for k in kept]
            else:
                rejections.append(Rejection(index=i, reason=f"AMBIGUOUS left with {len(kept)} valid options"))
        findings.append(Finding(
            phrase=item.phrase, source_text=item.source_text, source_span=locate(text, item.source_text),
            assertion=item.assertion, assessment=item.assessment, temporality=item.temporality,
            subject=item.subject, mapping_status=status, term=term, options=options,
            candidates=candidates.get(i, []),
        ))
    return Analysis(text=text, hpo_release=hpo_release(), findings=findings, split_failed=split_failed,
                    choose_failed=choose_failed, rejections=rejections)
