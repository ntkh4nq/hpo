"""One turn of a case as Server-Sent Events (docs/agent-impl-plan.md task 5; agent-plan §6, §7.2–§7.3, T96, T97).

Event types: case, step, text, answer_reset, artifact, review, done, error. A response ends after review, done or
error. The supervisor's text streams word by word, but the last `holdback_chars` characters of a message are held
back until the guard passes it, so a banned phrase never reaches the screen (T97). Stream shapes: T98 (2).
Every turn is also written to `case_turn` as the doctor saw it (TurnRecord, T116).
"""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack

import openai
from fastapi.sse import format_sse_event
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler

from agent.guard import banned_match, citations
from agent.rehydrate import transcript
from agent.state import CONFIG, effective_findings
from api import case_store

STREAM_MODES = ["messages", "updates", "tasks", "custom"]
WORKFLOW_NODES = {"extract", "remap_one", "split", "find_candidates", "choose_terms", "check_choices"}


def sse(event: str, data) -> bytes:
    return format_sse_event(event=event, data_str=json.dumps(data, ensure_ascii=False, default=str))


class TurnRecord:
    """What one turn showed: steps in first-emit order with their last status, and per message the text actually sent
    (an answer reset clears it). Written to case_turn when the turn ends, whatever the end (T116)."""

    def __init__(self, case_id: str, route: str, input_: dict):
        self.case_id, self.order, self.steps, self.texts = case_id, [], {}, {}
        self.outcome, self.citations, self.resets = "error", [], 0
        self.turn_no = case_store.start_turn(case_id, route, input_)

    def sse(self, event: str, data) -> bytes:
        if event == "step":
            if data["id"] not in self.steps:
                self.order.append(("step", data["id"]))
            self.steps[data["id"]] = data
        elif event == "text":
            mid = data["message_id"]
            if mid not in self.texts:
                self.order.append(("text", mid))
            self.texts[mid] = self.texts.get(mid, "") + data["delta"]
        elif event == "answer_reset":
            self.texts[data["message_id"]] = ""
            self.resets += 1
        elif event in ("review", "done", "error"):
            self.outcome = event
            self.citations = data.get("citations", []) if event == "done" else []
        return sse(event, data)

    def items(self) -> list[dict]:
        out = []
        for kind, key in self.order:
            if kind == "step":
                out.append({"kind": "step", "step": self.steps[key]})
            elif self.texts[key]:
                out.append({"kind": "text", "message_id": key, "text": self.texts[key]})
        return out

    def trace_output(self) -> dict:
        """What the doctor saw at the end of the turn, for its Langfuse trace."""
        return {"outcome": self.outcome, "answer": "\n\n".join(t for t in self.texts.values() if t),
                "guard_resets": self.resets}

    def finish(self) -> None:
        case_store.finish_turn(self.case_id, self.turn_no, self.items(), self.outcome, self.citations)


def trace_metadata(case_id: str) -> dict[str, str]:
    """Trace metadata of a case (Langfuse requires string values of at most 200 characters)."""
    case = case_store.get(case_id) or {}
    return {"case_title": str(case.get("title") or "")[:200]}


async def save_case(graph, case_id: str):
    """Read the case's checkpoint and keep its data in Postgres (T139); returns the snapshot."""
    snapshot = await graph.aget_state({"configurable": {"thread_id": case_id}})
    if snapshot.values:
        await asyncio.to_thread(case_store.save_state, case_id, snapshot.values)
    return snapshot


async def ensure_thread(graph, case_id: str) -> bool:
    """Rebuild the case's thread from Postgres when its Redis checkpoint expired (T139); True when it did.
    A live checkpoint is copied to Postgres instead, which also covers a turn whose client left before the end."""
    snapshot = await save_case(graph, case_id)
    if snapshot.values.get("messages"):
        return False
    state = await asyncio.to_thread(case_store.get_state, case_id)
    messages = transcript(await asyncio.to_thread(case_store.turns, case_id))
    if not (state or messages):
        return False   # a new case: nothing to rebuild
    # As the node with an edge to END: the thread is idle and the next input starts from START.
    await graph.aupdate_state({"configurable": {"thread_id": case_id}}, {**state, "messages": messages},
                              as_node="apply_findings_action")
    return True


def pending_review(snapshot) -> dict | None:
    """The value of the review pause the case is waiting on, if any (nested interrupts surface here, T98 (1))."""
    return next((i.value for i in snapshot.interrupts
                 if isinstance(i.value, dict) and i.value.get("type") == "review_findings"), None)


