"""Whether a defect inherits the session's declared feature. It does not.

`defects.feature_id` names **the feature that caused the bug**, not the one
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


def _declare(feature_id, trace, attributing_trace):
    attributing_trace(trace)
    runner.invoke(app, ["feature", "attribute", str(feature_id),
                        "--session", trace])


def test_open_with_a_session_and_no_feature_stays_unattributed(
        session_row, attributing_trace, feature_row):
    feature = feature_row()
    session_row("t-build", cost=1.0)
    _declare(feature.id, "t-build", attributing_trace)

    result = runner.invoke(defects_app, ["open", "something is off",
                                         "--session", "t-build"])

    assert result.exit_code == 0, result.output
    assert len(store.unattributed()) == 1
    assert store.for_feature(feature.id) == []


def test_the_sessions_declaration_is_hinted_never_inherited(
        session_row, attributing_trace, feature_row):
    """A one-line hint so the shorthand is discoverable at the moment it is
    wanted — and nothing more than a hint."""
    feature = feature_row()
    session_row("t-build", cost=1.0)
    _declare(feature.id, "t-build", attributing_trace)

    output = runner.invoke(defects_app, ["open", "something is off",
                                         "--session", "t-build"]).output

    assert str(feature.id) in output
    assert "@session" in output
    assert store.unattributed()[0].feature_id is None


def test_feature_at_session_shorthand_resolves_the_declaration(
        session_row, attributing_trace, feature_row):
    """A human typing `@session` is *affirming* the declaration, and an
    affirmation is a declaration; regin filling it in silently is an
    inference."""
    feature = feature_row()
    session_row("t-build", cost=1.0)
    _declare(feature.id, "t-build", attributing_trace)

    result = runner.invoke(defects_app, ["open", "still broken",
                                         "--feature", "@session",
                                         "--session", "t-build"])

    assert result.exit_code == 0, result.output
    assert [r.symptom for r in store.for_feature(feature.id)] == [
        "still broken"]


def test_at_session_refuses_when_two_features_are_declared(
        session_row, attributing_trace, feature_row):
    """The bug is in one of the two and which one is precisely what nobody has
    looked at."""
    first, second = feature_row("A"), feature_row("B")
    session_row("t-two", cost=1.0)
    _declare(first.id, "t-two", attributing_trace)
    _declare(second.id, "t-two", attributing_trace)

    result = runner.invoke(defects_app, ["open", "still broken",
                                         "--feature", "@session",
                                         "--session", "t-two"])

    assert result.exit_code != 0
    assert str(first.id) in result.output
    assert str(second.id) in result.output
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
    assert store.unattributed()[0].feature_id is None
    assert "regin defects attribute" in result.output


def test_defects_has_no_provenance_column():
    """`typed` and `@session`-resolved are indistinguishable in
    trustworthiness, which is the only thing provenance would be read for. The
    audit trail already lives in the activity log and the span."""
    from lib.orm.models.defects import Defect

    assert "provenance" not in Defect.model_fields
    # `session_features` is the different case: there the two provenances
    # differ in *who asserted them* — a human versus a run of a task.
    assert attribution.DECLARED != attribution.RUN


# ── re-attribution re-blames (defect #137) ───────────────────


def test_re_attribution_re_points_at_the_new_features_last_event(feature_row):
    """The blame snapshot is write-once per BLAME, not per row.

    It used to be per row: `_bind_blame` no-opped once the state word was set,
    so moving a defect to another feature left the first feature's word
    standing — and the escape verdict then described a feature the defect was
    no longer blamed on.
    """
    from lib.defects import escapes
    from lib.features import events

    shipped = feature_row("A", stage=events.SHIPPED)
    building = feature_row("B", stage=events.BUILD_STARTED)
    row = store.record("the gutter folds", feature_id=shipped.id)
    assert escapes.counts_for(shipped.id).escapes == 1

    moved = store.attribute(row.id, building.id)

    assert moved.feature_id == building.id
    assert moved.blamed_event_id == events.last(building.id).id
    assert escapes.counts_for(shipped.id).escapes == 0


def test_re_attributing_to_the_same_feature_keeps_the_original_pointer(
        feature_row):
    """No re-read when nothing moved: re-typing the id is not a new blame, and
    re-pointing would let a feature shipping later turn an old report into an
    escape."""
    from lib.features import events

    feature = feature_row(stage=events.BUILD_STARTED)
    row = store.record("the rail clips", feature_id=feature.id)
    pointed_at = row.blamed_event_id
    events.append(feature.id, events.SHIPPED, declared_by=events.OPERATOR)

    same = store.attribute(row.id, feature.id)

    assert same.blamed_event_id == pointed_at


def test_attributing_an_unattributed_defect_takes_its_first_pointer(
        feature_row):
    from lib.defects import escapes
    from lib.features import events

    feature = feature_row(stage=events.SHIPPED)
    row = store.record("nobody knows yet", session_id="t-1")
    assert row.blamed_event_id is None

    blamed = store.attribute(row.id, feature.id)

    assert blamed.blamed_event_id == events.last(feature.id).id
    assert escapes.counts_for(feature.id).escapes == 1
