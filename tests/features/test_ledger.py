"""What `feature stage` says, and the ledger that sits beside it.

Two commands, not one. `feature stage` is what the pipeline skill runs *first*
and what `regin gate pipeline-ran` counts, so it stays a glance; the ledger is
the report.

They used to have genuinely different failure modes — `stage` could not answer
at all without the tracker, so it exited 2, while the ledger stayed useful with
the tracker down and printed `unknown` for the one field it could not fetch.
Both of those are gone: the stage is the feature's last `feature_events` row
plus a count of escapes, so there is no unreachable reading left, and `unknown`
has nothing to describe.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.app import app
from lib.defects import store
from lib.features import events, store as feature_store

runner = CliRunner()


def _feature(title="a feature", *, stage=None):
    row = feature_store.create(title)
    if stage is not None:
        events.append(row.id, stage, declared_by=events.OPERATOR)
    return row


def _declare(feature_id, trace, attributing_trace):
    attributing_trace(trace)
    return runner.invoke(app, ["feature", "attribute", str(feature_id),
                               "--session", trace])


# ── `feature stage` ───────────────────────────────────────────

def test_feature_stage_prints_two_lines_and_gains_only_escapes():
    feature = _feature(stage=events.BUILD_STARTED)

    result = runner.invoke(app, ["feature", "stage", str(feature.id)])

    lines = [ln for ln in result.output.strip().splitlines() if ln.strip()]
    assert len(lines) == 2, result.output
    assert "escapes=" in lines[0]


def test_a_shipped_feature_with_open_escapes_reads_escaped():
    """"shipped, and bugs have been blamed on it since" is the live reading,
    and it is the one-word answer to *how is this going*."""
    feature = _feature(stage=events.SHIPPED)
    store.record("it leaks", feature_id=feature.id)

    assert events.stage(feature.id) == events.STAGE_ESCAPED


def test_a_shipped_feature_with_no_escapes_reads_shipped():
    """A defect blamed while the feature was still building is not an escape,
    and shipping afterwards does not make it one — the pointer names the event
    blame was assigned against, and that event cannot change."""
    feature = _feature(stage=events.BUILD_STARTED)
    store.record("caught before ship", feature_id=feature.id)
    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)

    assert events.stage(feature.id) == events.STAGE_SHIPPED


def test_reworking_is_not_in_the_stage_vocabulary():
    """Deleted, not re-read: keeping the name would lie, because nothing is
    being reworked and the board still says Done."""
    assert "reworking" not in set(events.STAGES)
    assert not hasattr(events, "REWORKING")
    assert set(events.STAGES) == {"spec", "building", "shipped", "escaped",
                                 "abandoned"}


def test_the_feature_stage_span_carries_the_id_it_reports_on(posted_spans):
    """The gate counts span *names*, so the attribute rename cannot move it —
    but the attributes are this command's output contract, and a reader of the
    trace needs the id they can actually open."""
    feature = _feature(stage=events.BUILD_STARTED)

    runner.invoke(app, ["feature", "stage", str(feature.id),
                        "--session", "t-span"])

    attributes = [p["attributes"] for p in posted_spans
                  if p["name"] == "feature.stage"][0]
    assert {"stage", "blurb", "rounds", "open_defects"} <= set(attributes)
    assert attributes["feature_id"] == feature.id
    assert "feature_key" not in attributes


def test_feature_stage_exits_two_only_on_an_id_that_names_nothing(no_tracker):
    """It used to exit 2 when no tracker answered, because the tracker *was*
    the answer. The only unanswerable question left is a feature that does not
    exist."""
    assert runner.invoke(app, ["feature", "stage", "9999"]).exit_code == 2
    feature = _feature()
    assert runner.invoke(app, ["feature", "stage",
                               str(feature.id)]).exit_code == 0


# ── `feature ledger` ──────────────────────────────────────────

def test_feature_ledger_leads_with_the_stage_line(session_row,
                                                  attributing_trace):
    """The stage frames every number under it — `$31.80` means something
    different on a `spec` than on a `shipped`."""
    feature = _feature(stage=events.SHIPPED)
    session_row("t1", cost=31.80)
    _declare(feature.id, "t1", attributing_trace)

    output = runner.invoke(app, ["feature", "ledger", str(feature.id)]).output
    lines = [ln for ln in output.splitlines() if ln.strip()]

    assert "shipped" in lines[1]
    assert "31.80" not in lines[1]


def test_feature_ledger_lists_sessions_by_date_short_trace_and_title(
        session_row, attributing_trace):
    """`regin stats sessions`' convention verbatim, so an id carries by eye
    between the two surfaces. The list is the *evidence* for an upper-bound
    figure — a bare count gives a reader no way to check it."""
    feature = _feature()
    session_row("cc72f3a2-0198-4263-9866-ca3b251ce452", cost=2.48,
                title="Build the feature ledger", started="2026-09-13 10:00:00")
    _declare(feature.id, "cc72f3a2-0198-4263-9866-ca3b251ce452",
             attributing_trace)

    output = runner.invoke(app, ["feature", "ledger", str(feature.id)]).output

    assert "2026-09-13" in output
    assert "cc72f3a2" in output
    assert "cc72f3a2-0198" not in output
    assert "Build the feature ledger" in output


def test_a_recalled_session_row_shows_a_dash_for_its_round(
        session_row, attributing_trace):
    feature = _feature()
    session_row("t-recalled", cost=1.00, title="somebody else's day")
    attributing_trace("t-me")
    runner.invoke(app, ["feature", "attribute", str(feature.id),
                        "--session", "t-recalled"])

    row = [ln for ln in runner.invoke(
        app, ["feature", "ledger", str(feature.id)]).output.splitlines()
        if "t-recall" in ln][0]

    assert "recalled" in row
    assert "—" in row


def test_the_ledger_reports_the_features_own_title_not_a_tasks(native_task):
    """Two titles, two questions. `features.title` is the authority for what
    is being delivered; a task's says what one step does."""
    feature = _feature("what the feature delivers")
    feature_store.adopt_task(feature.id, native_task(title="one step"))

    output = runner.invoke(app, ["feature", "ledger", str(feature.id)]).output

    assert "what the feature delivers" in output
    assert "one step" not in output