class Holdback:
    """Per message: send text only up to len - n; on a banned match stop sending; release the rest on pass (T97)."""

    def __init__(self, n: int):
        self.n, self.text, self.sent, self.frozen = n, {}, {}, set()

    def push(self, message_id: str, delta: str) -> str:
        text = self.text[message_id] = self.text.get(message_id, "") + delta
        if message_id in self.frozen:
            return ""
        if banned_match(text):
            self.frozen.add(message_id)
            return ""
        start = self.sent.get(message_id, 0)
        end = max(len(text) - self.n, start)
        self.sent[message_id] = end
        return text[start:end]

    def passed(self, message_id: str) -> str:
        if message_id in self.frozen:
            return ""
        text = self.text.get(message_id, "")
        start, self.sent[message_id] = self.sent.get(message_id, 0), len(text)
        return text[start:]

    def drop(self, message_id: str) -> None:
        self.frozen.add(message_id)


def _tool_payload(result) -> object:
    """JSON of the ToolMessage a tool produced, or None."""
    msgs = (result or {}).get("messages") or []
    try:
        return json.loads(msgs[0].content) if msgs else None
    except (TypeError, ValueError):
        return None


def summary(key: str, result) -> dict | None:
    """Short result shown next to a finished step (agent-plan §7.3)."""
    try:
        if key == "split":
            return {"items": len(result.get("items") or [])}
        if key == "check_choices":
            statuses = [f.mapping_status for f in result["analysis"].findings]
            return {s.lower(): statuses.count(s) for s in ("MAPPED", "AMBIGUOUS", "UNMAPPED")}
        if key == "tool:analyze_findings" and result.get("findings") is not None:
            ranking = result.get("ranking") or {"suggestions": []}
            return {"findings": len(result["findings"]), "suggestions": len(ranking["suggestions"])}
        if key in ("tool:search_disease", "tool:search_hpo", "tool:find_passages"):
            found = _tool_payload(result)
            return {"results": len(found)} if isinstance(found, list) else None
        if key == "tool:explain_disease":
            found = _tool_payload(result)
            return {"rank": found["rank"]} if isinstance(found, dict) else None
    except (AttributeError, KeyError, TypeError):
        return None
    return None


class Steps:
    """The growing progress list: plan items, tool calls, workflow nodes; never shown before they start (T96)."""

    def __init__(self):
        self.todo_status: dict[str, str] = {}
        self.current_todo: str | None = None
        self.call_of_task: dict[str, str] = {}   # `tools` task id -> tool call id
        self.open: dict[str, dict] = {}          # running steps by id

    def _emit(self, step: dict, status: str, result_summary: dict | None = None) -> dict:
        step = {**step, "status": status, "summary": result_summary}
        if status == "running":
            self.open[step["id"]] = step
        else:
            self.open.pop(step["id"], None)
        return step

    def todos(self, todos: list[dict]) -> list[dict]:
        out = []
        for t in todos:
            sid = "todo:" + t["content"]
            base = {"id": sid, "parent_id": None, "kind": "todo", "key": "todo", "label": t["content"], "params": None}
            seen = self.todo_status.get(sid)
            if t["status"] == "in_progress":
                self.current_todo = sid
                if seen is None:
                    out.append(self._emit(base, "running"))
                    self.todo_status[sid] = "running"
            elif t["status"] == "completed" and seen != "done":
                if seen is None:
                    out.append(self._emit(base, "running"))
                out.append(self._emit(base, "done"))
                self.todo_status[sid] = "done"
                if self.current_todo == sid:
                    self.current_todo = None
        return out

    def task(self, ns: tuple, payload: dict) -> list[dict]:
        name, start = payload["name"], "input" in payload
        if len(ns) == 1 and name == "tools":
            if start:
                calls = payload["input"] if isinstance(payload["input"], list) else [payload["input"]["tool_call"]]
                c = calls[0]
                self.call_of_task[payload["id"]] = c["id"]
                return [self._emit({"id": c["id"], "parent_id": self.current_todo, "kind": "tool",
                                    "key": f"tool:{c['name']}", "label": None, "params": c.get("args")}, "running")]
            step = self.open.get(self.call_of_task.get(payload["id"], ""))
            return self._finish(step, payload) if step else []
        if name in WORKFLOW_NODES:
            if start:
                tool_task = next((p.split(":", 1)[1] for p in ns if p.startswith("tools:")), None)
                # Phase 3's steps run inside `extract`: "Phân tích mô tả" is their parent row (T134 (5)).
                extract_task = next((p.split(":", 1)[1] for p in ns if p.startswith("extract:")), None)
                parent = extract_task if extract_task in self.open else self.call_of_task.get(tool_task)
                return [self._emit({"id": payload["id"], "parent_id": parent, "kind": "node",
                                    "key": name, "label": None, "params": None}, "running")]
            step = self.open.get(payload["id"])
            return self._finish(step, payload) if step else []
        return []

    def _finish(self, step: dict, payload: dict) -> list[dict]:
        if payload.get("interrupts"):          # paused at the review step: still running
            return []
        result = payload.get("result")
        failed = payload.get("error") is not None or any(
            getattr(m, "status", None) == "error" for m in ((result or {}).get("messages") or []))
        return [self._emit(step, "error" if failed else "done", None if failed else summary(step["key"], result))]

    def fail_open(self) -> list[dict]:
        return [self._emit(step, "error") for step in list(self.open.values())]


