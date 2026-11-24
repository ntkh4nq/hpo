"""Case endpoints (docs/agent-impl-plan.md task 5; agent-plan §7.1, §7.4).

A case exists from its first message until the doctor deletes it (T116): its row in `case_record`, its graph state
in the Postgres checkpointer, its turns in `case_turn`. One turn at a time per case: a second request while a turn
runs gets 409. Turns stream SSE (api/stream.py); card edits and reads return JSON.
Explanations of the suggestions (docs/evidence-impl-plan.md task 3) read the case and never write it.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.sse import EventSourceResponse
from langchain_core.messages import HumanMessage
from langfuse import get_client, propagate_attributes
from langgraph.types import Command
from pydantic import BaseModel, field_validator

from agent import explanations
from agent.explanations import Explanations, ModelResult
from agent.guard import citations
from agent.state import (CONFIG, FindingEdit, FindingView, ReviewDecision, check_edits, effective_findings,
                         is_doctor_message)
from api import case_store
from api.stream import pending_review, run_turn
from ranking.schema import Ranking

router = APIRouter(tags=["cases"])
RUNNING: set[str] = set()   # cases with a turn in progress (one uvicorn process, MVP)
EXPLAINING: dict[tuple[str, str], asyncio.Task] = {}   # (case_id, explanation_key) -> model step + cache write; identical calls share it
# Turn routes check the request, then return the stream; events are listed in api/stream.py.
SSE = {200: {"description": "Server-Sent Events (case, step, text, answer_reset, artifact, review, done, error)",
             "content": {"text/event-stream": {}}}}


class TextIn(BaseModel):
    text: str
    # True when the text is the whole case description (the description box): it replaces the description.
    # A chat message is added to the current description instead (T137).
    whole_description: bool = False

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > CONFIG["text_max_chars"]:
            raise ValueError(f"cần 1–{CONFIG['text_max_chars']} ký tự")
        return v


class TitleIn(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v or len(v) > CONFIG["case_title_chars"]:
            raise ValueError(f"cần 1–{CONFIG['case_title_chars']} ký tự")
        return v


class FindingsIn(BaseModel):
    edits: list[FindingEdit] = []
    notes: dict[str, str] = {}


class Citation(BaseModel):
    id: str
    kind: str | None
    label: str | None


class MessageView(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    citations: list[Citation] = []


class TurnView(BaseModel):             # one row of case_turn: what that turn showed the doctor (T116)
    turn_no: int
    route: Literal["message", "review", "findings"]
    input: dict[str, Any]                # {"text": ...}, the ReviewDecision, or {edits, notes, ranking_changed}
    items: list[dict[str, Any]]          # {"kind": "step", "step": Step} | {"kind": "text", "message_id", "text"}
    outcome: Literal["review", "done", "error"] | None   # None while the turn runs
    citations: list[Citation] = []


class CaseSummary(BaseModel):
    case_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    status: Literal["idle", "running", "review"]


class CaseView(BaseModel):
    case_id: str
    status: Literal["idle", "running", "review"]
    title: str = ""
    description: str | None = None
    hpo_release: str | None = None
    analysis_flags: dict | None = None
    findings: list[FindingView] = []
    ranking: Ranking | None = None
    messages: list[MessageView] = []
    turns: list[TurnView] = []
    notes_archive: list[dict[str, Any]] = []   # notes of earlier analyses of this case (agent/analysis.py)


def fail(status: int, code: str, message: str):
    raise HTTPException(status, {"code": code, "message": message})


def config(case_id: str) -> dict:
    return {"configurable": {"thread_id": case_id}}


def case_view(case_id: str, status: str, snapshot) -> CaseView:
    """Sync (two short queries): call it from a thread in async routes."""
    values = snapshot.values
    record = case_store.get(case_id) or {}
    messages = []
    for m in values.get("messages", []):
        if is_doctor_message(m):
            messages.append(MessageView(role="user", text=m.text))
        elif m.type == "ai" and m.text:
            messages.append(MessageView(role="assistant", text=m.text, citations=citations([m.text])))
    # While the case waits for review, its findings live in the paused analysis subgraph (T127).
    review = pending_review(snapshot) if status == "review" else None
    findings = review["findings"] if review is not None else effective_findings(values)
    return CaseView(case_id=case_id, status=status, title=record.get("title", ""),
                    description=(review or {}).get("description") or values.get("description"),
                    hpo_release=values.get("hpo_release"),
                    analysis_flags=values.get("analysis_flags"), findings=findings,
                    ranking=values.get("ranking"), messages=messages,
                    turns=case_store.turns(case_id) if record else [], notes_archive=values.get("notes_archive") or [])


def status_of(case_id: str, snapshot) -> str:
    if case_id in RUNNING:
        return "running"
    return "review" if pending_review(snapshot) is not None else "idle"


async def load(graph, case_id: str):
    """(status, snapshot) of an existing case. A case whose first turn failed before any checkpoint has an empty
    snapshot: it is an idle case with no findings."""
    if await asyncio.to_thread(case_store.get, case_id) is None:
        fail(404, "case_not_found", "Ca không tồn tại.")
    snapshot = await graph.aget_state(config(case_id))
    return status_of(case_id, snapshot), snapshot


async def view(case_id: str, status: str, snapshot) -> CaseView:
    return await asyncio.to_thread(case_view, case_id, status, snapshot)


def check(status: str, expected: str) -> None:
    """409 unless the case is in the status this request needs."""
    if status == "running":
        fail(409, "case_busy", "Ca đang xử lý một yêu cầu khác.")
    if expected == "idle" and status == "review":
        fail(409, "case_waiting_review", "Ca đang chờ bác sĩ xem biểu hiện.")
    if expected == "review" and status != "review":
        fail(409, "case_not_waiting_review", "Ca không chờ bước xem biểu hiện.")


def turn(request: Request, case_id: str, graph_input, route: str, input_: dict,
         new_case: bool = False) -> EventSourceResponse:
    graph = request.app.state.graph

    async def body():
        try:
            async for chunk in run_turn(graph, case_id, graph_input,
                                        lambda snap: case_view(case_id, "idle", snap).model_dump(mode="json"),
                                        route, input_, new_case=new_case):
                yield chunk
        finally:
            RUNNING.discard(case_id)

    return EventSourceResponse(body())


@router.post("/cases", responses=SSE)
async def create_case(body: TextIn, request: Request):
    """New case from the doctor's first message; the first SSE event `case` carries the case_id. The case is listed
    from now on, titled with the first line of the message (T116)."""
    case_id = str(uuid.uuid4())
    await asyncio.to_thread(case_store.create, case_id, case_store.default_title(body.text, CONFIG["case_title_chars"]))
    RUNNING.add(case_id)
    return turn(request, case_id, {"messages": [HumanMessage(body.text)]}, "message", {"text": body.text},
                new_case=True)


@router.get("/cases", response_model=list[CaseSummary])
async def list_cases(request: Request):
    """Every kept case, last used first (no user accounts: one team, MVP)."""
    graph = request.app.state.graph
    out = []
    for row in await asyncio.to_thread(case_store.list_cases):
        snapshot = await graph.aget_state(config(row["case_id"]))
        out.append(CaseSummary(**row, status=status_of(row["case_id"], snapshot)))
    return out


@router.patch("/cases/{case_id}", response_model=CaseView)
async def rename_case(case_id: str, body: TitleIn, request: Request):
    status, snapshot = await load(request.app.state.graph, case_id)
    await asyncio.to_thread(case_store.rename, case_id, body.title)
    return await view(case_id, status, snapshot)


@router.delete("/cases/{case_id}", status_code=204)
async def delete_case(case_id: str, request: Request):
    """Delete the case for good: graph state, turns, explanations cache (T116)."""
    graph = request.app.state.graph
    status, _ = await load(graph, case_id)
    if status == "running":
        fail(409, "case_busy", "Ca đang xử lý một yêu cầu khác.")
    await graph.checkpointer.adelete_thread(case_id)
    await asyncio.to_thread(case_store.delete, case_id)


@router.post("/cases/{case_id}/messages", responses=SSE)
async def post_message(case_id: str, body: TextIn, request: Request):
    status, _ = await load(request.app.state.graph, case_id)
    check(status, "idle")
    RUNNING.add(case_id)   # no await between the check and the add
    message = HumanMessage(body.text, additional_kwargs={"whole_description": True} if body.whole_description else {})
    return turn(request, case_id, {"messages": [message]}, "message",
                {"text": body.text, **({"whole_description": True} if body.whole_description else {})})


@router.post("/cases/{case_id}/review", responses=SSE)
async def post_review(case_id: str, body: ReviewDecision, request: Request):
    """The doctor's decision at the review step: continue (with edits and notes), remap one finding, or cancel."""
    status, snapshot = await load(request.app.state.graph, case_id)
    check(status, "review")
    views = [FindingView.model_validate(f) for f in pending_review(snapshot)["findings"]]
    try:
        if body.action == "continue":
            check_edits(views, body.edits, body.notes)
        elif body.action == "remap":
            check_edits(views, [FindingEdit(finding_id=body.finding_id)], {})
    except ValueError as exc:
        fail(422, "invalid_request", str(exc))
    RUNNING.add(case_id)
    decision = body.model_dump(mode="json")
    return turn(request, case_id, Command(resume=decision), "review", decision)


