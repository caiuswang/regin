"""The column definition of a spec's work-slice table, and its parser.

The slice table *is* the plan. There is no draft entity, no version chain
and no approval record beside it — the table lives in `features.body` and
the spec's revision history is the record of what was approved. So the
columns are a contract: a reader that guesses which column meant "the
tests" would make the plan mean different things to different sessions.

The parser is deliberately mechanical and refuses on ambiguity. It
validates *columns* — every required cell present and well-formed — and
stops there. The one thing it reads inside a cell is a dependency's kind,
because that belongs to the edge and a second column could not say whose. Whether `depends_on` points at a slice that exists, whether
the edges form a cycle, and what to do about a slice already running are
questions about the graph, not about the table, and they belong to
transcription.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from lib.orm.models.tasks import DEP_KIND_REQUIRES, DEP_KINDS
from lib.tasks.commands import DEFAULT_SKILL

SLICE_ID_RE = re.compile(r"^S\d+$")

# `S3` is the edge that was always there; `S3 (after)` is an ordering the
# author drew over slices that do not consume each other. The kind rides in
# the cell rather than in a second column because it belongs to the edge, and
# a column of kinds beside a column of ids cannot say which kind is whose.
_DEP_CELL_RE = re.compile(r"^(?P<n>\S+)\s*\(\s*(?P<kind>[a-z]+)\s*\)$")

# Column id → the header spellings that address it. Headers are matched
# after case-folding and collapsing every non-alphanumeric run to one space,
# so "Spec refs", "spec_refs" and "Spec Refs" are the same column.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "n": ("#", "n", "slice id", "id"),
    "title": ("slice", "title", "name"),
    "delivers": ("delivers", "deliverable"),
    "spec_refs": ("spec refs", "spec ref", "spec 依据", "spec basis"),
    "tests": ("tests", "test", "验收测试名"),
    "depends_on": ("depends on", "depends", "deps", "depends upon"),
    "base": ("base", "base branch", "branch base"),
    "skill": ("skill", "arm skill"),
    "verify_cmd": ("verify", "verify cmd", "verify command"),
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

_LIST_SEPARATORS = re.compile(r"[,，;；]")


class SliceTableError(ValueError):
    """The table cannot be read as a plan. Wholesale — no partial rows."""

    def __init__(self, message: str, row: Optional[str] = None,
                 column: Optional[str] = None) -> None:
        super().__init__(message)
        self.row = row
        self.column = column


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
    verify_cmd: Optional[str] = None
    owns_schema_change: bool = False


def _normalize_header(cell: str) -> str:
    folded = re.sub(r"[^0-9a-z一-鿿#]+", " ", cell.strip().lower()).strip()
    return folded or cell.strip().lower()


_HEADER_LOOKUP = {
    alias: column for column, aliases in COLUMN_ALIASES.items() for alias in aliases
}


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _is_divider(cells: list[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in cells)


def _clean(cell: str) -> str:
    return cell.strip().strip("`").strip()


def _is_empty(cell: str) -> bool:
    return _clean(cell).lower() in _EMPTY_CELL


def _has_no_value(cell: str) -> bool:
    return _clean(cell).lower() in _NO_VALUE


def _as_list(cell: str) -> list[str]:
    if _is_empty(cell):
        return []
    parts = (_clean(part) for part in _LIST_SEPARATORS.split(cell))
    return [part for part in parts if part and part.lower() not in _EMPTY_CELL]


def _as_deps(cell: str, row_id: str) -> list[SliceDep]:
    """A `depends_on` cell as edges. `S3` is `requires`, `S3 (after)` is not."""
    out: list[SliceDep] = []
    for part in _as_list(cell):
        match = _DEP_CELL_RE.match(part)
        if match is None:
            out.append(SliceDep(part))
            continue
        kind = match.group("kind")
        if kind not in DEP_KINDS:
            raise SliceTableError(
                f"row {row_id} names dependency kind {kind!r} — expected one "
                f"of {list(DEP_KINDS)}", row_id, "depends_on")
        out.append(SliceDep(match.group("n"), kind))
    return out


def _header_columns(cells: list[str]) -> Optional[dict[str, int]]:
    columns: dict[str, int] = {}
    for index, cell in enumerate(cells):
        column = _HEADER_LOOKUP.get(_normalize_header(cell))
        if column is not None and column not in columns:
            columns[column] = index
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    return None if missing else columns


def _find_table(body: str) -> tuple[dict[str, int], list[list[str]]]:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if "|" not in line:
            continue
        columns = _header_columns(_split_row(line))
        if columns is None:
            continue
        rows: list[list[str]] = []
        for following in lines[index + 1:]:
            if "|" not in following:
                break
            cells = _split_row(following)
            if _is_divider(cells):
                continue
            rows.append(cells)
        return columns, rows
    raise SliceTableError(
        "no slice table found — a table needs a header row naming "
        + ", ".join(REQUIRED_COLUMNS))


def _cell(cells: list[str], columns: dict[str, int], column: str) -> str:
    index = columns.get(column)
    if index is None or index >= len(cells):
        return ""
    return cells[index]


def _required_text(cells, columns, column: str, row_id: str) -> str:
    value = _clean(_cell(cells, columns, column))
    if _has_no_value(value):
        raise SliceTableError(
            f"row {row_id} is missing {column!r}", row_id, column)
    return value


def _required_list(cells, columns, column: str, row_id: str) -> list[str]:
    values = _as_list(_cell(cells, columns, column))
    if not values:
        raise SliceTableError(
            f"row {row_id} is missing {column!r}", row_id, column)
    return values


# A claim cell is a tick, not a sentence. Only an affirmative counts: read the
# other way round, an author writing "no schema change" in the cell would be
# made an owner by their own denial.
_AFFIRMATIONS = {"yes", "y", "true", "1", "x", "✓", "是", "有"}


def _is_claimed(cell: str) -> bool:
    return _clean(cell).lower() in _AFFIRMATIONS


def _optional_text(cells, columns, column: str) -> Optional[str]:
    value = _clean(_cell(cells, columns, column))
    return None if _has_no_value(value) else value


def _parse_row(cells: list[str], columns: dict[str, int], ordinal: int) -> SliceRow:
    row_id = _clean(_cell(cells, columns, "n"))
    if _has_no_value(row_id):
        raise SliceTableError(f"row {ordinal} is missing 'n'", str(ordinal), "n")
    if not SLICE_ID_RE.match(row_id):
        raise SliceTableError(
            f"row {row_id!r} is not a slice id — expected S<number>", row_id, "n")
    return SliceRow(
        n=row_id,
        title=_required_text(cells, columns, "title", row_id),
        delivers=_required_text(cells, columns, "delivers", row_id),
        spec_refs=_required_list(cells, columns, "spec_refs", row_id),
        tests=_required_list(cells, columns, "tests", row_id),
        base=_required_text(cells, columns, "base", row_id),
        depends_on=_as_deps(_cell(cells, columns, "depends_on"), row_id),
        skill=_optional_text(cells, columns, "skill") or DEFAULT_SKILL,
        verify_cmd=_optional_text(cells, columns, "verify_cmd"),
        owns_schema_change=_is_claimed(_cell(cells, columns, "schema")),
    )


def parse_slice_table(body: str) -> list[SliceRow]:
    """Parse the slice table out of a spec body, or refuse wholesale.

    Refusal names the row and the column, because "some row is missing
    something" is not actionable against a twelve-row table.
    """
    columns, raw_rows = _find_table(body)
    rows: list[SliceRow] = []
    seen: dict[str, int] = {}
    for ordinal, cells in enumerate(raw_rows, start=1):
        if not any(_clean(cell) for cell in cells):
            continue
        row = _parse_row(cells, columns, ordinal)
        if row.n in seen:
            raise SliceTableError(
                f"slice {row.n} appears twice — a slice id is unique per feature",
                row.n, "n")
        seen[row.n] = ordinal
        rows.append(row)
    if not rows:
        raise SliceTableError("the slice table has no rows")
    return rows