async def run_turn(graph, case_id: str, graph_input, view: Callable[[object], dict], route: str, input_: dict,
                   new_case: bool = False) -> AsyncIterator[bytes]:
    """Run one turn and yield its SSE events. `view(snapshot)` builds the CaseView sent with `done`.
    `route` ('message' | 'review') and `input_` (the request body) are recorded with the turn."""
    config = {"configurable": {"thread_id": case_id}}
    hold, steps, checked = Holdback(CONFIG["holdback_chars"]), Steps(), []
    record = TurnRecord(case_id, route, input_)
    sse = record.sse   # every event goes through the record
    # The turn's Langfuse trace: named by turn and route, grouped by case (session), with the doctor's input and,
    # at the end, what the doctor saw (outcome, answer text, guard resets).
    name = f"turn {record.turn_no} · {route}"
    trace, root = ExitStack(), None
    try:
        root = trace.enter_context(get_client().start_as_current_observation(name=name, as_type="span", input=input_))
        trace.enter_context(propagate_attributes(session_id=case_id, trace_name=name, tags=[route],
                                                 metadata=trace_metadata(case_id)))
        if new_case:
            yield sse("case", {"case_id": case_id})
        if route == "message":   # a review resumes its live checkpoint; post_review checks it is there
            await ensure_thread(graph, case_id)   # this turn's row has no outcome yet: not in the rebuilt history
        async for ns, mode, data in graph.astream(graph_input, {**config, "callbacks": [CallbackHandler()]},
                                                  stream_mode=STREAM_MODES, subgraphs=True):
            supervisor = len(ns) == 1 and ns[0].startswith("supervisor:")
            if mode == "messages":
                chunk, meta = data
                # A whole AIMessage arrives when the answer was not streamed (a replayed answer, core/llm_cache.py);
                # after streaming, LangGraph drops it as a duplicate of the chunks' message id.
                if supervisor and meta.get("langgraph_node") == "model" and chunk.type in ("AIMessageChunk", "ai") \
                        and chunk.text and (delta := hold.push(chunk.id, chunk.text)):
                    yield sse("text", {"message_id": chunk.id, "delta": delta})
            elif mode == "custom" and supervisor and data.get("type") == "check_answer":
                mid = data["message_id"]
                if data["passed"]:
                    if rest := hold.passed(mid):
                        yield sse("text", {"message_id": mid, "delta": rest})
                    checked.append(hold.text.get(mid, ""))
                else:
                    hold.drop(mid)
                    yield sse("answer_reset", {"message_id": mid})
                    if data.get("replacement"):
                        yield sse("text", {"message_id": mid, "delta": data["replacement"]})
                        checked.append(data["replacement"])
            elif mode == "tasks" and (len(ns) >= 1 and ns[0].startswith("supervisor:")):
                for step in steps.task(ns, data):
                    yield sse("step", step)
            elif mode == "updates" and supervisor:
                for update in data.values():
                    if not isinstance(update, dict):
                        continue
                    if update.get("todos") is not None:
                        for step in steps.todos(update["todos"]):
                            yield sse("step", step)
                    if "findings" in update:
                        yield sse("artifact", {"kind": "findings", "data": [
                            v.model_dump(mode="json") for v in effective_findings(update)]})
                    if update.get("ranking"):
                        yield sse("artifact", {"kind": "ranking", "data": update["ranking"]})
        snapshot = await save_case(graph, case_id)
        if (review := pending_review(snapshot)) is not None:
            yield sse("review", {"findings": review["findings"], "carry_over": review.get("carry_over"),
                                 "description": review.get("description")})
        else:
            yield sse("done", {"citations": citations(checked), "view": view(snapshot)})
    except ModelCallLimitExceededError:
        for step in steps.fail_open():
            yield sse("step", step)
        yield sse("text", {"message_id": f"limit-{uuid.uuid4()}", "delta": CONFIG["limit_answer"]})
        yield sse("done", {"citations": citations(checked), "view": view(await save_case(graph, case_id))})
    except Exception as exc:  # noqa: BLE001 - any failure ends the turn with an error event
        with contextlib.suppress(Exception):   # keep what the turn wrote before failing; Redis may be the failure
            await save_case(graph, case_id)
        for step in steps.fail_open():
            yield sse("step", step)
        code = "model_unavailable" if isinstance(exc, openai.APIError) else "internal_error"
        yield sse("error", {"code": code, "message": str(exc) or type(exc).__name__})
    finally:
        if root is not None:
            root.update(output=record.trace_output())
        trace.close()
        record.finish()   # synchronous: also runs when the client left and the generator is closed
