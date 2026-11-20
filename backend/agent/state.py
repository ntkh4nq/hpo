"""State of one case (docs/agent-impl-plan.md task 2; agent-plan §4.1, §5).

The case keeps the machine's findings as phase 3 produced them, plus the doctor's remaps, edits and notes
side by side (T91). `effective_findings` combines them; the machine's values are never overwritten, so every
change stays traceable (principle #6). Every key holds JSON only, because the state lives in Redis (T92).
"""

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from deepagents import DeepAgentState
from pydantic import BaseModel, model_validator

from phenotype.schema import Analysis, Assertion, Assessment, Finding, Subject, Term

CONFIG = tomllib.loads(Path(__file__).with_name("config.toml").read_text(encoding="utf-8"))

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
        return f"{t.label_vi} / {t.label_en} [{t.hpo_id}]" if t.label_vi else f"{t.label_en} [{t.hpo_id}]"
    if v.mapping_status == "MAPPED" and v.term:
        return name(v.term)
    if v.mapping_status == "AMBIGUOUS":
        return "phân vân: " + ", ".join(name(t) for t in v.options)
    return "chưa ánh xạ"


def matched_count(suggestion: Mapping) -> int:
    return sum(m["kind"] not in ("none", "excluded") for m in suggestion["matches"])


def render_case(state: Mapping) -> str:
    """The case summary appended to the supervisor's system prompt on every model call (agent-plan §5)."""
    lines = ["# Ca hiện tại"]
    flags = state.get("analysis_flags") or {}
    if flags.get("split_failed"):
        lines.append("Mô tả chưa được phân tích (lỗi bước tách).")
    views = effective_findings(state)
    if not views:
        lines.append("(Chưa có biểu hiện: cần gọi analyze_findings với mô tả của bác sĩ.)")
    else:
        lines.append("Biểu hiện (finding_id | chữ của bác sĩ | khái niệm HPO | trạng thái, mức đánh giá | chủ thể | ghi chú):")
        for v in views:
            parts = [v.finding_id, v.phrase, _concept(v), f"{v.assertion}, {v.assessment}", v.subject]
            if v.machine:
                parts.append("bác sĩ sửa: " + ", ".join(v.machine))
            if v.excluded:
                parts.append("bác sĩ bỏ khỏi xếp hạng")
            if v.note:
                parts.append(f'nhận xét của bác sĩ: "{v.note}"')
            lines.append("- " + " | ".join(parts))
    ranking = state.get("ranking")
    if ranking:
        lines.append("Gợi ý hiện tại (hạng · mã · tên · nguồn · số biểu hiện khớp):")
        lines += [f"{s['rank']:g} · {s['disease_id']} · {s['disease_name']} · {s['source_db']} · {matched_count(s)}"
                  for s in ranking["suggestions"]]
    return "\n".join(lines)
