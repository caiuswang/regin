"""Settle the sessions whose tmux server died with the machine.

The tmux-tier twin of `agent_sdk.store.reap_orphaned_runs`. A session the
operator started themselves — `claude` in a pane, registered in `bridge_panes`
— has no `agent_runs` row at all, so the SDK sweep, keyed entirely on that
table, walks past every one of them. Killing the tmux server (a reboot, a
`tmux kill-server`) takes those `claude` processes with it before any of them
can emit a `session.end`, and their `sessions` rows keep the launch-time
`status='active'` / `ended_at IS NULL` forever: permanently green in the
session list, unsteerable because the pane they name no longer exists, and
never offered for resume because they never look finished.

A tmux socket hosts exactly one server, so one probe per socket answers for
every row on it: a row whose `tmux_server_pid` is not the pid the server
reports is on a server that is gone. Sockets the probe could not read are
skipped entirely — see `_live_server_pid`.

That leaves one reboot shape uncovered, deliberately: a socket whose *file*
was cleared too answers `error connecting to … (No such file or directory)`,
which is indeterminate here, so nothing on it settles until a server comes
back on that socket. Widening the match to that string is what would let a
`FileNotFoundError` from a missing tmux binary — same words, via `_tmux`'s
OSError collapse — read as proof that every session in the table is dead.

Reads only. The `_SELF_NOT_ENDED` clause in `store` already hides a pane whose
session has an `ended_at`, so settling the session retires the pane; writing
`reachable=0` here would add nothing except a second writer to a table whose
every write lives behind `hook_manager/handlers/bridge_registry.py`.
"""

from __future__ import annotations

import sqlite3

from lib.activity_log import get_activity_logger
from lib.agent_bridge.delivery import _tmux
from lib.orm.engine import get_connection

log = get_activity_logger("agent_bridge")

# `reachable = 1` because a retired claim says nothing about the session that
# once held the pane: SessionEnd retires one, and so does a handover to the SDK
# tier, whose child registers no pane of its own. Without the clause a session
# resumed off its dead terminal is settled from the row its terminal life left
# behind — while the run driving it is alive.
_PANES_SQL = """
SELECT trace_id, tmux_socket, tmux_server_pid
FROM bridge_panes
WHERE trace_id IS NOT NULL AND trace_id <> '' AND reachable = 1
"""

_NO_SERVER = "no server running"


def _pane_rows() -> list[dict]:
    """Every registered pane's `(trace_id, socket, server pid)`.

    An absent or drifted `bridge_panes` degrades to an empty sweep, never an
    exception: this runs at boot, and a server that cannot read the registry
    must still serve.
    """
    conn = get_connection()
    try:
        rows = conn.execute(_PANES_SQL).fetchall()
    except sqlite3.OperationalError:
        log.error("bridge_reap_panes_query_failed", exc_info=True)
        return []
    finally:
        conn.close()
    log.read("bridge_reap_panes_listed", count=len(rows))
    return [dict(r) for r in rows]


def _live_run_ids() -> set[str] | None:
    """Ids a LIVE `agent_runs` row names, or None if the table went unread.

    None is not an empty set. A sweep that could not ask which sessions this
    process is running has no business deciding any of them is over, and the
    two answers must not share a return value.
    """
    from lib.agent_sdk.store import _LIVE_RUN_STATUSES  # noqa: PLC0415

    statuses = tuple(_LIVE_RUN_STATUSES)
    marks = ",".join("?" * len(statuses))
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT trace_id, cli_session_id FROM agent_runs "
            f"WHERE status IN ({marks})", statuses).fetchall()
    except sqlite3.OperationalError:
        log.error("bridge_reap_live_runs_query_failed", exc_info=True)
        return None
    finally:
        conn.close()
    log.read("bridge_reap_live_runs_listed", count=len(rows))
    return {t for r in rows for t in (r["trace_id"], r["cli_session_id"]) if t}


def _live_server_pid(socket: str | None) -> tuple[int | None, bool]:
    """`(pid, answered)` for the tmux server on `socket`.

    `answered` is False whenever the probe itself failed rather than reporting
    an absence — and the two are indistinguishable by return code. `_tmux`
    resolves a missing tmux binary, a timeout and a dead socket alike to
    returncode 1, so reading the code alone would make a machine with no tmux
    installed look like a machine where every server had died, and the sweep
    would settle every session in the database. Only tmux's own "no server
    running" line is proof; anything else is a question that went unanswered.
    """
    proc = _tmux(socket, "display-message", "-p", "#{pid}")
    out = (proc.stdout or "").strip()
    if proc.returncode == 0 and out.isdigit():
        log.read("bridge_reap_server_probed", socket=socket or "", pid=out)
        return int(out), True
    if _NO_SERVER in (proc.stderr or "").lower():
        log.read("bridge_reap_server_gone", socket=socket or "")
        return None, True
    log.read("bridge_reap_probe_indeterminate", socket=socket or "",
             returncode=proc.returncode)
    return None, False


def _dead_pane_rows(rows: list[dict]) -> list[dict]:
    """The rows whose server is provably gone, one probe per distinct socket."""
    dead: list[dict] = []
    for socket in {r["tmux_socket"] for r in rows}:
        pid, answered = _live_server_pid(socket)
        if not answered:
            continue
        dead.extend(r for r in rows if r["tmux_socket"] == socket
                    and r["tmux_server_pid"] != pid)
    return dead


def settle_dead_tmux_sessions(reason: str = "tmux server gone") -> int:
    """Close the trace rows of every registered pane on a dead tmux server.

    Returns how many rows were settled. Idempotent, and re-runnable at every
    boot: it is keyed on the *state* of the server a pane names, not on the
    moment that server died, so sessions orphaned by an earlier restart are
    still reachable by a later sweep.

    Sessions a live `agent_runs` row names are spared whatever their pane says
    — this process may be driving them right now, and a run regin owns outranks
    an inference drawn from a pane registration.
    """
    from lib.agent_sdk.store import end_trace_session  # noqa: PLC0415

    rows = _pane_rows()
    if not rows:
        return 0
    live = _live_run_ids()
    if live is None:
        return 0
    dead = [r["trace_id"] for r in _dead_pane_rows(rows)
            if r["trace_id"] not in live]
    if not dead:
        return 0
    settled = end_trace_session(*dead, reason=reason)
    if settled:
        log.write("bridge_tmux_sessions_settled", count=len(settled),
                  reason=reason)
    return len(settled)
