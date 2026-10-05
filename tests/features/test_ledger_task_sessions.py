"""A session row names the task it serves, so the feature page can draw each
task's sessions and stop showing N runs of one skill as N identical titles."""

from __future__ import annotations

from typer.testing import CliRunner

from cli.app import app
from lib.features import attribution, ledger

runner = CliRunner()


def _rows(feature_id):
    return {r["short_trace"]: r
            for r in ledger.build(feature_id).to_dict()["sessions"]["rows"]}


def test_bare_command_matches_only_a_title_that_is_one_command():
    assert ledger.bare_command("/goal-verified-treenav") == "/goal-verified-treenav"
    assert ledger.bare_command("  /feature-pipeline ") == "/feature-pipeline"
    assert ledger.bare_command("/plugin:skill") == "/plugin:skill"
    assert ledger.bare_command("/goal-verified-treenav build it") is None
    assert ledger.bare_command("Fix the /api/sessions select") is None
    assert ledger.bare_command("/") is None
    assert ledger.bare_command("") is None
    assert ledger.bare_command(None) is None


def test_a_skill_line_session_reads_as_its_tasks_title(
        session_row, task_lane, feature_row):
    feature = feature_row()
    session_row("aaaaaaaa-run", cost=1.0, title="/goal-verified-treenav")
    task_lane(7, feature.id, [("aaaaaaaa-run", "done")])

    row = _rows(feature.id)["aaaaaaaa"]

    assert row["task_id"] == 7
    assert row["task_title"] == "task 7"
    assert row["command"] == "/goal-verified-treenav"
    assert row["display_title"] == "task 7"
    assert row["title"] == "/goal-verified-treenav"


def test_a_session_with_its_own_words_keeps_them(
        session_row, task_lane, feature_row):
    feature = feature_row()
    session_row("bbbbbbbb-run", cost=1.0, title="Rename untitled sessions")
    task_lane(8, feature.id, [("bbbbbbbb-run", "done")])

    row = _rows(feature.id)["bbbbbbbb"]

    assert row["command"] is None
    assert row["display_title"] == "Rename untitled sessions"


def test_a_skill_line_with_no_task_keeps_the_command_as_its_title(
        session_row, feature_row, attributing_trace):
    feature = feature_row()
    session_row("cccccccc-decl", cost=1.0, title="/feature-pipeline")
    attributing_trace("cccccccc-decl")
    runner.invoke(app, ["feature", "attribute", str(feature.id),
                        "--session", "cccccccc-decl"])

    row = _rows(feature.id)["cccccccc"]

    assert row["task_id"] is None
    assert row["display_title"] == "/feature-pipeline"


def test_a_declared_session_a_run_also_produced_carries_the_task(
        session_row, task_lane, feature_row, attributing_trace):
    """The declaration keeps its provenance; the task still gets to see it."""
    feature = feature_row()
    session_row("dddddddd-both", cost=1.0, title="/goal-verified")
    attributing_trace("dddddddd-both")
    runner.invoke(app, ["feature", "attribute", str(feature.id),
                        "--session", "dddddddd-both"])
    task_lane(9, feature.id, [("dddddddd-both", "done")])

    [row] = attribution.sessions_for_feature(feature.id)

    assert row.provenance == "declared"
    assert row.task_id == 9


def test_the_cli_ledger_prints_the_display_title(
        session_row, task_lane, feature_row):
    feature = feature_row()
    session_row("eeeeeeee-run", cost=1.0, title="/goal-verified-treenav")
    task_lane(10, feature.id, [("eeeeeeee-run", "done")])

    output = runner.invoke(app, ["feature", "ledger", str(feature.id)]).output

    assert "eeeeeeee  task 10" in output
