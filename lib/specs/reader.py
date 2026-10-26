"""`read_spec`: the one function every slicing gate asks.

slice-check, preview, the transcribe entries and the feature's slices
report all read a spec through `read_spec`, so for one body they report
one list of problems (Invariant 9) — a gate that read the spec its own
way is a gate an agent could satisfy by picking the lenient entry.

Coverage is a relation between the two halves of the spec: every
acceptance name belongs to exactly one build slice, and a build slice's
Tests hold acceptance names and nothing else. The tail rows (integrate /
migrate / verify) are recognised by their fixed names and may re-list
acceptance names, because re-checking them is their job.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from lib.specs import markdown
from lib.specs.gherkin_blocks import Scenario, read_fence
from lib.specs.names import derive_test_name
from lib.specs.problems import Problem
from lib.specs.slice_rows import (
    NO_TABLE_MESSAGE, SliceRow, SliceTableError, find_slice_table, read_rows)
from lib.tasks.tail import TAIL_TESTS


@dataclass
class SpecDoc:
    scenarios: list[Scenario] = field(default_factory=list)
    slices: Optional[list[SliceRow]] = None
    problems: list[Problem] = field(default_factory=list)
    #: The first table fault, for callers that refuse wholesale.
    table_error: Optional[SliceTableError] = None

    @property
    def acceptance_names(self) -> list[str]:
        return [s.test_name for s in self.scenarios if s.test_name]

    def coverage(self) -> tuple[int, int]:
        """`(covered, total)` acceptance names, counting build slices only."""
        names = set(self.acceptance_names)
        owned = {test for row in build_rows(self.slices or ()) for test in row.tests}
        return len(names & owned), len(names)


def is_tail_row(row: SliceRow) -> bool:
    return bool(TAIL_TESTS.intersection(row.tests))


def build_rows(rows) -> list[SliceRow]:
    return [row for row in rows if not is_tail_row(row)]


def _scenarios(stream) -> tuple[list[Scenario], list[Problem]]:
    fences = [f for f in markdown.acceptance_fences(stream) if f.language == "gherkin"]
    if not fences:
        return [], [Problem(
            "no_acceptance", None, "Acceptance",
            "no ```gherkin block under an Acceptance / 验收 heading — a spec "
            "is sliceable when it says, in Gherkin, what would falsify it")]
    scenarios: list[Scenario] = []
    problems: list[Problem] = []
    for fence in fences:
        raw, found = read_fence(fence)
        problems.extend(found)
        for item in raw:
            name, why = derive_test_name(item.title)
            scenarios.append(Scenario(item.title, name, item.line, item.keyword))
            if name is None:
                problems.append(Problem("no_test_name", item.line, item.title, why))
    return scenarios, problems + _duplicates(scenarios)


def _duplicates(scenarios: list[Scenario]) -> list[Problem]:
    counts = Counter(s.test_name for s in scenarios if s.test_name)
    problems: list[Problem] = []
    for name, count in counts.items():
        if count < 2:
            continue
        lines = [s.line for s in scenarios if s.test_name == name]
        problems.append(Problem(
            "duplicate_name", lines[1], name,
            f"{count} scenarios derive the test name {name} (lines "
            + ", ".join(map(str, lines)) + ") — give each its own name"))
    return problems


def _table(stream) -> tuple[Optional[list[SliceRow]], list[SliceTableError]]:
    found = find_slice_table(markdown.tables(stream))
    if found is None:
        return None, [SliceTableError(NO_TABLE_MESSAGE)]
    rows, errors = read_rows(*found)
    return rows, errors


def _table_problems(errors: list[SliceTableError]) -> list[Problem]:
    return [Problem("table_shape", error.line, error.row or "table", str(error))
            for error in errors]


def _foreign_items(row: SliceRow, names: set[str]) -> list[Problem]:
    allowed = names | TAIL_TESTS if is_tail_row(row) else names
    return [Problem(
        "not_an_acceptance_name", row.line or None, item,
        f"{row.n}'s Tests lists {item!r}, which is not the test name of any "
        "acceptance Scenario — list acceptance names only")
        for item in row.tests if item not in allowed]


def _owners(rows: list[SliceRow]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for row in build_rows(rows):
        for test in dict.fromkeys(row.tests):
            owners.setdefault(test, []).append(row.n)
    return owners


def _ownership(scenario: Scenario, owned_by: list[str]) -> Optional[Problem]:
    name = scenario.test_name
    if not owned_by:
        return Problem(
            "uncovered", scenario.line, name,
            f"no build slice lists {name} in its Tests — give it to exactly one")
    if len(owned_by) > 1:
        return Problem(
            "double_owned", scenario.line, name,
            f"{name} is listed by {', '.join(owned_by)} — one slice owns it")
    return None


def _coverage(scenarios: list[Scenario], rows: list[SliceRow]) -> list[Problem]:
    names = {s.test_name for s in scenarios if s.test_name}
    if not names:
        return []
    problems = [p for row in rows for p in _foreign_items(row, names)]
    owners = _owners(rows)
    first = {s.test_name: s for s in reversed(scenarios) if s.test_name}
    for scenario in sorted(first.values(), key=lambda s: s.line):
        problem = _ownership(scenario, owners.get(scenario.test_name, []))
        if problem is not None:
            problems.append(problem)
    return problems


def read_spec(body: str) -> SpecDoc:
    stream = markdown.tokens(body or "")
    scenarios, problems = _scenarios(stream)
    rows, errors = _table(stream)
    problems.extend(_table_problems(errors))
    if rows is not None and not errors:
        problems.extend(_coverage(scenarios, rows))
    return SpecDoc(scenarios=scenarios, slices=rows, problems=problems,
                   table_error=errors[0] if errors else None)


def problems_summary(body: str, limit: int = 5) -> dict:
    """How many problems a spec has and the first few — for a write that
    stores the draft regardless and tells its author what slicing will say."""
    problems = read_spec(body).problems
    return {"count": len(problems),
            "first": [p.to_dict() for p in problems[:limit]]}
