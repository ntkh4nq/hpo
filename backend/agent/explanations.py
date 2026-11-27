"""Explanations of the suggestions: "what the literature says" for each matched finding of the top 10
(docs/evidence-plan.md §4; docs/evidence-impl-plan.md task 3).

The exact part of a card (matches, frequencies, annotation sources, genes, limits) is ranking data built by code.
This module adds the literature part: one DeepSeek request writes Vietnamese claims, each tied to one pair
(disease, disease term) and to passages retrieved for that very pair; code checks every claim.

    prepare(values)        pairs → passages → context → explanation_key      (embedding only)
    run_model(prepared)    1 request (+1 repair) → ModelResult                 (what the cache stores)
    assemble(prepared, r)  statuses, items, passages from the CURRENT ranking (no request)

The cache key is the model input itself (T82): everything that changes the model's output is in it, and anything
taken from the case is rebuilt by `assemble` on every call.
"""

import functools
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from agent.guard import banned_match, find_codes
from agent.state import CONFIG, effective_findings
from core.llm import MODEL_PARAMS, invoke_json
from kb.ingest.chunking import count_tokens
from kb.search.literature import Passage, PassageRequest, find_overviews, find_passages_many
from ranking.index import load_index
from ranking.schema import Ranking

EXPLAIN = CONFIG["explain"]
PROMPTS = Path(__file__).with_name("prompts")
PROMPT = (PROMPTS / "explanations.md").read_text(encoding="utf-8")
REPAIR_PROMPT = (PROMPTS / "explanations_repair.md").read_text(encoding="utf-8")   # appended to a repair request
PROMPT_VERSION = hashlib.sha256((PROMPT + REPAIR_PROMPT).encode()).hexdigest()[:12]

Pair = tuple[str, str]   # (disease_id, disease_hpo_id)


class ClaimOut(BaseModel):               # model output (evidence-plan §4.2 step 6)
    hpo_id: str
    text: str
    chunks: list[int]


class ItemOut(BaseModel):
    disease_id: str
    claims: list[ClaimOut]


class ExplainOut(BaseModel):
    items: list[ItemOut]


class Claim(BaseModel):
    text: str
    refs: list[str]                      # ["CHUNK:<id>", ...]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0


class ModelResult(BaseModel):            # the cached part; depends only on the model input
    claims: dict[str, list[Claim]] = {}  # "<disease_id>|<disease_hpo_id>" -> checked claims
    written: list[str] = []              # pairs the model's final word gave ≥ 1 claim (kept or not)
    request_failed: bool = False         # the first request failed after every attempt
    usage: Usage = Usage()
    trace: dict = {}                     # {"first_claims", "first_invalid", "repaired"} (E4)
    created_at: str

    @property
    def cacheable(self) -> bool:         # no pair would be "failed" (evidence-impl-plan §Audit C6)
        return not self.request_failed and set(self.written) <= set(self.claims)


class PairExplanation(BaseModel):
    query_hpo_id: str
    disease_hpo_id: str
    status: Literal["cited", "no_passage", "no_supporting_passage", "failed"]
    claims: list[Claim] = []


class DiseaseExplanation(BaseModel):
    disease_id: str
    overview: str | None = None          # ref of the disease's overview passage (T110); never pair evidence
    pairs: list[PairExplanation]


class Explanations(BaseModel):
    explanation_key: str                 # sha256 of the model input
    cached: bool                         # the model part came from the cache (usage is then zero)
    created_at: str                      # when the model part was written
    model: str
    prompt_version: str
    usage: Usage
    items: list[DiseaseExplanation]      # one per suggestion, in ranking order
    passages: dict[str, Passage]         # cited and overview passages, by ref


@dataclass
class Prepared:
    ranking: Ranking
    pairs: list[Pair]                    # every distinct pair, in suggestion/match order
    sent: dict[Pair, list[Passage]]      # pairs with passages -> the passages in the context
    headers: dict[str, str]              # disease_id -> "# <id> · <name>" (diseases with a sent pair)
    blocks: dict[Pair, str]              # sent pair -> its "## Cặp …" block with passages
    context: str | None                  # None when no pair has a passage: no request
    key: str
    overviews: dict[str, Passage]        # disease_id -> overview passage (not model input)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def pair_key(pair: Pair) -> str:
    return f"{pair[0]}|{pair[1]}"


def _cited(match) -> bool:
    return match.kind in EXPLAIN["cited_kinds"]