@router.post("/cases/{case_id}/findings", response_model=CaseView)
async def post_findings(case_id: str, body: FindingsIn, request: Request):
    """Card edits after the suggestions: re-rank in code, no model request (T91)."""
    graph = request.app.state.graph
    status, snapshot = await load(graph, case_id)
    check(status, "idle")
    if not snapshot.values.get("ranking"):
        fail(409, "no_ranking", "Ca chưa có gợi ý để xếp hạng lại.")
    try:
        check_edits(effective_findings(snapshot.values), body.edits, body.notes)
    except ValueError as exc:
        fail(422, "invalid_request", str(exc))
    RUNNING.add(case_id)
    edits = [e.model_dump(mode="json", exclude_none=True) for e in body.edits]
    try:
        await graph.ainvoke({"pending_action": {"type": "update_findings", "edits": edits, "notes": body.notes}},
                            config(case_id))
    finally:
        RUNNING.discard(case_id)
    after = await graph.aget_state(config(case_id))
    changed = _suggestions(after.values.get("ranking")) != _suggestions(snapshot.values.get("ranking"))
    await asyncio.to_thread(record_findings, case_id, {"edits": edits, "notes": body.notes, "ranking_changed": changed})
    return await view(case_id, "idle", after)


def _suggestions(ranking: dict | None) -> list:
    return [s["disease_id"] for s in (ranking or {}).get("suggestions", [])]


