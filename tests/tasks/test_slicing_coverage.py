"""Coverage: every acceptance name belongs to exactly one build slice, and a
build slice's Tests hold acceptance names only (Rules §7)."""

from __future__ import annotations

from typer.testing import CliRunner

from cli.app import app
from lib.features import store as feature_store
from lib.specs.reader import read_spec
from tests.spec_fixtures import gherkin, row, spec

runner = CliRunner()


def _problems(body: str) -> list[tuple[str, str]]:
    return [(p.code, p.subject) for p in read_spec(body).problems]


def _slice_check(tmp_db, body: str):
    fid = feature_store.create("sliced", body=body).id
    return runner.invoke(app, ["task", "slice-check", "--feature", str(fid)])


def test_untitled_gherkin_block_is_refused(tmp_db):
    body = spec("## Acceptance\n\nIntro.\n\n```gherkin\nGiven x\nWhen y\nThen z\n```\n",
                row("S1", "test_a"))
    result = _slice_check(tmp_db, body)
    assert result.exit_code != 0
    untitled = [p for p in read_spec(body).problems if p.code == "untitled_block"]
    assert len(untitled) == 1
    assert untitled[0].line == 5
    assert "L5 untitled_block" in result.output


def test_uncovered_scenario_refuses_slicing(tmp_db):
    body = spec(gherkin("test_a", "test_b"), row("S1", "test_a"))
    result = _slice_check(tmp_db, body)
    assert result.exit_code != 0
    assert _problems(body) == [("uncovered", "test_b")]
    assert "uncovered test_b" in result.output


def test_scenario_owned_by_two_slices_is_refused():
    body = spec(gherkin("test_a", "test_b"),
                row("S1", "test_a"), row("S2", "test_a, test_b"))
    assert ("double_owned", "test_a") in _problems(body)


def test_tests_entry_not_an_acceptance_name_is_refused():
    body = spec(gherkin("test_a", "test_b"),
                row("S1", "test_a, live check: B1–B8, test_b（单测版）"),
                row("S2", "test_b"))
    foreign = [s for code, s in _problems(body) if code == "not_an_acceptance_name"]
    assert foreign == ["live check: B1–B8", "test_b（单测版）"]


def test_tail_rows_may_relist_acceptance_names():
    body = spec(gherkin("test_a"), row("S1", "test_a"),
                row("S2", "test_every_slice_merged_back, test_a", "S1"))
    codes = [code for code, _ in _problems(body)]
    assert "double_owned" not in codes
    assert "not_an_acceptance_name" not in codes
    assert read_spec(body).problems == []


def test_a_fully_covered_spec_passes_slice_check(tmp_db):
    body = spec(gherkin("test_a", "test_b"), row("S1", "test_a"),
                row("S2", "test_b", "S1"))
    result = _slice_check(tmp_db, body)
    assert result.exit_code == 0, result.output
