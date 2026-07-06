"""Session-level retention prune (lib/trace/prune.py).

Golden spec for `prune_trace_data`'s three opt-in modes — purge test fixtures,
sweep orphans, apply an age cutoff — plus the dry-run / confirmation contract
the CLI depends on.
"""

from __future__ import annotations

from lib.orm.engine import get_connection
from lib.trace.prune import prune_trace_data


def _add_session(conn, tid, *, is_test, age_days):
    conn.execute(
        "INSERT INTO sessions (trace_id, is_test, started_at, last_seen) "
        "VALUES (?, ?, datetime('now', ?), datetime('now', ?))",
        (tid, is_test, f"-{age_days} days", f"-{age_days} days"))


def _add_span(conn, tid, span_id):
    conn.execute(
        "INSERT INTO session_spans (trace_id, span_id, name, kind, start_time, "
        "source) VALUES (?, ?, 'tool.Bash', 'internal', '2026-04-18T12:00:00', "
        "'hook')",
        (tid, span_id))


def _seed(conn):
    """recent-real, old-real, a test fixture, and one orphan span."""
    _add_session(conn, "real-recent", is_test=0, age_days=1)
    _add_span(conn, "real-recent", "s-rr")
    _add_session(conn, "real-old", is_test=0, age_days=100)
    _add_span(conn, "real-old", "s-ro")
    _add_session(conn, "fixture", is_test=1, age_days=1)
    _add_span(conn, "fixture", "s-fx")
    _add_span(conn, "ghost", "s-orphan")  # no sessions row
    conn.commit()


def _spans(conn):
    return {r["span_id"] for r in
            conn.execute("SELECT span_id FROM session_spans").fetchall()}


def _sessions(conn):
    return {r["trace_id"] for r in
            conn.execute("SELECT trace_id FROM sessions").fetchall()}


def test_no_mode_enabled_writes_nothing(tmp_db):
    """No mode flag → empty plan, DB untouched, guidance to the caller."""
    conn = get_connection()
    try:
        _seed(conn)
        before = _spans(conn)
    finally:
        conn.close()

    result = prune_trace_data()  # all modes default off
    assert result["enabled"] == []
    assert result["rows"] == 0

    conn = get_connection()
    try:
        assert _spans(conn) == before
    finally:
        conn.close()


def test_dry_run_reports_but_writes_nothing(tmp_db):
    """--dry-run tallies the would-delete rows and touches nothing."""
    conn = get_connection()
    try:
        _seed(conn)
        before_spans, before_sessions = _spans(conn), _sessions(conn)
    finally:
        conn.close()

    result = prune_trace_data(purge_test=True, orphans=True, dry_run=True)
    assert result["rows"] > 0
    assert result["by_table"]["session_spans"] == 2  # fixture + orphan span

    conn = get_connection()
    try:
        assert _spans(conn) == before_spans
        assert _sessions(conn) == before_sessions
    finally:
        conn.close()


def test_purge_test_removes_fixture_session_and_spans(tmp_db):
    """is_test=1 sessions vanish from every trace table; real sessions stay."""
    conn = get_connection()
    try:
        _seed(conn)
    finally:
        conn.close()

    prune_trace_data(purge_test=True, dry_run=False, vacuum=False)

    conn = get_connection()
    try:
        assert "fixture" not in _sessions(conn)
        assert {"real-recent", "real-old"} <= _sessions(conn)
        assert "s-fx" not in _spans(conn)
    finally:
        conn.close()


def test_orphans_removes_parentless_child_rows_only(tmp_db):
    """A span with no sessions row is swept; real spans are untouched."""
    conn = get_connection()
    try:
        _seed(conn)
    finally:
        conn.close()

    prune_trace_data(orphans=True, dry_run=False, vacuum=False)

    conn = get_connection()
    try:
        assert "s-orphan" not in _spans(conn)
        assert {"s-rr", "s-ro", "s-fx"} <= _spans(conn)
    finally:
        conn.close()


def test_orphans_spares_session_less_inbox_rows(tmp_db):
    """The orphan sweep is scoped to detail tables: a session-less
    `agent_messages` row (e.g. a synthetic `wiki-debt` inbox warning) must
    survive, not be mistaken for orphaned trace data."""
    conn = get_connection()
    try:
        _seed(conn)
        conn.execute(
            "INSERT INTO agent_messages (trace_id, msg_type, body) "
            "VALUES ('wiki-debt', 'warning', 'drift')")
        conn.commit()
    finally:
        conn.close()

    prune_trace_data(orphans=True, dry_run=False, vacuum=False)

    conn = get_connection()
    try:
        kept = conn.execute(
            "SELECT COUNT(*) c FROM agent_messages WHERE trace_id = 'wiki-debt'"
        ).fetchone()["c"]
        assert kept == 1
        assert "s-orphan" not in _spans(conn)  # detail orphan still swept
    finally:
        conn.close()


def test_days_cutoff_drops_old_detail_keeps_aggregate(tmp_db):
    """Retention keeps the old session's aggregate row but drops its detail;
    the recent real session is fully untouched."""
    conn = get_connection()
    try:
        _seed(conn)
    finally:
        conn.close()

    prune_trace_data(days=60, dry_run=False, vacuum=False)

    conn = get_connection()
    try:
        assert "real-old" in _sessions(conn)   # aggregate row kept
        assert "s-ro" not in _spans(conn)       # its detail dropped
        assert "s-rr" in _spans(conn)           # recent session intact
    finally:
        conn.close()


def test_days_drop_sessions_also_removes_aggregate(tmp_db):
    """--drop-sessions additionally removes the aged-out aggregate row."""
    conn = get_connection()
    try:
        _seed(conn)
    finally:
        conn.close()

    prune_trace_data(days=60, drop_sessions=True, dry_run=False, vacuum=False)

    conn = get_connection()
    try:
        assert "real-old" not in _sessions(conn)
        assert "real-recent" in _sessions(conn)
    finally:
        conn.close()
