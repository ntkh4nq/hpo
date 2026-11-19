"""Cut a ParsedDocument into chunks (docs/ingestion-plan.md §5, T61). Pure: no DB, no model calls.

Every chunk has two texts: `display_text` (verbatim, shown to the doctor) and `embed_text` (what is embedded:
context line + content). Figure descriptions from `vlm` only ever go into `embed_text` (principle #1).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from kb.ingest.config import CHUNKING, INGEST
from kb.ingest.model import Figure, Paragraph, ParsedDocument, Table, absolute

SENTENCE_END = re.compile(r"(?<=[.?!])\s+(?=[A-Z])|\n")  # a new line keeps the next line's indentation
PARAGRAPH_JOIN = "\n\n"


@dataclass(frozen=True)
class ChunkDraft:
    section: int        # index into doc.sections (= section.ord)
    ord: int
    kind: str           # 'text' | 'table' | 'figure'
    role: str           # 'leaf' | 'parent' | 'child'
    display_text: str
    embed_text: str | None  # None for parents (never embedded)
    figure_path: str | None = None
    parent_ord: int | None = None


@lru_cache(maxsize=1)
def _tokenizer():
    from tokenizers import Tokenizer

    return Tokenizer.from_file(str(absolute(INGEST["tokenizer"])))


def count_tokens(text: str) -> int:
    return len(_tokenizer().encode(text, add_special_tokens=False).ids)


def strategy(name: str | None = None) -> dict:
    return CHUNKING[name or CHUNKING["default"]]


def context_prefix(doc: ParsedDocument, heading_path: list[str], disease_names: list[str]) -> str:
    """Line 1: title › section path. Line 2: up to 3 disease names (absent when the document has no codes)."""
    lines = [" › ".join([doc.title, *heading_path])]
    if disease_names:
        lines.append("Diseases: " + "; ".join(disease_names[:3]))
    return "\n".join(lines)


def _with_prefix(prefix: str, body: str) -> str:
    return f"{prefix}\n{body}" if prefix else body


# ---- paragraphs ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Unit:
    text: str
    paragraph: int  # which paragraph the piece came from
    sep: str        # separator before it inside its paragraph (" " or "\n"); unused for a paragraph's first piece


def _join(units: list[_Unit]) -> str:
    out = units[0].text
    for prev, u in zip(units, units[1:]):
        out += (u.sep if u.paragraph == prev.paragraph else PARAGRAPH_JOIN) + u.text
    return out


def _split(text: str, paragraph: int) -> list[_Unit]:
    """Pieces at sentence ends (., ?, ! + whitespace + capital) and at line breaks (list lines, T78)."""
    units, start, sep = [], 0, " "
    for m in SENTENCE_END.finditer(text):
        piece = text[start:m.start()].rstrip()
        if piece.strip():
            units.append(_Unit(piece, paragraph, sep))
        start, sep = m.end(), ("\n" if "\n" in m.group() else " ")
    if text[start:].strip():
        units.append(_Unit(text[start:].rstrip(), paragraph, sep))
    return units


def pack(paragraphs: list[str], fits: Callable[[str], bool]) -> list[str]:
    """Rule 2: group consecutive paragraphs while the chunk fits; split a paragraph that alone does not
    fit at sentence boundaries; a sentence that alone does not fit stands as its own chunk."""
    units: list[_Unit] = []
    for i, p in enumerate(paragraphs):
        units += [_Unit(p, i, " ")] if fits(p) else _split(p, i)
    chunks: list[list[_Unit]] = []
    for u in units:
        if chunks and fits(_join(chunks[-1] + [u])):
            chunks[-1].append(u)
        else:
            chunks.append([u])
    return [_join(c) for c in chunks]


# ---- tables -------------------------------------------------------------------------------------
def _cell(v: str) -> str:
    return v.replace("|", "\\|").replace("\n", " ")


def table_markdown(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(_cell(h) for h in header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def row_text(header: list[str], row: list[str]) -> str:
    """'<column>: <value>; …', empty cells left out."""
    return "; ".join(f"{h}: {v}" for h, v in zip(header, row) if v.strip())


def table_parts(table: Table, prefix: str, max_tokens: int, count=count_tokens) -> list[tuple[str, str]]:
    """Rule 4: (display, embed) per part. A table over max_tokens is cut into row groups; each part
    repeats caption, footnotes and the column header."""
    head = [x for x in (table.caption, table.footnotes) if x]

    def embed(rows):
        return _with_prefix(prefix, "\n".join(head + [row_text(table.header, r) for r in rows]))

    def display(rows):
        return "\n\n".join(x for x in (table.caption, table_markdown(table.header, rows), table.footnotes) if x)

    groups: list[list[list[str]]] = []
    for row in table.rows:
        if groups and count(embed(groups[-1] + [row])) <= max_tokens:
            groups[-1].append(row)
        else:
            groups.append([row])
    return [(display(g), embed(g)) for g in groups or [[]]]


# ---- figures ------------------------------------------------------------------------------------
def figure_texts(figure: Figure, prefix: str, description: str | None) -> tuple[str, str]:
    """Rule 5: display = original caption only; embed = context, caption, then the vlm description."""
    body = [x for x in (figure.caption, f"Figure description: {description}" if description else "") if x]
    return figure.caption, _with_prefix(prefix, "\n".join(body))


# ---- document -----------------------------------------------------------------------------------
def chunk_document(doc: ParsedDocument, settings: dict, disease_names: list[str],
                   descriptions: dict[str, str | None], count: Callable[[str], int] = count_tokens) -> list[ChunkDraft]:
    """All chunks of one document for one strategy, in reading order (rules 1–7)."""
    parent_child = "child_max_tokens" in settings
    leaf_max = settings["child_max_tokens"] if parent_child else settings["max_tokens"]
    out: list[ChunkDraft] = []

    def emit(**kw) -> int:
        out.append(ChunkDraft(ord=len(out), **kw))
        return len(out) - 1

    for si, section in enumerate(doc.sections):
        prefix = context_prefix(doc, section.heading_path, disease_names) if settings["context_line"] else ""

        def text_chunks(paragraphs: list[str]) -> None:
            if not paragraphs:
                return
            if not parent_child:
                for body in pack(paragraphs, lambda t: count(_with_prefix(prefix, t)) <= leaf_max):
                    emit(section=si, kind="text", role="leaf", display_text=body,
                         embed_text=_with_prefix(prefix, body))
                return
            for parent in pack(paragraphs, lambda t: count(t) <= settings["max_tokens"]):
                p = emit(section=si, kind="text", role="parent", display_text=parent, embed_text=None)
                for child in pack(parent.split(PARAGRAPH_JOIN),
                                  lambda t: count(_with_prefix(prefix, t)) <= leaf_max):
                    emit(section=si, kind="text", role="child", display_text=child,
                         embed_text=_with_prefix(prefix, child), parent_ord=p)

        run: list[str] = []
        for block in section.blocks:
            if isinstance(block, Paragraph):
                run.append(block.text)
                continue
            text_chunks(run)
            run = []
            if isinstance(block, Table):
                for display, embed in table_parts(block, prefix, leaf_max, count):
                    emit(section=si, kind="table", role="leaf", display_text=display, embed_text=embed)
            else:
                display, embed = figure_texts(block, prefix, descriptions.get(block.image_path or ""))
                emit(section=si, kind="figure", role="leaf", display_text=display, embed_text=embed,
                     figure_path=block.image_path)
        text_chunks(run)
    return out
