"""Read JATS (PMC articles) and BITS (GeneReviews chapters) XML into a ParsedDocument.

Rules: docs/ingestion-plan.md Appendix C.1. Text is kept verbatim (only whitespace collapsed), including
in-sentence citations, because GeneReviews and CC BY-NC-ND forbid modifying the content.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from kb.ingest.model import Block, Figure, Paragraph, Section, Table, rel

XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
SKIP = {"ref-list", "ack", "app-group", "fn-group", "supplementary-material", "disp-formula", "inline-graphic"}
BLOCK = {"p", "table-wrap", "fig", "list", "boxed-text", "disp-quote"}
IMAGE_SUFFIXES = (".jpg", ".png", ".gif")
FLOATS_SEC_TYPE = "figs-and-tables"  # untitled <sec> where GeneReviews collects its figures (T77)


def norm(text: str) -> str:
    return " ".join(text.split())


def _raw_text(el: ET.Element) -> str:
    """itertext without the skipped elements (their tails are kept)."""
    parts = [el.text or ""]
    for child in el:
        if child.tag == "break":
            parts.append(" ")
        elif child.tag not in SKIP:
            parts.append(_raw_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def text(el: ET.Element | None) -> str:
    return norm(_raw_text(el)) if el is not None else ""


def caption_of(el: ET.Element) -> str:
    """<label> + <caption> of a table-wrap or fig."""
    return norm(" ".join(t for t in (text(el.find("label")), text(el.find("caption"))) if t))


class Reader:
    def __init__(self, base_dir: Path, skip_sections: list[str]):
        self.base_dir = base_dir
        self.skip_sections = {s.casefold() for s in skip_sections}
        self.sections: list[Section] = []
        self._closed = False  # True after a <sec> boundary: the next block starts a new section entry
        self.floats: dict[str, ET.Element] = {}  # id -> fig/table-wrap waiting for its first citation

    # ---- sections -------------------------------------------------------------------------------
    def add(self, path: list[str], blocks: list[Block]) -> None:
        """Append blocks to the section at `path`, starting a new one when reading order moved elsewhere."""
        if not blocks:
            return
        if not self.sections or self.sections[-1].heading_path != path or self._closed:
            self.sections.append(Section(list(path)))
            self._closed = False
        self.sections[-1].blocks.extend(blocks)

    def read(self, body: ET.Element, floats_group: ET.Element | None) -> list[Section]:
        """Body sections in reading order. Floats collected apart (a figs-and-tables <sec>, <floats-group>)
        are placed right after the first block that cites them; floats never cited go to a last [] section."""
        holders = [s for s in body.iter("sec") if s.get("sec-type") == FLOATS_SEC_TYPE]
        holders += [floats_group] if floats_group is not None else []
        for holder in holders:
            for el in holder:
                if el.tag in ("fig", "table-wrap"):
                    self.floats[el.get("id") or f"_{len(self.floats)}"] = el
        self.walk(body, [])
        self._closed = True
        for el in list(self.floats.values()):
            self.add([], self.blocks(el))
        self.floats.clear()
        return self.sections

    def walk(self, container: ET.Element, path: list[str]) -> None:
        """Content directly inside `container` belongs to `path`; each child <sec> is its own section."""
        for child in container:
            if child.tag == "sec":
                title = text(child.find("title"))
                if title.casefold() in self.skip_sections or child.get("sec-type") == FLOATS_SEC_TYPE:
                    continue
                self._closed = True
                self.walk(child, path + [title] if title else path)
                self._closed = True
            elif child.tag in BLOCK:
                self.add(path, self.blocks(child))
                for xref in child.iter("xref"):
                    cited = self.floats.pop(xref.get("rid") or "", None)
                    if cited is not None:
                        self.add(path, self.blocks(cited))

    # ---- blocks ---------------------------------------------------------------------------------
    def blocks(self, el: ET.Element) -> list[Block]:
        tag = el.tag
        if tag == "p":
            return self.paragraph(el)
        if tag == "list":
            return [Paragraph("\n".join(list_lines(el)))]
        if tag == "table-wrap":
            return tables(el)
        if tag == "fig":
            return [self.figure(el)]
        if tag in ("boxed-text", "disp-quote"):
            inner: list[Block] = []
            for child in el.iter():
                if child is not el and child.tag in BLOCK and not _inside_block(child, el):
                    inner.extend(self.blocks(child))
            title = text(el.find("caption/title")) or text(el.find("title"))
            if title and inner:
                inner[0] = _prefix(inner[0], title)
            return inner
        return []

    def paragraph(self, p: ET.Element) -> list[Block]:
        """A <p>; table-wrap, fig and list inside it become separate blocks right after the text before them."""
        out: list[Block] = []
        buf = [p.text or ""]
        for child in p:
            if child.tag in ("table-wrap", "fig", "list"):
                if norm("".join(buf)):
                    out.append(Paragraph(norm("".join(buf))))
                buf = []
                out.extend(self.blocks(child))
            elif child.tag not in SKIP:
                buf.append(_raw_text(child))
            buf.append(child.tail or "")
        if norm("".join(buf)):
            out.append(Paragraph(norm("".join(buf))))
        return out

    def figure(self, fig: ET.Element) -> Figure:
        graphic = fig.find(".//graphic")
        return Figure(caption_of(fig), self.image(graphic.get(XLINK_HREF)) if graphic is not None else None)

    def image(self, href: str | None) -> str | None:
        if not href:
            return None
        candidates = [href] if Path(href).suffix else [href + s for s in IMAGE_SUFFIXES]
        for name in candidates:
            if (self.base_dir / name).is_file():
                return rel(self.base_dir / name)
        return None


def _inside_block(child: ET.Element, root: ET.Element) -> bool:
    """True when `child` sits inside another block element below `root` (handled by that block)."""
    parents = {c: p for p in root.iter() for c in p}
    node = parents.get(child)
    while node is not None and node is not root:
        if node.tag in BLOCK:
            return True
        node = parents.get(node)
    return False


def _prefix(block: Block, title: str) -> Block:
    if isinstance(block, Paragraph):
        return Paragraph(f"{title}\n{block.text}")
    if isinstance(block, Table):
        return Table(f"{title}\n{block.caption}".strip(), block.header, block.rows, block.footnotes)
    return Figure(f"{title}\n{block.caption}".strip(), block.image_path)


def list_lines(lst: ET.Element, depth: int = 0) -> list[str]:
    """One line per <list-item>: '- ' (or '1. ' for list-type="order"), nested lists indented 2 spaces."""
    lines = []
    ordered = lst.get("list-type") == "order"
    for i, item in enumerate(lst.findall("list-item"), 1):
        own = norm(" ".join(_raw_text(c) if c.tag != "list" else "" for c in item if c.tag not in SKIP)
                   + " " + (item.text or ""))
        lines.append("  " * depth + (f"{i}. " if ordered else "- ") + own)
        for nested in item.iter("list"):
            if nested is not item:
                lines.extend(list_lines(nested, depth + 1))
                break
    return lines


# ---- tables -------------------------------------------------------------------------------------
def _cell_text(cell: ET.Element) -> str:
    lists = cell.findall(".//list-item")
    if lists:
        return "; ".join(norm(_raw_text(li)) for li in lists)
    return text(cell)


def _grid(rows: list[ET.Element]) -> tuple[list[list[str]], list[list[bool]]]:
    """Expand rowspan/colspan: a merged cell's text goes to every position it covers."""
    grid: dict[tuple[int, int], tuple[str, bool]] = {}
    for r, tr in enumerate(rows):
        c = 0
        for cell in tr:
            if cell.tag not in ("td", "th"):
                continue
            while (r, c) in grid:
                c += 1
            rs, cs = int(cell.get("rowspan") or 1), int(cell.get("colspan") or 1)
            value = (_cell_text(cell), cell.tag == "th")
            for dr in range(rs):
                for dc in range(cs):
                    grid[(r + dr, c + dc)] = value
            c += cs
    n_rows = len(rows)
    n_cols = max((c for _, c in grid), default=-1) + 1
    texts = [[grid.get((r, c), ("", False))[0] for c in range(n_cols)] for r in range(n_rows)]
    is_th = [[grid.get((r, c), ("", False))[1] for c in range(n_cols)] for r in range(n_rows)]
    return texts, is_th


