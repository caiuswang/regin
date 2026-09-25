"""Start an interactive `claude` on a pane regin owns — the terminal launch kind.

The SDK launch (`lib/agent_sdk/supervisor.launch`) holds the session's input
stream, which is exactly what an interactive TUI cannot offer: slash commands,
`/` menus and native permission prompts only exist in the terminal. So this
kind starts the TUI itself on regin's own tmux server; the operator lands on
the session's page, whose browser terminal attaches to that pane.

Identity is decided here, not discovered: regin picks the uuid and passes it as
`--session-id`, so the trace id in the reply is the id the process's own hooks
will report. The `bridge_panes` row is written before replying, so the terminal
route can attach the moment the client opens it — nothing waits for the hook.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass

from lib.activity_log import get_activity_logger
from lib.agent_bridge import attach
from lib.settings import settings

log = get_activity_logger("agent_bridge")


class TerminalLaunchRefused(Exception):
    """The terminal kind cannot run here; the message is the operator's reason."""


@dataclass(frozen=True)
class TerminalLaunch:
    trace_id: str
    pane_id: str


def unavailable_reason(role: str | None) -> str:
    """Why the terminal kind is refused for a caller of `role`, or "" if it is not.

    One function for both the launch-options answer and the POST, so the form
    can never offer a kind the route then refuses for a reason it did not show.
    """
    cfg = settings.agent_bridge
    if not cfg.enabled:
        return "bridge disabled"
    if not cfg.terminal_enabled:
        return "terminal disabled"
    if role != "admin":
        return "admin role required"
    missing = [b for b in ("tmux", "claude") if shutil.which(b) is None]
    if missing:
        return f"{' and '.join(missing)} not found on the server's PATH"
    return ""


def _session_name(trace_id: str) -> str:
    return f"term-{trace_id[:8]}"


def _argv(trace_id: str, *, model: str, effort: str, permission_mode: str,
          prompt: str, resume: bool) -> list[str]:
    # Absolute: the tmux server may predate this process and does not carry
    # its PATH, and an nvm-installed claude is on no system path.
    # `--resume` without `--fork-session` keeps the session's own id, so the
    # id is still regin's to name — it is just not a fresh one.
    argv = [shutil.which("claude") or "claude",
            "--resume" if resume else "--session-id", trace_id]
    for flag, value in (("--model", model), ("--effort", effort),
                        ("--permission-mode", permission_mode)):
        if value:
            argv += [flag, value]
    if prompt:
        # `--` ends option parsing: a prompt opening with `-` (a markdown
        # bullet, or `--dangerously-skip-permissions`) is a first turn, never a flag.
        argv += ["--", prompt]
    return argv


def _resumed_id(resume: str) -> str:
    """The id to hand `--resume`, which lands on argv and in a tmux target
    name: anything but a CLI session uuid would be parsed as a flag or a
    `session:window.pane` address."""
    try:
        return str(uuid.UUID(resume))
    except ValueError:
        raise TerminalLaunchRefused(f"not a CLI session id: {resume!r}") from None


def _pane_env() -> list[str]:
    """`-e` pairs for the pane: the server's PATH (an nvm claude's `node`
    lives there), and the bridge opted in. `child_env` forces it off for
    spawned children because their inherited `TMUX_PANE` is the *server's*
    pane; here tmux sets `TMUX_PANE` to the pane regin just made, so the
    SessionStart hook registering it is correct — and keeps a `/clear` in the
    terminal on the same pane row chain as any other bridged session."""
    return ["-e", f"PATH={os.environ.get('PATH', '')}", "-e", "REGIN_BRIDGE=1"]


def launch_terminal(*, role: str | None, cwd: str | None, model: str = "",
                    effort: str = "", permission_mode: str = "",
                    prompt: str = "", resume: str = "") -> TerminalLaunch:
    """Spawn `claude` in a new `term-<id8>` session on the attach socket.

    `resume` is a CLI session id to continue; the launch then runs under that
    id instead of a new one.

    Raises `TerminalLaunchRefused` for every refusal; on any refusal after the
    spawn, the tmux session is killed and the row retired before raising.
    """
    reason = unavailable_reason(role)
    if reason:
        raise TerminalLaunchRefused(reason)
    workdir = cwd or str(settings.project_root)
    # tmux falls back to $HOME for a missing `-c` directory and still succeeds,
    # which would run claude somewhere nobody chose.
    if not os.path.isdir(workdir):
        raise TerminalLaunchRefused(f"no such directory: {workdir}")
    trace_id = _resumed_id(resume) if resume else str(uuid.uuid4())
    name = _session_name(trace_id)
    r = attach._tmux(
        "new-session", "-d", "-s", name, "-c", workdir,
        "-x", str(attach._PANE_COLS), "-y", str(attach._PANE_ROWS),
        *_pane_env(),
        *_argv(trace_id, model=model, effort=effort,
               permission_mode=permission_mode, prompt=prompt,
               resume=bool(resume)))
    if r.returncode != 0:
        raise TerminalLaunchRefused(f"tmux refused: {r.stderr.strip()}")
    identity = attach._pane_identity(name)
    if identity is None:
        attach.release_session(name, trace_id)
        raise TerminalLaunchRefused("claude exited before its pane could be read")
    pane_id, server_pid, pane_pid, command = identity
    if command not in settings.agent_bridge.allowed_pane_commands:
        attach.release_session(name, trace_id)
        raise TerminalLaunchRefused(f"pane is running {command!r}, not the CLI")
    attach._record(trace_id, pane_id, server_pid, pane_pid, cwd=workdir)
    log.write("bridge_terminal_launched", trace_id=trace_id, pane_id=pane_id,
              cwd=workdir, resumed=bool(resume), tmux_socket=settings.agent_bridge.attach_socket)
    return TerminalLaunch(trace_id, pane_id)
