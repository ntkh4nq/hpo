"""State of one case (docs/agent-impl-plan.md task 2; agent-plan §4.1, §5).

The case keeps the machine's findings as phase 3 produced them, plus the doctor's remaps, edits and notes
side by side (T91). `effective_findings` combines them; the machine's values are never overwritten, so every
change stays traceable (principle #6). Every key holds JSON only: the state is checkpointed in Redis and the
SAVED_KEYS are kept in Postgres (T139).
"""

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from deepagents import DeepAgentState
from pydantic import BaseModel, model_validator

from phenotype.schema import Analysis, Assertion, Assessment, Finding, Subject, Term

CONFIG = tomllib.loads(Path(__file__).with_name("config.toml").read_text(encoding="utf-8"))

# Names of human-role messages that the system writes, never the doctor (T128): the guard's rewrite request and the
# notice that the case changed outside the conversation. Views, the guard and analyze_findings skip them.
GUARD_NAME = "answer_guard"
CASE_EVENT_NAME = "case_event"


def is_doctor_message(m) -> bool:
    return m.type == "human" and m.name not in (GUARD_NAME, CASE_EVENT_NAME)


def case_event(key: str):
    """A notice in the conversation that the case changed, so earlier answers are not taken as current (T128)."""
    from langchain_core.messages import HumanMessage
    return HumanMessage(name=CASE_EVENT_NAME, content=CONFIG["case"]["events"][key])


# Fields whose machine value is kept in FindingView.machine when the doctor changed them.
TRACKED = ("phrase", "term", "mapping_status", "options", "assertion", "assessment", "subject")


class FindingEdit(BaseModel):            # one doctor edit of one finding (T91 b, c, d); None = unchanged
    finding_id: str
    excluded: bool | None = None         # b: drop from ranking
    assertion: Assertion | None = None   # c
    assessment: Assessment | None = None # c
    subject: Subject | None = None       # c
    hpo_id: str | None = None            # d: must be one of the finding's candidates (closed set, #8)


class ReviewDecision(BaseModel):         # resume value of the review step (agent-plan §3.3)
    action: Literal["continue", "remap", "cancel"]
    edits: list[FindingEdit] = []        # continue: the full current set of edits
    notes: dict[str, str] = {}           # continue: finding_id -> note (T91 f); "" deletes
    finding_id: str | None = None        # remap
    phrase: str | None = None            # remap: the doctor's corrected wording (T91 e)
    assertion: Assertion | None = None   # remap: the doctor's conclusion with it, e.g. a measurement read as abnormal (T118)

    @model_validator(mode="after")
    def _check(self):
        limit = CONFIG["text_max_chars"]
        if self.action == "remap" and not (self.finding_id and self.phrase and self.phrase.strip()):
            raise ValueError("remap cần finding_id và phrase không rỗng")
        if self.phrase and len(self.phrase) > limit:
            raise ValueError(f"phrase dài quá {limit} ký tự")
        if any(len(n) > limit for n in self.notes.values()):
            raise ValueError(f"ghi chú dài quá {limit} ký tự")
        return self


class FindingView(Finding):              # one effective finding as the UI and the supervisor see it
    finding_id: str
    excluded: bool = False
    note: str | None = None
    machine: dict[str, Any] = {}         # field -> the machine's original value, only for changed fields (#6)


class CaseState(DeepAgentState, total=False):   # DeepAgentState keeps the messages DeltaChannel reducer
    description: str          # Analysis.text of the last analysis (preprocessed; source_span offsets refer to it)
    hpo_release: str          # Analysis.hpo_release (#6)
    findings: list[dict]      # Finding.model_dump(mode="json") + "finding_id"
    analysis_flags: dict      # {"split_failed": bool, "choose_failed": bool}
    remaps: dict[str, dict]   # finding_id -> remapped Finding (json) + "finding_id"
    edits: dict[str, dict]    # finding_id -> FindingEdit json (exclude_none)
    notes: dict[str, str]     # finding_id -> note
    ranking: dict | None      # Ranking.model_dump(mode="json") of the current effective findings
    pending_action: dict | None   # card action input; cleared by apply_findings_action
    notes_archive: list[dict]  # [{description, findings: [FindingView json with a note]}] of replaced analyses (T116)


