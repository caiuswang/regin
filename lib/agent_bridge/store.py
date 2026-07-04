"""Read access to the agent-bridge pane registry (`bridge_panes`).

The slice-1 SessionStart handler
(`hook_manager/handlers/bridge_registry.py`) writes `bridge_panes` through
`lib.orm.engine.get_connection` (raw sqlite3). This module reads the same
table through the same access layer on purpose — splitting one table across
the SQLModel layer and raw sqlite3 would fork its shape. Every write still
lives behind the handler; delivery only reads.
"""

from __future__ import annotations

import sqlite3

from lib.activity_log import get_activity_logger
from lib.orm.engine import get_connection

log = get_activity_logger("agent_bridge")

_REACHABLE_SQL = """
SELECT pane_id, tmux_socket, tmux_server_pid, pane_pid
FROM bridge_panes
WHERE trace_id = ? AND reachable = 1
"""


def get_reachable_pane(trace_id: str) -> dict | None:
    """The bridge-reachable pane identity for a session, or None.

    None when the session never registered, isn't marked reachable, or the
    schema is absent/drifted (table missing, or an old shape lacking a
    column this SELECT names — e.g. `tmux_socket` on a pre-migration DB).
    Callers treat None as "no reachable session" and refuse delivery —
    never an error. This keeps `deliver()`'s no-raise contract on a DB the
    schema-repair path hasn't reached yet.
    """
    if not trace_id:
        return None
    conn = get_connection()
    try:
        row = conn.execute(_REACHABLE_SQL, (trace_id,)).fetchone()
    except sqlite3.OperationalError:
        log.error("bridge_pane_query_failed", trace_id=trace_id, exc_info=True)
        return None
    finally:
        conn.close()
    log.read("bridge_pane_resolved", trace_id=trace_id, found=row is not None)
    return dict(row) if row is not None else None
