"""The work-slice table of a spec, refused wholesale on the first fault.

The slice table *is* the plan. There is no draft entity, no version chain
and no approval record beside it — the table lives in `features.body` and
the spec's revision history is the record of what was approved.

Reading the table is `lib.specs`' job (markdown-it tokens, columns in
`lib.specs.slice_rows`); this module is the wholesale face of it for the
callers that refuse a table outright — transcription, the tail, the arm
proposal. Whether `depends_on` points at a slice that exists, whether the
edges form a cycle, and what to do about a slice already running are
questions about the graph, and they belong to transcription.
"""

from __future__ import annotations

from dataclasses import dataclass

from lib.specs import markdown
from lib.specs.slice_rows import (  # noqa: F401 — this module's public names
    COLUMN_ALIASES, NO_TABLE_MESSAGE, REQUIRED_COLUMNS, SliceDep, SliceRow,
    SliceTableError, find_slice_table, read_rows)


@dataclass(frozen=True)
class TableBounds:
    """Where the slice table sits in a body: its header's cells and column
    map, and the 0-based line indexes of its header and last row."""

    header: list[str]
    columns: dict[str, int]
    header_line: int
    last_line: int


def _located(body: str):
    found = find_slice_table(markdown.tables(markdown.tokens(body)))
    if found is None:
        raise SliceTableError(NO_TABLE_MESSAGE)
    return found


def table_bounds(body: str) -> TableBounds:
    table, columns = _located(body)
    return TableBounds([cell.raw for cell in table.header], columns,
                       table.start, table.last)


def parse_slice_table(body: str) -> list[SliceRow]:
    """Parse the slice table out of a spec body, or refuse wholesale.

    Refusal names the row and the column, because "some row is missing
    something" is not actionable against a twelve-row table.
    """
    rows, errors = read_rows(*_located(body))
    if errors:
        raise errors[0]
    return rows
