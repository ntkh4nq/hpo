"""Capability "read the description into findings" (docs/agent-impl-plan.md task 2; agent-plan §3.3, T91).

Subgraph `analysis`: extract (phase 3) -> carry_over -> review (pause for the doctor) -> remap_one -> review ... -> END.
carry_over moves the doctor's corrections of the previous analysis onto the matching new findings (T132).
The tool `analyze_findings` runs it on the doctor's own last message, never on text the supervisor wrote (T26).
The tool is async so the nested, checkpointed subgraph uses the async Postgres saver; the nodes stay sync (T98, T116).
"""

import unicodedata
from collections import Counter
from typing import TypedDict

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent.state import CONFIG, ReviewDecision, effective_findings, finding_rows, is_doctor_message
from agent.tools import case_ranking
from phenotype import graph as phenotype_graph
from phenotype import mapping
from phenotype.schema import Item


class AnalysisState(TypedDict, total=False):   # JSON only: it is checkpointed while paused
    text: str                 # input: raw doctor text; the preprocessed Analysis.text after extract
    hpo_release: str
    findings: list[dict]
    analysis_flags: dict
    remaps: dict[str, dict]
    edits: dict[str, dict]    # carried edits (FindingEdit json); the doctor's full set comes back with "continue"
    notes: dict[str, str]     # carried notes
    previous: dict            # input: {findings, remaps, edits, notes} of the analysis this one replaces
    carry_over: dict          # {applied, not_applied, carried: [old finding ids]}
    decision: dict | None     # last ReviewDecision (json)


def extract(state: AnalysisState) -> dict:
    a = phenotype_graph.graph.invoke(phenotype_graph.initial_state(state["text"]))["analysis"]
    return {"text": a.text, "hpo_release": a.hpo_release, "findings": finding_rows(a),
            "analysis_flags": {"split_failed": a.split_failed, "choose_failed": a.choose_failed}, "remaps": {}}


def _key_text(text: str | None) -> str:
    return " ".join(unicodedata.normalize("NFC", text or "").lower().split())


def _match_previous(rows: list[dict], old_rows: list[dict]) -> dict[str, str]:
    """new finding_id -> old finding_id, when exactly one old finding has the same (source sentence, phrase), else the
    same (source sentence, machine term). An old finding claimed by two new ones goes to neither (T132)."""
    def code(r: dict) -> str | None:
        return (r.get("term") or {}).get("hpo_id")
    pairs: dict[str, str] = {}
    for r in rows:
        sentence = _key_text(r["source_text"])
        same = [o for o in old_rows if _key_text(o["source_text"]) == sentence]
        by_phrase = [o for o in same if _key_text(o["phrase"]) == _key_text(r["phrase"])]
        by_term = [o for o in same if code(r) and code(o) == code(r)]
        found = by_phrase if len(by_phrase) == 1 else by_term if not by_phrase and len(by_term) == 1 else []
        if found:
            pairs[r["finding_id"]] = found[0]["finding_id"]
    claimed = Counter(pairs.values())
    return {new: old for new, old in pairs.items() if claimed[old] == 1}


def carry_over(state: AnalysisState) -> dict:
    """The doctor's remaps, edits and notes of the previous analysis, moved to the matching new findings (T132).
    The review then already shows the corrected findings; corrections without a match are counted."""
    prev = state.get("previous") or {}
    old_remaps, old_edits, old_notes = prev.get("remaps") or {}, prev.get("edits") or {}, prev.get("notes") or {}
    corrected = set(old_remaps) | set(old_edits) | {k for k, v in old_notes.items() if v}
    rows = {r["finding_id"]: r for r in state["findings"]}
    pairs = _match_previous(state["findings"], prev.get("findings") or [])
    remaps, edits, notes, carried = {}, {}, {}, []
    for new, old in pairs.items():
        if old not in corrected:
            continue
        carried.append(old)
        if old in old_remaps:   # the doctor's wording, at the new finding's place in the new text
            remaps[new] = {**old_remaps[old], "finding_id": new, "source_text": rows[new]["source_text"],
                           "source_span": rows[new]["source_span"]}
        if old in old_edits:
            edit = {**old_edits[old], "finding_id": new}
            candidates = {c["hpo_id"] for c in (remaps.get(new) or rows[new])["candidates"]}
            if edit.get("hpo_id") and edit["hpo_id"] not in candidates:
                edit.pop("hpo_id")   # a chosen term must stay among this finding's candidates (#8)
            if len(edit) > 1:
                edits[new] = edit
        if old_notes.get(old):
            notes[new] = old_notes[old]
    applied = len(carried)
    return {"remaps": remaps, "edits": edits, "notes": notes,
            "carry_over": {"applied": applied, "not_applied": len(corrected) - applied, "carried": carried}}


def review(state: AnalysisState) -> dict:
    # Only the interrupt here: on resume the node runs again from the top.
    counts = state.get("carry_over") or {}
    value = interrupt({"type": "review_findings", "description": state["text"],
                       "findings": [v.model_dump(mode="json") for v in effective_findings(state)],
                       "carry_over": {"applied": counts.get("applied", 0), "not_applied": counts.get("not_applied", 0)}})
    return {"decision": value}


def after_review(state: AnalysisState) -> str:
    return "remap_one" if state["decision"]["action"] == "remap" else END


