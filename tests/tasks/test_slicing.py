"""The first pass refuses a spec it cannot slice, and says what is missing."""

from __future__ import annotations

import dataclasses

import pytest

from lib.specs.reader import read_spec
from lib.tasks.slice_table import parse_slice_table
from lib.tasks.slicing import (
    SlicingRefusal, acceptance_test_names, check_schema_ownership,
    check_slicing, render_slice_table, slices_owning_schema)

HEADER = "| # | Slice | Delivers | Spec refs | Tests | Depends on | Base |"
DIVIDER = "|---|---|---|---|---|---|---|"


def _table(*rows: str) -> str:
    return "\n".join(["**Base: `master`**", "", HEADER, DIVIDER, *rows])


def _codes(body: str) -> list[str]:
    return [problem.code for problem in read_spec(body).problems]


ACCEPTANCE = """
## Acceptance

```gherkin
Scenario: test_a
  Given a table with only two keys
  Then  it refuses

Scenario: test_b
  Then  no file is created

Scenario: test_c
  Then  it runs
```
"""

SPEC = ACCEPTANCE + "\n## Work slices\n\n" + _table(
    "| S0 | contracts | the two contracts | R1 | test_a, test_b | — | `master` |",
    "| S1 | runner | R4 in full | R4 | test_c | S0 | integration |",
)


def test_a_sliceable_spec_yields_its_rows():
    rows = check_slicing(SPEC)
    assert [row.n for row in rows] == ["S0", "S1"]


def test_acceptance_test_names_are_read_in_order():
    assert acceptance_test_names(SPEC) == ["test_a", "test_b", "test_c"]


def test_slicing_refuses_a_spec_without_named_tests():
    """A spec whose acceptance is prose has nothing to put in Tests."""
    prose = """
## Acceptance

The feature is done when the operator is happy with it and test_a reads
well. It should feel fast.

## Work slices
""" + _table("| S0 | contracts | the two contracts | R1 | test_a | — | m |")
    with pytest.raises(SlicingRefusal) as caught:
        check_slicing(prose)
    assert [p.code for p in caught.value.problems] == ["no_acceptance"]
    assert "gherkin" in str(caught.value)


GHERKIN_ACCEPTANCE = """
## Acceptance

```gherkin
Feature: production Python complexity

  Scenario: no production function at radon grade C or worse
    When I run radon
    Then it prints no block lines

  Scenario Outline: 1.1 the list is readable
    Then no file exceeds <n> lines

    Examples:
      | n    |
      | 1000 |
```

## Build vehicle
"""


def test_an_ascii_scenario_title_slugs_to_its_test_name():
    assert acceptance_test_names(GHERKIN_ACCEPTANCE) == [
        "test_no_production_function_at_radon_grade_c_or_worse",
        "test_1_1_the_list_is_readable",
    ]


def test_an_explicit_test_name_in_a_scenario_title_wins_over_its_slug():
    body = ("## Acceptance\n\n```gherkin\nScenario: test_reader_refuses — "
            "the reader refuses a missing key\n  Then it refuses\n```\n")
    assert acceptance_test_names(body) == ["test_reader_refuses"]


def test_two_explicit_names_in_one_title_name_no_test():
    body = ("## Acceptance\n\n```gherkin\nScenario: test_one or test_two\n"
            "  Then it refuses\n```\n")
    assert "no_test_name" in _codes(body)


def test_slicing_refuses_a_spec_with_no_acceptance_section_at_all():
    assert _codes("## Intent\n\nmake it nice.\n")[0] == "no_acceptance"


def test_slicing_refuses_an_empty_spec():
    assert _codes("   ")[0] == "no_acceptance"


def test_a_gherkin_block_outside_the_acceptance_section_does_not_count():
    """Only the falsifiable section counts — otherwise any mention passes."""
    body = ("## Intent\n\n```gherkin\nScenario: test_something\n  Then x\n```"
            "\n\n## Acceptance\n\nsoon.\n")
    assert _codes(body)[0] == "no_acceptance"


def test_slicing_refuses_two_slices_owning_one_schema_change():
    """One migration, one owner — caught while it is still an edit."""
    body = ACCEPTANCE + "\n" + _table(
        "| S0 | store | a new alembic revision for the runs table | R1 | "
        "test_a | — | master |",
        "| S1 | reader | reads it | R2 | test_b | S0 | integration |",
        "| S2 | index | another alembic revision for the same table | R3 | "
        "test_c | S0 | integration |",
    )
    with pytest.raises(SlicingRefusal) as caught:
        check_slicing(body)
    assert caught.value.rows == ["S0", "S2"]
    assert "S0" in str(caught.value) and "S2" in str(caught.value)
    assert "one slice" in str(caught.value)


