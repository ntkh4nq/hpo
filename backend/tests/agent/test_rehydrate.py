"""The conversation rebuilt from case_turn when the Redis checkpoint expired (T139)."""

from agent.rehydrate import transcript
from agent.state import CASE_EVENT_NAME


def turn(route, input_, texts=(), outcome="done"):
    return {"route": route, "input": input_, "outcome": outcome,
            "items": [{"kind": "step", "step": {}}] + [{"kind": "text", "message_id": "m", "text": t} for t in texts]}


def test_transcript():
    msgs = transcript([
        turn("message", {"text": "Thai có thông liên thất.", "whole_description": True}, outcome="review"),
        turn("review", {"action": "continue"}, ["Đã ghi 1 biểu hiện."]),
        turn("findings", {"edits": [], "notes": {}, "ranking_changed": False}),
        turn("findings", {"edits": [], "notes": {}, "ranking_changed": True}),
        turn("message", {"text": "Gene nào?"}, ["Gene A.", "Gene B."]),
        turn("message", {"text": "đang chạy"}, outcome=None),
    ])
    assert [(m.type, m.text) for m in msgs] == [
        ("human", "Thai có thông liên thất."), ("ai", "Đã ghi 1 biểu hiện."), ("human", msgs[2].text),
        ("human", "Gene nào?"), ("ai", "Gene A."), ("ai", "Gene B.")]
    assert msgs[0].additional_kwargs == {"whole_description": True} and msgs[3].additional_kwargs == {}
    assert msgs[2].name == CASE_EVENT_NAME
