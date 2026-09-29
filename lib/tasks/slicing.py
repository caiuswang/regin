"""The first pass: a spec becomes a slice table, or slicing is refused.

Slicing is the act that turns a spec into the graph. It is mechanical on
purpose — the table *is* the plan, so a machine that guessed a column, or
sliced a spec that never said what "done" means, would be authoring the
plan rather than transcribing one.

Two refusals live here, and both are refusals of the *spec*, not of the
table's syntax (that belongs to `slice_table`):

A spec with no falsifiable acceptance item cannot be sliced at all. A
slice's `tests` column holds the *names* of acceptance tests, so a spec
that names none has nothing to put there — slicing it would produce rows
whose verification is a sentence of prose, which is the thing the whole
pipeline exists to stop. The refusal names what is missing, because "this
spec is not ready" is not actionable against a thousand-line document.

A schema change belongs to exactly one slice. Two slices both editing
`db/schema.sql` and both adding an alembic revision is the migration
conflict the operator discovers at integrate time, after both have run —
so it is refused while it is still a table, when moving it costs an edit.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Sequence

from lib.activity_log import get_activity_logger
from lib.tasks.slice_table import (
    SliceRow, SliceTableError, parse_slice_table, table_bounds)

log = get_activity_logger("tasks")

#: A heading that opens the spec's falsifiable section. Matched as whole
#: words, so "## Acceptance" and "### 验收" count and "## Unacceptable
#: latency" does not — a substring match let any heading containing the
#: letters hijack the scan.
ACCEPTANCE_HEADING_RE = re.compile(
    r"(?:^|[^0-9a-z])(?:acceptance|验收)(?:[^0-9a-z]|$)", re.IGNORECASE)

#: A bare `test_…` token anywhere in the acceptance section names a test.
TEST_NAME_RE = re.compile(r"\btest_[0-9a-z_]+\b")

#: A Gherkin scenario's title is a test name too, once transcribed to
#: `test_<snake_case>`: spec-notation lets Acceptance be Gherkin, and a
#: titled scenario is as falsifiable as a `test_` token. Only the title is
#: read — a paragraph of prose still names nothing a runner can be handed.
SCENARIO_RE = re.compile(
    r"^\s*(?:Scenario(?: Outline)?|Example):\s*(.+?)\s*$")
_SLUG_WORD_RE = re.compile(r"[0-9a-z]+")

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
                 rows: Optional[Sequence[str]] = None) -> None:
        super().__init__(message)
        self.missing: list[str] = list(missing or ())
        self.rows: list[str] = list(rows or ())


_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _heading_levels(body: str) -> list[int]:
    """Per line, its markdown heading level — 0 for anything that is not one.

    Fenced code is not markdown, and a spec's acceptance section is mostly
    fenced Gherkin whose comments start with `#`. Reading `# R1` inside a
    ```gherkin block as an h1 ends the section at its first line, which is
    how a spec full of named scenarios reads as naming none.
    """
    levels: list[int] = []
    fenced = False
    for line in body.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
            levels.append(0)
            continue
        if fenced:
            levels.append(0)
            continue
        stripped = line.lstrip()
        hashes = len(stripped) - len(stripped.lstrip("#"))
        levels.append(hashes if 0 < hashes <= 6 else 0)
    return levels


def _acceptance_sections(body: str) -> list[str]:
    lines = body.splitlines()
    levels = _heading_levels(body)
    sections: list[str] = []
    for index, line in enumerate(lines):
        level = levels[index]
        if not level or not ACCEPTANCE_HEADING_RE.search(line.lstrip("#")):
            continue
        collected: list[str] = []
        for offset, following in enumerate(lines[index + 1:], start=index + 1):
            if 0 < levels[offset] <= level:
                break
            collected.append(following)
        sections.append("\n".join(collected))
    return sections


def _scenario_test_names(title: str) -> list[str]:
    """The test names a scenario title yields — its `test_` tokens, else its slug."""
    explicit = TEST_NAME_RE.findall(title)
    if explicit:
        return explicit
    words = _SLUG_WORD_RE.findall(title.lower())
    return ["test_" + "_".join(words)] if words else []


def _section_test_names(section: str) -> list[str]:
    seen: dict[str, None] = {}
    for line in section.splitlines():
        scenario = SCENARIO_RE.match(line)
        names = (_scenario_test_names(scenario.group(1)) if scenario
                 else TEST_NAME_RE.findall(line))
        for name in names:
            seen.setdefault(name, None)
    return list(seen)


def acceptance_section(body: str) -> Optional[str]:
    """The spec's acceptance section verbatim, or None if it has none.

    A spec may carry more than one — a superseded draft kept above the real
    one, an "Acceptance of risk" aside. The section that *names tests* wins,
    because taking the first match refused a sliceable spec on the strength
    of a heading somebody left behind.
    """
    sections = _acceptance_sections(body)
    if not sections:
        return None
    for names_tests in (TEST_NAME_RE.search, _section_test_names):
        for section in sections:
            if names_tests(section):
                return section
    return sections[0]


def acceptance_test_names(body: str) -> list[str]:
    """Every acceptance test the spec names, in order, deduplicated."""
    section = acceptance_section(body)
    if section is None:
        return []
    return _section_test_names(section)


def _acceptance_missing(body: str) -> tuple[list[str], list[str]]:
    """(test names, what is missing) — exactly one of the two is non-empty."""
    if not (body or "").strip():
        return [], ["the spec is empty"]
    if acceptance_section(body) is None:
        return [], [
            "no Acceptance section — a spec is sliceable when it says, in a "
            "section named Acceptance, what would falsify it"]
    names = acceptance_test_names(body)
    if names:
        return names, []
    return [], [
        "the Acceptance section names no test — a slice's Tests column "
        "holds test names, so give it test_… names or titled Gherkin "
        "`Scenario:` lines, not a paragraph of prose"]


def _acceptance_refusal(missing: list[str]) -> SlicingRefusal:
    return SlicingRefusal(
        "this spec cannot be sliced: " + "; ".join(missing), missing=missing)


def check_spec_is_sliceable(body: str) -> list[str]:
    """Refuse a spec that names no falsifiable acceptance item.

    Returns the test names when the spec is sliceable, so the caller that
    checked does not have to scan the document twice.
    """
    names, missing = _acceptance_missing(body)
    if names:
        return names
    log.write("slicing_refused", reason="acceptance", missing=len(missing))
    raise _acceptance_refusal(missing)


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
    """The whole first-pass gate: a sliceable spec with a sound table.

    Wholesale, in the order a human can act on: a spec with no acceptance
    item is refused before its table is read, because rewriting the table
    of a spec that cannot be sliced is wasted work.
    """
    check_spec_is_sliceable(spec_body)
    rows = parse_slice_table(spec_body)
    check_schema_ownership(rows)
    return rows


def preview(spec_body: str) -> tuple[list[SliceRow], Optional[SlicingRefusal]]:
    """`check_slicing`'s verdict as a value, without the refusal log.

    For readers that ask on every page render — the feature index's
    `next_step` — where a `slicing_refused` activity row per read would
    record a page view as if an operator had tried to slice.
    """
    _, missing = _acceptance_missing(spec_body)
    if missing:
        return [], _acceptance_refusal(missing)
    try:
        rows = parse_slice_table(spec_body)
    except SliceTableError as exc:
        return [], SlicingRefusal(str(exc), missing=[str(exc)])
    owners = slices_owning_schema(rows)
    if len(owners) > 1:
        return rows, _schema_refusal(owners)
    return rows, None


_HEADERS = ("#", "Slice", "Delivers", "Spec refs", "Tests", "Schema",
            "Depends on", "Base", "Skill", "Verify")


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
        "verify_cmd": row.verify_cmd or "",
    }


_COLUMN_ORDER = ("n", "title", "delivers", "spec_refs", "tests", "schema",
                 "depends_on", "base", "skill", "verify_cmd")


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
