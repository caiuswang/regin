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

# A pane hosts one claude at a time, but a row is keyed by trace_id and never
# retired — so a pane the operator keeps open accumulates a row per session
# that ever ran there. Delivery's identity guard cannot separate them: the
# tmux server pid, the `pane_pid` (the pane's shell) and the foreground
# command are all properties of the PANE, unchanged when the next session
# takes it over. Without this, steering any of those older ids reaches
# whoever occupies the pane now — a `/exit` meant for a finished session
# killed a live one. The pane's current occupant is its newest registration:
# the SessionStart hook re-upserts on every turn event, so the live session's
# row keeps moving ahead of the ones it displaced.
_NOT_DISPLACED = """
AND NOT EXISTS (
    SELECT 1 FROM bridge_panes AS newer
    WHERE newer.pane_id = p.pane_id
      AND newer.tmux_server_pid = p.tmux_server_pid
      AND newer.tmux_socket IS p.tmux_socket
      AND newer.trace_id <> p.trace_id
      AND newer.reachable = 1
      AND (newer.updated_at > p.updated_at
           OR (newer.updated_at = p.updated_at AND newer.id > p.id))
)
"""

_REACHABLE_SQL = f"""
SELECT pane_id, tmux_socket, tmux_server_pid, pane_pid
FROM bridge_panes AS p
WHERE trace_id = ? AND reachable = 1
{_NOT_DISPLACED}
"""

_INSERT_MESSAGE_SQL = """
INSERT INTO bridge_messages (trace_id, body, sender, is_test, kind, state)
VALUES (?, ?, ?, ?, ?, ?)
"""

_PENDING_STEERS_SQL = """
SELECT id, body, created_at, delivered_at
FROM bridge_messages
WHERE trace_id = ? AND kind = 'steer' AND state = 'pending' AND delivered = 1
ORDER BY created_at ASC, id ASC
"""

_SETTLE_STEERS_SQL = """
UPDATE bridge_messages
SET state = ?, state_at = datetime('now')
WHERE id IN ({ids}) AND state = 'pending'
"""

_DISMISS_STEER_SQL = """
UPDATE bridge_messages
SET state = 'dismissed', state_at = datetime('now')
WHERE id = ? AND trace_id = ? AND kind = 'steer' AND state = 'pending'
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

_REACHABLE_SESSIONS_SQL = f"""
SELECT trace_id, pane_id, cwd, tmux_socket, updated_at
FROM bridge_panes AS p
WHERE reachable = 1
{_NOT_DISPLACED}
ORDER BY updated_at DESC
"""

_LATEST_TRACE_SQL = """
SELECT trace_id
FROM bridge_panes
WHERE reachable = 1
ORDER BY updated_at DESC, id DESC
LIMIT 1
"""

_PANE_CWD_SQL = """
SELECT cwd
FROM bridge_panes
WHERE trace_id = ?
ORDER BY updated_at DESC, id DESC
LIMIT 1
"""


def get_reachable_pane(trace_id: str) -> dict | None:
    """The bridge-reachable pane identity for a session, or None.

    None when the session never registered, isn't marked reachable, has been
    displaced from its pane by a session that registered there later, or the
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


def record_bridge_message(trace_id: str, body: str, sender: str | None,
                          kind: str = "steer", pending: bool = False) -> int:
    """Append an inbox row for a steering message and return its id.

    The VIEW (not this store) calls `delivery.deliver` next and then
    `mark_delivered` — keeping delivery out of the store avoids a
    store→delivery import cycle. Rows created under a truthy REGIN_TRACE_TEST
    are stamped is_test=1 so synthetic inbox rows are distinguishable from
    real steering traffic (matching how trace/agent_messages stamp tests).

    `kind` is what the row is (steer | answer | decision); `pending` opts the
    row into the /live chip lifecycle. Only a tmux-delivered steer should pass
    pending=True — an answer or decision is audit-only, and an SDK-tier steer
    is displayed from the runner's own queue, so all of those are born
    `closed` and can never linger as chips.
    """
    is_test = 1 if _env_truthy("REGIN_TRACE_TEST") else 0
    state = "pending" if pending and kind == "steer" else "closed"
    conn = get_connection()
    try:
        cursor = conn.execute(_INSERT_MESSAGE_SQL,
                              (trace_id, body, sender, is_test, kind, state))
        conn.commit()
        row_id = cursor.lastrowid
    finally:
        conn.close()
    log.write("bridge_message_recorded", trace_id=trace_id, row_id=row_id,
              kind=kind)
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


def list_pending_steers(trace_id: str) -> list[dict]:
    """Delivered steer rows still in chip state `pending`, oldest first —
    the /live card's bridge-tier queue. [] on a pre-migration DB (columns
    absent), same fail-closed contract as the other reads."""
    if not trace_id:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(_PENDING_STEERS_SQL, (trace_id,)).fetchall()
    except sqlite3.OperationalError:
        log.error("bridge_pending_steers_failed", trace_id=trace_id,
                  exc_info=True)
        return []
    finally:
        conn.close()
    log.read("bridge_pending_steers_listed", trace_id=trace_id,
             count=len(rows))
    return [dict(r) for r in rows]


def settle_steers(row_ids: list[int], state: str) -> None:
    """Advance pending steer rows to `consumed` or `closed`. One-way and
    idempotent: a row that already left `pending` is untouched, so the poll
    path may re-run this freely."""
    if not row_ids or state not in ("consumed", "closed"):
        return
    ids = ",".join("?" for _ in row_ids)
    conn = get_connection()
    try:
        conn.execute(_SETTLE_STEERS_SQL.format(ids=ids),
                     (state, *row_ids))
        conn.commit()
    except sqlite3.OperationalError:
        log.error("bridge_settle_steers_failed", exc_info=True)
        return
    finally:
        conn.close()
    log.write("bridge_steers_settled", count=len(row_ids), state=state)


def dismiss_steer(trace_id: str, row_id: int) -> bool:
    """Operator-removed chip → `dismissed`. False when the row is not this
    session's, not a steer, or already left `pending` — an ordinary refusal
    (the chip the operator saw was a poll out of date), never an error."""
    conn = get_connection()
    try:
        cursor = conn.execute(_DISMISS_STEER_SQL, (row_id, trace_id))
        conn.commit()
        dismissed = cursor.rowcount > 0
    except sqlite3.OperationalError:
        log.error("bridge_dismiss_steer_failed", trace_id=trace_id,
                  exc_info=True)
        return False
    finally:
        conn.close()
    log.write("bridge_steer_dismissed", trace_id=trace_id, row_id=row_id,
              dismissed=dismissed)
    return dismissed


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


def get_pane_cwd(trace_id: str) -> str | None:
    """The starting cwd registered for a session's pane, or None.

    None when the session never registered, has no recorded cwd, or the
    registry is absent/drifted — same fail-closed contract as the other
    reads. Callers fall back to the regin project root.
    """
    if not trace_id:
        return None
    conn = get_connection()
    try:
        row = conn.execute(_PANE_CWD_SQL, (trace_id,)).fetchone()
    except sqlite3.OperationalError:
        log.error("bridge_pane_cwd_failed", trace_id=trace_id, exc_info=True)
        return None
    finally:
        conn.close()
    cwd = row["cwd"] if row is not None else None
    log.read("bridge_pane_cwd_resolved", trace_id=trace_id, found=bool(cwd))
    return cwd or None


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