# The case data kept in Postgres (case_record.state) after every turn; the Redis checkpoint is rebuilt from it plus
# case_turn (T139). Not kept: messages (rebuilt from case_turn), pending_action (empty once a turn ends), and the
# per-turn keys of Deep Agents (todos, files).
SAVED_KEYS = ("description", "hpo_release", "findings", "analysis_flags", "remaps", "edits", "notes", "ranking",
              "notes_archive")


def finding_rows(analysis: Analysis) -> list[dict]:
    return [{**f.model_dump(mode="json"), "finding_id": f"f{i + 1}"} for i, f in enumerate(analysis.findings)]


def effective_findings(state: Mapping) -> list[FindingView]:
    """Machine findings with the doctor's remaps, edits and notes applied, in phase 3 order (agent-plan §4.1)."""
    remaps, edits, notes = state.get("remaps") or {}, state.get("edits") or {}, state.get("notes") or {}
    views = []
    for row in state.get("findings") or []:
        fid = row["finding_id"]
        view = FindingView.model_validate({**remaps.get(fid, row), "finding_id": fid, "note": notes.get(fid)})
        if fid in edits:
            edit = FindingEdit.model_validate(edits[fid])
            update: dict[str, Any] = {k: getattr(edit, k) for k in ("assertion", "assessment", "subject")
                                      if getattr(edit, k) is not None}
            if edit.hpo_id is not None:
                cand = next((c for c in view.candidates if c.hpo_id == edit.hpo_id), None)
                if cand is None:
                    raise ValueError(f"{edit.hpo_id} is not a candidate of {fid}")
                update |= {"term": Term.model_validate(cand.model_dump()), "mapping_status": "MAPPED", "options": []}
            if edit.excluded is not None:
                update["excluded"] = edit.excluded
            view = view.model_copy(update=update)
        now = view.model_dump(mode="json")
        view.machine = {k: row[k] for k in TRACKED if now[k] != row[k]}
        views.append(view)
    return views


def check_edits(views: list[FindingView], edits: list[FindingEdit], notes: dict[str, str]) -> None:
    """Raise ValueError (Vietnamese message) when an edit or note does not fit these findings (agent-plan §3.3)."""
    by_id = {v.finding_id: v for v in views}
    seen: set[str] = set()
    for e in edits:
        if e.finding_id not in by_id:
            raise ValueError(f"Không có biểu hiện {e.finding_id}.")
        if e.finding_id in seen:
            raise ValueError(f"Biểu hiện {e.finding_id} có hai bản sửa.")
        seen.add(e.finding_id)
        if e.hpo_id is not None and e.hpo_id not in {c.hpo_id for c in by_id[e.finding_id].candidates}:
            raise ValueError(f"Mã {e.hpo_id} không nằm trong các ứng viên của {e.finding_id}.")
    for fid, text in notes.items():
        if fid not in by_id:
            raise ValueError(f"Không có biểu hiện {fid}.")
        if len(text) > CONFIG["text_max_chars"]:
            raise ValueError(f"Ghi chú của {fid} dài quá {CONFIG['text_max_chars']} ký tự.")


def _concept(v: FindingView) -> str:
    def name(t: Term) -> str:
        return f"{t.label_en} [{t.hpo_id}]"
    if v.mapping_status == "MAPPED" and v.term:
        return name(v.term)
    if v.mapping_status == "AMBIGUOUS":
        return "phân vân: " + ", ".join(name(t) for t in v.options)
    return "chưa ánh xạ"


def matched_count(suggestion: Mapping) -> int:
    return sum(m["kind"] not in ("none", "excluded") for m in suggestion["matches"])


def _words(kind: str, code: str | None) -> str:
    """A state code in words (config [case.<kind>_words]); an unknown code is shown as it is."""
    return CONFIG["case"][f"{kind}_words"].get(code, code) if code else ""


