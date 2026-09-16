"""The slice table's columns are a contract; the parser refuses ambiguity."""

from __future__ import annotations

import pytest

from lib.tasks.commands import DEFAULT_SKILL
from lib.tasks.slice_table import SliceTableError, parse_slice_table

TABLE = """
## Work slices

| # | Slice | Delivers | Spec refs | Tests | Depends on | Base | Skill | Verify |
|---|---|---|---|---|---|---|---|---|
| S0 | 两个契约 | the two contracts | R1, Model | test_a, test_b | — | `master` | | |
| S1 | verify runner | R4 in full | R4 | test_c | S0 | integration | goal-verified | pytest -q |
| S2 | notice | R5 in full | R5, Inv 6 | test_d | S0, S1 | integration | | |
"""


def test_the_table_parses_into_rows():
    rows = parse_slice_table(TABLE)
    assert [row.n for row in rows] == ["S0", "S1", "S2"]
    assert rows[0].title == "两个契约"
    assert rows[0].delivers == "the two contracts"
    assert rows[0].spec_refs == ["R1", "Model"]
    assert rows[0].tests == ["test_a", "test_b"]
    assert rows[0].base == "master"


def test_an_empty_depends_on_cell_is_no_edges():
    rows = parse_slice_table(TABLE)
    assert rows[0].depends_on == []
    assert rows[2].depends_on == ["S0", "S1"]


def test_skill_defaults_and_verify_cmd_is_optional():
    rows = parse_slice_table(TABLE)
    assert rows[0].skill == DEFAULT_SKILL
    assert rows[0].verify_cmd is None
    assert rows[1].skill == "goal-verified"
    assert rows[1].verify_cmd == "pytest -q"


def _table_without(column: str) -> str:
    """The same table with S1's cell for `column` blanked."""
    header = "| # | Slice | Delivers | Spec refs | Tests | Depends on | Base |"
    divider = "|---|---|---|---|---|---|---|"
    cells = {"n": "S1", "title": "t", "delivers": "d", "spec_refs": "R4",
             "tests": "test_c", "depends_on": "—", "base": "integration"}
    good = "| S0 | a | b | R1 | test_a | — | master |"
    cells[column] = ""
    bad = "| " + " | ".join(
        cells[k] for k in ("n", "title", "delivers", "spec_refs", "tests",
                           "depends_on", "base")) + " |"
    return "\n".join([header, divider, good, bad])


@pytest.mark.parametrize("column", ["delivers", "spec_refs", "tests", "base"])
def test_a_row_missing_a_required_column_is_refused_wholesale(column):
    with pytest.raises(SliceTableError) as caught:
        parse_slice_table(_table_without(column))
    assert caught.value.row == "S1"
    assert caught.value.column == column
    assert "S1" in str(caught.value) and column in str(caught.value)


def test_a_row_missing_base_names_the_row_and_the_column():
    with pytest.raises(SliceTableError) as caught:
        parse_slice_table(_table_without("base"))
    assert (caught.value.row, caught.value.column) == ("S1", "base")


def test_a_non_slice_id_is_refused():
    body = TABLE.replace("| S1 |", "| one |")
    with pytest.raises(SliceTableError) as caught:
        parse_slice_table(body)
    assert caught.value.column == "n"


def test_a_repeated_slice_id_is_refused():
    body = TABLE.replace("| S2 |", "| S1 |")
    with pytest.raises(SliceTableError) as caught:
        parse_slice_table(body)
    assert caught.value.row == "S1"


def test_a_table_without_the_required_headers_is_not_a_slice_table():
    with pytest.raises(SliceTableError):
        parse_slice_table("| # | Slice | Base |\n|---|---|---|\n| S0 | a | m |")


def test_headers_are_matched_case_and_separator_insensitively():
    body = TABLE.replace(
        "| # | Slice | Delivers | Spec refs | Tests | Depends on | Base | Skill | Verify |",
        "| ID | TITLE | delivers | spec_refs | TESTS | depends_on | base | skill | verify_cmd |")
    assert [row.n for row in parse_slice_table(body)] == ["S0", "S1", "S2"]


def test_prose_before_the_table_is_skipped():
    rows = parse_slice_table("Some intro text.\n\n" + TABLE)
    assert len(rows) == 3


def test_a_required_text_cell_may_hold_a_word_that_spells_emptiness():
    body = TABLE.replace("| S0 | 两个契约 |", "| S0 | none |")
    assert parse_slice_table(body)[0].title == "none"


def test_a_dash_in_a_required_cell_is_still_missing():
    body = TABLE.replace(
        "| S0 | 两个契约 | the two contracts |", "| S0 | t | — |")
    with pytest.raises(SliceTableError) as caught:
        parse_slice_table(body)
    assert (caught.value.row, caught.value.column) == ("S0", "delivers")