def test_feature_ledger_leaves_no_span(session_row, posted_spans,
                                       attributing_trace):
    """A second span named `feature.stage` would let an idle report satisfy
    the gate. One fingerprint, one command."""
    feature = _feature()
    session_row("t1", cost=5.00)
    _declare(feature.id, "t1", attributing_trace)
    posted_spans.clear()

    runner.invoke(app, ["feature", "ledger", str(feature.id),
                        "--session", "t1"])

    assert posted_spans == []


def test_feature_ledger_json_groups_each_gap_under_its_figure(
        session_row, attributing_trace):
    """Flattened they read as noise: a reader cannot tell which number
    `no_usage_captured` qualifies.

    `defects` carries no `gaps` any more, and that is the point: its two —
    `state_unknown` and `unknown_vocabulary` — described the readings a cached
    state *string* could have, and a pointer at an append-only row has none.
    """
    feature = _feature()
    session_row("t-nousage", cost=None)
    session_row("t-zero", cost=1.00, active_work_ms=0)
    _declare(feature.id, "t-nousage", attributing_trace)
    _declare(feature.id, "t-zero", attributing_trace)

    payload = json.loads(runner.invoke(
        app, ["feature", "ledger", str(feature.id), "--json"]).output)

    assert payload["cost"]["gaps"]["no_usage_captured"] == 1
    assert payload["work_time"]["gaps"]["zero_duration"] == 1
    assert "traceless_runs" in payload["sessions"]["gaps"]
    assert "gaps" not in payload["defects"]
    assert "gaps" not in payload