def remap_one(state: AnalysisState) -> dict:
    """Map one finding again from the doctor's corrected wording (T91 e); 1 Qwen request unless gated."""
    fid, phrase = state["decision"]["finding_id"], state["decision"]["phrase"].strip()
    remaps = state.get("remaps") or {}
    base = remaps.get(fid) or next(r for r in state["findings"] if r["finding_id"] == fid)
    # The doctor's wording is always what is searched; the measurement (if any) is kept for display only (T118).
    item = Item(phrase=phrase, source_text=base["source_text"],
                assertion=state["decision"].get("assertion") or base["assertion"],
                assessment=base["assessment"], temporality=base["temporality"], subject=base["subject"])
    cands = mapping.find_candidates([item])
    gated = mapping.gate(cands, mapping.CONFIG["unmapped_dense_threshold"])
    choices, failed = mapping.choose_terms(state["text"], [item], cands, gated)
    f = mapping.check_choices(state["text"], [item], cands, gated, choices, failed, False).findings[0]
    row = {**f.model_dump(mode="json"), "measurement": base.get("measurement"), "finding_id": fid}
    return {"remaps": {**remaps, fid: row}}


analysis_graph = (
    StateGraph(AnalysisState)
    .add_node("extract", extract)
    .add_node("carry_over", carry_over)
    .add_node("review", review)
    .add_node("remap_one", remap_one)
    .add_edge(START, "extract")
    .add_edge("extract", "carry_over")
    .add_edge("carry_over", "review")
    .add_conditional_edges("review", after_review, ["remap_one", END])
    .add_edge("remap_one", "review")
    .compile()   # no checkpointer argument: nested in the case graph it uses the case's checkpointer
)


def notes_to_archive(state, carried: list[str] = ()) -> dict | None:
    """{description, findings} of the current findings that carry a note not carried to the new analysis, or None."""
    noted = [v.model_dump(mode="json") for v in effective_findings(state) if v.note and v.finding_id not in carried]
    return {"description": state.get("description") or "", "findings": noted} if noted else None


def description_to_analyze(state) -> str:
    """The text the analysis reads (T137): a message from the description box (`whole_description`) is the whole
    description; a chat message is added to the case's current description, so "the fetus also has X" keeps
    the earlier findings (their edits and notes carry over by source sentence, T132)."""
    message = next(m for m in reversed(state["messages"]) if is_doctor_message(m))
    current = state.get("description")
    if message.additional_kwargs.get("whole_description") or not current:
        return message.text
    return f"{current}\n{message.text}"


@tool
async def analyze_findings(runtime: ToolRuntime) -> Command | str:
    """Đọc tin nhắn mới nhất của bác sĩ thành các biểu hiện chuẩn HPO và ghi vào ca.

    Gọi khi tin nhắn mới nhất của bác sĩ mô tả thai (siêu âm, MRI, ghi chú khám), dù có kèm câu hỏi hay không,
    trước mọi việc khác. Gọi riêng, không gọi cùng tool khác trong một lượt. Không có tham số: tool tự lấy
    nguyên văn chữ của bác sĩ. Tin nhắn trong khung chat được thêm vào mô tả hiện có của ca rồi đọc lại cả mô tả
    (biểu hiện cũ và chỉnh sửa của bác sĩ được giữ); mô tả sửa ở ô mô tả thì thay mô tả cũ. Hệ thống hiện các biểu
    hiện để bác sĩ xem, sửa rồi bấm Tiếp tục. Khi bác sĩ bấm Tiếp tục, hệ thống tự xếp hạng bệnh/gene từ các biểu hiện (hạng do
    code tính, không phải xác suất mắc bệnh); kết quả hiện ở mục 'Ca hiện tại'.
    """
    previous = {k: runtime.state.get(k) for k in ("findings", "remaps", "edits", "notes")}
    out = await analysis_graph.ainvoke({"text": description_to_analyze(runtime.state), "previous": previous},
                                       config=runtime.config)
    decision = ReviewDecision.model_validate(out["decision"])
    if decision.action == "cancel":
        return "Bác sĩ dừng phân tích; ca không đổi."
    case = {
        "description": out["text"],
        "hpo_release": out["hpo_release"],
        "findings": out["findings"],
        "analysis_flags": out["analysis_flags"],
        "remaps": out["remaps"],
        "edits": {e.finding_id: e.model_dump(mode="json", exclude_none=True) for e in decision.edits},
        "notes": {k: v for k, v in decision.notes.items() if v},
    }
    # The doctor's notes on the findings this analysis replaces stay with the case (T116).
    if archived := notes_to_archive(runtime.state, (out.get("carry_over") or {}).get("carried", [])):
        case["notes_archive"] = [*(runtime.state.get("notes_archive") or []), archived]
    # Rank right away in code (0 model requests), so a case with findings always has its suggestions (T99).
    ranking = case_ranking(case) if case["findings"] else None
    case["ranking"] = ranking.model_dump(mode="json") if ranking else None
    if out["analysis_flags"]["split_failed"]:
        summary = "Mô tả chưa được phân tích (lỗi bước tách)."
    else:
        summary = (f"Đã ghi {len(out['findings'])} biểu hiện vào ca và xếp hạng: "
                   f"{len(ranking.suggestions) if ranking else 0} bệnh gợi ý (xem mục 'Ca hiện tại'). "
                   + CONFIG["case"]["events"]["new_analysis"])
    return Command(update={**case, "messages": [ToolMessage(summary, tool_call_id=runtime.tool_call_id)]})
