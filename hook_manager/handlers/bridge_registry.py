"""Handler: agent-bridge pane registry.

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
- the pane's foreground command is not in
  `settings.agent_bridge.allowed_pane_commands` → no row, logged refusal
  (`bridge_pane_register_refused`, naming the observed command). Delivery
  would refuse such a pane anyway; a row for it only buys a `/live` steer
  composer that fails at send time. Known limitation: a claude launched
  behind a tty-holding wrapper (`caffeinate …`, `script`, a supervisor)
  reports the wrapper as the foreground command and is refused here, so it
  gets no composer at all rather than one that fails on send — the log line
  is what makes that diagnosable. Widening
  `allowed_pane_commands` is the operator's escape hatch.
- Resume fires SessionStart again → UPSERT keyed on trace_id overwrites
  ALL mutable columns, so one row per session always holds the freshest
  coordinates (a partial merge could leave stale identity behind).

Registration fires on SessionStart AND self-heals on the session's turn
events (`handle_turn`, wired to UserPromptSubmit/PreToolUse). SessionStart
is a single shot with silent fail-soft returns, so a session whose pane
query blipped, whose DB write raced, or that only became bridge-capable
after launch would otherwise lose the /live steer composer permanently
with no recovery short of a re-`clear`. Re-running the idempotent
registration on the next turn recovers it. The `(trace_id, pane_id)` dedup
spans one process only, and every hook event runs as a fresh process
(`python -m hook_manager <Event>`), so in production it never hits: the
heal costs one tmux query — bounded by the timeout below — plus the UPSERT
on every turn event. An env-less session (no REGIN_BRIDGE / not in tmux)
stays a pure no-op on every turn, same as SessionStart.

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

# (trace_id, pane_id) pairs registered in THIS process — lets the turn-event
# self-heal skip a redundant tmux query once the pane is already recorded,
# while still catching a resume that moved the session to a new pane.
_registered_panes: set[tuple[str, str]] = set()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bridge_panes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL UNIQUE,
    pane_id         TEXT NOT NULL,
    tmux_server_pid INTEGER NOT NULL,
    pane_pid        INTEGER NOT NULL,
    tmux_socket     TEXT,
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
    (trace_id, pane_id, tmux_server_pid, pane_pid, tmux_socket, reachable, cwd,
     created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
ON CONFLICT(trace_id) DO UPDATE SET
    pane_id         = excluded.pane_id,
    tmux_server_pid = excluded.tmux_server_pid,
    pane_pid        = excluded.pane_pid,
    tmux_socket     = excluded.tmux_socket,
    reachable       = excluded.reachable,
    cwd             = excluded.cwd,
    updated_at      = excluded.updated_at
"""


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def handle_start(payload: HookPayload) -> HookResponse | None:
    try:
        _register_pane(payload, healing=False)
    except Exception:
        _log_register_failure(payload)
    return HookResponse(suppress_output=True)


def handle_turn(payload: HookPayload) -> HookResponse | None:
    """Self-heal the SessionStart one-shot on a session's turn events.

    A pure no-op when the pane is already registered this process, or when
    the env is bridge-less (same fast returns as SessionStart)."""
    try:
        if _already_registered(payload):
            return HookResponse(suppress_output=True)
        _register_pane(payload, healing=True)
    except Exception:
        _log_register_failure(payload)
    return HookResponse(suppress_output=True)


def _already_registered(payload: HookPayload) -> bool:
    pane_id = (os.environ.get('TMUX_PANE') or '').strip()
    trace_id = payload.session_id or ''
    return bool(pane_id and trace_id) and (trace_id, pane_id) in _registered_panes


def _log_register_failure(payload: HookPayload) -> None:
    try:
        from lib.activity_log import get_activity_logger
        get_activity_logger('agent_bridge').error(
            'bridge_pane_register_failed',
            trace_id=payload.session_id, exc_info=True,
        )
    except Exception:
        pass