def _limit(sent: dict[Pair, list[Passage]], order: list[Pair]) -> None:
    """Drop passages until the context fits max_context_tokens: always the last passage of the pair holding the
    most passages (ties: the later pair) (evidence-plan §4.2 step 3)."""
    size = functools.cache(count_tokens)
    total = sum(size(p.display_text) for ps in sent.values() for p in ps)
    while total > EXPLAIN["max_context_tokens"]:
        biggest = max((p for p in order if sent.get(p)), key=lambda p: (len(sent[p]), order.index(p)))
        total -= size(sent[biggest].pop().display_text)


def _frequency(match) -> str:
    values = [f for f in match.frequency_raw if f]
    return "; ".join(dict.fromkeys(values)) or "không ghi"


def prepare(values: Mapping) -> Prepared:
    ranking = Ranking.model_validate(values["ranking"])
    pairs = list(dict.fromkeys((s.disease_id, m.disease_hpo_id) for s in ranking.suggestions for m in s.matches
                               if _cited(m)))
    found = find_passages_many([PassageRequest(d, hpo_id=h) for d, h in pairs], k=EXPLAIN["passages_per_pair"],
                               exclude_sources=EXPLAIN["overview_sources"]) if pairs else []
    sent = {p: list(ps) for p, ps in zip(pairs, found) if ps}
    _limit(sent, pairs)
    sent = {p: ps for p, ps in sent.items() if ps}

    labels = load_index().labels
    headers, blocks = {}, {}
    for s in ranking.suggestions:
        for d, h in [p for p in pairs if p[0] == s.disease_id and p in sent]:
            headers[d] = f"# {d} · {s.disease_name or d}"
            lines = [f"## Cặp {h} — bệnh có: {labels.get(h, (h, None))[0]}"]
            for m in s.matches:
                if _cited(m) and m.disease_hpo_id == h:
                    en, vi = labels.get(m.query_hpo_id, (m.query_hpo_id, None))
                    lines.append(f"thai: {vi or en} [{m.query_hpo_id}]; kiểu khớp: {m.kind}; tần suất: {_frequency(m)}")
            lines += [f"[{p.chunk_id}] ({' › '.join([p.title, *p.section_path])}) {p.display_text}" for p in sent[(d, h)]]
            blocks[(d, h)] = "\n".join(lines)

    context = None
    if sent:
        parts = ["# Mô tả của bác sĩ", values.get("description") or ""]
        notes = [f'- "{v.phrase}": {v.note}' for v in effective_findings(values) if v.note]
        if notes:
            parts += ["", "# Nhận xét của bác sĩ (không phải bằng chứng)", *notes]
        for d, header in headers.items():
            parts += ["", header, *[blocks[p] for p in pairs if p[0] == d and p in blocks]]
        context = "\n".join(parts)

    key = hashlib.sha256(json.dumps({
        "model": EXPLAIN["model"], "params": MODEL_PARAMS[EXPLAIN["model"]]["thinking_off"],
        "system": PROMPT, "repair": REPAIR_PROMPT, "human": context, "schema": ExplainOut.model_json_schema(),
        "checks": [CONFIG["code_pattern"], CONFIG["banned_patterns"]],
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    overviews = find_overviews([s.disease_id for s in ranking.suggestions], EXPLAIN["overview_sources"])
    return Prepared(ranking, pairs, sent, headers, blocks, context, key, overviews)


def check_claims(out: ExplainOut, sent: Mapping[Pair, list[Passage]]
                 ) -> tuple[dict[Pair, list[Claim]], dict[Pair, list[tuple[ClaimOut, list[str]]]]]:
    """Valid claims by pair, and rejected claims with their reasons by pair (evidence-plan §4.2 step 7)."""
    diseases = {d for d, _ in sent}
    valid: dict[Pair, list[Claim]] = {}
    rejected: dict[Pair, list[tuple[ClaimOut, list[str]]]] = {}
    for item in out.items:
        for c in item.claims:
            pair, reasons = (item.disease_id, c.hpo_id), []
            if item.disease_id not in diseases:
                reasons.append(f"Bệnh {item.disease_id} không nằm trong các bệnh đã gửi.")
            elif pair not in sent:
                reasons.append(f"{c.hpo_id} không phải một cặp đã gửi của bệnh {item.disease_id}.")
            if not c.chunks:
                reasons.append("Ý không có đoạn nguồn.")
            elif pair in sent and (stray := sorted(set(c.chunks) - {p.chunk_id for p in sent[pair]})):
                reasons.append(f"Đoạn {', '.join(map(str, stray))} không thuộc cặp này.")
            if codes := find_codes(c.text):
                reasons.append(f"Câu chứa mã {', '.join(codes)}; câu không được chứa mã nào.")
            if phrase := banned_match(c.text):
                reasons.append(f"Câu chứa cụm cấm '{phrase}' (không nói hạng hay tần suất thành nguy cơ hay xác suất).")
            if reasons:
                rejected.setdefault(pair, []).append((c, reasons))
            else:
                valid.setdefault(pair, []).append(
                    Claim(text=c.text, refs=[f"CHUNK:{n}" for n in dict.fromkeys(c.chunks)]))
    return valid, rejected


def _claims_with_pairs(out: ExplainOut, pairs) -> set[Pair]:
    return {(i.disease_id, c.hpo_id) for i in out.items for c in i.claims} & set(pairs)


def _ask(human: str, usage: dict, name: str = "explanations") -> ExplainOut | None:
    return invoke_json(PROMPT, human, ExplainOut, thinking=False, timeout=EXPLAIN["timeout"], model=EXPLAIN["model"],
                       usage=usage, name=name)


def run_model(prepared: Prepared) -> ModelResult:
    """One request; one repair request for the pairs that had a rejected claim (evidence-plan §4.2 steps 6–7)."""
    usage: dict = {}
    out = _ask(prepared.context, usage)
    if out is None:
        return ModelResult(request_failed=True, usage=Usage(**usage), created_at=now())
    sent = prepared.sent
    valid, rejected = check_claims(out, sent)
    written = _claims_with_pairs(out, sent)
    trace = {"first_claims": sum(len(i.claims) for i in out.items),
             "first_invalid": sum(len(v) for v in rejected.values()), "repaired": False}
    bad = [p for p in prepared.pairs if p in rejected and p in sent]   # rejected claims elsewhere are just dropped
    if bad:
        trace["repaired"] = True
        human = []
        for d in dict.fromkeys(d for d, _ in bad):
            human += [prepared.headers[d], *[prepared.blocks[p] for p in bad if p[0] == d], ""]
        accepted = [{"disease_id": d, "hpo_id": h, "text": c.text, "chunks": [int(r.split(":")[1]) for r in c.refs]}
                    for d, h in bad for c in valid.get((d, h), [])]
        wrong = [{"disease_id": d, "hpo_id": h, **c.model_dump(), "reasons": reasons}
                 for d, h in bad for c, reasons in rejected[(d, h)]]
        human += ["# Các ý đã đạt", json.dumps(accepted, ensure_ascii=False, indent=1), "",
                  "# Các ý bị bước kiểm bắt", json.dumps(wrong, ensure_ascii=False, indent=1), "", REPAIR_PROMPT]
        fixed = _ask("\n".join(human), usage, name="explanations_repair")
        if fixed is not None:   # the repair's word replaces the bad pairs' claims; other pairs in it are ignored
            again, _ = check_claims(fixed, {p: sent[p] for p in bad})
            valid.update({p: again.get(p, []) for p in bad})
            written = (written - set(bad)) | _claims_with_pairs(fixed, bad)
    return ModelResult(claims={pair_key(p): cs for p, cs in valid.items() if cs and p in sent},
                       written=sorted(pair_key(p) for p in written), usage=Usage(**usage), trace=trace,
                       created_at=now())


def assemble(prepared: Prepared, result: ModelResult, cached: bool = False) -> Explanations:
    """Statuses, items and cited passages from the current ranking and passages (evidence-plan §4.2 step 8, §4.3)."""
    written = set(result.written)

    def status(pair: Pair) -> str:
        if pair not in prepared.sent:
            return "no_passage"
        if result.request_failed:
            return "failed"
        if result.claims.get(pair_key(pair)):
            return "cited"
        return "failed" if pair_key(pair) in written else "no_supporting_passage"

    by_ref = {p.ref: p for ps in prepared.sent.values() for p in ps}
    items, passages = [], {}
    for s in prepared.ranking.suggestions:
        pairs = []
        for m in s.matches:
            if not _cited(m):
                continue
            pair = (s.disease_id, m.disease_hpo_id)
            claims = result.claims.get(pair_key(pair), [])
            for c in claims:
                passages |= {r: by_ref[r] for r in c.refs if r in by_ref}
            pairs.append(PairExplanation(query_hpo_id=m.query_hpo_id, disease_hpo_id=m.disease_hpo_id,
                                         status=status(pair), claims=claims))
        overview = prepared.overviews.get(s.disease_id)
        if overview:
            passages[overview.ref] = overview
        items.append(DiseaseExplanation(disease_id=s.disease_id, overview=overview.ref if overview else None,
                                        pairs=pairs))
    return Explanations(explanation_key=prepared.key, cached=cached, created_at=result.created_at,
                        model=EXPLAIN["model"], prompt_version=PROMPT_VERSION,
                        usage=Usage() if cached else result.usage, items=items, passages=passages)


def explain_case(values: Mapping) -> Explanations:
    prepared = prepare(values)
    result = run_model(prepared) if prepared.context is not None else ModelResult(created_at=now())
    return assemble(prepared, result)
