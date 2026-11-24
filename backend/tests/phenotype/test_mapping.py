"""Phase 3 tests (docs/mapping-plan.md §10). No gateway calls: model and search are faked."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from core.db import connect
from phenotype import graph as graph_module
from core import llm
from phenotype import mapping
from core.llm import invoke_json
from phenotype.mapping import best_dense, check_choices, gate
from phenotype.schema import Candidate, Choice, ChoiceSet, Item, Measurement
from phenotype.split import locate

# ---------- locate

TEXT = "Thai 22 tuần. Ghi nhận thông liên thất. Chưa ghi nhận giãn não thất."


def test_locate_exact():
    assert locate(TEXT, "Ghi nhận thông liên thất.") == (14, 39)
    assert TEXT[14:39] == "Ghi nhận thông liên thất."


def test_locate_strips_surrounding_spaces():
    assert locate(TEXT, "  Ghi nhận thông liên thất. ") == (14, 39)


def test_locate_not_found():
    assert locate(TEXT, "Ghi nhận thông liên nhĩ.") is None
    assert locate(TEXT, "") is None


# ---------- invoke_json

GOOD = '{"choices": [{"index": 0, "decision": "MAPPED", "hpo_id": "HP:0001629"}]}'


class FakeChat:
    """Stands in for ChatOpenAI: replies are consumed in order; an Exception reply is raised."""

    def __init__(self, replies):
        self.replies, self.calls, self.structured = list(replies), [], None

    def __call__(self, model=None, timeout=120, thinking=False):
        self.thinking = thinking
        return self

    def with_structured_output(self, schema, method, include_raw=False):
        self.structured = (schema, method)
        return self

    def invoke(self, messages, config=None):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if not self.structured:
            return SimpleNamespace(content=reply)
        error = reply if isinstance(reply, ValueError) else None   # a ValueError reply stands for a parsing error
        return {"raw": AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}),
                "parsed": None if error else reply, "parsing_error": error}


@pytest.fixture
def fake(monkeypatch):
    def install(*replies):
        f = FakeChat(replies)
        monkeypatch.setattr(llm, "chat", f)
        return f
    return install


def test_invoke_json_thinking_valid(fake):
    f = fake(GOOD)
    out = invoke_json("SYS", "HUMAN", ChoiceSet, thinking=True, timeout=1)
    assert out.choices[0].hpo_id == "HP:0001629"
    assert len(f.calls) == 1 and f.thinking
    system = f.calls[0][0][1]
    assert system.startswith("SYS") and llm.JSON_INSTRUCTION in system and '"choices"' in system


def test_invoke_json_retries_once_after_bad_json(fake):
    f = fake('{"choices": [{"index": "x"}]}', GOOD)
    assert invoke_json("S", "H", ChoiceSet, thinking=True, timeout=1).choices[0].index == 0
    assert len(f.calls) == 2


def test_invoke_json_gives_none_after_two_failures(fake):
    f = fake("not json", TimeoutError())
    assert invoke_json("S", "H", ChoiceSet, thinking=True, timeout=1) is None
    assert len(f.calls) == 2


def test_invoke_json_strips_markdown_fence(fake):
    fake(f"```json\n{GOOD}\n```")
    assert invoke_json("S", "H", ChoiceSet, thinking=True, timeout=1).choices[0].decision == "MAPPED"


def test_invoke_json_counts_usage(fake):
    expected = ChoiceSet.model_validate_json(GOOD)
    fake(TimeoutError(), expected)
    usage = {}
    assert invoke_json("S", "H", ChoiceSet, thinking=False, timeout=1, usage=usage) is expected
    assert usage == {"requests": 2, "input_tokens": 10, "output_tokens": 3}


def test_invoke_json_parsing_error_is_a_failed_attempt(fake):
    expected = ChoiceSet.model_validate_json(GOOD)
    f = fake(ValueError("bad tool arguments"), expected)
    assert invoke_json("S", "H", ChoiceSet, thinking=False, timeout=1) is expected
    assert len(f.calls) == 2


def test_invoke_json_thinking_off_uses_function_calling(fake):
    expected = ChoiceSet.model_validate_json(GOOD)
    f = fake(expected)
    assert invoke_json("S", "H", ChoiceSet, thinking=False, timeout=1) is expected
    assert f.structured == (ChoiceSet, "function_calling") and f.calls[0][0] == ("system", "S")


# ---------- shared fakes for steps 3-5

VSD, VENTRIC, CLAV, SHORT_CLAV = "HP:0001629", "HP:0002119", "HP:0000889", "HP:0000894"
DESC = "Ghi nhận thông liên thất. Xương đòn ngắn."


def cand(hpo_id, dense=0.9, label_en=None):
    return Candidate(hpo_id=hpo_id, label_en=label_en or f"EN {hpo_id}", label_vi=f"VI {hpo_id}",
                     label_vi_reviewed=True, definition_en=f"DEF {hpo_id}", matched_text="m", dense_score=dense)


def item(phrase, source_text):
    return Item(phrase=phrase, source_text=source_text, assertion="PRESENT", assessment="ASSESSED",
                temporality=None, subject="FETUS")


ITEMS = [item("thông liên thất", "Ghi nhận thông liên thất."), item("xương đòn ngắn", "Xương đòn ngắn.")]
CANDS = {0: [cand(VSD), cand(VENTRIC)], 1: [cand(CLAV), cand(SHORT_CLAV)]}


def check(*choices, gated=frozenset(), choose_failed=False, candidates=CANDS):
    return check_choices(DESC, ITEMS, candidates, set(gated), ChoiceSet(choices=list(choices)),
                         choose_failed, False)


def reasons(analysis):
    return [(r.index, r.reason) for r in analysis.rejections]


def statuses(analysis):
    return [f.mapping_status for f in analysis.findings]


# ---------- check_choices (§6)

def test_check_correct_answers():
    a = check(Choice(index=0, decision="MAPPED", hpo_id=VSD), Choice(index=1, decision="MAPPED", hpo_id=SHORT_CLAV))
    assert statuses(a) == ["MAPPED", "MAPPED"] and reasons(a) == []
    assert [f.term.hpo_id for f in a.findings] == [VSD, SHORT_CLAV]
    assert a.findings[0].source_span == (0, 25) and a.hpo_release
    assert a.findings[0].candidates == CANDS[0]


def test_check_term_comes_from_candidate():
    candidates = {0: [cand(VSD, label_en="label from KB")], 1: CANDS[1]}
    a = check(Choice(index=0, decision="MAPPED", hpo_id=VSD), Choice(index=1, decision="UNMAPPED"),
              candidates=candidates)
    t = a.findings[0].term
    assert (t.label_en, t.definition_en) == ("label from KB", f"DEF {VSD}") and "label_vi" not in t.model_dump()


def test_check_missing_answer():
    a = check(Choice(index=0, decision="MAPPED", hpo_id=VSD))
    assert statuses(a) == ["MAPPED", "UNMAPPED"] and reasons(a) == [(1, "no answer")]


def test_check_duplicate_index_keeps_first():
    a = check(Choice(index=0, decision="MAPPED", hpo_id=VSD), Choice(index=0, decision="MAPPED", hpo_id=VENTRIC),
              Choice(index=1, decision="UNMAPPED"))
    assert a.findings[0].term.hpo_id == VSD and reasons(a) == [(0, "duplicate answer")]


def test_check_unknown_index_including_gated_item():
    a = check(Choice(index=0, decision="MAPPED", hpo_id=VSD), Choice(index=1, decision="MAPPED", hpo_id=CLAV),
              Choice(index=7, decision="MAPPED", hpo_id=VSD), gated={1})
    assert statuses(a) == ["MAPPED", "UNMAPPED"]
    assert reasons(a) == [(None, "unknown index 1"), (None, "unknown index 7")]
    assert a.findings[1].candidates == CANDS[1]  # the gate keeps what it dropped


def test_check_mapped_without_hpo_id():
    a = check(Choice(index=0, decision="MAPPED"), Choice(index=1, decision="UNMAPPED"))
    assert statuses(a) == ["UNMAPPED", "UNMAPPED"] and reasons(a) == [(0, "MAPPED without hpo_id")]


def test_check_code_of_another_items_candidates():
    a = check(Choice(index=0, decision="MAPPED", hpo_id=CLAV), Choice(index=1, decision="UNMAPPED"))
    assert statuses(a)[0] == "UNMAPPED" and reasons(a) == [(0, f"{CLAV} not in its candidates")]


def test_check_obsolete_or_unknown_code():
    with connect() as conn:
        obsolete = conn.execute("SELECT hpo_id FROM hpo_term WHERE is_obsolete LIMIT 1").fetchone()[0]
    candidates = {0: [cand(obsolete)], 1: [cand("HP:9999999")]}
    a = check(Choice(index=0, decision="MAPPED", hpo_id=obsolete), Choice(index=1, decision="MAPPED", hpo_id="HP:9999999"),
              candidates=candidates)
    assert statuses(a) == ["UNMAPPED", "UNMAPPED"]
    assert reasons(a) == [(0, f"{obsolete} obsolete or not in KB"), (1, "HP:9999999 obsolete or not in KB")]


def test_check_ambiguous_duplicate_options_dropped_silently():
    a = check(Choice(index=0, decision="AMBIGUOUS", options=[VSD, VSD, VENTRIC]), Choice(index=1, decision="UNMAPPED"))
    assert statuses(a)[0] == "AMBIGUOUS" and reasons(a) == []
    assert [t.hpo_id for t in a.findings[0].options] == [VSD, VENTRIC] and a.findings[0].term is None


def test_check_ambiguous_left_with_one_option():
    a = check(Choice(index=0, decision="AMBIGUOUS", options=[VSD, CLAV]), Choice(index=1, decision="UNMAPPED"))
    assert statuses(a)[0] == "UNMAPPED" and a.findings[0].options == []
    assert reasons(a) == [(0, f"{CLAV} not in its candidates"), (0, "AMBIGUOUS left with 1 valid options")]


def test_check_choose_failed():
    a = check_choices(DESC, ITEMS, CANDS, set(), None, True, False)
    assert statuses(a) == ["UNMAPPED", "UNMAPPED"] and reasons(a) == [] and a.choose_failed


# ---------- best_dense and gate (§4.2)

def test_best_dense_and_gate():
    candidates = {0: [cand(VSD, 0.4), cand(VENTRIC, 0.8)], 1: [cand(CLAV, 0.3)], 2: [cand(SHORT_CLAV, None)]}
    assert best_dense(candidates[0]) == 0.8 and best_dense(candidates[2]) == 0.0 and best_dense([]) == 0.0
    assert gate(candidates, 0.5) == {1, 2}
    assert gate(candidates, 0.0) == set()


# ---------- graph (§7)

def hit(hpo_id, dense):
    return {"hpo_id": hpo_id, "label_en": f"EN {hpo_id}", "label_vi": None, "matched_text": "m",
            "scores": {"dense_score": dense}}


@pytest.fixture
def graph_fakes(monkeypatch):
    calls = {"choose": 0}
    state = {"items": ITEMS, "choices": None}
    monkeypatch.setattr(graph_module, "split", lambda text: state["items"])
    monkeypatch.setattr(mapping, "search_hpo",
                        lambda text, k: [hit(VSD, 0.9), hit(VENTRIC, 0.5)] if "thất" in text else [hit(SHORT_CLAV, 0.7)])

    def fake_invoke(system, human, schema, **kw):
        calls["choose"] += 1
        calls["human"] = human
        return state["choices"]
    monkeypatch.setattr(mapping, "invoke_json", fake_invoke)
    return state, calls


def test_graph_happy_path(graph_fakes):
    state, calls = graph_fakes
    state["choices"] = ChoiceSet(choices=[Choice(index=0, decision="MAPPED", hpo_id=VSD),
                                          Choice(index=1, decision="MAPPED", hpo_id=SHORT_CLAV)])
    a = graph_module.analyze("Ghi nhận  thông liên thất.\nXương đòn ngắn.")
    assert statuses(a) == ["MAPPED", "MAPPED"] and calls["choose"] == 1
    assert a.findings[1].term.definition_en  # read from the KB, not from search_hpo
    assert "[0] phrase: thông liên thất" in calls["human"] and f"  - {VSD} | EN {VSD} | — |" in calls["human"]


def test_graph_split_failed(graph_fakes):
    state, calls = graph_fakes
    state["items"] = None
    a = graph_module.analyze(DESC)
    assert a.split_failed and a.findings == [] and calls["choose"] == 0 and a.hpo_release


def test_graph_no_items(graph_fakes):
    state, calls = graph_fakes
    state["items"] = []
    a = graph_module.analyze(DESC)
    assert not a.split_failed and a.findings == [] and calls["choose"] == 0


def test_graph_choose_failed(graph_fakes):
    state, calls = graph_fakes  # invoke_json returns None
    a = graph_module.analyze(DESC)
    assert a.choose_failed and statuses(a) == ["UNMAPPED", "UNMAPPED"] and calls["choose"] == 1


def test_graph_everything_gated_skips_model(graph_fakes, monkeypatch):
    _, calls = graph_fakes
    monkeypatch.setitem(mapping.CONFIG, "unmapped_dense_threshold", 0.95)
    a = graph_module.analyze(DESC)
    assert statuses(a) == ["UNMAPPED", "UNMAPPED"] and calls["choose"] == 0 and not a.choose_failed
    assert a.rejections == [] and len(a.findings[0].candidates) == 2


# ---------- measurements (docs/followup-plan.md §B, T117, T118)

def test_measurement_awaiting_doctor_is_searched_by_quantity_and_skips_choose(graph_fakes, monkeypatch):
    state, calls = graph_fakes
    searched = []
    real = mapping.search_hpo
    monkeypatch.setattr(mapping, "search_hpo", lambda text, k: searched.append(text) or real(text, k))
    plain = Item(phrase="bất thường kích thước não thất", source_text="Kích thước não thất 11 mm.", assertion="UNCERTAIN",
                 assessment="ASSESSED", temporality=None, subject="FETUS",
                 measurement=Measurement(quantity="Kích thước não thất", value="11 mm"))
    state["items"] = [ITEMS[0], plain]
    state["choices"] = ChoiceSet(choices=[Choice(index=0, decision="MAPPED", hpo_id=VSD)])
    a = graph_module.analyze("Ghi nhận thông liên thất. Kích thước não thất 11 mm.")
    assert searched == ["thông liên thất", "Kích thước não thất"]
    assert "[1]" not in calls["human"] and calls["choose"] == 1
    f = a.findings[1]
    assert (f.mapping_status, f.assertion, f.measurement.value) == ("UNMAPPED", "UNCERTAIN", "11 mm")
    assert [c.hpo_id for c in f.candidates] == [VSD, VENTRIC] and a.rejections == []


def test_measurement_with_conclusion_goes_through_choose(graph_fakes):
    state, calls = graph_fakes
    concluded = Item(phrase="giãn não thất", source_text="Giãn não thất 12 mm.", assertion="PRESENT", assessment="ASSESSED",
                     temporality=None, subject="FETUS", measurement=Measurement(quantity="não thất", value="12 mm"))
    state["items"] = [concluded]
    state["choices"] = ChoiceSet(choices=[Choice(index=0, decision="MAPPED", hpo_id=VENTRIC)])
    a = graph_module.analyze("Giãn não thất 12 mm.")
    assert calls["choose"] == 1 and a.findings[0].term.hpo_id == VENTRIC and a.findings[0].measurement.quantity == "não thất"


def test_measurement_not_verbatim_is_dropped_and_logged(graph_fakes):
    state, calls = graph_fakes
    invented = Item(phrase="bất thường não thất", source_text="Não thất 11 mm.", assertion="UNCERTAIN",
                    assessment="ASSESSED", temporality=None, subject="FETUS",
                    measurement=Measurement(quantity="não thất", value="1,1 cm"))
    state["items"] = [invented]
    a = graph_module.analyze("Não thất 11 mm.")
    assert a.findings[0].measurement is None and calls["choose"] == 1   # not verbatim: an ordinary UNCERTAIN item
    assert reasons(a) == [(0, "measurement not verbatim in source_text")]