def _state(v: Mapping) -> str:
    """The state that must always travel with a finding's phrase: assertion, then subject when not the fetus."""
    parts = [_words("assertion", v["assertion"])]
    if v.get("subject") and v["subject"] != "FETUS":
        parts.append(_words("subject", v["subject"]))
    return " — ".join(parts)


def render_case(state: Mapping) -> str:
    """The case summary appended to the supervisor's system prompt on every model call (agent-plan §5).
    Facts are written here by code, in words, never as state codes (T129)."""
    lines = ["# Ca hiện tại"]
    flags = state.get("analysis_flags") or {}
    if flags.get("split_failed"):
        lines.append("Mô tả chưa được phân tích (lỗi bước tách).")
    views = effective_findings(state)
    if not views:
        lines.append("(Chưa có biểu hiện: cần gọi analyze_findings với mô tả của bác sĩ.)")
    else:
        lines.append("Biểu hiện (finding_id | cụm máy tách — trạng thái | mức đánh giá | khái niệm HPO | câu gốc của bác sĩ "
                     "| ghi chú). Cụm máy tách luôn viết ở dạng khẳng định; nghĩa của nó chỉ đúng khi đọc cùng trạng thái:")
        for v in views:
            row = v.model_dump(mode="json")
            parts = [v.finding_id, f'"{v.phrase}" — {_state(row)}', _words("assessment", v.assessment), _concept(v),
                     f'câu gốc: "{v.source_text}"']
            if v.measurement:
                parts.append(f"số đo trong mô tả: {v.measurement.quantity} {v.measurement.value}")
            if v.machine:
                parts.append("bác sĩ sửa: " + ", ".join(v.machine))
            if v.excluded:
                parts.append("bác sĩ bỏ khỏi tìm kiếm")
            if v.note:
                parts.append(f'nhận xét của bác sĩ: "{v.note}"')
            lines.append("- " + " | ".join(parts))
    ranking = state.get("ranking")
    if ranking:
        lines += _render_ranking(ranking, views)
    return "\n".join(lines)


def _frequency(raw: list[str | None]) -> str:
    """hpoa frequency values in words (config [case.frequency_words]); "n/m" stays as patients; empty = not recorded."""
    words = CONFIG["case"]["frequency_words"]
    out = []
    for value in dict.fromkeys(v for v in raw if v):
        out.append(words.get(value) or (f"{value} bệnh nhân" if "/" in value else value))
    return ", ".join(out) or "tần suất chưa ghi"


def _basis(frequency_raw: list[str | None], references: list[list[str]]) -> str:
    """Frequency per source row with its references: what each number rests on (principle #6)."""
    parts = []
    for raw, refs in zip(frequency_raw, references or [[]] * len(frequency_raw)):
        parts.append(f"{_frequency([raw])}" + (f" theo {', '.join(refs)}" if refs else ""))
    return "; ".join(dict.fromkeys(parts)) or "tần suất chưa ghi"


def _traits(items: list[Mapping]) -> str:
    return "; ".join(f"{t['label_en']}"
                     + (f" ({_frequency(t['frequency_raw'])})" if any(t["frequency_raw"]) else "")
                     + (f" theo {', '.join(dict.fromkeys(r for refs in t['references'] for r in refs))}"
                        if any(t["references"]) else "") for t in items)


def _evidence(matched: list[Mapping]) -> str:
    """What the matches of one disease rest on, counted in code so the summary need not recount numbers (T114)."""
    rows = [(raw, refs) for m in matched for raw, refs in zip(m["frequency_raw"], m.get("references") or [])]
    with_freq = sum(any(m["frequency_raw"]) for m in matched)
    papers = {r for _, refs in rows for r in refs if r.startswith("PMID:")}
    databases = {r.split(":", 1)[0] for _, refs in rows for r in refs if not r.startswith("PMID:")}
    patients = [int(raw.split("/")[1]) for raw, _ in rows if raw and "/" in raw and raw.split("/")[1].isdigit()]
    parts = [f"{with_freq}/{len(matched)} biểu hiện khớp có ghi tần suất"]
    if papers:
        parts.append(f"{len(papers)} bài báo ({', '.join(sorted(papers))})")
    if databases:
        source_names = CONFIG["case"]["source_names"]
        parts.append("ghi nhận của cơ sở dữ liệu " + ", ".join(source_names.get(d, d) for d in sorted(databases)))
    if patients:
        parts.append(f"mỗi biểu hiện dựa trên nhiều nhất {max(patients)} bệnh nhân được mô tả")
    return "; ".join(parts)


