"""What every source adapter produces after reading one document (docs/ingestion-plan.md §3).

Paths (`raw_path`, `Figure.image_path`) are strings relative to the repo root, so the same file gives
the same ParsedDocument on any machine.
"""

from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


class PermanentIngestError(Exception):
    """Retrying cannot help (no text layer, unreadable format): the job fails at once (§8)."""


@dataclass(frozen=True)
class Paragraph:
    text: str


@dataclass(frozen=True)
class Table:
    caption: str
    header: list[str]
    rows: list[list[str]]
    footnotes: str


@dataclass(frozen=True)
class Figure:
    caption: str
    image_path: str | None  # None when the image file is missing


Block = Paragraph | Table | Figure


@dataclass
class Section:
    heading_path: list[str]
    blocks: list[Block] = field(default_factory=list)


@dataclass
class ParsedDocument:
    """Metadata known from the file itself (columns of `document`, §7) plus its sections."""

    source: str
    source_ref: str
    title: str
    citation: str | None
    url: str | None
    license: str
    language: str
    doc_type: str
    raw_path: str
    sections: list[Section] = field(default_factory=list)


@dataclass(frozen=True)
class DocRef:
    source_ref: str
    raw_path: str


@dataclass(frozen=True)
class ManifestFile:
    """One downloaded file = one kb_manifest row (§2)."""

    file: str  # path relative to the release directory
    url: str | None
    license: str | None


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT))


def absolute(path: str) -> Path:
    return REPO_ROOT / path
