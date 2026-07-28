"""Tests for the provider-agnostic session-id resolver in lib/session_probe.py.

The resolver is what makes the session-linked skill loops (`goal preflight`,
`memory recall-for-task`, `regin gate`, `goal feedback --trace-id`) runnable
outside Claude Code: a harness-agnostic override first, each provider's own
variable next, and an opt-in read of regin's own session table last.
"""

from datetime import datetime, timedelta

from lib import session_probe
from lib.orm import SessionLocal
from lib.orm.models.trace import Session as SessionRow


def _clear_env(monkeypatch):
    for name in session_probe.env_candidates():
        monkeypatch.delenv(name, raising=False)


def _stamp(minutes_ago: float) -> str:
    """A `last_seen` on the same clock the span writer uses (naive local
    isoformat) — a UTC or space-separated stamp compares wrong against it."""
    return (datetime.now() - timedelta(minutes=minutes_ago)).isoformat()


def _session(trace_id: str, cwd: str, *, ended: str | None = None,
             minutes_ago: float = 1,
             origin: str | None = "session", is_test: int = 0) -> SessionRow:
    return SessionRow(
        trace_id=trace_id, cwd=cwd, started_at=_stamp(minutes_ago + 60),
        last_seen=_stamp(minutes_ago), ended_at=ended, origin=origin,
        is_test=is_test, span_count=0, skill_reads=0, file_edits=0,
        rule_checks=0, plan_enters=0, prompts=0, tool_calls=0)


def _seed(*rows: SessionRow) -> None:
    with SessionLocal() as s:
        for row in rows:
            s.add(row)
        s.commit()


# ── env resolution ────────────────────────────────────────────

