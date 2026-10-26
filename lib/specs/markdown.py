"""The markdown layer: sections, fences and tables out of markdown-it tokens.

CommonMark decides what a fence is (its length, what nests inside it) and
GFM decides what a table row and a cell are, so a 4-backtick fence holding
a ```gherkin example is one fence whose info is not `gherkin`, and a `\\|`
inside a cell is a pipe rather than a column break.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from markdown_it import MarkdownIt
from markdown_it.token import Token

_MD = MarkdownIt("commonmark").enable("table")

ACCEPTANCE_PREFIXES = ("acceptance", "验收")


def tokens(body: str) -> list[Token]:
    return _MD.parse(body or "")


@dataclass(frozen=True)
class Fence:
    info: str
    content: str
    #: 0-based line of the opening fence.
    start: int

    @property
    def language(self) -> str:
        words = self.info.split()
        return words[0].casefold() if words else ""


@dataclass(frozen=True)
class Cell:
    #: The cell's source text, inline markup included.
    raw: str
    #: Inline children: `(type, content)` pairs as markdown-it gives them.
    parts: tuple[tuple[str, str], ...] = ()

    def items(self, separators: str) -> list[str]:
        """The cell as a list, split on `separators` outside inline code.

        A `code_inline` span is taken verbatim, so a backticked name that
        happens to contain a separator is still one item.
        """
        out: list[str] = []
        current: list[str] = []
        for kind, content in self.parts:
            if kind == "code_inline":
                current.append(content)
                continue
            if kind in ("softbreak", "hardbreak"):
                current.append(" ")
                continue
            if kind != "text":
                continue
            for char in content:
                if char in separators:
                    out.append("".join(current))
                    current = []
                else:
                    current.append(char)
        out.append("".join(current))
        return [item.strip() for item in out if item.strip()]


@dataclass(frozen=True)
class Row:
    cells: tuple[Cell, ...]
    #: 0-based source line.
    line: int


@dataclass(frozen=True)
class Table:
    header: tuple[Cell, ...]
    rows: tuple[Row, ...]
    #: 0-based lines: the header row, and the last line the table spans.
    start: int
    last: int


@dataclass
class Section:
    title: str
    level: int
    #: 0-based line of the heading.
    line: int
    fences: list[Fence] = field(default_factory=list)

    @property
    def is_acceptance(self) -> bool:
        folded = self.title.casefold().lstrip()
        return folded.startswith(ACCEPTANCE_PREFIXES)


def _inline_after(stream: list[Token], index: int) -> Optional[Token]:
    if index + 1 < len(stream) and stream[index + 1].type == "inline":
        return stream[index + 1]
    return None


def _heading_text(stream: list[Token], index: int) -> str:
    inline = _inline_after(stream, index)
    return inline.content.strip() if inline is not None else ""


def acceptance_fences(stream: list[Token]) -> list[Fence]:
    """Every fence under a heading that opens an Acceptance section.

    A section runs to the next heading of its level or higher, so the
    `### #7 …` groups under one `## Acceptance` are all inside it. Several
    acceptance sections may exist; each fence is reported once.
    """
    open_levels: list[tuple[int, bool]] = []
    fences: list[Fence] = []
    for index, token in enumerate(stream):
        if token.type == "heading_open":
            level = int(token.tag[1:])
            while open_levels and open_levels[-1][0] >= level:
                open_levels.pop()
            section = Section(_heading_text(stream, index), level,
                              token.map[0] if token.map else 0)
            open_levels.append((level, section.is_acceptance))
            continue
        if token.type != "fence" or not any(a for _, a in open_levels):
            continue
        fences.append(Fence(token.info, token.content,
                            token.map[0] if token.map else 0))
    return fences


def has_acceptance_heading(stream: list[Token]) -> bool:
    return any(
        token.type == "heading_open"
        and Section(_heading_text(stream, index), 0, 0).is_acceptance
        for index, token in enumerate(stream))


def _cell(inline: Optional[Token]) -> Cell:
    if inline is None:
        return Cell("")
    parts = tuple((child.type, child.content) for child in inline.children or ())
    return Cell(inline.content.strip(), parts)


def _rows(stream: list[Token], start: int) -> tuple[list[Row], int]:
    """The rows of the table opened at `start`, and the index of its close."""
    rows: list[Row] = []
    cells: list[Cell] = []
    line = 0
    index = start
    while index < len(stream) and stream[index].type != "table_close":
        token = stream[index]
        if token.type == "tr_open":
            cells, line = [], token.map[0] if token.map else 0
        elif token.type in ("th_open", "td_open"):
            cells.append(_cell(_inline_after(stream, index)))
        elif token.type == "tr_close":
            rows.append(Row(tuple(cells), line))
        index += 1
    return rows, index


def tables(stream: list[Token]) -> Iterable[Table]:
    index = 0
    while index < len(stream):
        token = stream[index]
        if token.type != "table_open":
            index += 1
            continue
        rows, index = _rows(stream, index)
        if not rows:
            continue
        span = token.map or [rows[0].line, rows[-1].line + 1]
        yield Table(rows[0].cells, tuple(rows[1:]), span[0], span[1] - 1)


def _section_inlines(stream: list[Token], wanted: str) -> list[Token]:
    """The inline tokens of the first section whose heading reads `wanted`."""
    level: Optional[int] = None
    found: list[Token] = []
    for index, token in enumerate(stream):
        if token.type == "heading_open":
            this = int(token.tag[1:])
            if level is not None and this <= level:
                break
            if level is None and _heading_text(stream, index).casefold() == wanted:
                level = this
        elif level is not None and token.type == "inline" \
                and stream[index - 1].type != "heading_open":
            found.append(token)
    return found


def _inline_items(inline: Token) -> list[str]:
    code = [c.content for c in inline.children or () if c.type == "code_inline"]
    return code or [inline.content]


def section_items(body: str, heading: str) -> list[str]:
    """The list items (or inline-code spans) under the heading named `heading`.

    For a task body's `## 验收测试名` section: each bullet's text, with a
    backticked name taken as the item.
    """
    inlines = _section_inlines(tokens(body), heading.casefold().strip())
    items = [item.strip() for inline in inlines for item in _inline_items(inline)]
    return [item for item in items if item]
