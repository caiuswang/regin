"""Acceptance items for CAI-236 — what `feature stage` says, and the ledger
that sits beside it.

Two commands, not one. `feature stage` is what the pipeline skill runs *first*
and what `regin gate pipeline-ran` counts, so it stays a glance; the ledger is
the report, and the two have genuinely different failure modes — `stage` cannot
answer at all without the tracker, the ledger is mostly local and stays useful
with the tracker down.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from cli.app import app
from lib.defects import stage as stage_module
from lib.defects import store
from lib.features import ledger

runner = CliRunner()
KEY = "CAI-242"


def _declare(key, trace, tracker_issue, attributing_trace, **kw):
    tracker_issue(key, **kw)
    attributing_trace(trace)
    return runner.invoke(app, ["feature", "attribute", key,
                               "--session", trace])


def _blame(symptom, key, state):
    """Open a defect already carrying the state it was blamed against."""
    row = store.record(symptom, feature_key=key)
    from sqlmodel import text
    from lib.orm import SessionLocal
    with SessionLocal() as s:
        s.exec(text("UPDATE defects SET blamed_against_state = :st "
                    "WHERE id = :i").bindparams(st=state, i=row.id))
        s.commit()
    return row


# ── `feature stage` ───────────────────────────────────────────

def test_feature_stage_prints_two_lines_and_gains_only_escapes(tracker_issue):
    tracker_issue(KEY, state_category="started")

    result = runner.invoke(app, ["feature", "stage", KEY])

    lines = [ln for ln in result.output.strip().splitlines() if ln.strip()]
    assert len(lines) == 2, result.output
    assert "escapes=" in lines[0]


def test_a_shipped_feature_with_open_escapes_reads_escaped(tracker_issue):
    """`reworking` could never fire again — constraint 4 forbids reopening the
    parent. "shipped, and bugs have been blamed on it since" is the live
    reading, and it is the one-word answer to *how is this going*."""
    tracker_issue(KEY, state_category="completed")
    _blame("it leaks", KEY, "completed")

    result = stage_module.stage_for_key(KEY)

    assert result.name == "escaped"
    assert result.escapes == 1


def test_a_shipped_feature_with_no_escapes_reads_shipped(tracker_issue):
    tracker_issue(KEY, state_category="completed")
    _blame("caught before ship", KEY, "started")

    result = stage_module.stage_for_key(KEY)

    assert result.name == "shipped"
    assert result.escapes == 0


def test_reworking_is_not_in_the_stage_vocabulary():
    """Deleted, not re-read: keeping the name would lie, because nothing is
    being reworked and the board still says Done. (The module still *names* it
    in prose, explaining why it went — that is the point of the record.)"""
    vocabulary = {stage_module.SPEC, stage_module.BUILDING,
                  stage_module.SHIPPED, stage_module.ESCAPED}

    assert "reworking" not in vocabulary
    assert not hasattr(stage_module, "REWORKING")
    assert set(stage_module._BLURB) == vocabulary
    assert stage_module.ESCAPED == "escaped"


def test_stage_to_dict_stays_flat_and_gains_one_key():
    """Three live consumers — the span attributes, the task-link payload and
    `TaskLinkCard.vue`. Span attributes are flat key/values by nature, so
    nesting breaks all three for nothing."""
    payload = stage_module.Stage(name="shipped", rounds=1, open_defects=0,
                                 escapes=2, key=KEY).to_dict()

    assert set(payload) == {"feature_key", "stage", "blurb", "rounds",
                            "open_defects", "escapes"}
    assert all(not isinstance(v, (dict, list)) for v in payload.values())


def test_the_feature_stage_span_still_carries_every_existing_attribute(
        tracker_issue, posted_spans):
    tracker_issue(KEY, state_category="started")

    runner.invoke(app, ["feature", "stage", KEY, "--session", "t-span"])

    attributes = [p["attributes"] for p in posted_spans
                  if p["name"] == "feature.stage"][0]
    assert {"stage", "blurb", "rounds", "open_defects"} <= set(attributes)
    # CAI-235's correction: one fact, one spelling.
    assert attributes["feature_key"] == KEY
    assert "key" not in attributes


def test_feature_stage_still_exits_two_when_the_tracker_is_unreachable(
        no_tracker):
    """There the tracker *is* the answer."""
    assert runner.invoke(app, ["feature", "stage", KEY]).exit_code == 2


# ── `feature ledger` ──────────────────────────────────────────

def test_feature_ledger_leads_with_the_stage_line(
        session_row, tracker_issue, attributing_trace):
    """The stage frames every number under it — `$31.80` means something
    different on a `spec` than on a `shipped`."""
    session_row("t1", cost=31.80)
    _declare(KEY, "t1", tracker_issue, attributing_trace,
             state_category="completed")

    output = runner.invoke(app, ["feature", "ledger", KEY]).output
    first = [ln for ln in output.splitlines() if ln.strip()][0]

    assert "shipped" in first
    assert "31.80" not in first


def test_feature_ledger_lists_sessions_by_date_short_trace_and_title(
        session_row, tracker_issue, attributing_trace):
    """`regin stats sessions`' convention verbatim, so an id carries by eye
    between the two surfaces. The list is the *evidence* for an upper-bound
    figure — a bare count gives a reader no way to check it."""
    session_row("cc72f3a2-0198-4263-9866-ca3b251ce452", cost=2.48,
                title="Build the feature ledger", started="2026-09-13 10:00:00")
    _declare(KEY, "cc72f3a2-0198-4263-9866-ca3b251ce452", tracker_issue,
             attributing_trace)

    output = runner.invoke(app, ["feature", "ledger", KEY]).output

    assert "2026-09-13" in output
    assert "cc72f3a2" in output
    assert "cc72f3a2-0198" not in output
    assert "Build the feature ledger" in output


def test_a_recalled_session_row_shows_a_dash_for_its_round(
        session_row, tracker_issue, attributing_trace):
    session_row("t-recalled", cost=1.00, title="somebody else's day")
    tracker_issue(KEY, rounds=3)
    attributing_trace("t-me")
    runner.invoke(app, ["feature", "attribute", KEY, "--session", "t-recalled"])

    row = [ln for ln in runner.invoke(app, ["feature", "ledger", KEY]).output
           .splitlines() if "t-recall" in ln][0]

    assert "recalled" in row
    assert "3" not in row.split("recalled")[-1]
    assert "—" in row


def test_feature_ledger_exits_zero_when_the_tracker_is_unreachable(
        session_row, tracker_issue, attributing_trace, tracker_down):
    """Its substance is local and correct; exiting non-zero would withhold a
    correct answer because one field could not be fetched."""
    session_row("t1", cost=5.00)
    _declare(KEY, "t1", tracker_issue, attributing_trace)
    tracker_down()

    result = runner.invoke(app, ["feature", "ledger", KEY])

    assert result.exit_code == 0, result.output
    assert "unknown" in result.output
    assert "5.00" in result.output


def test_unknown_never_appears_as_a_stage_name_in_json_or_a_span(
        session_row, tracker_issue, attributing_trace, tracker_down):
    """A render-only string, not a fifth stage. It is a statement about us,
    not about the feature."""
    session_row("t1", cost=5.00)
    _declare(KEY, "t1", tracker_issue, attributing_trace)
    tracker_down()

    payload = json.loads(
        runner.invoke(app, ["feature", "ledger", KEY, "--json"]).output)

    assert payload["stage"] is None
    assert "unknown" not in json.dumps(payload.get("stage"))
    assert stage_module.stage_from_snapshot(None) is None


def test_feature_ledger_leaves_no_span(session_row, posted_spans,
                                       tracker_issue, attributing_trace):
    """A second span named `feature.stage` would let an idle report satisfy
    the gate. One fingerprint, one command."""
    session_row("t1", cost=5.00)
    _declare(KEY, "t1", tracker_issue, attributing_trace)
    posted_spans.clear()

    runner.invoke(app, ["feature", "ledger", KEY, "--session", "t1"])

    assert posted_spans == []


def test_feature_ledger_json_groups_each_gap_under_its_figure(
        session_row, tracker_issue, attributing_trace):
    """Flattened they read as noise: a reader cannot tell which number
    `no_usage_captured` qualifies."""
    session_row("t-nousage", cost=None)
    session_row("t-zero", cost=1.00, active_work_ms=0)
    _declare(KEY, "t-nousage", tracker_issue, attributing_trace)
    _declare(KEY, "t-zero", tracker_issue, attributing_trace)

    payload = json.loads(
        runner.invoke(app, ["feature", "ledger", KEY, "--json"]).output)

    assert payload["cost"]["gaps"]["no_usage_captured"] == 1
    assert payload["work_time"]["gaps"]["zero_duration"] == 1
    assert "traceless_runs" in payload["sessions"]["gaps"]
    assert "state_unknown" in payload["defects"]["gaps"]
    assert "gaps" not in payload
