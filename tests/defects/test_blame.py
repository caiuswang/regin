"""Acceptance items for CAI-238 — whether a defect inherits the session's
declared feature. It does not.

`defects.feature_key` names **the feature that caused the bug**, not the one
the reporting session had on screen. The two come apart constantly: a session
building one feature trips over a bug shipped months ago. Charging that defect
to the feature on screen inflates one feature's rounds and exonerates another.
"""

from __future__ import annotations

from typer.testing import CliRunner

from cli.app import app
from cli.commands.defects import defects_app
from lib.defects import store
from lib.features import attribution

runner = CliRunner()
KEY = "CAI-242"
OTHER = "CAI-230"


def _declare(key, trace, tracker_issue, attributing_trace):
    tracker_issue(key)
    attributing_trace(trace)
    runner.invoke(app, ["feature", "attribute", key, "--session", trace])


def test_open_with_a_session_and_no_feature_stays_unattributed(
        session_row, tracker_issue, attributing_trace):
    session_row("t-build", cost=1.0)
    _declare(KEY, "t-build", tracker_issue, attributing_trace)

    result = runner.invoke(defects_app, ["open", "something is off",
                                         "--session", "t-build"])

    assert result.exit_code == 0, result.output
    assert len(store.unattributed()) == 1
    assert store.for_feature(KEY) == []


def test_the_sessions_declaration_is_hinted_never_inherited(
        session_row, tracker_issue, attributing_trace):
    """A one-line hint so the shorthand is discoverable at the moment it is
    wanted — and nothing more than a hint."""
    session_row("t-build", cost=1.0)
    _declare(KEY, "t-build", tracker_issue, attributing_trace)

    output = runner.invoke(defects_app, ["open", "something is off",
                                         "--session", "t-build"]).output

    assert KEY in output
    assert "@session" in output
    assert store.unattributed()[0].feature_key is None


def test_feature_at_session_shorthand_resolves_the_declaration(
        session_row, tracker_issue, attributing_trace):
    """A human typing `@session` is *affirming* the declaration, and an
    affirmation is a declaration; regin filling it in silently is an
    inference."""
    session_row("t-build", cost=1.0)
    _declare(KEY, "t-build", tracker_issue, attributing_trace)

    result = runner.invoke(defects_app, ["open", "still broken",
                                         "--feature", "@session",
                                         "--session", "t-build"])

    assert result.exit_code == 0, result.output
    assert [r.symptom for r in store.for_feature(KEY)] == ["still broken"]


def test_at_session_refuses_when_two_features_are_declared(
        session_row, tracker_issue, attributing_trace):
    """The bug is in one of the two and which one is precisely what nobody has
    looked at."""
    session_row("t-two", cost=1.0)
    _declare(KEY, "t-two", tracker_issue, attributing_trace)
    _declare(OTHER, "t-two", tracker_issue, attributing_trace)

    result = runner.invoke(defects_app, ["open", "still broken",
                                         "--feature", "@session",
                                         "--session", "t-two"])

    assert result.exit_code != 0
    assert KEY in result.output and OTHER in result.output
    assert store.unattributed() == []


def test_at_session_refuses_non_zero_when_nothing_is_declared(session_row):
    """Falling back to unattributed is the worst outcome available: the
    operator typed something specifically to attribute and would walk away
    believing it landed."""
    session_row("t-bare", cost=1.0)

    result = runner.invoke(defects_app, ["open", "still broken",
                                         "--feature", "@session",
                                         "--session", "t-bare"])

    assert result.exit_code != 0
    assert "regin feature attribute" in result.output
    assert store.unattributed() == []


def test_at_session_refuses_non_zero_on_an_unknown_trace():
    result = runner.invoke(defects_app, ["open", "still broken",
                                         "--feature", "@session",
                                         "--session", "t-nonexistent"])

    assert result.exit_code != 0
    assert store.unattributed() == []


def test_defects_close_never_writes_feature_key():
    """Attribution stays a single verb with a single writer — and `close` is
    the moment the temptation is strongest."""
    row = store.record("unattributed and staying that way", session_id="t-x")

    result = runner.invoke(defects_app, ["close", str(row.id),
                                         "--check", "a regression test"])

    assert result.exit_code == 0, result.output
    assert store.unattributed()[0].feature_key is None
    assert "regin defects attribute" in result.output


def test_defects_has_no_provenance_column():
    """`typed` and `@session`-resolved are indistinguishable in
    trustworthiness, which is the only thing provenance would be read for. The
    audit trail already lives in the activity log and the span."""
    from lib.orm.models.defects import Defect

    assert "provenance" not in Defect.model_fields
    # `session_features` is the different case: there the two provenances
    # differ in *who asserted them* — a human versus a task link.
    assert attribution.DECLARED != attribution.TASK_LINK


# ── re-attribution re-blames (defect #137) ───────────────────


def test_re_attribution_re_reads_the_new_features_state(tracker_issue):
    """The blame snapshot is write-once per BLAME, not per row.

    It used to be per row: `_bind_blame` no-ops once `blamed_against_state` is
    set, so moving a defect to another feature left the first feature's
    `state_category` standing — and the escape verdict then described a
    feature the defect was no longer blamed on.
    """
    tracker_issue(KEY, state_category="completed")
    tracker_issue(OTHER, state_category="started")
    row = store.record("the gutter folds", feature_key=KEY)
    assert row.blamed_against_state == "completed"

    moved = store.attribute(row.id, OTHER)

    assert moved.feature_key == OTHER
    assert moved.blamed_against_state == "started"


def test_re_attributing_to_the_same_key_keeps_the_original_snapshot(
        tracker_issue):
    """No re-read when nothing moved: re-typing the key is not a new blame,
    and re-reading would let a feature shipping later turn an old report into
    an escape."""
    tracker_issue(KEY, state_category="started")
    row = store.record("the rail clips", feature_key=KEY)
    assert row.blamed_against_state == "started"
    tracker_issue(KEY, state_category="completed")

    same = store.attribute(row.id, KEY)

    assert same.blamed_against_state == "started"


def test_attributing_an_unattributed_defect_takes_its_first_snapshot(
        tracker_issue):
    tracker_issue(KEY, state_category="completed")
    row = store.record("nobody knows yet", session_id="t-1")
    assert row.blamed_against_state is None

    blamed = store.attribute(row.id, KEY)

    assert blamed.blamed_against_state == "completed"