def test_resolve_returns_provider_env_var(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv(session_probe._ENV_SESSION_ID, 'env-sid')
    assert session_probe.resolve() == 'env-sid'


def test_resolve_returns_none_when_env_absent(monkeypatch):
    _clear_env(monkeypatch)
    assert session_probe.resolve() is None


def test_resolve_treats_empty_env_as_miss(monkeypatch):
    # An exported-but-empty var is a miss, not the empty string — callers rely
    # on falsy stdout meaning "omit the flag".
    _clear_env(monkeypatch)
    monkeypatch.setenv(session_probe._ENV_SESSION_ID, '')
    assert session_probe.resolve() is None


def test_portable_override_wins_over_provider_var(monkeypatch):
    # The whole point of REGIN_SESSION_ID: a wrapper can pin the id even when
    # a provider variable is also present (e.g. a nested harness).
    _clear_env(monkeypatch)
    monkeypatch.setenv(session_probe._ENV_SESSION_ID, 'claude-sid')
    monkeypatch.setenv(session_probe._PORTABLE_ENV, 'portable-sid')
    assert session_probe.resolve() == 'portable-sid'


def test_candidates_lead_with_the_portable_override_and_dedupe():
    names = session_probe.env_candidates()
    assert names[0] == session_probe._PORTABLE_ENV
    assert session_probe._ENV_SESSION_ID in names
    assert len(names) == len(set(names))


# ── trace fallback (opt-in) ───────────────────────────────────

def test_from_trace_finds_the_single_live_session(tmp_path):
    _seed(_session("sid-live", str(tmp_path)))
    assert session_probe.resolve_from_trace(str(tmp_path)) == "sid-live"


def test_from_trace_refuses_to_guess_between_two_live_sessions(tmp_path):
    # Two agents in one checkout is exactly the case the retired cwd-keyed
    # cache got wrong. Returning None is the whole safety property.
    _seed(_session("sid-a", str(tmp_path)),
          _session("sid-b", str(tmp_path), minutes_ago=2))
    assert session_probe.resolve_from_trace(str(tmp_path)) is None


def test_from_trace_ignores_ended_sessions(tmp_path):
    _seed(_session("sid-old", str(tmp_path), ended=_stamp(30)),
          _session("sid-now", str(tmp_path)))
    assert session_probe.resolve_from_trace(str(tmp_path)) == "sid-now"


def test_from_trace_ignores_other_directories(tmp_path):
    _seed(_session("sid-elsewhere", str(tmp_path / "other")))
    assert session_probe.resolve_from_trace(str(tmp_path)) is None


def test_from_trace_ignores_test_and_synthetic_rows(tmp_path):
    _seed(_session("sid-test", str(tmp_path), is_test=1),
          _session("sid-synthetic", str(tmp_path), origin="wiki-debt"))
    assert session_probe.resolve_from_trace(str(tmp_path)) is None


def test_from_trace_ignores_a_session_that_went_quiet(tmp_path):
    # `ended_at` is written only on a clean SessionEnd, so a crashed session
    # leaks a permanently-unended row. Without a recency bound the fallback
    # hands back an id from days ago as if it were the current session.
    _seed(_session("sid-stale", str(tmp_path), minutes_ago=60 * 24))
    assert session_probe.resolve_from_trace(str(tmp_path)) is None


def test_from_trace_picks_the_fresh_one_over_a_stale_sibling(tmp_path):
    # The common real state: one live session plus old never-ended rows in the
    # same checkout. That must resolve, not read as ambiguous.
    _seed(_session("sid-stale", str(tmp_path), minutes_ago=60 * 24),
          _session("sid-fresh", str(tmp_path), minutes_ago=1))
    assert session_probe.resolve_from_trace(str(tmp_path)) == "sid-fresh"


def test_from_trace_honours_a_widened_window(tmp_path):
    _seed(_session("sid-hour", str(tmp_path), minutes_ago=45))
    assert session_probe.resolve_from_trace(str(tmp_path)) is None
    assert session_probe.resolve_from_trace(
        str(tmp_path), window_minutes=120) == "sid-hour"


def test_from_trace_treats_a_null_origin_as_a_session(tmp_path):
    # A NULL origin reads as 'session' everywhere else in regin.
    _seed(_session("sid-null-origin", str(tmp_path), origin=None))
    assert session_probe.resolve_from_trace(str(tmp_path)) == "sid-null-origin"


def test_from_trace_resolves_symlinked_paths(tmp_path):
    # On macOS a session started in /tmp/x is recorded under /private/tmp/x.
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    _seed(_session("sid-real", str(real)))
    assert session_probe.resolve_from_trace(str(link)) == "sid-real"


def test_from_trace_reads_a_space_separated_stamp(tmp_path):
    # `last_seen` has more than one writer and they don't agree on the
    # separator. A string `>=` against an isoformat cutoff sorts a same-day
    # space-stamp BELOW it, so a live session would read as stale.
    fresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = _session("sid-space", str(tmp_path))
    row.last_seen = fresh
    _seed(row)
    assert session_probe.resolve_from_trace(str(tmp_path)) == "sid-space"


def test_from_trace_rejects_a_stamp_from_the_future(tmp_path):
    # A stamp carrying another timezone's date sorts above every cutoff, so a
    # string comparison would call a week-old session live.
    row = _session("sid-ahead", str(tmp_path))
    row.last_seen = _stamp(-60 * 24)
    _seed(row)
    assert session_probe.resolve_from_trace(str(tmp_path)) is None


def test_from_trace_ignores_an_unparseable_stamp(tmp_path):
    row = _session("sid-junk", str(tmp_path))
    row.last_seen = "not-a-timestamp"
    _seed(row)
    assert session_probe.resolve_from_trace(str(tmp_path)) is None


def test_from_trace_handles_a_timezone_aware_stamp(tmp_path):
    # Subtracting an aware datetime from a naive one raises TypeError outside
    # the parse guard, so one such row would crash `session-id --from-trace`.
    from datetime import timezone
    row = _session("sid-aware", str(tmp_path))
    row.last_seen = datetime.now(timezone.utc).isoformat()
    _seed(row)
    assert session_probe.resolve_from_trace(str(tmp_path)) == "sid-aware"


def test_from_trace_sees_past_a_pile_of_stale_rows(tmp_path):
    # Ordering (not just filtering) has to be chronological: a space-separated
    # stamp sorts BELOW every same-day "T" stamp, so a SQL order-and-truncate
    # would drop the live session behind hundreds of dead ones.
    # Same *day* as the live row on purpose: the separator is then the first
    # differing character, so every stale "T" stamp sorts above the live
    # space-separated one and a truncating scan never reaches it.
    rows = [_session(f"sid-dead-{i}", str(tmp_path), minutes_ago=45)
            for i in range(250)]
    live = _session("sid-live-late", str(tmp_path))
    live.last_seen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _seed(*rows, live)
    assert session_probe.resolve_from_trace(str(tmp_path)) == "sid-live-late"


def test_from_trace_still_refuses_to_guess_behind_a_pile_of_stale_rows(tmp_path):
    rows = [_session(f"sid-dead-{i}", str(tmp_path), minutes_ago=45)
            for i in range(250)]
    live_a = _session("sid-a", str(tmp_path))
    live_a.last_seen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _seed(*rows, live_a, _session("sid-b", str(tmp_path), minutes_ago=2))
    assert session_probe.resolve_from_trace(str(tmp_path)) is None


def test_from_trace_treats_an_unreadable_db_as_a_miss(tmp_path, monkeypatch):
    # A fresh install that never ran `regin init` must still let
    # `SID=$(regin session-id --from-trace)` be an empty string, not a crash.
    from lib import orm

    def boom():
        raise RuntimeError("unable to open database file")

    monkeypatch.setattr(orm, "SessionLocal", boom)
    assert session_probe.resolve_from_trace(str(tmp_path)) is None
