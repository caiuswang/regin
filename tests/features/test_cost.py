"""An honest cost roll-up.

One rule underneath all of it: **a gap is a count of sessions, never a
substituted zero**. The roll-up never lets a missing value read as $0.00, and
it never claims the figure is precise — attribution binds a whole session, so
what comes out is an upper bound and says so.
"""

from __future__ import annotations

from typer.testing import CliRunner

from cli.app import app
from lib.features import ledger, store as feature_store

runner = CliRunner()


def _feature(title="a feature"):
    return feature_store.create(title).id


def _declare(feature_id, trace, attributing_trace, *extra):
    attributing_trace(trace)
    return runner.invoke(app, ["feature", "attribute", str(feature_id),
                               "--session", trace, *extra])


def test_a_features_cost_is_the_live_sum_of_its_declared_sessions(
        session_row, attributing_trace):
    key = _feature()
    session_row("t1", cost=10.00)
    session_row("t2", cost=2.50)
    _declare(key, "t1", attributing_trace)
    _declare(key, "t2", attributing_trace)

    assert ledger.build(key).cost.usd == 12.50


def test_cost_is_read_live_and_moves_when_a_session_reprices(
        session_row, attributing_trace):
    """Never snapshotted: `sessions.cost_usd` rises while a session is alive,
    and freezing it would need a refresh trigger — the machinery the
    root-cause rule exists to avoid."""
    key = _feature()
    from sqlmodel import text
    from lib.orm import SessionLocal
    session_row("t1", cost=1.00)
    _declare(key, "t1", attributing_trace)
    assert ledger.build(key).cost.usd == 1.00

    with SessionLocal() as s:
        s.exec(text("UPDATE sessions SET cost_usd = 4.00 WHERE trace_id='t1'"))
        s.commit()

    assert ledger.build(key).cost.usd == 4.00


def test_cost_is_labelled_an_upper_bound_wherever_it_is_printed(
        session_row, attributing_trace, no_tracker):
    key = _feature()
    session_row("t1", cost=10.00)
    _declare(key, "t1", attributing_trace)

    human = runner.invoke(app, ["feature", "ledger", str(key)])
    payload = ledger.build(key).to_dict()

    assert "upper bound" in human.output
    assert payload["cost"]["upper_bound"] is True


def test_a_shared_session_charges_the_primary_in_full_and_the_secondary_zero(
        session_row, attributing_trace):
    """Splitting invents a ratio; duplicating breaks the one invariant worth
    holding — ∑ per-feature cost ≤ total attributed spend."""
    key, other = _feature("A"), _feature("B")
    session_row("t-shared", cost=8.00)
    _declare(key, "t-shared", attributing_trace)
    _declare(other, "t-shared", attributing_trace)

    primary = ledger.build(key).cost
    secondary = ledger.build(other).cost

    assert primary.usd == 8.00 and primary.shared == 0
    assert secondary.usd == 0.00 and secondary.shared == 1
    assert secondary.sessions == 1


def test_a_session_with_no_usage_captured_is_a_counted_gap_not_a_zero(
        session_row, attributing_trace):
    """Spans present, zero `turn_usage` rows — a regin ingestion gap, and 27%
    of the live database."""
    key = _feature()
    session_row("t-priced", cost=3.00, input_tokens=5, output_tokens=5)
    session_row("t-nousage", cost=None)
    _declare(key, "t-priced", attributing_trace)
    _declare(key, "t-nousage", attributing_trace)

    cost = ledger.build(key).cost

    assert cost.usd == 3.00
    assert cost.gaps["no_usage_captured"] == 1
    assert cost.gaps["model_unpriced"] == 0


def test_a_model_with_no_price_is_a_counted_gap_not_a_zero(
        session_row, attributing_trace):
    """Turns ingested, model outside the pricing catalogue. A separate counter
    from `no-usage-captured` because only this one is fixable by editing a
    price table."""
    key = _feature()
    session_row("t-unpriced", cost=None, input_tokens=100, output_tokens=200)
    _declare(key, "t-unpriced", attributing_trace)

    cost = ledger.build(key).cost

    assert cost.gaps["model_unpriced"] == 1
    assert cost.gaps["no_usage_captured"] == 0


def test_zero_duration_is_counted_separately_from_the_cost_gaps(
        session_row, attributing_trace):
    """`active_work_ms = 0` is never NULL, so it is a gap wearing a number's
    clothes — and it qualifies work time, not dollars."""
    key = _feature()
    session_row("t-zero", cost=2.00, active_work_ms=0)
    _declare(key, "t-zero", attributing_trace)

    report = ledger.build(key)

    assert report.work_time.gaps["zero_duration"] == 1
    assert "zero_duration" not in report.cost.gaps


def test_tokens_are_a_breakdown_and_are_never_summed(
        session_row, attributing_trace):
    """`cache_read` runs five orders of magnitude above `input`, so any total
    is cache reads wearing a work number's clothes."""
    key = _feature()
    session_row("t1", cost=1.0, input_tokens=118, output_tokens=57783,
                cache_read_tokens=6206875, cache_creation_tokens=42)
    _declare(key, "t1", attributing_trace)

    payload = ledger.build(key).to_dict()["tokens"]

    assert payload == {"input": 118, "output": 57783,
                       "cache_read": 6206875, "cache_creation": 42}
    assert not [k for k in payload if k in ("total", "tokens", "sum")]


def test_a_feature_with_no_declared_sessions_reads_none_declared(no_tracker):
    """A feature built in zero sessions is impossible, so a literal `0` could
    only ever mean "nobody declared" — and printing it as a number invites it
    into a comparison."""
    key = _feature()
    report = ledger.build(key)
    rendered = runner.invoke(app, ["feature", "ledger", str(key)]).output

    assert report.cost.usd is None
    cost_line = [ln for ln in rendered.splitlines() if ln.startswith("cost")][0]
    assert "none declared" in cost_line
    assert "$0.00" not in cost_line


def test_coverage_is_reported_wherever_the_ledger_is_read(
        session_row, attributing_trace, no_tracker):
    """The denominator that stops a per-feature figure being read as a share
    of the whole. A permanent fixture, not a launch-time apology."""
    key = _feature()
    session_row("t1", cost=10.00)
    session_row("t-unattributed", cost=90.00)
    _declare(key, "t1", attributing_trace)

    report = ledger.build(key)
    rendered = runner.invoke(app, ["feature", "ledger", str(key)]).output

    assert report.coverage.declared_sessions == 1
    assert report.coverage.all_sessions == 2
    assert report.coverage.declared_usd == 10.00
    assert report.coverage.all_usd == 100.00
    assert "coverage" in rendered.lower()