def _register_pane(payload: HookPayload, *, healing: bool) -> bool:
    # Guard order is pinned: flag → pane env → tmux query → command
    # allowlist → upsert. With the flag off this must be a pure no-op (no
    # subprocess, no row).
    if not _env_truthy('REGIN_BRIDGE'):
        return False
    pane_id = (os.environ.get('TMUX_PANE') or '').strip()
    if not pane_id:
        return False
    trace_id = payload.session_id
    if not trace_id:
        return False
    identity = _query_pane_identity(pane_id)
    if identity is None:
        return False
    server_pid, pane_pid, command = identity
    if not _command_allowed(command):
        _log_command_refusal(trace_id, pane_id, command, healing)
        return False
    # $TMUX = "<socket_path>,<server_pid>,<session_id>"; the first
    # comma-field is the absolute socket path. NULL when outside tmux or on
    # the default socket — delivery threads a non-NULL value into every
    # tmux call and omits -S when NULL.
    tmux_socket = (os.environ.get('TMUX') or '').split(',')[0] or None
    _upsert_pane(trace_id, pane_id, server_pid, pane_pid, tmux_socket,
                 payload.cwd)
    _registered_panes.add((trace_id, pane_id))
    return True


def _query_pane_identity(pane_id: str) -> tuple[int, int, str] | None:
    """One timeout-guarded tmux call → (server pid, pane pid, foreground
    command), or None.

    Any failure shape — tmux missing, dead socket, pane gone, timeout,
    garbled output — resolves to None; the caller records nothing.
    """
    try:
        proc = subprocess.run(
            ['tmux', 'display-message', '-p', '-t', pane_id,
             '#{pid}\t#{pane_pid}\t#{pane_current_command}'],
            capture_output=True, text=True,
            timeout=_TMUX_QUERY_TIMEOUT_SEC,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    # Only the line ending is stripped: a blank command field is a refusal
    # to report, not a malformed line, and `.strip()` would eat the tab
    # that carries it.
    parts = (proc.stdout or '').strip('\r\n').split('\t')
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), parts[2].strip()
    except ValueError:
        return None


def _command_allowed(command: str) -> bool:
    from lib.settings import settings
    return command in settings.agent_bridge.allowed_pane_commands


def _log_command_refusal(trace_id: str, pane_id: str, command: str,
                         healing: bool) -> None:
    # INFO once per session from SessionStart; the turn heal re-derives the
    # same refusal on every prompt AND every tool call, so it drops to DEBUG
    # rather than writing a flock-serialized record per event forever.
    try:
        from lib.activity_log import get_activity_logger
        log = get_activity_logger('agent_bridge')
        emit = log.read if healing else log.write
        emit('bridge_pane_register_refused',
             trace_id=trace_id, pane_id=pane_id, pane_command=command,
             reason='command_not_allowlisted')
    except Exception:
        pass


def _column_exists(conn, table: str, column: str) -> bool:
    return any(row[1] == column
               for row in conn.execute(f"PRAGMA table_info({table})"))


def ensure_schema() -> None:
    """Create `bridge_panes` if this DB predates the agent bridge, and
    backfill `tmux_socket` on tables created before that column landed.

    `CREATE TABLE IF NOT EXISTS` is a no-op on a pre-existing table, so an
    upgraded install (or the live `db/regin.db` from slice 1) needs the
    additive ALTER — otherwise the socket-aware UPSERT/SELECT hit
    `OperationalError: no column named tmux_socket`. Same DDL as
    `db/schema.sql` (fresh installs); a new column also needs an alembic
    revision under `alembic/versions/` — keep them in sync.
    """
    global _schema_ready
    if _schema_ready:
        return
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.execute(_SCHEMA_SQL)
        if not _column_exists(conn, "bridge_panes", "tmux_socket"):
            conn.execute("ALTER TABLE bridge_panes ADD COLUMN tmux_socket TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bridge_panes_reachable "
                     "ON bridge_panes(reachable)")
        conn.commit()
    finally:
        conn.close()
    _schema_ready = True


def _upsert_pane(trace_id: str, pane_id: str, server_pid: int,
                 pane_pid: int, tmux_socket: str | None,
                 cwd: str | None) -> None:
    ensure_schema()
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        # reachable=1: rows written via the REGIN_BRIDGE opt-in are
        # bridge-reachable; later slices may flip the column off without
        # deleting the identity row.
        conn.execute(_UPSERT_SQL,
                     (trace_id, pane_id, server_pid, pane_pid, tmux_socket,
                      1, cwd))
        conn.commit()
    finally:
        conn.close()
    from lib.activity_log import get_activity_logger
    get_activity_logger('agent_bridge').write(
        'bridge_pane_registered',
        trace_id=trace_id, pane_id=pane_id,
        tmux_server_pid=server_pid, pane_pid=pane_pid,
        tmux_socket=tmux_socket,
    )
