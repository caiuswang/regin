"""Acceptance items for CAI-231 and CAI-235 — what "the sessions of a feature"
means, and the act that declares one.

Every row here is written because somebody said so. Nothing in this file
derives an attribution from a cwd, a branch name or a timestamp, because the
whole point of the two statistics downstream is that they measure work rather
than measuring the guess.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cli.app import app
from lib.features import attribution

runner = CliRunner()
KEY = "CAI-242"


def _attribute(key, trace, *extra):
    return runner.invoke(app, ["feature", "attribute", key,
                               "--session", trace, *extra])


# ── What the list contains ────────────────────────────────────

def test_a_run_and_the_conversation_it_drove_count_as_one_session(
        session_row, run_alias, task_lane):
    """Two trace ids, one piece of work. Counting both inflates the session
    count and simultaneously loses the cost, which sits on the conversation."""
    session_row("sdk-run-1", cost=None)
    session_row("conv-1", cost=2.48)
    run_alias("sdk-run-1", "conv-1")
    task_lane(1, [KEY], [("sdk-run-1", "done")])

    rows = attribution.sessions_for_feature(KEY)

    assert [r.trace_id for r in rows] == ["conv-1"]


def test_every_run_that_produced_a_trace_counts_whatever_its_status(
        session_row, task_lane):
    """The tokens were spent either way — excluding failures makes an
    expensive feature look cheap."""
    for trace in ("t-done", "t-failed", "t-running"):
        session_row(trace, cost=1.0)
    task_lane(1, [KEY], [("t-done", "done"), ("t-failed", "failed"),
                         ("t-running", "running")])

    rows = attribution.sessions_for_feature(KEY)

    assert sorted(r.trace_id for r in rows) == ["t-done", "t-failed",
                                                "t-running"]


def test_a_traceless_run_is_counted_separately_rather_than_dropped(
        session_row, task_lane):
    """A feature that burned three failed launches must not read untouched."""
    session_row("t-ok", cost=1.0)
    task_lane(1, [KEY], [("t-ok", "done"), (None, "failed"), (None, "failed")])

    rows = attribution.sessions_for_feature(KEY)

    assert [r.trace_id for r in rows] == ["t-ok"]
    assert attribution.traceless_runs(KEY) == 2


def test_a_task_linked_to_two_issues_is_refused(session_row, task_lane):
    """The posture `registry.resolve_ref` takes on an ambiguous paste, for the
    same reason: first-wins would silently charge one session to one feature."""
    session_row("t-amb", cost=1.0)
    task_lane(1, [KEY, "CAI-999"], [("t-amb", "done")])

    with pytest.raises(attribution.AmbiguousTaskLink) as exc:
        attribution.sessions_for_feature(KEY)

    assert KEY in str(exc.value) and "CAI-999" in str(exc.value)


# ── The declaring act ─────────────────────────────────────────

def test_attribute_writes_a_session_features_row_and_leaves_a_span(
        session_row, posted_spans, tracker_issue, attributing_trace):
    tracker_issue(KEY)
    attributing_trace("t-self")
    session_row("t-self", cost=3.0)

    result = _attribute(KEY, "t-self")

    assert result.exit_code == 0, result.output
    rows = attribution.features_for_session("t-self")
    assert [r.feature_key for r in rows] == [KEY]
    assert "feature.attributed" in [p["name"] for p in posted_spans]


def test_attribute_works_long_after_the_session_ended(
        session_row, tracker_issue, attributing_trace):
    """Nothing about the act is time-bounded — CAI-239 makes the late claim
    the normal case, so a closed session must still be attributable."""
    tracker_issue(KEY)
    attributing_trace(None)
    session_row("t-ancient", cost=9.0, started="2026-04-29 00:00:00")

    assert _attribute(KEY, "t-ancient").exit_code == 0
    assert attribution.features_for_session("t-ancient")


def test_a_session_may_declare_two_features(session_row, tracker_issue,
                                            attributing_trace):
    """Forbidding it pushes people to declare the single *wrong* feature."""
    tracker_issue(KEY)
    tracker_issue("CAI-230")
    attributing_trace("t-two")
    session_row("t-two", cost=5.0)

    _attribute(KEY, "t-two")
    _attribute("CAI-230", "t-two")

    assert sorted(r.feature_key for r in
                  attribution.features_for_session("t-two")) == ["CAI-230", KEY]


def test_is_primary_is_first_writer_wins_until_primary_overrides(
        session_row, tracker_issue, attributing_trace):
    """A later unrelated declaration must not silently move money between two
    features' totals; an explicit `--primary` may."""
    tracker_issue(KEY)
    tracker_issue("CAI-230")
    attributing_trace("t-p")
    session_row("t-p", cost=5.0)

    _attribute(KEY, "t-p")
    _attribute("CAI-230", "t-p")
    primary = {r.feature_key: r.is_primary
               for r in attribution.features_for_session("t-p")}
    assert primary == {KEY: 1, "CAI-230": 0}

    _attribute("CAI-230", "t-p", "--primary")

    primary = {r.feature_key: r.is_primary
               for r in attribution.features_for_session("t-p")}
    assert primary == {KEY: 0, "CAI-230": 1}


def test_an_unattributed_session_is_the_absence_of_a_row(session_row):
    """Not a column on `sessions`, not a sentinel value — no row at all, the
    way a session with no repo has no `session_repos` row."""
    session_row("t-silent", cost=4.0)

    assert attribution.features_for_session("t-silent") == []
    assert attribution.sessions_for_feature(KEY) == []


def test_feature_stage_is_not_a_declaration(session_row, posted_spans,
                                            tracker_issue):
    """"I consulted the pipeline for X" is not "I built X" — the skill tells
    you to run `stage` before touching code, so it fires in sessions that then
    go and do something else. Reading it as attribution is inference-by-proxy."""
    tracker_issue(KEY, state_category="started")
    session_row("t-glance", cost=4.0)

    result = runner.invoke(app, ["feature", "stage", KEY,
                                 "--session", "t-glance"])

    assert result.exit_code == 0, result.output
    assert "feature.stage" in [p["name"] for p in posted_spans]
    assert attribution.features_for_session("t-glance") == []


def test_the_round_is_snapshotted_on_a_declared_row(session_row, tracker_issue,
                                                    attributing_trace):
    """The round the tracker reported at the moment the claim was made —
    cached the way `agent_task_links.snapshot_json` caches its issue, refreshed
    on an event and never on a clock."""
    tracker_issue(KEY, rounds=3)
    attributing_trace("t-round")
    session_row("t-round", cost=1.0)

    _attribute(KEY, "t-round")
    tracker_issue(KEY, rounds=9)

    assert attribution.features_for_session("t-round")[0].round == 3
