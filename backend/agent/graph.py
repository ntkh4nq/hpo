"""Case graph (docs/agent-impl-plan.md task 4; agent-plan §3.1, §3.5).

    START ─┬─ messages input       → supervisor (Deep Agents) → END
           └─ pending_action input → apply_findings_action    → END

One thread per case (thread_id = case_id) on the Redis checkpointer with a TTL, rebuilt from Postgres (T139).
A card edit after the suggestions re-ranks in code, never through the supervisor: 0 model requests (T91).
"""

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from agent.state import CaseState, case_event
from agent.supervisor import build_supervisor
from agent.tools import case_ranking


def route(state: CaseState) -> str:
    return "apply_findings_action" if state.get("pending_action") else "supervisor"


def apply_findings_action(state: CaseState) -> dict:
    """Card action {"type": "update_findings", "edits": [...], "notes": {...}}, already checked by the API.
    Edits and notes replace the current ones (the UI always sends the whole set); notes never enter ranking (N4)."""
    action = state["pending_action"]
    edits = {e["finding_id"]: e for e in action["edits"]}
    update = {"edits": edits, "notes": {k: v for k, v in action["notes"].items() if v}, "pending_action": None}
    if edits != (state.get("edits") or {}):
        update["ranking"] = case_ranking({**state, "edits": edits}).model_dump(mode="json")
        if update["ranking"] != state.get("ranking"):
            # Earlier answers describe the old list; the next turn must not take them as current (T128).
            update["messages"] = [case_event("findings_edited")]
    return update


def build_graph(checkpointer, model: BaseChatModel | None = None):
    return (StateGraph(CaseState)
            .add_node("supervisor", build_supervisor(model))
            .add_node("apply_findings_action", apply_findings_action)
            .add_conditional_edges(START, route, ["supervisor", "apply_findings_action"])
            .add_edge("supervisor", END)
            .add_edge("apply_findings_action", END)
            .compile(checkpointer=checkpointer))
