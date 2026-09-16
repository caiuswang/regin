"""What "the sessions of a feature" means, and the act that declares one.

Every row here is written because somebody said so. Nothing in this file
derives an attribution from a cwd, a branch name or a timestamp, because the
whole point of the two statistics downstream is that they measure work rather
than measuring the guess.

The ambiguity test that used to sit here is gone with its subject: a task held
its feature through a link, a parent or its own key, so it could hold two and
every reader had to refuse it. One foreign key makes the second unwritable.
"""

from __future__ import annotations

from typer.testing import CliRunner

from cli.app import app
from lib.features import attribution, store as feature_store

runner = CliRunner()


def _attribute(feature_id, trace, *extra):
    return runner.invoke(app, ["feature", "attribute", str(feature_id),
                               "--session", trace, *extra])


# ── What the list contains ────────────────────────────────────

def test_a_run_and_the_conversation_it_drove_count_as_one_session(
        session_row, run_alias, task_lane, feature_row):
    """Two trace ids, one piece of work. Counting both inflates the session
    count and simultaneously loses the cost, which sits on the conversation."""
    feature = feature_row()
    session_row("sdk-run-1", cost=None)
    session_row("conv-1", cost=2.48)
    run_alias("sdk-run-1", "conv-1")
    task_lane(1, feature.id, [("sdk-run-1", "done")])

    rows = attribution.sessions_for_feature(feature.id)

    assert [r.trace_id for r in rows] == ["conv-1"]


def test_every_run_that_produced_a_trace_counts_whatever_its_status(
        session_row, task_lane, feature_row):
    """The tokens were spent either way — excluding failures makes an
    expensive feature look cheap."""
    feature = feature_row()
    for trace in ("t-done", "t-failed", "t-running"):
        session_row(trace, cost=1.0)
    task_lane(1, feature.id, [("t-done", "done"), ("t-failed", "failed"),
                              ("t-running", "running")])

    rows = attribution.sessions_for_feature(feature.id)

    assert sorted(r.trace_id for r in rows) == ["t-done", "t-failed",
                                                "t-running"]


def test_a_traceless_run_is_counted_separately_rather_than_dropped(
        session_row, task_lane, feature_row):
    """A feature that burned three failed launches must not read untouched."""
    feature = feature_row()
    session_row("t-ok", cost=1.0)
    task_lane(1, feature.id,
              [("t-ok", "done"), (None, "failed"), (None, "failed")])

    rows = attribution.sessions_for_feature(feature.id)

    assert [r.trace_id for r in rows] == ["t-ok"]
    assert attribution.traceless_runs(feature.id) == 2


def test_a_task_belongs_to_one_feature_structurally(task_lane, feature_row,
                                                    session_row):
    """The refusal this replaced was a reader defending against a shape three
    writers could produce. Re-homing is an UPDATE, so the second is unwritable
    and the first feature simply stops counting the session."""
    first, second = feature_row("A"), feature_row("B")
    session_row("t-moved", cost=1.0)
    task_lane(1, first.id, [("t-moved", "done")])

    feature_store.adopt_task(second.id, 1)

    assert attribution.sessions_for_feature(first.id) == []
    assert [r.trace_id for r in
            attribution.sessions_for_feature(second.id)] == ["t-moved"]


# ── The declaring act ─────────────────────────────────────────

def test_attribute_writes_a_session_features_row_and_leaves_a_span(
        session_row, posted_spans, attributing_trace, feature_row):
    feature = feature_row()
    attributing_trace("t-self")
    session_row("t-self", cost=3.0)

    result = _attribute(feature.id, "t-self")

    assert result.exit_code == 0, result.output
    rows = attribution.features_for_session("t-self")
    assert [r.feature_id for r in rows] == [feature.id]
    assert "feature.attributed" in [p["name"] for p in posted_spans]


def test_attribute_works_long_after_the_session_ended(session_row,
                                                      attributing_trace,
                                                      feature_row):
    """Nothing about the act is time-bounded — the late claim is the normal
    case, so a closed session must still be attributable."""
    feature = feature_row()
    attributing_trace(None)
    session_row("t-ancient", cost=9.0, started="2026-04-29 00:00:00")

    assert _attribute(feature.id, "t-ancient").exit_code == 0
    assert attribution.features_for_session("t-ancient")


def test_a_session_may_declare_two_features(session_row, attributing_trace,
                                            feature_row):
    """Forbidding it pushes people to declare the single *wrong* feature."""
    first, second = feature_row("A"), feature_row("B")
    attributing_trace("t-two")
    session_row("t-two", cost=5.0)

    _attribute(first.id, "t-two")
    _attribute(second.id, "t-two")

    assert sorted(r.feature_id for r in
                  attribution.features_for_session("t-two")) == sorted(
                      [first.id, second.id])


def test_is_primary_is_first_writer_wins_until_primary_overrides(
        session_row, attributing_trace, feature_row):
    """A later unrelated declaration must not silently move money between two
    features' totals; an explicit `--primary` may."""
    first, second = feature_row("A"), feature_row("B")
    attributing_trace("t-p")
    session_row("t-p", cost=5.0)

    _attribute(first.id, "t-p")
    _attribute(second.id, "t-p")
    primary = {r.feature_id: r.is_primary
               for r in attribution.features_for_session("t-p")}
    assert primary == {first.id: 1, second.id: 0}

    _attribute(second.id, "t-p", "--primary")

    primary = {r.feature_id: r.is_primary
               for r in attribution.features_for_session("t-p")}
    assert primary == {first.id: 0, second.id: 1}


def test_an_unattributed_session_is_the_absence_of_a_row(session_row,
                                                         feature_row):
    """Not a column on `sessions`, not a sentinel value — no row at all, the
    way a session with no repo has no `session_repos` row."""
    feature = feature_row()
    session_row("t-silent", cost=4.0)

    assert attribution.features_for_session("t-silent") == []
    assert attribution.sessions_for_feature(feature.id) == []


def test_feature_stage_is_not_a_declaration(session_row, posted_spans,
                                            feature_row):
    """"I consulted the pipeline for X" is not "I built X" — the skill tells
    you to run `stage` before touching code, so it fires in sessions that then
    go and do something else. Reading it as attribution is inference-by-proxy."""
    feature = feature_row()
    session_row("t-glance", cost=4.0)

    result = runner.invoke(app, ["feature", "stage", str(feature.id),
                                 "--session", "t-glance"])

    assert result.exit_code == 0, result.output
    assert "feature.stage" in [p["name"] for p in posted_spans]
    assert attribution.features_for_session("t-glance") == []


def test_the_round_is_snapshotted_on_a_declared_row(session_row,
                                                    attributing_trace,
                                                    feature_row):
    """The round the feature stood at when the claim was made. A later restart
    must not retroactively move it — the claim describes an instant.

    Always answerable now, unlike the tracker fetch it replaced: it is a count
    of local events, so there is no NULL on a `declared` row meaning "we could
    not look".
    """
    from lib.features import events

    feature = feature_row(stage=events.SHIPPED)
    attributing_trace("t-round")
    session_row("t-round", cost=1.0)

    _attribute(feature.id, "t-round")
    events.append(feature.id, events.RESTARTED, declared_by=events.OPERATOR)

    assert attribution.features_for_session("t-round")[0].round == 1
    assert events.rounds(feature.id) == 2
