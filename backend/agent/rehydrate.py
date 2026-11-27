"""The conversation of a case rebuilt from its turns, for a thread whose Redis checkpoint expired (T139,
docs/checkpoint-plan.md §2.3).

Only what the next supervisor turn needs: the doctor's messages, the answers as the doctor saw them, and the notice
that a card edit changed the list. Tool calls and their results are not rebuilt: every fact of the case reaches the
supervisor's prompt through `render_case` on each model call.
"""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from agent.state import case_event


def transcript(turns: list[dict]) -> list[BaseMessage]:
    """`case_turn` rows in turn order; a turn still running (no outcome) is skipped."""
    out: list[BaseMessage] = []
    for t in turns:
        if t["outcome"] is None:
            continue
        if t["route"] == "message":
            flags = {"whole_description": True} if t["input"].get("whole_description") else {}
            out.append(HumanMessage(t["input"]["text"], additional_kwargs=flags))
        elif t["route"] == "findings":
            if t["input"].get("ranking_changed"):
                out.append(case_event("findings_edited"))
            continue
        out += [AIMessage(i["text"]) for i in t["items"] if i["kind"] == "text"]
    return out
