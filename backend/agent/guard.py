"""Answer check of the supervisor (docs/agent-impl-plan.md task 3; agent-plan §6, T97).

After every supervisor model call: every source code in the text must come from the case summary, a tool result
or an earlier (already checked) answer (principle #8), and no phrase may turn a score into a risk (principle #2).
First failure: the message is removed and the model rewrites once; second failure in a row: a fixed answer.
"""

import re
import unicodedata
from collections.abc import Mapping

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from agent.analysis import GUARD_NAME
from agent.state import CONFIG, render_case
from core.db import connect
from ranking.index import load_index

CODE_RE = re.compile(CONFIG["code_pattern"])
BANNED = [re.compile(p, re.IGNORECASE) for p in CONFIG["banned_patterns"]]


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def find_codes(text: str) -> list[str]:
    return list(dict.fromkeys(CODE_RE.findall(text)))


def banned_match(text: str) -> str | None:
    text = nfc(text)
    return next((m.group(0) for p in BANNED if (m := p.search(text))), None)


def allowed_codes(state: Mapping) -> set[str]:
    """Codes in the case summary, in every tool result, and in every earlier supervisor message."""
    messages = state["messages"]
    texts = [render_case(state)]
    texts += [m.text for m in messages[:-1] if m.type in ("tool", "ai")]
    return {c for t in texts for c in find_codes(t)}


def problems(text: str, allowed: set[str]) -> list[str]:
    found = [f"Mã {c} không có trong dữ liệu ca hay kết quả tool." for c in find_codes(text) if c not in allowed]
    if phrase := banned_match(text):
        found.append(f"Câu chứa cụm cấm: '{phrase}' (không nói điểm hay tần suất thành nguy cơ hay xác suất mắc bệnh).")
    return found


def citations(texts: list[str]) -> list[dict]:
    """Source codes of checked answers, in order, de-duplicated, with kind and a KB label (agent-plan §6)."""
    codes = list(dict.fromkeys(c for t in texts for c in find_codes(t)))
    index = load_index() if codes else None
    out = []
    for code in codes:
        prefix = code.split(":", 1)[0]
        kind = CONFIG["citation_kinds"].get(prefix)
        label = None
        if kind == "hpo":
            en, vi = index.labels.get(code, (None, None))
            label = vi or en
        elif kind == "disease":
            label = index.disease_name.get(code)
            if label is None:
                with connect() as conn:
                    row = conn.execute("SELECT name FROM disease WHERE disease_id = %s", (code,)).fetchone()
                label = row[0] if row else None
        out.append({"id": code, "kind": kind, "label": label})
    return out


class AnswerGuardMiddleware(AgentMiddleware):
    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime) -> dict | None:
        msg = state["messages"][-1]
        found = problems(msg.text, allowed_codes(state))
        write = runtime.stream_writer
        if not found:
            write({"type": "check_answer", "message_id": msg.id, "passed": True})
            return None
        prev = state["messages"][-2] if len(state["messages"]) > 1 else None
        if isinstance(prev, HumanMessage) and prev.name == GUARD_NAME:   # second failure in a row
            write({"type": "check_answer", "message_id": msg.id, "passed": False,
                   "replacement": CONFIG["fallback_answer"]})
            return {"messages": [AIMessage(id=msg.id, content=CONFIG["fallback_answer"])]}
        write({"type": "check_answer", "message_id": msg.id, "passed": False, "replacement": None})
        note = ("Câu trả lời vừa rồi bị chặn:\n" + "\n".join(found) + "\nViết lại, chỉ dùng mã có trong dữ liệu ca "
                "hoặc kết quả tool; cần mã khác thì gọi tool để tra trước.")
        # Removing the message also drops any tool calls it carried, so no call is left without a result (T98 (5)).
        return {"messages": [RemoveMessage(id=msg.id), HumanMessage(name=GUARD_NAME, content=note)],
                "jump_to": "model"}
