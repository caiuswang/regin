"""`/clear` succession on the tmux tier (`lib/agent_bridge/store`).

`/clear` ends the conversation and opens another in the same pane, so the id
the operator's page holds names a session that is over while the agent works
on under one nothing links to. `clear_successor` is the tmux answer to
`agent_sdk.live_child`.

Pinned here because the inference is only safe under a narrow condition. A
pane an operator keeps open accumulates a row per session that ever ran in
it, so "the newer occupant of my pane" alone would hand an unrelated later
`claude` the steering a dead page still offers — the swap that once made a
stale card's `/exit` end someone else's running session. The predecessor's
own `ended_reason = 'clear'` is what separates the two, and most of what
follows is the refusals.
"""

from __future__ import annotations

from lib.agent_bridge import store
from lib.orm.engine import get_connection


def _seed(trace_id, pane_id="%0", *, at, server_pid=111, pane_pid=222,
          socket=None, reachable=1):
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


def _seed_session(trace_id, *, ended_at=None, ended_reason=None):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, status, started_at, last_seen, "
            "ended_at, ended_reason) VALUES (?, ?, ?, ?, ?, ?)",
            (trace_id, "ended" if ended_at else "active",
             "2026-08-01 09:00:00", ended_at or "2026-08-01 12:00:00",
             ended_at, ended_reason),
        )
        conn.commit()
    finally:
        conn.close()


def _cleared_pane(*, socket=None, server_pid=111):
    """The shape after a `/clear`: `old` retired by reason, `new` in its pane."""
    _seed("old", at="2026-08-01 10:00:00", socket=socket,
          server_pid=server_pid)
    _seed_session("old", ended_at="2026-08-01 11:00:00", ended_reason="clear")
    _seed("new", at="2026-08-01 11:00:01", socket=socket,
          server_pid=server_pid)
    _seed_session("new")


def test_cleared_session_resolves_to_the_pane_s_new_occupant(tmp_db):
    _cleared_pane()
    assert store.clear_successor("old") == "new"


def test_the_successor_itself_has_none(tmp_db):
    """Nothing to redirect: this IS the live half."""
    _cleared_pane()
    assert store.clear_successor("new") is None


def test_a_live_session_has_no_successor(tmp_db):
    _seed("live", at="2026-08-01 10:00:00")
    _seed_session("live")
    assert store.clear_successor("live") is None


def test_an_unrelated_later_claude_is_not_a_successor(tmp_db):
    """The destructive case. A terminal reused hours later holds a session
    that never had anything to do with the one the page is on — steering
    through to it would type a prompt into a stranger's conversation."""
    _seed("old", at="2026-08-01 10:00:00")
    _seed_session("old", ended_at="2026-08-01 10:30:00",
                  ended_reason="prompt_input_exit")
    _seed("unrelated", at="2026-08-01 18:00:00")
    _seed_session("unrelated")
    assert store.clear_successor("old") is None


def test_a_session_killed_without_a_reason_has_no_successor(tmp_db):
    """No SessionEnd, or one that carried no reason: the succession is not
    provable, and this read fails closed rather than guessing."""
    _seed("old", at="2026-08-01 10:00:00")
    _seed_session("old", ended_at="2026-08-01 10:30:00")
    _seed("next", at="2026-08-01 11:00:00")
    _seed_session("next")
    assert store.clear_successor("old") is None


def test_a_session_with_no_row_at_all_has_no_successor(tmp_db):
    _seed("old", at="2026-08-01 10:00:00")
    _seed("next", at="2026-08-01 11:00:00")
    assert store.clear_successor("old") is None


def test_another_tmux_server_is_a_different_pane(tmp_db):
    """Pane ids are per-server: `%0` under a restarted server names some
    other terminal entirely."""
    _seed("old", at="2026-08-01 10:00:00", server_pid=111)
    _seed_session("old", ended_at="2026-08-01 11:00:00", ended_reason="clear")
    _seed("elsewhere", at="2026-08-01 11:00:01", server_pid=999)
    _seed_session("elsewhere")
    assert store.clear_successor("old") is None


