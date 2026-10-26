"""The first pass: a spec becomes a slice table, or slicing is refused.

Slicing is the act that turns a spec into the graph. It is mechanical on
purpose — the table *is* the plan, so a machine that guessed a column, or
sliced a spec that never said what "done" means, would be authoring the
plan rather than transcribing one.

Two refusals live here, and both are refusals of the *spec*, not of the
table's syntax (that belongs to `slice_table`):

A spec whose `lib.specs.read_spec` reports any problem cannot be sliced:
no Gherkin acceptance, a scenario with no test name, a malformed table, or
a table that does not give every acceptance name to exactly one build
slice. The refusal lists every problem with its line, because "this spec
is not ready" is not actionable against a thousand-line document.

A schema change belongs to exactly one slice. Two slices both editing
`db/schema.sql` and both adding an alembic revision is the migration
conflict the operator discovers at integrate time, after both have run —
so it is refused while it is still a table, when moving it costs an edit.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from lib.activity_log import get_activity_logger
from lib.specs.problems import Problem
from lib.specs.reader import read_spec
from lib.tasks.slice_table import SliceRow, table_bounds

log = get_activity_logger("tasks")

#: Text that claims a slice changes the database's shape. Slicing reads the
#: `delivers` cell for these because that cell is where a slice says what it
#: owns; a slice that edits the schema without saying so in its deliverable
#: has a deeper problem than this check.
#:
#: Every marker names the database explicitly. Bare "migration" and "迁移"
#: are deliberately absent: they are what a slice that moves code between
#: modules, or documents an upgrade, also says, and a refusal an operator
#: can only satisfy by rewording prose is a refusal that gets worked around.
SCHEMA_MARKERS = (
    "alembic",
    "db/schema.sql",
    "schema.sql",
    "schema change",
    "schema migration",
    "db migration",
    "database migration",
    "create table",
    "alter table",
    "add a column",
    "adds a column",
    "new column",
    "schema 变更",
    "schema 迁移",
    "数据库迁移",
    "建表",
    "加表",
    "加列",
    "新增列",
)

#: Disclaimers removed before the markers are matched. A slice that says
#: "no schema change" says the opposite of what a substring scan reads.
SCHEMA_DISCLAIMERS = (
    "no schema change",
    "without a schema change",
    "no db migration",
    "no database migration",
    "无 schema 变更",
    "没有 schema 变更",
    "不改 schema",
)


class SlicingRefusal(ValueError):
    """The spec cannot be sliced as written, and says what is missing.

    `missing` is the list a human acts on — every reason, not the first
    one found, so one round of edits can fix the spec.
    """

    def __init__(self, message: str, missing: Optional[Sequence[str]] = None,
                 rows: Optional[Sequence[str]] = None,
                 problems: Optional[Sequence[Problem]] = None) -> None:
        super().__init__(message)
        self.missing: list[str] = list(missing or ())
        self.rows: list[str] = list(rows or ())
        self.problems: list[Problem] = list(problems or ())

    @property
    def reason(self) -> str:
        return "problems" if self.problems else "schema"


def _problems_refusal(problems: Sequence[Problem]) -> SlicingRefusal:
    lines = [problem.render() for problem in problems]
    return SlicingRefusal(
        f"this spec cannot be sliced — {len(lines)} problem(s):\n  "
        + "\n  ".join(lines), missing=lines, problems=problems)


def acceptance_test_names(body: str) -> list[str]:
    """Every acceptance test the spec names, in order, deduplicated."""
    return list(dict.fromkeys(read_spec(body).acceptance_names))


def _claims_schema(row: SliceRow) -> bool:
    if row.owns_schema_change:
        return True
    text = row.delivers.lower()
    for disclaimer in SCHEMA_DISCLAIMERS:
        text = text.replace(disclaimer, " ")
    return any(marker in text for marker in SCHEMA_MARKERS)


def slices_owning_schema(rows: Iterable[SliceRow]) -> list[str]:
    """The slice ids that own a schema change — declared, or said in `delivers`."""
    return [row.n for row in rows if _claims_schema(row)]


def _schema_refusal(owners: list[str]) -> SlicingRefusal:
    return SlicingRefusal(
        "a schema change belongs to one slice, but "
        + ", ".join(owners)
        + " all claim one — give the migration to a single slice and let the "
          "others depend on it",
        missing=["two slices own one schema change"],
        rows=owners)


def check_schema_ownership(rows: Sequence[SliceRow]) -> Optional[str]:
    """Refuse a table in which two slices own one schema change.

    Returns the owning slice id, or None when no slice changes the schema.
    """
    owners = slices_owning_schema(rows)
    if len(owners) <= 1:
        return owners[0] if owners else None
    log.write("slicing_refused", reason="schema", slices=",".join(owners))
    raise _schema_refusal(owners)


def check_slicing(spec_body: str) -> list[SliceRow]:
    """The whole first-pass gate: a spec whose `read_spec` problems are none,
    and whose table gives a schema change to at most one slice.

    Every problem at once, with its line, so one round of edits can fix the
    spec — the same list `preview` and the slices report carry, because all
    three read it from `read_spec`.
    """
    rows, refusal = preview(spec_body)
    if refusal is not None:
        log.write("slicing_refused", reason=refusal.reason,
                  problems=len(refusal.problems))
        raise refusal
    return rows


def preview(spec_body: str) -> tuple[list[SliceRow], Optional[SlicingRefusal]]:
    """`check_slicing`'s verdict as a value, without the refusal log.

    For readers that ask on every page render — the feature index's
    `next_step` — where a `slicing_refused` activity row per read would
    record a page view as if an operator had tried to slice.
    """
    doc = read_spec(spec_body)
    rows = doc.slices or []
    if doc.problems:
        return rows, _problems_refusal(doc.problems)
    owners = slices_owning_schema(rows)
    if len(owners) > 1:
        return rows, _schema_refusal(owners)
    return rows, None


_HEADERS = ("#", "Slice", "Delivers", "Spec refs", "Tests", "Schema",
            "Depends on", "Base", "Skill")


def _cell(value: str, row_id: str, column: str) -> str:
    """A cell, or a refusal — a `|` inside one cannot be written back.

    An escaped `\\|` is not a round trip: the parser splits on a bare `|`
    and never unescapes, so the rendered row gains a column and comes back
    refused for the wrong reason. Refuse it here, where the cell is still
    addressable by slice and column.
    """
    if "|" in value:
        raise SlicingRefusal(
            f"slice {row_id}'s {column!r} contains a '|', which a markdown "
            "table cell cannot carry — reword it or move the detail into the "
            "slice's spec refs",
            missing=[f"{row_id}.{column} contains '|'"], rows=[row_id])
    return value or "—"


def _row_values(row: SliceRow) -> dict[str, str]:
    """A row's cells keyed by column id — the one place a row becomes text."""
    return {
        "n": row.n,
        "title": row.title,
        "delivers": row.delivers,
        "spec_refs": ", ".join(row.spec_refs),
        "tests": ", ".join(row.tests),
        "schema": "yes" if row.owns_schema_change else "",
        "depends_on": ", ".join(row.depends_on),
        "base": row.base,
        "skill": row.skill,
    }


