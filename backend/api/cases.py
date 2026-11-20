"""Case endpoints (docs/agent-impl-plan.md task 5; agent-plan §7.1, §7.4).

A case exists while its checkpoint lives in Redis (24 h after last use, T92). One turn at a time per case: a
second request while a turn runs gets 409. Turns stream SSE (api/stream.py); card edits and reads return JSON.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.sse import EventSourceResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, field_validator

from agent.analysis import GUARD_NAME
from agent.guard import citations
from agent.state import CONFIG, FindingEdit, FindingView, ReviewDecision, check_edits, effective_findings
from api.stream import pending_review, run_turn
from ranking.schema import Ranking

router = APIRouter(tags=["cases"])
RUNNING: set[str] = set()   # cases with a turn in progress (one uvicorn process, MVP)
# Turn routes check the request, then return the stream; events are listed in api/stream.py.
SSE = {200: {"description": "Server-Sent Events (case, step, text, answer_reset, artifact, review, done, error)",
             "content": {"text/event-stream": {}}}}


class TextIn(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > CONFIG["text_max_chars"]:
            raise ValueError(f"cần 1–{CONFIG['text_max_chars']} ký tự")
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


class CaseView(BaseModel):
    case_id: str
    status: Literal["idle", "running", "review"]
    description: str | None = None
    hpo_release: str | None = None
    analysis_flags: dict | None = None
    findings: list[FindingView] = []
    ranking: Ranking | None = None
    messages: list[MessageView] = []


def fail(status: int, code: str, message: str):
    raise HTTPException(status, {"code": code, "message": message})


def config(case_id: str) -> dict:
    return {"configurable": {"thread_id": case_id}}


def case_view(case_id: str, status: str, snapshot) -> CaseView:
    values = snapshot.values
    messages = []
    for m in values.get("messages", []):
        if m.type == "human" and m.name != GUARD_NAME:
            messages.append(MessageView(role="user", text=m.text))
        elif m.type == "ai" and m.text:
            messages.append(MessageView(role="assistant", text=m.text, citations=citations([m.text])))
    return CaseView(case_id=case_id, status=status, description=values.get("description"),
                    hpo_release=values.get("hpo_release"), analysis_flags=values.get("analysis_flags"),
                    findings=effective_findings(values), ranking=values.get("ranking"), messages=messages)


async def load(graph, case_id: str):
    snapshot = await graph.aget_state(config(case_id))
    if not snapshot.values:
        fail(404, "case_not_found", "Ca không tồn tại hoặc đã hết hạn.")
    if case_id in RUNNING:
        return "running", snapshot
    return ("review" if pending_review(snapshot) is not None else "idle"), snapshot


def check(status: str, expected: str) -> None:
    """409 unless the case is in the status this request needs."""
    if status == "running":
        fail(409, "case_busy", "Ca đang xử lý một yêu cầu khác.")
    if expected == "idle" and status == "review":
        fail(409, "case_waiting_review", "Ca đang chờ bác sĩ xem biểu hiện.")
    if expected == "review" and status != "review":
        fail(409, "case_not_waiting_review", "Ca không chờ bước xem biểu hiện.")


def turn(request: Request, case_id: str, graph_input, new_case: bool = False) -> EventSourceResponse:
    graph = request.app.state.graph

    async def body():
        try:
            async for chunk in run_turn(graph, case_id, graph_input,
                                        lambda snap: case_view(case_id, "idle", snap).model_dump(mode="json"),
                                        new_case=new_case):
                yield chunk
        finally:
            RUNNING.discard(case_id)

    return EventSourceResponse(body())


@router.post("/cases", responses=SSE)
async def create_case(body: TextIn, request: Request):
    """New case from the doctor's first message; the first SSE event `case` carries the case_id."""
    case_id = str(uuid.uuid4())
    RUNNING.add(case_id)
    return turn(request, case_id, {"messages": [HumanMessage(body.text)]}, new_case=True)


@router.post("/cases/{case_id}/messages", responses=SSE)
async def post_message(case_id: str, body: TextIn, request: Request):
    status, _ = await load(request.app.state.graph, case_id)
    check(status, "idle")
    RUNNING.add(case_id)   # no await between the check and the add
    return turn(request, case_id, {"messages": [HumanMessage(body.text)]})


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
    return turn(request, case_id, Command(resume=body.model_dump(mode="json")))


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
    try:
        await graph.ainvoke({"pending_action": {
            "type": "update_findings", "edits": [e.model_dump(mode="json", exclude_none=True) for e in body.edits],
            "notes": body.notes}}, config(case_id))
    finally:
        RUNNING.discard(case_id)
    return case_view(case_id, "idle", await graph.aget_state(config(case_id)))


@router.get("/cases/{case_id}", response_model=CaseView)
async def get_case(case_id: str, request: Request):
    status, snapshot = await load(request.app.state.graph, case_id)
    return case_view(case_id, status, snapshot)
