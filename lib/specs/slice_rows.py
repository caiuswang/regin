"""The slice table's columns, and its rows read out of a parsed GFM table.

The slice table *is* the plan, so its columns are a contract: a reader
that guessed which column meant "the tests" would make the plan mean
different things to different sessions. The table is the first one whose
header names every required column; its cells come from markdown-it, so a
pipe inside inline code or a `\\|` is a character, not a column break.

Rows are validated per column and every fault is collected, so a caller
that lists problems lists all of them; `lib.tasks.slice_table` raises the
first one for the callers that refuse wholesale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from lib.orm.models.tasks import DEP_KIND_REQUIRES, DEP_KINDS
from lib.specs.markdown import Cell, Row, Table
from lib.tasks.commands import DEFAULT_SKILL

# Column id → the header spellings that address it. Headers are matched
# after case-folding and collapsing every other run of characters to one
# space, so "Spec refs", "spec_refs" and "Spec Refs" are the same column.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "n": ("#", "n", "slice id", "id"),
    "title": ("slice", "title", "name"),
    "delivers": ("delivers", "deliverable"),
    "spec_refs": ("spec refs", "spec ref", "spec 依据", "spec basis"),
    "tests": ("tests", "test", "验收测试名"),
    "depends_on": ("depends on", "depends", "deps", "depends upon"),
    "base": ("base", "base branch", "branch base"),
    "skill": ("skill", "arm skill"),
    # Which slice owns the install-wide schema move. Declared rather than
    # inferred: a transcriber that sniffed the word "migrate" out of a
    # `delivers` cell would refuse the very slice that delivers the command
    # table, whose three mandated keys include `migrate`.
    "schema": ("schema", "schema change", "schema 变更", "migration"),
}

REQUIRED_COLUMNS = ("n", "title", "delivers", "spec_refs", "tests", "base")

# A cell that deliberately holds nothing. An em dash is what the spec's own
# tables use; a bare hyphen is what a keyboard produces for the same intent.
_NO_VALUE = {"", "—", "–", "-"}
# Only a *list* cell may spell its emptiness as a word. A required text cell
# may not: a slice legitimately titled "none" would otherwise read as missing.
_EMPTY_CELL = _NO_VALUE | {"n/a", "none", "无"}

LIST_SEPARATORS = ",，;；"
#: The Tests column also splits on the enumeration comma a Chinese spec uses.
TEST_SEPARATORS = LIST_SEPARATORS + "、"

# A claim cell is a tick, not a sentence. Only an affirmative counts: read the
# other way round, an author writing "no schema change" in the cell would be
# made an owner by their own denial.
_AFFIRMATIONS = {"yes", "y", "true", "1", "x", "✓", "是", "有"}


class SliceTableError(ValueError):
    """The table cannot be read as a plan. Wholesale — no partial rows."""

    def __init__(self, message: str, row: Optional[str] = None,
                 column: Optional[str] = None,
                 line: Optional[int] = None) -> None:
        super().__init__(message)
        self.row = row
        self.column = column
        #: 1-based spec line, when the fault sits on one.
        self.line = line


class SliceDep(str):
    """A slice id in a `depends_on` cell, carrying what the edge means.

    A `str` subclass so every reader that treats the cell as a list of slice
    ids — the cycle walk, the unknown-id check, the tail's leaf set — keeps
    working on the id and nothing else; only the transcriber that writes the
    edge reads `.kind`. The two can never drift apart into a list and a
    lookup table that disagree, because they are one object.
    """

    kind: str

    def __new__(cls, n: str, kind: str = DEP_KIND_REQUIRES) -> "SliceDep":
        dep = super().__new__(cls, n)
        dep.kind = kind
        return dep


@dataclass(frozen=True)
class SliceRow:
    n: str
    title: str
    delivers: str
    spec_refs: list[str]
    tests: list[str]
    base: str
    depends_on: list[SliceDep] = field(default_factory=list)
    skill: str = DEFAULT_SKILL
    owns_schema_change: bool = False
    #: 1-based spec line of the row; 0 for a row built in code.
    line: int = field(default=0, compare=False)


def is_slice_id(value: str) -> bool:
    digits = value[1:]
    return (value.startswith("S") and digits.isascii() and digits.isdigit())


def normalize_header(cell: str) -> str:
    out: list[str] = []
    for char in cell.strip().lower():
        keep = (char.isascii() and char.isalnum()) or char == "#" \
            or "一" <= char <= "鿿"
        if keep:
            out.append(char)
        elif out and out[-1] != " ":
            out.append(" ")
    folded = "".join(out).strip()
    return folded or cell.strip().lower()


_HEADER_LOOKUP = {
    alias: column for column, aliases in COLUMN_ALIASES.items() for alias in aliases
}


def header_columns(header: tuple[Cell, ...]) -> Optional[dict[str, int]]:
    """Column id → index, or None when a required column is absent."""
    columns: dict[str, int] = {}
    for index, cell in enumerate(header):
        column = _HEADER_LOOKUP.get(normalize_header(cell.raw))
        if column is not None and column not in columns:
            columns[column] = index
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    return None if missing else columns


def find_slice_table(tables) -> Optional[tuple[Table, dict[str, int]]]:
    for table in tables:
        columns = header_columns(table.header)
        if columns is not None:
            return table, columns
    return None


def _clean(text: str) -> str:
    return text.strip().strip("`").strip()


def _has_no_value(text: str) -> bool:
    return _clean(text).lower() in _NO_VALUE


def _as_list(cell: Cell, separators: str) -> list[str]:
    if _clean(cell.raw).lower() in _EMPTY_CELL:
        return []
    parts = (_clean(part) for part in cell.items(separators))
    return [part for part in parts if part and part.lower() not in _EMPTY_CELL]


def _split_dep(part: str) -> tuple[str, Optional[str]]:
    """`S3 (after)` → `("S3", "after")`; anything else is a bare id."""
    if not part.endswith(")") or "(" not in part:
        return part, None
    head, _, rest = part.partition("(")
    n, kind = head.strip(), rest[:-1].strip()
    if not n or len(n.split()) != 1 or not (kind.isascii() and kind.isalpha()
                                             and kind.islower()):
        return part, None
    return n, kind


class _RowReader:
    """One table row's cells by column id, refusing per column."""

    def __init__(self, row: Row, columns: dict[str, int]) -> None:
        self.row = row
        self.columns = columns
        self.line = row.line + 1
        self.id = ""

    def cell(self, column: str) -> Cell:
        index = self.columns.get(column)
        if index is None or index >= len(self.row.cells):
            return Cell("")
        return self.row.cells[index]

    def refuse(self, message: str, column: str) -> SliceTableError:
        return SliceTableError(message, self.id or None, column, self.line)

    def text(self, column: str) -> str:
        value = _clean(self.cell(column).raw)
        if _has_no_value(value):
            raise self.refuse(f"row {self.id} is missing {column!r}", column)
        return value

    def items(self, column: str, separators: str = LIST_SEPARATORS) -> list[str]:
        values = _as_list(self.cell(column), separators)
        if not values:
            raise self.refuse(f"row {self.id} is missing {column!r}", column)
        return values

    def optional(self, column: str) -> Optional[str]:
        value = _clean(self.cell(column).raw)
        return None if _has_no_value(value) else value

    def deps(self) -> list[SliceDep]:
        out: list[SliceDep] = []
        for part in _as_list(self.cell("depends_on"), LIST_SEPARATORS):
            n, kind = _split_dep(part)
            if kind is None:
                out.append(SliceDep(n))
                continue
            if kind not in DEP_KINDS:
                raise self.refuse(
                    f"row {self.id} names dependency kind {kind!r} — expected "
                    f"one of {list(DEP_KINDS)}", "depends_on")
            out.append(SliceDep(n, kind))
        return out


