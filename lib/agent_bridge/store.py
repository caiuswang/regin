"""Read access to the agent-bridge pane registry (`bridge_panes`).

The slice-1 SessionStart handler
(`hook_manager/handlers/bridge_registry.py`) writes `bridge_panes` through
`lib.orm.engine.get_connection` (raw sqlite3). This module reads the same
table through the same access layer on purpose — splitting one table across
the SQLModel layer and raw sqlite3 would fork its shape. Every write still
lives behind the handler; delivery only reads.
"""

from __future__ import annotations

import os
import sqlite3

from lib.activity_log import get_activity_logger
from lib.orm.engine import get_connection

log = get_activity_logger("agent_bridge")


def _env_truthy(name: str) -> bool:
    """Mirror the hook-side idiom (bridge_registry._env_truthy)."""
    return (os.environ.get(name) or '').strip().lower() in {
        '1', 'true', 'yes', 'on'}

_REACHABLE_SQL = """
SELECT pane_id, tmux_socket, tmux_server_pid, pane_pid
FROM bridge_panes
WHERE trace_id = ? AND reachable = 1
"""

_INSERT_MESSAGE_SQL = """
INSERT INTO bridge_messages (trace_id, body, sender, is_test)
VALUES (?, ?, ?, ?)
"""

_MARK_DELIVERED_SQL = """
UPDATE bridge_messages
SET delivered = ?, delivery_detail = ?, delivery_path = 'tmux',
    delivered_at = datetime('now')
WHERE id = ?
"""

_LIST_MESSAGES_SQL = """
SELECT id, trace_id, body, sender, delivered, delivery_detail,
       delivery_path, created_at, delivered_at
FROM bridge_messages
{where}
ORDER BY created_at DESC, id DESC
LIMIT ?
"""

_REACHABLE_SESSIONS_SQL = """
SELECT trace_id, pane_id, cwd, tmux_socket, updated_at
FROM bridge_panes
WHERE reachable = 1
ORDER BY updated_at DESC
"""

_LATEST_TRACE_SQL = """
SELECT trace_id
FROM bridge_panes
WHERE reachable = 1
ORDER BY updated_at DESC, id DESC
LIMIT 1
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


def record_bridge_message(trace_id: str, body: str, sender: str | None) -> int:
    """Append an inbox row for a steering message and return its id.

    The VIEW (not this store) calls `delivery.deliver` next and then
    `mark_delivered` — keeping delivery out of the store avoids a
    store→delivery import cycle. Rows created under a truthy REGIN_TRACE_TEST
    are stamped is_test=1 so synthetic inbox rows are distinguishable from
    real steering traffic (matching how trace/agent_messages stamp tests).
    """
    is_test = 1 if _env_truthy("REGIN_TRACE_TEST") else 0
    conn = get_connection()
    try:
        cursor = conn.execute(_INSERT_MESSAGE_SQL,
                              (trace_id, body, sender, is_test))
        conn.commit()
        row_id = cursor.lastrowid
    finally:
        conn.close()
    log.write("bridge_message_recorded", trace_id=trace_id, row_id=row_id)
    return row_id


def mark_delivered(row_id: int, delivered: bool, detail: str) -> None:
    """Persist the delivery outcome onto an inbox row (path='tmux')."""
    conn = get_connection()
    try:
        conn.execute(_MARK_DELIVERED_SQL,
                     (1 if delivered else 0, detail, row_id))
        conn.commit()
    finally:
        conn.close()
    log.write("bridge_message_delivered", row_id=row_id, delivered=delivered)


def list_bridge_messages(session_id: str | None = None,
                         limit: int = 50) -> list[dict]:
    """Inbox rows newest first, optionally filtered to one trace_id.

    Returns [] on a pre-migration DB (table absent) rather than raising —
    the same fail-closed contract `get_reachable_pane` keeps.

    Defensively clamps `limit` into [1, 200] even though the view already
    floors it: a negative LIMIT is unlimited in SQLite (full-inbox dump), so
    no caller of this store can bypass the cap.
    """
    limit = max(1, min(int(limit), 200))
    where = "WHERE trace_id = ?" if session_id else ""
    params = (session_id, limit) if session_id else (limit,)
    sql = _LIST_MESSAGES_SQL.format(where=where)
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        log.error("bridge_messages_query_failed", exc_info=True)
        return []
    finally:
        conn.close()
    log.read("bridge_messages_listed", count=len(rows), session_id=session_id)
    return [dict(r) for r in rows]


def list_reachable_sessions() -> list[dict]:
    """Bridge-reachable sessions (registry rows), newest-registered first.

    Returns [] on a pre-migration/absent registry rather than raising.
    """
    conn = get_connection()
    try:
        rows = conn.execute(_REACHABLE_SESSIONS_SQL).fetchall()
    except sqlite3.OperationalError:
        log.error("bridge_sessions_query_failed", exc_info=True)
        return []
    finally:
        conn.close()
    log.read("bridge_sessions_listed", count=len(rows))
    return [dict(r) for r in rows]


def resolve_latest_trace_id() -> str | None:
    """The most-recently-registered reachable session's trace_id, or None.

    None when no reachable session exists or the registry is absent/drifted;
    callers treat None as 'no reachable session' and refuse.
    """
    conn = get_connection()
    try:
        row = conn.execute(_LATEST_TRACE_SQL).fetchone()
    except sqlite3.OperationalError:
        log.error("bridge_latest_query_failed", exc_info=True)
        return None
    finally:
        conn.close()
    log.read("bridge_latest_resolved", found=row is not None)
    return row["trace_id"] if row is not None else None