_COLUMN_ORDER = ("n", "title", "delivers", "spec_refs", "tests", "schema",
                 "depends_on", "base", "skill")


def render_slice_table(rows: Sequence[SliceRow], base: str) -> str:
    """The table as it goes into `features.body`, with its Base heading.

    Two places carry the base and both are load-bearing: the heading states
    the branch the feature's own work sits on (invariant 3 — a slice's base
    is never the repo default unless the table said so), and the column
    states it per slice, because the chain-tail slices branch from the
    integration branch rather than from where the feature started.
    """
    lines = [f"**Base: `{base}`**", "",
             "| " + " | ".join(_HEADERS) + " |",
             "|" + "---|" * len(_HEADERS)]
    for row in rows:
        values = _row_values(row)
        lines.append("| " + " | ".join(
            _cell(values[column], row.n, column)
            for column in _COLUMN_ORDER) + " |")
    return "\n".join(lines)


def append_rows(body: str, rows: Sequence[SliceRow]) -> str:
    """`body` with `rows` added under the last row of its slice table.

    Written in the table's own column order, so a spec whose author ordered
    or named the columns differently gets rows its parser reads back the same
    way. A column the table does not have is dropped — its reader falls back
    to that column's default, exactly as for the rows already there.
    """
    bounds = table_bounds(body)
    by_index = {index: column for column, index in bounds.columns.items()}
    rendered = []
    for row in rows:
        values = _row_values(row)
        cells = [_cell(values.get(by_index.get(i, ""), ""), row.n,
                       by_index.get(i, header))
                 for i, header in enumerate(bounds.header)]
        rendered.append("| " + " | ".join(cells) + " |")
    lines = body.splitlines()
    lines[bounds.last_line + 1:bounds.last_line + 1] = rendered
    return "\n".join(lines) + ("\n" if body.endswith("\n") else "")
