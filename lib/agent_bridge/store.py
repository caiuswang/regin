"""Read access to the agent-bridge pane registry (`bridge_panes`).

The slice-1 SessionStart handler
(`hook_manager/handlers/bridge_registry.py`) writes `bridge_panes` through
`lib.orm.engine.get_connection` (raw sqlite3). This module reads the same
table through the same access layer on purpose — splitting one table across
the SQLModel layer and raw sqlite3 would fork its shape.

Registration stays the handler's alone. Retirement does not: a claim also
stops being true at moments no hook of that session's can observe — the SDK
tier taking the conversation over from a terminal that is already gone. So
`retire_pane` lives here, and the handler's SessionEnd path calls it rather
than keeping a second copy of the same UPDATE.
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
# killed a live one. The pane's current occupant is therefore its newest
# registration that is still RUNNING — liveness, not recency alone.
#
# Recency alone was the original rule, on the theory that "the live session's
# row keeps moving ahead" because turn events re-upsert it. That is false for
# an idle session: `handle_turn` fires on prompt/tool events, so a session
# parked waiting for the operator emits nothing, and a `claude` launch that
# starts and exits in two seconds in the same pane (a script, a stray
# invocation) would out-rank it forever — stripping the /live composer off the
# session actually sitting there, with no way to get it back. SessionEnd now
# retires a claim (`reachable=0`); the `sessions` join is the backstop for a
# session killed before it could fire one.
_NOT_DISPLACED = """
AND NOT EXISTS (
    SELECT 1 FROM bridge_panes AS newer
    LEFT JOIN sessions AS s ON s.trace_id = newer.trace_id
    WHERE newer.pane_id = p.pane_id
      AND newer.tmux_server_pid = p.tmux_server_pid
      AND newer.tmux_socket IS p.tmux_socket
      AND newer.trace_id <> p.trace_id
      AND newer.reachable = 1
      AND s.ended_at IS NULL
      AND (newer.updated_at > p.updated_at
           OR (newer.updated_at = p.updated_at AND newer.id > p.id))
)
"""

# An ended session never occupies a pane, whatever its row still says. Paired
# with `_NOT_DISPLACED`: that clause alone would let a dead claimant AND the
# live session both resolve to the same pane, and steering the dead one's card
# would type into the live one — the exact swap this module exists to prevent.
# A trace with no `sessions` row is treated as live: refusing to steer is
# cheaper than typing into someone else's terminal.
_SELF_NOT_ENDED = """
AND NOT EXISTS (
    SELECT 1 FROM sessions AS own
    WHERE own.trace_id = p.trace_id AND own.ended_at IS NOT NULL
)
"""

_REACHABLE_SQL = f"""
SELECT pane_id, tmux_socket, tmux_server_pid, pane_pid
FROM bridge_panes AS p
WHERE trace_id = ? AND reachable = 1
{_NOT_DISPLACED}
{_SELF_NOT_ENDED}
"""

# One hop of the `/clear` chain: the registration that came NEXT on this
# session's pane, and whether it is still live.
#
# Two things make the hop safe to follow, and the guard needs both. `own`'s
# `ended_reason = 'clear'` says this session was retired BY whatever took the
# pane from it — every other ending (logout, prompt_input_exit, a kill that
# never fired the hook) resolves to no successor at all. And `n` must be the
# very next claim on the pane, which is why `id` orders this rather than the
# `updated_at` occupancy runs on: a pane an operator reuses accumulates a row
# per session that ever ran in it, and `updated_at` moves — a live session's
# turn events push its row ahead of rows that registered after it. Ordering
# the chain by a column that reorders itself is how the walk would step over
# the real heir and land on a stranger.
_CLEAR_HOP_SQL = """
SELECT n.trace_id, n.reachable, s.ended_at, s.ended_reason
FROM bridge_panes AS p
JOIN sessions AS own ON own.trace_id = p.trace_id
JOIN bridge_panes AS n
  ON n.pane_id = p.pane_id
 AND n.tmux_server_pid = p.tmux_server_pid
 AND n.tmux_socket IS p.tmux_socket
 AND n.id > p.id
