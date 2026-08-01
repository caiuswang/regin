"""One pane, one steerable session (`lib/agent_bridge/store`).

`bridge_panes` keys on trace_id and retires nothing, so a terminal an
operator keeps open accumulates a row per session that ever ran in it.
Delivery's identity guard cannot tell those rows apart — the tmux server
pid, the `pane_pid` (the pane's *shell*) and the foreground command are all
properties of the pane, unchanged when the next session takes it over. So
the store, not the guard, has to answer "is this session still the one in
that pane": the newest registration wins, and every session it displaced
resolves to no pane at all.

Pinned here because the failure is silent and destructive rather than an
error — steering a finished session typed into a live one (a `/live`
`/exit` for a stale card exited an unrelated running session).
"""

from __future__ import annotations

from lib.agent_bridge import store
from lib.orm.engine import get_connection


def _seed(trace_id, pane_id="%0", *, at, server_pid=111, pane_pid=222,
          socket=None, reachable=1):
    """One registry row, with `updated_at` pinned so occupancy is explicit."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO bridge_panes (trace_id, pane_id, tmux_server_pid, "
            "pane_pid, tmux_socket, reachable, cwd, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '/work', ?, ?)",
            (trace_id, pane_id, server_pid, pane_pid, socket, reachable,
             at, at))
        conn.commit()
    finally:
        conn.close()


def test_sole_occupant_resolves(tmp_db):
    _seed("only", at="2026-08-01 10:00:00")
    assert store.get_reachable_pane("only")["pane_id"] == "%0"


def test_displaced_session_resolves_to_nothing(tmp_db):
    """The reported bug: the old session must not reach the pane it lost."""
    _seed("old", at="2026-08-01 10:00:00")
    _seed("new", at="2026-08-01 11:00:00")
    assert store.get_reachable_pane("old") is None
    assert store.get_reachable_pane("new")["pane_id"] == "%0"


def test_reregistering_reclaims_the_pane(tmp_db):
    """A session's own turn events re-upsert its row; moving back ahead of
    the child that displaced it must make it steerable again."""
    _seed("parent", at="2026-08-01 10:00:00")
    _seed("child", at="2026-08-01 10:30:00")
    assert store.get_reachable_pane("parent") is None
    conn = get_connection()
    try:
        conn.execute("UPDATE bridge_panes SET updated_at = ? "
                     "WHERE trace_id = 'parent'", ("2026-08-01 11:00:00",))
        conn.commit()
    finally:
        conn.close()
    assert store.get_reachable_pane("parent")["pane_id"] == "%0"


def test_same_pane_id_on_another_tmux_server_does_not_displace(tmp_db):
    """Pane ids are per-server: `%0` under a restarted server is a different
    pane, and must not evict the session registered under the old one."""
    _seed("mine", at="2026-08-01 10:00:00", server_pid=111)
    _seed("elsewhere", at="2026-08-01 11:00:00", server_pid=999)
    assert store.get_reachable_pane("mine")["tmux_server_pid"] == 111


def test_same_pane_id_on_another_socket_does_not_displace(tmp_db):
    _seed("mine", at="2026-08-01 10:00:00", socket=None)
    _seed("elsewhere", at="2026-08-01 11:00:00", socket="/tmp/other")
    assert store.get_reachable_pane("mine") is not None


def test_unreachable_newer_row_does_not_displace(tmp_db):
    """Only a session that is itself reachable can claim the pane."""
    _seed("live", at="2026-08-01 10:00:00")
    _seed("ghost", at="2026-08-01 11:00:00", reachable=0)
    assert store.get_reachable_pane("live") is not None


def test_listing_shows_only_current_occupants(tmp_db):
    _seed("old", at="2026-08-01 10:00:00")
    _seed("new", at="2026-08-01 11:00:00")
    _seed("other-pane", pane_id="%9", at="2026-08-01 09:00:00")
    listed = {r["trace_id"] for r in store.list_reachable_sessions()}
    assert listed == {"new", "other-pane"}
