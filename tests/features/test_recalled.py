"""Acceptance items for CAI-239 — recalling a session long after it ran.

There is no backfill sweep. What there is instead is a third provenance, and
the whole weight of this file is that it is decided by comparing two
identifiers — never by asking how old anything is.
"""

from __future__ import annotations

import inspect

from typer.testing import CliRunner

from cli.app import app
from lib.features import attribution

runner = CliRunner()
KEY = "CAI-242"


def _attribute(key, trace, *extra):
    return runner.invoke(app, ["feature", "attribute", key,
                               "--session", trace, *extra])


def test_attributing_a_trace_that_is_not_the_attributing_trace_is_recalled(
        session_row, tracker_issue, attributing_trace):
    tracker_issue(KEY)
    attributing_trace("t-me")
    session_row("t-me", cost=1.0)
    session_row("t-someone-else", cost=1.0)

    _attribute(KEY, "t-me")
    _attribute(KEY, "t-someone-else")

    provenance = {r.trace_id: r.provenance
                  for r in attribution.sessions_for_feature(KEY)}
    assert provenance == {"t-me": attribution.DECLARED,
                          "t-someone-else": attribution.RECALLED}


def test_provenance_is_decided_by_identity_never_by_elapsed_time(
        session_row, tracker_issue, attributing_trace):
    """The borderline case — declaring your own session as it ends — must read
    `declared`, and an ancient session declared by itself must too. There is no
    window to tune because there is no window."""
    tracker_issue(KEY)
    attributing_trace("t-old")
    session_row("t-old", cost=1.0, started="2026-04-29 00:00:00")

    _attribute(KEY, "t-old")

    row = attribution.features_for_session("t-old")[0]
    assert row.provenance == attribution.DECLARED
    # And the decision itself consults no clock.
    source = inspect.getsource(attribution._provenance_for)
    for forbidden in ("now(", "datetime", "time.", "opened_at", "started_at"):
        assert forbidden not in source, forbidden


def test_a_recalled_row_has_a_null_round(session_row, tracker_issue,
                                         attributing_trace):
    """Writing today's round would be worse than writing nothing: it looks
    like a measurement and nothing downstream could tell it from a real one."""
    tracker_issue(KEY, rounds=4)
    attributing_trace("t-me")
    session_row("t-other", cost=1.0)

    _attribute(KEY, "t-other")

    assert attribution.features_for_session("t-other")[0].round is None


def test_recalled_rows_are_counted_and_labelled_never_filtered(
        session_row, tracker_issue, attributing_trace):
    tracker_issue(KEY)
    attributing_trace("t-me")
    session_row("t-me", cost=1.00)
    session_row("t-other", cost=2.00)
    _attribute(KEY, "t-me")
    _attribute(KEY, "t-other")

    from lib.features import ledger
    report = ledger.build(KEY)

    assert report.cost.sessions == 2
    assert report.cost.recalled == 1
    assert report.cost.usd == 3.00


def test_there_is_no_flag_to_exclude_recalled_rows():
    """Excluding by default would make the ledger disagree with itself
    depending on who ran the command; an opt-out invites tuning the number
    until it looks good."""
    helps = "".join(
        runner.invoke(app, ["feature", verb, "--help"]).output
        for verb in ("ledger", "attribute", "stage"))
    flags = {word for word in helps.split() if word.startswith("--")}

    assert not [f for f in flags if "recalled" in f.lower()]