LEFT JOIN sessions AS s ON s.trace_id = n.trace_id
WHERE p.trace_id = ?
  AND own.ended_reason = 'clear'
ORDER BY n.id ASC
LIMIT 1
"""

# A pane holds one claude at a time, so a chain of clears is short. The cap is
# a guard against a malformed registry, not a real depth — a walk that has
# taken this many hops is reading a loop, not a conversation history.
_MAX_CLEAR_HOPS = 16

# The reverse of `_CLEAR_HOP_SQL`: the `/clear`ed session whose pane this one
# took over. Asked by the registration side, which knows only that it just
# claimed a pane and needs to name the session that page-follows onto it.
#
# `id DESC` for the NEAREST earlier claim, not the earliest — the chain is
# walked forward from whatever this returns, and handing the forward walk a
# distant ancestor would make it re-derive hops it can already reach. A row
# that is not the immediate predecessor simply fails that walk, which is the
# fail-closed direction.
_CLEARED_PREDECESSOR_SQL = """
SELECT p.trace_id
FROM bridge_panes AS n
JOIN bridge_panes AS p
  ON p.pane_id = n.pane_id
 AND p.tmux_server_pid = n.tmux_server_pid
 AND p.tmux_socket IS n.tmux_socket
 AND p.id < n.id
JOIN sessions AS own ON own.trace_id = p.trace_id
WHERE n.trace_id = ?
  AND own.ended_reason = 'clear'
ORDER BY p.id DESC
LIMIT 1
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

_DROPPED_PREFIX = 'dropped: '

# `mark_dropped` is terminal, and the two writers race by construction: the
# runner reports an abandonment from its own loop while the request thread is
# still on its way to stamping the ack. Excluding an already-dropped row here
# is what makes the outcome order-independent — the drop is the truth about a
# prompt that will never run, whichever write lands second.
_MARK_DELIVERED_SQL = f"""
UPDATE bridge_messages
SET delivered = ?, delivery_detail = ?, delivery_path = 'tmux',
    delivered_at = datetime('now')
WHERE id = ? AND COALESCE(delivery_detail, '') NOT LIKE '{_DROPPED_PREFIX}%'
"""

# `state` stays `pending` so the existing dismiss route can retire the chip
# this row now earns; `delivered = 0` is what keeps it out of the delivered-only
# `_PENDING_STEERS_SQL` feed, so the two never serve the same row twice.
_MARK_DROPPED_SQL = """
UPDATE bridge_messages
SET delivered = 0, delivery_detail = ?, state = 'pending',
    state_at = datetime('now')
WHERE id = ?
"""

