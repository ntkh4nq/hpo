"""Capability "read the description into findings" (docs/agent-impl-plan.md task 2; agent-plan §3.3, T91).

Subgraph `analysis`: extract (phase 3) -> review (pause for the doctor) -> remap_one -> review ... -> END.
The tool `analyze_findings` runs it on the doctor's own last message, never on text the supervisor wrote (T26).
The tool is async so the nested, checkpointed subgraph uses the async Redis saver; the nodes stay sync (T98).
"""

from typing import TypedDict

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent.state import ReviewDecision, effective_findings, finding_rows
from agent.tools import case_ranking
from phenotype import graph as phenotype_graph
from phenotype import mapping
from phenotype.schema import Item

GUARD_NAME = "answer_guard"  # name of the guard's rewrite request (agent/guard.py); never a doctor message


class AnalysisState(TypedDict, total=False):   # JSON only: it is checkpointed in Redis while paused
    text: str                 # input: raw doctor text; the preprocessed Analysis.text after extract
    hpo_release: str
    findings: list[dict]
    analysis_flags: dict
    remaps: dict[str, dict]
    decision: dict | None     # last ReviewDecision (json)


def extract(state: AnalysisState) -> dict:
    a = phenotype_graph.graph.invoke(phenotype_graph.initial_state(state["text"]))["analysis"]
    return {"text": a.text, "hpo_release": a.hpo_release, "findings": finding_rows(a),
            "analysis_flags": {"split_failed": a.split_failed, "choose_failed": a.choose_failed}, "remaps": {}}


def review(state: AnalysisState) -> dict:
    # Only the interrupt here: on resume the node runs again from the top.
    value = interrupt({"type": "review_findings",
                       "findings": [v.model_dump(mode="json") for v in effective_findings(state)]})
    return {"decision": value}


def after_review(state: AnalysisState) -> str:
    return "remap_one" if state["decision"]["action"] == "remap" else END


def remap_one(state: AnalysisState) -> dict:
    """Map one finding again from the doctor's corrected wording (T91 e); 1 Qwen request unless gated."""
    fid, phrase = state["decision"]["finding_id"], state["decision"]["phrase"].strip()
    remaps = state.get("remaps") or {}
    base = remaps.get(fid) or next(r for r in state["findings"] if r["finding_id"] == fid)
    item = Item(phrase=phrase, source_text=base["source_text"], assertion=base["assertion"],
                assessment=base["assessment"], temporality=base["temporality"], subject=base["subject"])
    cands = mapping.find_candidates([item])
    gated = mapping.gate(cands, mapping.CONFIG["unmapped_dense_threshold"])
    choices, failed = mapping.choose_terms(state["text"], [item], cands, gated)
    f = mapping.check_choices(state["text"], [item], cands, gated, choices, failed, False).findings[0]
    return {"remaps": {**remaps, fid: {**f.model_dump(mode="json"), "finding_id": fid}}}


analysis_graph = (
    StateGraph(AnalysisState)
    .add_node("extract", extract)
    .add_node("review", review)
    .add_node("remap_one", remap_one)
    .add_edge(START, "extract")
    .add_edge("extract", "review")
    .add_conditional_edges("review", after_review, ["remap_one", END])
    .add_edge("remap_one", "review")
    .compile()   # no checkpointer argument: nested in the case graph it uses the case's checkpointer
)


def last_doctor_text(messages) -> str:
    return next(m.text for m in reversed(messages) if m.type == "human" and m.name != GUARD_NAME)


@tool
async def analyze_findings(runtime: ToolRuntime) -> Command | str:
    """Đọc tin nhắn mới nhất của bác sĩ thành các biểu hiện chuẩn HPO và ghi vào ca.

    Gọi khi tin nhắn mới nhất của bác sĩ mô tả thai (siêu âm, MRI, ghi chú khám), dù có kèm câu hỏi hay không,
    trước mọi việc khác. Gọi riêng, không gọi cùng tool khác trong một lượt. Không có tham số: tool tự lấy
    nguyên văn chữ của bác sĩ. Hệ thống hiện các biểu hiện để bác sĩ xem, sửa rồi bấm Tiếp tục; mô tả mới thay
    toàn bộ biểu hiện cũ của ca. Khi bác sĩ bấm Tiếp tục, hệ thống tự xếp hạng bệnh/gene từ các biểu hiện (hạng do
    code tính, không phải xác suất mắc bệnh); kết quả hiện ở mục 'Ca hiện tại'.
    """
    out = await analysis_graph.ainvoke({"text": last_doctor_text(runtime.state["messages"])}, config=runtime.config)
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
    # Rank right away in code (0 model requests), so a case with findings always has its suggestions (T99).
    ranking = case_ranking(case) if case["findings"] else None
    case["ranking"] = ranking.model_dump(mode="json") if ranking else None
    if out["analysis_flags"]["split_failed"]:
        summary = "Mô tả chưa được phân tích (lỗi bước tách)."
    else:
        summary = (f"Đã ghi {len(out['findings'])} biểu hiện vào ca và xếp hạng: "
                   f"{len(ranking.suggestions) if ranking else 0} bệnh gợi ý (xem mục 'Ca hiện tại').")
    return Command(update={**case, "messages": [ToolMessage(summary, tool_call_id=runtime.tool_call_id)]})