def _header(levels: list[list[str]], n_cols: int) -> list[str]:
    """Multi-level header: join top-down with ' / ', skipping empty cells and repeats of the level above."""
    out = []
    for c in range(n_cols):
        parts: list[str] = []
        for level in levels:
            v = level[c] if c < len(level) else ""
            if v and (not parts or parts[-1] != v):
                parts.append(v)
        out.append(" / ".join(parts))
    return out


def table_block(table: ET.Element, caption: str, footnotes: str) -> Table:
    head_rows = table.findall("thead/tr")
    body_rows = [tr for tr in table.iter("tr") if tr not in head_rows]
    texts, is_th = _grid(head_rows + body_rows)
    n_cols = max((len(r) for r in texts), default=0)
    if head_rows:
        header = _header(texts[:len(head_rows)], n_cols)
        rows = texts[len(head_rows):]
    elif texts and all(is_th[0]):
        header, rows = texts[0], texts[1:]
    else:
        header, rows = [], texts
    header = [h or f"Column {i}" for i, h in enumerate(header, 1)] if header else \
        [f"Column {i}" for i in range(1, n_cols + 1)]
    return Table(caption, header, rows, footnotes)


def tables(wrap: ET.Element) -> list[Block]:
    caption = caption_of(wrap)
    foot = text(wrap.find("table-wrap-foot"))
    return [table_block(t, caption, foot) for t in wrap.iter("table")]


# ---- documents ----------------------------------------------------------------------------------
def abstract_section(abstract: ET.Element | None) -> Section | None:
    """["Abstract"]: each inner <sec> is one paragraph starting with '<sec title>: '."""
    if abstract is None:
        return None
    blocks: list[Block] = []
    for child in abstract:
        if child.tag == "sec":
            body = norm(" ".join(text(p) for p in child if p.tag in ("p", "list")))
            title = text(child.find("title"))
            if body:
                blocks.append(Paragraph(f"{title}: {body}" if title else body))
        elif child.tag == "p" and text(child):
            blocks.append(Paragraph(text(child)))
    return Section(["Abstract"], blocks) if blocks else None


def read_sections(meta: ET.Element, body: ET.Element | None, base_dir: Path, skip_sections: list[str],
                  floats_group: ET.Element | None = None) -> list[Section]:
    """Abstract (main one: no abstract-type) then the body's sections in reading order."""
    abstract = next((a for a in meta.findall("abstract") if a.get("abstract-type") is None), None)
    sections = [s for s in [abstract_section(abstract)] if s]
    if body is not None:
        sections += Reader(base_dir, skip_sections).read(body, floats_group)
    return sections