def test_another_socket_is_a_different_pane(tmp_db):
    _seed("old", at="2026-08-01 10:00:00", socket=None)
    _seed_session("old", ended_at="2026-08-01 11:00:00", ended_reason="clear")
    _seed("elsewhere", at="2026-08-01 11:00:01", socket="/tmp/other")
    _seed_session("elsewhere")
    assert store.clear_successor("old") is None


def test_a_retired_claimant_is_not_a_successor(tmp_db):
    """`reachable=0` is SessionEnd's own retirement of the claim — the pane
    is empty, not occupied by an heir."""
    _seed("old", at="2026-08-01 10:00:00")
    _seed_session("old", ended_at="2026-08-01 11:00:00", ended_reason="clear")
    _seed("gone", at="2026-08-01 11:00:01", reachable=0)
    assert store.clear_successor("old") is None


def test_an_ended_claimant_is_not_a_successor(tmp_db):
    """The successor of a successor: once the heir itself ends, the cleared
    page has nowhere live to send to and must say so."""
    _seed("old", at="2026-08-01 10:00:00")
    _seed_session("old", ended_at="2026-08-01 11:00:00", ended_reason="clear")
    _seed("heir", at="2026-08-01 11:00:01")
    _seed_session("heir", ended_at="2026-08-01 11:30:00", ended_reason="logout")
    assert store.clear_successor("old") is None


def test_an_older_row_on_the_pane_is_not_a_successor(tmp_db):
    """Succession runs forward only: a session that held the pane BEFORE
    this one is not what `/clear` opened."""
    _seed("earlier", at="2026-08-01 09:00:00")
    _seed_session("earlier")
    _seed("old", at="2026-08-01 10:00:00")
    _seed_session("old", ended_at="2026-08-01 11:00:00", ended_reason="clear")
    assert store.clear_successor("old") is None


def test_the_newest_heir_wins_a_chain_of_clears(tmp_db):
    """Two clears in a row: the first page must land on the conversation
    actually in the pane, not on the intermediate one that is also over."""
    _seed("first", at="2026-08-01 10:00:00")
    _seed_session("first", ended_at="2026-08-01 10:30:00", ended_reason="clear")
    _seed("second", at="2026-08-01 10:30:01")
    _seed_session("second", ended_at="2026-08-01 11:00:00",
                  ended_reason="clear")
    _seed("third", at="2026-08-01 11:00:01")
    _seed_session("third")
    assert store.clear_successor("first") == "third"
    assert store.clear_successor("second") == "third"


def test_a_stranger_who_took_the_pane_after_the_heir_died_is_not_a_successor(
        tmp_db):
    """The hole a `/clear` gate alone does not close.

    `ended_reason` proves this session was retired by SOME heir — not that the
    session sitting in the pane hours later is that heir. A terminal an
    operator keeps open outlives the whole chain, and the stale trace page is
    exactly the artifact still offering to steer when an unrelated `claude`
    finally claims it.
    """
    _seed("cleared", at="2026-08-01 10:00:00")
    _seed_session("cleared", ended_at="2026-08-01 11:00:00",
                  ended_reason="clear")
    _seed("heir", at="2026-08-01 11:00:01", reachable=0)
    _seed_session("heir", ended_at="2026-08-01 12:00:00",
                  ended_reason="logout")
    _seed("stranger", at="2026-08-01 18:00:00")
    _seed_session("stranger")
    assert store.clear_successor("cleared") is None


def test_the_chain_survives_a_dead_link_that_was_itself_cleared(tmp_db):
    """The other side of that guard: two clears in a row leave a retired
    middle link, and the walk must pass through it rather than stop."""
    _seed("first", at="2026-08-01 10:00:00")
    _seed_session("first", ended_at="2026-08-01 10:30:00", ended_reason="clear")
    _seed("middle", at="2026-08-01 10:30:01", reachable=0)
    _seed_session("middle", ended_at="2026-08-01 11:00:00",
                  ended_reason="clear")
    _seed("live", at="2026-08-01 11:00:01")
    _seed_session("live")
    assert store.clear_successor("first") == "live"