def _elsewhere(u: Mapping, names: Mapping[str, str]) -> str:
    """Where else in the list a not-mentioned phenotype is recorded, as ranking computed it (T114)."""
    parts = ["cũng ghi ở: " + ", ".join(names.get(d, d) for d in u["also_in"]) if u.get("also_in")
             else "không bệnh nào khác trong danh sách ghi"]
    if u.get("excluded_in"):
        parts.append("ghi nhận KHÔNG có ở: " + ", ".join(names.get(d, d) for d in u["excluded_in"]))
    return "; ".join(parts)


def _render_ranking(ranking: Mapping, views: list["FindingView"]) -> list[str]:
    """Suggestions as the doctor's list shows them, with the data behind each row: matches with their sources,
    inheritance and course, findings the disease is recorded without, and a finding-by-disease view (T114)."""
    labels = {v.term.hpo_id: v.term.label_en for v in views if v.term and v.mapping_status == "MAPPED"}
    labels |= {t["hpo_id"]: t["label_en"] for t in ranking["terms"] + ranking.get("absent", []) if t.get("label_en")}
    by_id = {v.finding_id: v for v in views}
    kinds = CONFIG["case"]["match_words"]
    names = {s["disease_id"]: s["disease_name"] or s["disease_id"] for s in ranking["suggestions"]}
    listed = [s for s in ranking["suggestions"] if any(m["kind"] in kinds for m in s["matches"])]
    lines = ["Biểu hiện của thai dùng để tìm bệnh (khái niệm HPO ← cụm máy tách của các biểu hiện đứng sau nó):"]
    for t in ranking["terms"]:
        phrases = ", ".join(f'"{by_id[f].phrase}" ({f})' for f in t.get("finding_ids", []) if f in by_id)
        line = f"- {labels.get(t['hpo_id'], t['hpo_id'])} [{t['hpo_id']}]" + (f" ← {phrases}" if phrases else "")
        if t.get("from_options"):
            line += " · khái niệm chung của các lựa chọn " + ", ".join(
                f"{labels.get(o, o)} [{o}]" for o in t["from_options"])
        if t["assertion"] == "SUSPECTED":
            line += " · ở thai mới nghi ngờ"
        lines.append(line)
    lines.append("Gợi ý hiện tại: danh sách 'Bệnh liên quan' trên màn hình hiện đúng các bệnh khớp ít nhất một biểu hiện, "
                 "theo thứ tự dưới đây (hạng · mã · tên · nguồn · gene). Tần suất 'n/m bệnh nhân' là số bệnh nhân có biểu "
                 "hiện trên số bệnh nhân được mô tả trong nguồn ghi kèm:")
    for s in ranking["suggestions"]:
        genes = ", ".join(g["symbol"] for g in s["genes"]) or "chưa có gene trong dữ liệu"
        matched = [m for m in s["matches"] if m["kind"] in kinds]
        # Counts by kind, so "which disease matches the concept most" is read, not tallied by the model.
        by_kind = "; ".join(f"{word}: {n}" for kind, word in kinds.items()
                            if (n := sum(m["kind"] == kind for m in matched)))
        lines.append(f"- {s['rank']:g} · {s['disease_id']} · {names[s['disease_id']]} · {s['source_db']} · gene: {genes}"
                     f" · khớp {len(matched)}/{len(s['matches'])} biểu hiện" + (f" ({by_kind})" if by_kind else ""))
        if matched:
            lines.append("  - căn cứ: " + _evidence(matched))
        if s.get("inheritance"):
            lines.append("  - kiểu di truyền ghi nhận: " + _traits(s["inheritance"]))
        if s.get("clinical_course"):
            lines.append("  - diễn tiến, khởi phát ghi nhận: " + _traits(s["clinical_course"]))
        for m in matched:
            # Not an exact match: name what the disease records, so it is not said to record the fetus's finding (T138).
            records = f" (bệnh ghi {m['disease_label_en']})" if m["kind"] != "exact" and m.get("disease_label_en") else ""
            lines.append(f"  - khớp: {labels.get(m['query_hpo_id'], m['query_hpo_id'])} ({kinds[m['kind']]}{records}; ở bệnh: "
                         f"{_basis(m['frequency_raw'], m.get('references', []))}"
                         f"{'; ở thai mới nghi ngờ' if m['assertion'] == 'SUSPECTED' else ''})")
        excluded = [labels.get(m["query_hpo_id"], m["query_hpo_id"]) for m in s["matches"] if m["kind"] == "excluded"]
        if excluded:
            lines.append("  - bệnh được ghi nhận là KHÔNG có: " + ", ".join(excluded))
        for c in s.get("conflicts", []):
            disease_term = c.get("disease_label_en") or c["disease_hpo_id"]
            lines.append(f"  - thai được ghi là không có, bệnh thường có: {labels.get(c['query_hpo_id'], c['query_hpo_id'])}"
                         f" (bệnh ghi {disease_term}: {_basis(c['frequency_raw'], c.get('references', []))})")
        missing = [labels.get(m["query_hpo_id"], m["query_hpo_id"]) for m in s["matches"] if m["kind"] == "none"]
        if missing:
            lines.append("  - chưa có trong dữ liệu HPO của bệnh: " + ", ".join(missing))
        if s["unmentioned"]:
            lines.append("  - thường gặp ở bệnh, mô tả chưa nhắc: " + "; ".join(
                f"{u['label_en']} ({_basis(u['frequency_raw'], u.get('references', []))}; "
                + _elsewhere(u, names) + ")" for u in s["unmentioned"]))
    if listed:
        lines.append("Biểu hiện của thai theo bệnh trong danh sách (biểu hiện: kiểu khớp → các bệnh; KHÔNG có = bệnh được "
                     "ghi nhận là không có biểu hiện đó; chưa có = chưa có trong dữ liệu của bệnh):")
        for t in ranking["terms"]:
            by_kind: dict[str, list[str]] = {}
            for s in listed:
                m = next((m for m in s["matches"] if m["query_hpo_id"] == t["hpo_id"]), None)
                kind = kinds.get(m["kind"]) if m else None
                key = kind or ("KHÔNG có" if m and m["kind"] == "excluded" else "chưa có")
                by_kind.setdefault(key, []).append(names[s["disease_id"]])
            lines.append(f"- {labels.get(t['hpo_id'], t['hpo_id'])}: " + "; ".join(
                f"{k} → {', '.join(dict.fromkeys(v))}" for k, v in by_kind.items()))
    shared = [g for g in ranking["genes"] if len(g["disease_ids"]) > 1]
    if shared:
        lines.append("Gene gắn với nhiều gợi ý (gene · các bệnh trong danh sách): " + "; ".join(
            f"{g['symbol']} · {', '.join(names.get(d, d) for d in g['disease_ids'])}" for g in shared))
    if ranking["skipped"]:
        lines.append("Biểu hiện không dùng để tìm bệnh (giao diện đã liệt kê sẵn cho bác sĩ):")
        for k in ranking["skipped"]:
            v = by_id.get(k.get("finding_id") or "")
            state_ = _state(v.model_dump(mode="json")) if v else _words("assertion", k.get("assertion"))
            name = f'"{k["phrase"]}"' if k.get("phrase") else k["hpo_id"]
            lines.append(f"- {name} — {state_} — {_words('skip', k['reason'])}" if state_
                         else f"- {name} — {_words('skip', k['reason'])}")
    return lines
