"""Take over a conversation the CLI holds on a terminal regin cannot reach.

Claude Code's background sessions run under its own daemon on a private PTY.
They own no tmux pane, so the bridge — which steers by typing into a pane —
has nothing to aim at, and the trace page can offer neither a composer nor a
Resume button (see `lib/agent_sdk/resumable.py`).

`claude attach <handle>` is the way in. It is a PTY *client*: the session keeps
its own pid inside the daemon's tree, survives the attaching terminal dying,
and can be attached again afterwards. So running it inside a pane regin owns
puts the conversation on a pane without moving or restarting it, and every
existing bridge verb — `deliver`, `capture_screen`, `live_state`, the identity
guard — then works on it unchanged.

Two things follow from `attach` being a client rather than a re-host, and both
shape this module:

* **The hook cannot register the pane.** Registration reads `$TMUX_PANE` from
  the session's own process, and that process is still the daemon's child with
  no tmux in its environment. So this module writes `bridge_panes` itself —
  which it can do honestly, because it created the pane and knows the mapping.
  It is the only writer besides `hook_manager/handlers/bridge_registry.py`.
* **Nothing here owns the conversation.** Releasing the pane kills the client,
  never the session. That is why `release` is safe to call on any failure.

The pane lives on regin's OWN tmux server (`settings.agent_bridge.
attach_socket`), not the operator's: it has to work when they run no tmux at
all, it must not appear in their window list, and their `kill-server` must not
take regin's panes with it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from lib.activity_log import get_activity_logger
from lib.agent_bridge.pane_io import allowed_pane_commands
from lib.orm.engine import get_connection
from lib.settings import settings

log = get_activity_logger("agent_bridge")

# Bounded like every other tmux call on a request path. Creating a session
# spawns `claude attach`, which connects to a local socket; it is not a
# network call and does not need room to be slow.
_TMUX_TIMEOUT_SEC = 5.0

# The pane is never displayed, but `claude attach` renders a TUI into it and
# `capture_screen` reads that back, so it needs a plausible terminal size.
# Matches the geometry the /live peek already assumes.
_PANE_COLS, _PANE_ROWS = 200, 50
# A pane outlives many releases, and each update check compares the *running*
# version against latest, so a day-old pane reinstalls the global package
# every minute. npm unlinks `claude` from PATH for the length of each install,
# refusing any launch that looks for it in that window.
NO_AUTOUPDATE_ENV = ("-e", "DISABLE_AUTOUPDATER=1")

_UPSERT_SQL = """
INSERT INTO bridge_panes
    (trace_id, pane_id, tmux_server_pid, pane_pid, tmux_socket, reachable, cwd,
     created_at, updated_at)
VALUES (?, ?, ?, ?, ?, 1, ?, datetime('now'), datetime('now'))
ON CONFLICT(trace_id) DO UPDATE SET
    pane_id         = excluded.pane_id,
    tmux_server_pid = excluded.tmux_server_pid,
    pane_pid        = excluded.pane_pid,
    tmux_socket     = excluded.tmux_socket,
    reachable       = 1,
    cwd             = excluded.cwd,
    updated_at      = datetime('now')
