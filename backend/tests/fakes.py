"""Stand-ins for everything that would call the gateway (docs/agent-impl-plan.md §Task 2, §Task 3).

Ranking and KB reads stay real (Postgres); only model calls and the embedding search are replaced.
"""

import json
import re
from collections import Counter
from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from phenotype.schema import Candidate, Choice, ChoiceSet, Item, Term


class FakeChat(GenericFakeChatModel):
    """Scripted chat model that streams text word by word and also streams tool calls (T98 (6))."""

    model_name: str = "deepseek-v4-flash"
    bound_tools: list[str] = []          # names of the tools bound by the agent
    seen: list = []                      # the message lists the model was called with

    def bind_tools(self, tools, **kwargs):  # the base raises NotImplementedError
        self.bound_tools = sorted(t.name if hasattr(t, "name") else t["name"] for t in tools)
        return self

    def _get_ls_params(self, **kwargs):  # same harness-profile key as ChatOpenAI: "openai:<model>"
        return {"ls_provider": "openai", "ls_model_name": self.model_name, "ls_model_type": "chat"}

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # used when nobody streams tokens
        self.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        msg = next(self.messages)
        msg = AIMessage(content=msg) if isinstance(msg, str) else msg
        for token in re.split(r"(\s)", msg.content or ""):
            if token:
                chunk = ChatGenerationChunk(message=AIMessageChunk(content=token, id=msg.id))
                if run_manager:
                    run_manager.on_llm_new_token(token, chunk=chunk)
                yield chunk
        if msg.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", id=msg.id, tool_call_chunks=[
                {"name": tc["name"], "args": json.dumps(tc["args"], ensure_ascii=False), "id": tc["id"], "index": i}
                for i, tc in enumerate(msg.tool_calls)]))


def call(name: str, call_id: str, **args) -> AIMessage:
    """A scripted model turn that calls one tool."""
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])


def cand(code: str, label: str, score: float = 0.9) -> Candidate:
    return Candidate(hpo_id=code, label_en=label, label_vi=None, label_vi_reviewed=False, definition_en=None,
                     matched_text=label, dense_score=score)


def item(phrase: str, assertion: str = "PRESENT", subject: str = "FETUS") -> Item:
    return Item(phrase=phrase, source_text=phrase, assertion=assertion, assessment="ASSESSED", temporality=None,
                subject=subject)


def fake_phase3(monkeypatch, items: list[Item], candidates: dict[str, list[Candidate]],
                decisions: dict[str, tuple] | None = None) -> SimpleNamespace:
    """Replace phase 3's model and search steps. `candidates`: phrase -> candidates (a remap phrase gets its own);
    `decisions`: phrase -> ("MAPPED", code) | ("AMBIGUOUS", [codes]) | ("UNMAPPED",); default MAPPED to the first
    candidate. check_choices stays real, so codes must be current KB terms. Returns call counts and split texts."""
    decisions = decisions or {}
    seen = SimpleNamespace(calls=Counter(), split_texts=[])

    def split(text, *args, **kwargs):
        seen.calls["split"] += 1
        seen.split_texts.append(text)
        return list(items)

    def find_candidates(its):
        seen.calls["find_candidates"] += 1
        return {i: candidates[it.phrase] for i, it in enumerate(its)}

    def choose_terms(text, its, cands, gated, *args, **kwargs):
        seen.calls["choose_terms"] += 1
        choices = []
        for i, it in enumerate(its):
            if i in gated:
                continue
            d = decisions.get(it.phrase, ("MAPPED", cands[i][0].hpo_id))
            if d[0] == "MAPPED":
                choices.append(Choice(index=i, decision="MAPPED", hpo_id=d[1]))
            elif d[0] == "AMBIGUOUS":
                choices.append(Choice(index=i, decision="AMBIGUOUS", options=d[1]))
            else:
                choices.append(Choice(index=i, decision="UNMAPPED"))
        return ChoiceSet(choices=choices), False

    monkeypatch.setattr("phenotype.graph.split", split)
    monkeypatch.setattr("phenotype.mapping.find_candidates", find_candidates)
    monkeypatch.setattr("phenotype.mapping.choose_terms", choose_terms)
    return seen


def fake_search_hpo(monkeypatch, results: list[dict]) -> None:
    """agent/tools.py calls kb.search.hpo.search_hpo through the module, so one patch covers the tool."""
    monkeypatch.setattr("kb.search.hpo.search_hpo", lambda text, k=20, profile=None: results[:k])


def row(finding_id: str, phrase: str, code: str | None, *, assertion: str = "PRESENT", subject: str = "FETUS",
        candidates: list[Candidate] | None = None) -> dict:
    """One CaseState["findings"] row: MAPPED to `code`, or UNMAPPED when code is None."""
    cands = candidates or ([cand(code, code)] if code else [])
    term = Term.model_validate(cands[0].model_dump()).model_dump(mode="json") if code else None
    return {"phrase": phrase, "source_text": phrase, "source_span": None, "assertion": assertion,
            "assessment": "ASSESSED", "temporality": None, "subject": subject,
            "mapping_status": "MAPPED" if code else "UNMAPPED", "term": term, "options": [],
            "candidates": [c.model_dump(mode="json") for c in cands], "finding_id": finding_id}


def seed_case(rows: list[dict], **extra) -> dict:
    """A CaseState dict with these finding rows (no messages)."""
    return {"description": "; ".join(r["phrase"] for r in rows), "hpo_release": "test", "findings": rows,
            "analysis_flags": {"split_failed": False, "choose_failed": False}, "remaps": {}, "edits": {},
            "notes": {}, "ranking": None, **extra}