def test_one_slice_owning_the_schema_change_is_accepted():
    body = ACCEPTANCE + "\n" + _table(
        "| S0 | store | a new alembic revision for the runs table | R1 | "
        "test_a | — | master |",
        "| S1 | reader | reads it | R2 | test_b, test_c | S0 | integration |",
    )
    rows = check_slicing(body)
    assert check_schema_ownership(rows) == "S0"
    assert slices_owning_schema(rows) == ["S0"]


def test_a_feature_with_no_schema_change_has_no_owner():
    assert check_schema_ownership(parse_slice_table(SPEC)) is None


def test_the_rendered_table_carries_the_base_heading_and_column():
    rows = parse_slice_table(SPEC)
    rendered = render_slice_table(rows, "f76/integration")
    assert rendered.splitlines()[0] == "**Base: `f76/integration`**"
    assert "| Base |" in rendered.splitlines()[2]
    assert "| integration |" in rendered


def test_the_rendered_table_round_trips_through_the_parser():
    rows = parse_slice_table(SPEC)
    again = parse_slice_table(render_slice_table(rows, "f76/integration"))
    assert again == rows


def test_a_gherkin_comment_does_not_end_the_acceptance_section():
    """`# R1` inside a fence is a Gherkin comment, not a markdown heading.

    Reading it as an h1 truncated the section at its first line, so a spec
    full of named scenarios was refused for naming none.
    """
    body = """
## Acceptance

```gherkin
# R1
Scenario: test_first_rule
  Then it holds

# R2
Scenario: test_second_rule
  Then it holds too
```

## Work slices
""" + _table("| S0 | a | b | R1 | test_first_rule, test_second_rule | — | master |")
    assert acceptance_test_names(body) == ["test_first_rule",
                                           "test_second_rule"]
    assert [row.n for row in check_slicing(body)] == ["S0"]


def test_a_superseded_acceptance_section_does_not_hijack_the_scan():
    """The section that names tests wins over an earlier heading."""
    body = """
## Acceptance (draft, superseded)

we will know it when we see it.

## Acceptance

```gherkin
Scenario: test_the_real_one
  Then it holds
```

## Work slices
""" + _table("| S0 | a | b | R1 | test_the_real_one | — | master |")
    assert acceptance_test_names(body) == ["test_the_real_one"]


def test_a_heading_merely_containing_the_word_is_not_the_section():
    body = "# Unacceptable latency\n\n```gherkin\nScenario: test_bogus\n  Then x\n```\n"
    assert _codes(body)[0] == "no_acceptance"


def test_a_slice_disclaiming_a_schema_change_is_not_an_owner():
    rows = parse_slice_table(ACCEPTANCE + "\n" + _table(
        "| S0 | reader | a reader; no schema change here | R1 | test_a | — | m |",
        "| S1 | doc | documents the migration guide, 迁移到新模块 | R2 | "
        "test_b | S0 | m |",
    ))
    assert slices_owning_schema(rows) == []


def test_an_english_column_add_is_an_owner():
    rows = parse_slice_table(ACCEPTANCE + "\n" + _table(
        "| S0 | store | adds a column to the runs table | R1 | test_a | — | m |",
    ))
    assert slices_owning_schema(rows) == ["S0"]


def test_rendering_refuses_a_cell_that_contains_a_pipe():
    """An escaped pipe is not a round trip — the parser never unescapes it."""
    rows = list(parse_slice_table(SPEC))
    broken = dataclasses.replace(rows[0], delivers="run `a | b` now")
    with pytest.raises(SlicingRefusal) as caught:
        render_slice_table([broken], "master")
    assert caught.value.rows == ["S0"]
    assert "delivers" in str(caught.value)


def test_the_rendered_table_carries_the_schema_owner():
    """A declared owner must survive the round trip, or R2 reads a table in
    which nobody owns the migration."""
    body = ACCEPTANCE + "\n" + "\n".join([
        "**Base: `master`**", "",
        "| # | Slice | Delivers | Spec refs | Tests | Schema | Depends on | Base |",
        "|---|---|---|---|---|---|---|---|",
        "| S0 | store | holds the runs table | R1 | test_a | yes | — | master |",
        "| S1 | reader | reads it | R2 | test_b, test_c | — | S0 | integration |",
    ])
    rows = check_slicing(body)
    assert slices_owning_schema(rows) == ["S0"]
    again = parse_slice_table(render_slice_table(rows, "master"))
    assert [row.owns_schema_change for row in again] == [True, False]
    assert slices_owning_schema(again) == ["S0"]