def _parse_row(reader: _RowReader, ordinal: int) -> SliceRow:
    row_id = _clean(reader.cell("n").raw)
    if _has_no_value(row_id):
        raise reader.refuse(f"row {ordinal} is missing 'n'", "n")
    reader.id = row_id
    if not is_slice_id(row_id):
        raise reader.refuse(
            f"row {row_id!r} is not a slice id — expected S<number>", "n")
    return SliceRow(
        n=row_id,
        title=reader.text("title"),
        delivers=reader.text("delivers"),
        spec_refs=reader.items("spec_refs"),
        tests=reader.items("tests", TEST_SEPARATORS),
        base=reader.text("base"),
        depends_on=reader.deps(),
        skill=reader.optional("skill") or DEFAULT_SKILL,
        owns_schema_change=_clean(reader.cell("schema").raw).lower() in _AFFIRMATIONS,
        line=reader.line,
    )


def read_rows(table: Table, columns: dict[str, int]
              ) -> tuple[list[SliceRow], list[SliceTableError]]:
    """Every well-formed row, and every fault — a row with one is skipped."""
    rows: list[SliceRow] = []
    errors: list[SliceTableError] = []
    seen: set[str] = set()
    for ordinal, raw in enumerate(table.rows, start=1):
        if not any(_clean(cell.raw) for cell in raw.cells):
            continue
        try:
            row = _parse_row(_RowReader(raw, columns), ordinal)
        except SliceTableError as exc:
            errors.append(exc)
            continue
        if row.n in seen:
            errors.append(SliceTableError(
                f"slice {row.n} appears twice — a slice id is unique per feature",
                row.n, "n", row.line))
            continue
        seen.add(row.n)
        rows.append(row)
    if not rows and not errors:
        errors.append(SliceTableError("the slice table has no rows",
                                      line=table.start + 1))
    return rows, errors


NO_TABLE_MESSAGE = ("no slice table found — a table needs a header row naming "
                    + ", ".join(REQUIRED_COLUMNS))