"""

_RETIRE_SQL = """
UPDATE bridge_panes SET reachable = 0, updated_at = datetime('now')
WHERE trace_id = ? AND reachable = 1
"""


@dataclass(frozen=True)
class AttachResult:
    attached: bool
    detail: str
    pane_id: str = ""


def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux", "-L", settings.agent_bridge.attach_socket, *args],
        capture_output=True, text=True, timeout=_TMUX_TIMEOUT_SEC)


def _session_name(trace_id: str) -> str:
    """One tmux session per conversation, named for it.

    Named rather than a window in a shared session so releasing one is
    `kill-session -t <name>` — targeted, and impossible to aim at the wrong
    conversation.
    """
    return f"bg-{trace_id[:8]}"


def _pane_identity(name: str) -> tuple[str, int, int, str] | None:
    """`(pane_id, server_pid, pane_pid, foreground command)` for our session."""
    r = _tmux("list-panes", "-t", name, "-F",
              "#{pane_id}\t#{pid}\t#{pane_pid}\t#{pane_current_command}")
    if r.returncode != 0 or not r.stdout.strip():
        return None
    pane_id, server_pid, pane_pid, command = (
        r.stdout.strip().splitlines()[0].split("\t"))
    return pane_id, int(server_pid), int(pane_pid), command


def _handle_for(trace_id: str) -> str | None:
    """The CLI's own name for this conversation, or None if it holds no such
    session — or holds one it will not let a second client open."""
    from lib.providers import get_active_provider

    rows = get_active_provider().live_sessions()
    if rows is None:
        return None
    return rows.get(trace_id) or None


def take_over(trace_id: str) -> AttachResult:
    """Put `trace_id` on a pane regin owns, so the bridge can steer it.

    Idempotent: a conversation already on a live pane of ours is left where it
    is. Every refusal is structured, never an exception — this answers a button
    on the trace page.
    """
    if not settings.agent_bridge.enabled:
        return AttachResult(False, "bridge disabled")
    handle = _handle_for(trace_id)
    if not handle:
        return AttachResult(
            False, "the CLI is not holding this conversation, or will not "
                   "let a second client open it")

    name = _session_name(trace_id)
    existing = _pane_identity(name)
    if existing is None:
        r = _tmux("new-session", "-d", "-s", name,
                  "-x", str(_PANE_COLS), "-y", str(_PANE_ROWS),
                  *NO_AUTOUPDATE_ENV, "claude", "attach", handle)
        if r.returncode != 0:
            return AttachResult(False, f"tmux refused: {r.stderr.strip()}")
        existing = _pane_identity(name)
        if existing is None:
            release(trace_id)
            return AttachResult(False, "the pane exited before it could be read")

    pane_id, server_pid, pane_pid, command = existing
    # The same guard the hook applies, for the same reason: a pane that has
    # fallen back to a shell would run a delivered message as a command.
    if command not in allowed_pane_commands():
        release(trace_id)
        return AttachResult(
            False, f"pane is running {command!r}, not the CLI")

    _record(trace_id, pane_id, server_pid, pane_pid)
    log.write("bridge_attach_taken_over", trace_id=trace_id, pane_id=pane_id,
              handle=handle, tmux_socket=settings.agent_bridge.attach_socket)
    return AttachResult(True, f"attached on {pane_id}", pane_id)


def release(trace_id: str) -> bool:
    """Drop regin's pane for `trace_id`. The conversation keeps running.

    Killing the pane kills the `claude attach` client, and the CLI's daemon
    still holds the session — which is exactly what makes taking over
    reversible, and what makes this safe to call while cleaning up a failure.
    """
    return release_session(_session_name(trace_id), trace_id)


def release_session(name: str, trace_id: str) -> bool:
    """Kill regin's tmux session `name` and retire `trace_id`'s pane row."""
    killed = _tmux("kill-session", "-t", name).returncode == 0
    _retire(trace_id)
    log.write("bridge_attach_released", trace_id=trace_id, session=name,
              killed=killed)
    return killed


def release_own_session(name: str, trace_id: str) -> bool:
    """`release_session` for a caller running inside the session it kills — a
    hook of the pane's own `claude`. The kill hangs up this process too, so
    everything that must be written is written before it."""
    _retire(trace_id)
    log.write("bridge_attach_releasing_own", trace_id=trace_id, session=name)
    return _tmux("kill-session", "-t", name).returncode == 0


def _retire(trace_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(_RETIRE_SQL, (trace_id,))
        conn.commit()
    finally:
        conn.close()


def _record(trace_id: str, pane_id: str, server_pid: int,
            pane_pid: int, cwd: str | None = None) -> None:
    """Claim the pane in `bridge_panes` on the session's behalf.

    The second writer of this table, and the only one that is not a hook. The
    row is the same shape the hook writes, because everything downstream —
    the displacement rule, the identity guard, delivery — must not be able to
    tell the two apart.
    """
    from lib.agent_bridge import roots

    conn = get_connection()
    try:
        cwd = cwd or roots.session_cwd(trace_id)
        conn.execute(_UPSERT_SQL, (trace_id, pane_id, server_pid, pane_pid,
                                   _socket_path(), str(cwd) if cwd else None))
        conn.commit()
    finally:
        conn.close()


def _socket_path() -> str:
    """The absolute socket path delivery threads into every tmux call.

    `-L <name>` resolves to a path tmux picks; asking it rather than rebuilding
    the path keeps this correct when `TMUX_TMPDIR` is set.
    """
    r = _tmux("display-message", "-p", "#{socket_path}")
    return r.stdout.strip() if r.returncode == 0 else ""