_DROPPED_STEERS_SQL = f"""
SELECT id, body, delivery_detail, created_at
FROM bridge_messages
WHERE trace_id = ? AND kind = 'steer' AND state = 'pending'
  AND delivered = 0 AND delivery_detail LIKE '{_DROPPED_PREFIX}%'
ORDER BY created_at ASC, id ASC
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
{_SELF_NOT_ENDED}
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


def resolve_reachable_pane(trace_id: str) -> tuple[dict | None, bool]:
    """`(row, answered)` for a session's bridge-reachable pane identity.

    `answered` is False only when the store could not be *asked* — a locked
    DB, a missing table, an old shape lacking a column this SELECT names
    (`tmux_socket` on a pre-migration DB). That is a different fact from a
    clean read that found nothing, and collapsing the two is how a failed
    query comes to mean "no session is running here": a caller deciding
    whether it may report a session stopped needs the distinction, even
    though a caller deciding whether to type into a pane does not.
    """
    if not trace_id:
        return None, True
    conn = get_connection()
    try:
        row = conn.execute(_REACHABLE_SQL, (trace_id,)).fetchone()
    except sqlite3.OperationalError:
        log.error("bridge_pane_query_failed", trace_id=trace_id, exc_info=True)
        return None, False
    finally:
        conn.close()
    log.read("bridge_pane_resolved", trace_id=trace_id, found=row is not None)
    return (dict(row) if row is not None else None), True


def get_reachable_pane(trace_id: str) -> dict | None:
    """The bridge-reachable pane identity for a session, or None.

    None when the session never registered, isn't marked reachable, has been
    displaced from its pane by a session that registered there later, or the
    schema is absent/drifted. Callers treat None as "no reachable session"
    and refuse delivery — never an error. This keeps `deliver()`'s no-raise
    contract on a DB the schema-repair path hasn't reached yet, which is why
    this fail-closed view survives alongside `resolve_reachable_pane`.
    """
    return resolve_reachable_pane(trace_id)[0]


_RETIRE_SQL = """
UPDATE bridge_panes SET reachable = 0, updated_at = datetime('now')
WHERE trace_id = ? AND reachable = 1
"""


def retire_pane(trace_id: str | None) -> int:
    """Flip `reachable` off for `trace_id`. Returns how many rows changed.

    The row is retained, not deleted: it is the audit trail of which session
    held which pane, and `clear_successor` walks those rows to follow a
    `/clear` chain through a terminal an operator kept open. Deleting one
    would break a succession the operator can still see on the page.

    Never raises on a DB the schema-repair path hasn't reached — the callers
    are a hook that must not fail a session's teardown and a launch that must
    not be refused over a registry it does not need.
    """
    if not trace_id:
        return 0
    conn = get_connection()
    try:
        retired = conn.execute(_RETIRE_SQL, (trace_id,)).rowcount
        conn.commit()
    except sqlite3.OperationalError:
        log.error("bridge_pane_retire_failed", trace_id=trace_id, exc_info=True)
        return 0
    finally:
        conn.close()
    if retired:
        log.write("bridge_pane_retired", trace_id=trace_id)
    return retired


def _clear_hop(conn, trace_id: str) -> dict | None:
    """The next claim on `trace_id`'s pane, when `trace_id` ended by `/clear`."""
    row = conn.execute(_CLEAR_HOP_SQL, (trace_id,)).fetchone()
    return dict(row) if row is not None else None


def clear_successor(trace_id: str) -> str | None:
    """The session `/clear` opened in this one's pane, or None.

    The tmux-tier answer to `agent_sdk.live_child`: a `/clear` ends the
    conversation and starts another in the same terminal, so the id the
    operator is holding names a session that is over while the agent keeps
    working under one nothing on the page points at.

    Walked one pane hop at a time rather than jumped to the pane's current
    occupant, because "who is in my pane now" is not the same question as "who
    did my `/clear` open". A terminal an operator keeps open outlives the whole
    chain: once the heir itself exits and some later `claude` claims the pane,
    the newest occupant is a stranger, and the stale trace page that would
    steer into it is exactly the artifact that survives those hours. So the
    walk follows only unbroken `/clear` links and stops dead at the first
    ending that was something else.

    None whenever the succession is not provable — this session did not end by
    `/clear`, the chain reaches a session that ended some other way or was
    retired from the pane, or the schema is absent/drifted. Fail-closed like
    `get_reachable_pane`, and for a sharper reason: the caller uses this to
    pick which live terminal to type into, so a wrong answer is a keystroke in
    a stranger's session, not a missing feature.
    """
    if not trace_id:
        return None
    conn = get_connection()
    try:
        candidate = _walk_clear_chain(conn, trace_id)
        # The walk says who the chain leads to; occupancy says who is in the
        # pane. Both must agree. They can differ because a row's `id` records
        # when the SESSION first registered anywhere, not when it claimed THIS
        # pane — a session that resumes into another pane carries a stale id
        # into that pane's chain and can be stepped onto out of order. Making
        # the walk's answer pass `_NOT_DISPLACED` too costs one query and
        # turns every such misread into a refusal instead of a wrong pane.
        successor = candidate if (
            candidate and conn.execute(
                _REACHABLE_SQL, (candidate,)).fetchone() is not None) else None
    except sqlite3.OperationalError:
        log.error("bridge_clear_successor_failed", trace_id=trace_id,
                  exc_info=True)
        return None
    finally:
        conn.close()
    log.read("bridge_clear_successor_resolved", trace_id=trace_id,
             successor=successor)
    return successor


