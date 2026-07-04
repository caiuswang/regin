"""Handler: SessionStart agent-bridge pane registry (slice 1).

Records the session's tmux pane identity triple — pane id (`$TMUX_PANE`),
tmux server pid (`#{pid}`), pane shell pid (`#{pane_pid}`) — so the bridge
delivery engine (later slices) can resolve `session → pane` exactly and
refuse stale ids recycled by a tmux server restart (see
`docs/agent-bridge-design.md`, *Pane identity and staleness*).

Opt-in and fail-soft by design:

- `REGIN_BRIDGE` not truthy → immediate no-op: no subprocess, no row.
- `$TMUX_PANE` absent (not inside tmux) → no row, no error.
- tmux query fails or times out (~2s guard — SessionStart is on the
  interactive startup path; a hung tmux socket must not stall launch)
  → no row, no error.
- Resume fires SessionStart again → UPSERT keyed on trace_id overwrites
  ALL mutable columns, so one row per session always holds the freshest
  coordinates (a partial merge could leave stale identity behind).

Env is read from `os.environ` — handler processes see the full
environment; the hook payload does not carry env.
"""

from __future__ import annotations

import os
import subprocess

from ..core import HookPayload, HookResponse

# SessionStart runs on the interactive startup path; never wait on a hung
# tmux socket longer than this.
_TMUX_QUERY_TIMEOUT_SEC = 2.0

_schema_ready = False

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bridge_panes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL UNIQUE,
    pane_id         TEXT NOT NULL,
    tmux_server_pid INTEGER NOT NULL,
    pane_pid        INTEGER NOT NULL,
    reachable       INTEGER NOT NULL DEFAULT 0,
    cwd             TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# Overwrites ALL mutable columns on conflict: a resume re-registration must
# never inherit stale coordinates from a prior tmux server lifetime.
_UPSERT_SQL = """
INSERT INTO bridge_panes
    (trace_id, pane_id, tmux_server_pid, pane_pid, reachable, cwd,
     created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
ON CONFLICT(trace_id) DO UPDATE SET
    pane_id         = excluded.pane_id,
    tmux_server_pid = excluded.tmux_server_pid,
    pane_pid        = excluded.pane_pid,
    reachable       = excluded.reachable,
    cwd             = excluded.cwd,
    updated_at      = excluded.updated_at
"""


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def handle_start(payload: HookPayload) -> HookResponse | None:
    try:
        _register_pane(payload)
    except Exception:
        try:
            from lib.activity_log import get_activity_logger
            get_activity_logger('agent_bridge').error(
                'bridge_pane_register_failed',
                trace_id=payload.session_id, exc_info=True,
            )
        except Exception:
            pass
    return HookResponse(suppress_output=True)


def _register_pane(payload: HookPayload) -> None:
    # Guard order is pinned: flag → pane env → tmux query → upsert. With
    # the flag off this must be a pure no-op (no subprocess, no row).
    if not _env_truthy('REGIN_BRIDGE'):
        return
    pane_id = (os.environ.get('TMUX_PANE') or '').strip()
    if not pane_id:
        return
    trace_id = payload.session_id
    if not trace_id:
        return
    identity = _query_pane_identity(pane_id)
    if identity is None:
        return
    server_pid, pane_pid = identity
    _upsert_pane(trace_id, pane_id, server_pid, pane_pid, payload.cwd)


def _query_pane_identity(pane_id: str) -> tuple[int, int] | None:
    """One timeout-guarded tmux call → (server pid, pane pid), or None.

    Any failure shape — tmux missing, dead socket, pane gone, timeout,
    garbled output — resolves to None; the caller records nothing.
    """
    try:
        proc = subprocess.run(
            ['tmux', 'display-message', '-p', '-t', pane_id,
             '#{pid}\t#{pane_pid}'],
            capture_output=True, text=True,
            timeout=_TMUX_QUERY_TIMEOUT_SEC,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    parts = (proc.stdout or '').strip().split('\t')
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def ensure_schema() -> None:
    """Create `bridge_panes` if this DB predates the agent bridge.

    Same DDL as `db/schema.sql` (fresh installs) and
    `web/startup.py:init_bridge_panes_schema` (serve startup) — keep all
    three in sync.
    """
    global _schema_ready
    if _schema_ready:
        return
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.execute(_SCHEMA_SQL)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bridge_panes_reachable "
                     "ON bridge_panes(reachable)")
        conn.commit()
    finally:
        conn.close()
    _schema_ready = True


def _upsert_pane(trace_id: str, pane_id: str, server_pid: int,
                 pane_pid: int, cwd: str | None) -> None:
    ensure_schema()
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        # reachable=1: rows written via the REGIN_BRIDGE opt-in are
        # bridge-reachable; later slices may flip the column off without
        # deleting the identity row.
        conn.execute(_UPSERT_SQL,
                     (trace_id, pane_id, server_pid, pane_pid, 1, cwd))
        conn.commit()
    finally:
        conn.close()
    from lib.activity_log import get_activity_logger
    get_activity_logger('agent_bridge').write(
        'bridge_pane_registered',
        trace_id=trace_id, pane_id=pane_id,
        tmux_server_pid=server_pid, pane_pid=pane_pid,
    )