def record_findings(case_id: str, input_: dict) -> None:
    """A card edit is a turn without model output; `ranking_changed` tells a reopened case that the summary written
    before it no longer describes the list (T116, P7)."""
    turn_no = case_store.start_turn(case_id, "findings", input_)
    case_store.finish_turn(case_id, turn_no, [], "done", [])


@router.get("/cases/{case_id}", response_model=CaseView)
async def get_case(case_id: str, request: Request):
    status, snapshot = await load(request.app.state.graph, case_id)
    return await view(case_id, status, snapshot)


async def _model_and_cache(case_id: str, prepared) -> ModelResult:
    """The model step, then the cache write, inside one task: a request arriving while it runs joins it, one arriving
    after finds the cache."""
    result = await asyncio.to_thread(explanations.run_model, prepared)
    if result.cacheable:
        await asyncio.to_thread(case_store.cache_put, case_id, prepared.key, result.model_dump(mode="json"))
    return result


@router.post("/cases/{case_id}/explanations", response_model=Explanations)
async def post_explanations(case_id: str, request: Request):
    """"What the literature says" for every matched finding of the current suggestions (evidence-plan §4).

    Call after `done` with `artifact ranking` and after every POST /findings; use the response of the latest call.
    Reads the case, never writes it. Cached by the model input: an unchanged input costs no model request."""
    graph = request.app.state.graph
    status, snapshot = await load(graph, case_id)
    check(status, "idle")
    if not snapshot.values.get("ranking"):
        fail(409, "no_ranking", "Ca chưa có gợi ý để giải thích.")
    with get_client().start_as_current_observation(name="case:explanations", as_type="span"), \
            propagate_attributes(session_id=case_id):
        prepared = await asyncio.to_thread(explanations.prepare, snapshot.values)
        if prepared.context is None:     # no passage for any pair: nothing to ask, nothing to cache
            return explanations.assemble(prepared, ModelResult(created_at=explanations.now()))
        key = prepared.key
        if (hit := await asyncio.to_thread(case_store.cache_get, case_id, key)) is not None:
            return explanations.assemble(prepared, ModelResult.model_validate(hit), cached=True)
        task = EXPLAINING.get((case_id, key))
        if task is None:
            task = EXPLAINING[(case_id, key)] = asyncio.create_task(_model_and_cache(case_id, prepared))
            task.add_done_callback(lambda _: EXPLAINING.pop((case_id, key), None))
        result = await asyncio.shield(task)   # a client that leaves cancels its wait, not the shared request
        return explanations.assemble(prepared, result)