def _walk_clear_chain(conn, trace_id: str) -> str | None:
    """Follow `/clear` links forward from `trace_id` to the live session, if
    any. A dead heir continues the walk only when it too ended by `/clear`;
    anything else ends it with no successor."""
    seen = {trace_id}
    current = trace_id
    for _ in range(_MAX_CLEAR_HOPS):
        heir = _clear_hop(conn, current)
        if heir is None or heir["trace_id"] in seen:
            return None
        current = heir["trace_id"]
        seen.add(current)
        # An unknown session is treated as live, the same way `_NOT_DISPLACED`
        # does: refusing to steer costs a composer, guessing "dead" and walking
        # past it costs a keystroke in someone else's terminal.
        if heir["ended_at"] is None:
            return current if heir["reachable"] else None
        if heir["ended_reason"] != "clear":
            return None
    return None


def cleared_predecessor(trace_id: str) -> str | None:
    """The `/clear`ed session whose pane `trace_id` just took over, or None.

    The inverse of `clear_successor`, and the question the REGISTRATION side
    can answer: a session starting in a pane knows its own id and nothing
    about what it replaced. The caller announces succession by walking
    `clear_successor` FORWARD from what this returns, so every guard on that
    walk — the unbroken-`/clear` chain, the occupancy cross-check — still
    decides the answer. This only supplies a starting point.

    None whenever no earlier claim on the pane ended by `/clear`, or the
    schema is absent/drifted. Fail-closed like every read here.
    """
    if not trace_id:
        return None
    conn = get_connection()
    try:
        row = conn.execute(_CLEARED_PREDECESSOR_SQL, (trace_id,)).fetchone()
    except sqlite3.OperationalError:
        log.error("bridge_cleared_predecessor_failed", trace_id=trace_id,
                  exc_info=True)
        return None
    finally:
        conn.close()
    return row["trace_id"] if row is not None else None


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


def mark_dropped(row_id: int, reason: str) -> None:
    """Record that a prompt this row acked was abandoned before it ran.

    The operator's only copy of the text was the composer draft, which the
    send's ack already cleared — so the row is the last place it survives, and
    it has to stop reading `delivered`."""
    conn = get_connection()
    try:
        conn.execute(_MARK_DROPPED_SQL, (f"{_DROPPED_PREFIX}{reason}", row_id))
        conn.commit()
    finally:
        conn.close()
    log.error("bridge_message_dropped", row_id=row_id, reason=reason)


def list_dropped_steers(trace_id: str) -> list[dict]:
    """Prompts abandoned after their send was acked, oldest first — the
    operator's only remaining copy of that text. [] on a pre-migration DB."""
    if not trace_id:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(_DROPPED_STEERS_SQL, (trace_id,)).fetchall()
    except sqlite3.OperationalError:
        log.error("bridge_dropped_steers_failed", trace_id=trace_id,
                  exc_info=True)
        return []
    finally:
        conn.close()
    log.read("bridge_dropped_steers_listed", trace_id=trace_id,
             count=len(rows))
    return [dict(r) for r in rows]


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


def list_tmux_sockets() -> list[str | None]:
    """Distinct tmux sockets the registry has ever seen, newest row first.

    The batch screen read needs the set of tmux servers regin's sessions run
    on; a NULL socket (the default server) is a legitimate member, so the
    list is of `str | None`, not of paths.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT tmux_socket, MAX(rowid) AS r FROM bridge_panes "
            "GROUP BY tmux_socket ORDER BY r DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        log.error("bridge_sockets_query_failed", exc_info=True)
        return []
    finally:
        conn.close()
    log.read("bridge_sockets_listed", count=len(rows))
    return [r["tmux_socket"] for r in rows]