def test_a_live_heir_that_lost_the_pane_is_not_steerable(tmp_db):
    """`reachable=0` on a session with no ending is SessionEnd's retirement
    racing its trace row, or a claim the registry dropped. Either way the pane
    is not answering for it, and the walk must not hand it a keystroke."""
    _seed("cleared", at="2026-08-01 10:00:00")
    _seed_session("cleared", ended_at="2026-08-01 11:00:00",
                  ended_reason="clear")
    _seed("retired", at="2026-08-01 11:00:01", reachable=0)
    _seed_session("retired")
    assert store.clear_successor("cleared") is None


def test_a_live_session_whose_row_moved_ahead_is_still_the_heir(tmp_db):
    """The chain is ordered by registration (`id`), not by `updated_at`.

    A live session's turn events re-upsert its row, so its `updated_at` runs
    ahead of every row registered after it. Ordering the walk by that column
    would step over the real heir and land on whatever registered last.
    """
    _seed("cleared", at="2026-08-01 10:00:00")
    _seed_session("cleared", ended_at="2026-08-01 11:00:00",
                  ended_reason="clear")
    _seed("heir", at="2026-08-01 11:00:01")
    _seed_session("heir")
    _seed("later-and-gone", at="2026-08-01 12:00:00", reachable=0)
    _seed_session("later-and-gone", ended_at="2026-08-01 12:30:00",
                  ended_reason="logout")
    conn = get_connection()
    try:
        conn.execute("UPDATE bridge_panes SET updated_at = ? "
                     "WHERE trace_id = 'heir'", ("2026-08-01 18:00:00",))
        conn.commit()
    finally:
        conn.close()
    assert store.clear_successor("cleared") == "heir"


def test_a_resume_into_another_pane_cannot_promote_a_stranger(tmp_db):
    """`bridge_panes.id` records when a SESSION first registered anywhere, not
    when it claimed THIS pane, and the upsert keeps that id when a resume moves
    the row to a different pane. The chain can therefore be stepped onto out of
    order — so the walk's answer has to pass the occupancy read too, and a
    disagreement resolves to no successor rather than to the wrong pane.

    `stranger` registered in `%1` before `moved` resumed into it, so it sorts
    ahead of the real heir in `moved`'s chain.
    """
    _seed("moved", pane_id="%0", at="2026-08-01 09:00:00")
    _seed("stranger", pane_id="%1", at="2026-08-01 09:30:00")
    conn = get_connection()
    try:
        conn.execute("UPDATE bridge_panes SET pane_id = '%1', updated_at = ? "
                     "WHERE trace_id = 'moved'", ("2026-08-01 10:00:00",))
        conn.commit()
    finally:
        conn.close()
    _seed_session("moved", ended_at="2026-08-01 11:00:00",
                  ended_reason="clear")
    _seed("heir", pane_id="%1", at="2026-08-01 11:00:01")
    _seed_session("heir")
    assert store.clear_successor("moved") is None


def test_the_occupancy_cross_check_does_not_refuse_a_real_heir(tmp_db):
    """The other side of that guard: an ordinary clear still resolves, so the
    cross-check has not turned every succession into a refusal."""
    _cleared_pane()
    assert store.clear_successor("old") == "new"


def test_no_trace_id_is_a_clean_none(tmp_db):
    assert store.clear_successor("") is None


def test_a_drifted_schema_resolves_to_none(tmp_db):
    """Same fail-closed contract as `get_reachable_pane`: a DB the schema
    repair has not reached answers "no successor", never a 500."""
    conn = get_connection()
    try:
        conn.execute("DROP TABLE bridge_panes")
        conn.commit()
    finally:
        conn.close()
    assert store.clear_successor("old") is None
