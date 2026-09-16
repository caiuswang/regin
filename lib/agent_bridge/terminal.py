"""A real terminal onto a session's tmux pane: a PTY running a tmux client.

This is the byte-stream tier, and it is deliberately not built on the steering
tier beside it. `delivery` types *messages* into a pane through an allowlist, a
rate budget and a sanitizer, because its caller is a phone composer that can
only ever mean one thing. A terminal means everything a keyboard means, so
none of those guards translate: a partial filter over `tmux send-keys` would
only be false comfort. The controls live one layer up instead, at the route —
`settings.agent_bridge.terminal_enabled` (off by default) and the admin role —
and they are binary on purpose.

The client runs in a **grouped** session (`new-session -t`), never a bare
`attach`, so it is a separate session with its own client, its own current
window and its own lifecycle: closing the browser kills one session instead of
detaching a client from the operator's.

It does NOT buy independent sizing, and the older advice that it does no longer
holds. A window has exactly one size, grouped sessions share the target's
windows, and tmux's default `window-size latest` sizes that window to whichever
client last touched it — measured on tmux 3.6, an 80x24 pane became 200x49
while a browser client was attached, and returned to 80x24 the moment it left.
So an operator watching the same window sees it follow the browser for as long
as the browser is open. The alternative, `window-size smallest`, is a window
option on a shared window: it would protect the operator by clamping every
client to the smallest one, at the price of rewriting the operator's own tmux
configuration. Left alone deliberately — the effect is temporary and reverts
itself, and silently reconfiguring someone's tmux server is the larger harm.
"""

from __future__ import annotations

import fcntl
import os
import pty
import secrets
import select
import signal
import struct
import subprocess
import termios
import threading

from lib.activity_log import get_activity_logger
from lib.agent_bridge.env import child_env

log = get_activity_logger("agent_bridge")

_TMUX_TIMEOUT_SEC = 5

# A tmux client refuses to start inside another tmux ("sessions should be
# nested with care") when it inherits $TMUX. `regin serve` is very often run
# from a tmux pane, so the variable that must be dropped is the one this
# process legitimately has.
_INHERITED_TMUX_VARS = ("TMUX", "TMUX_PANE")


def _tmux(socket: str | None, *args: str) -> subprocess.CompletedProcess:
    """Run a tmux command, threading `-S <socket>`; failures are non-zero."""
    cmd = ["tmux", *(["-S", socket] if socket else []), *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TMUX_TIMEOUT_SEC)
    except (subprocess.SubprocessError, OSError) as exc:
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="",
                                           stderr=str(exc))


def _pane_location(socket: str | None, pane_id: str) -> tuple[str, str] | None:
    """(session name, window index) hosting `pane_id`, or None."""
    r = _tmux(socket, "display-message", "-p", "-t", pane_id,
              "#{session_name}\t#{window_index}")
    if r.returncode != 0:
        return None
    parts = (r.stdout or "").strip().split("\t")
    if len(parts) != 2 or not parts[0]:
        return None
    return parts[0], parts[1]


def _spawn_pty(argv: list[str], cols: int, rows: int) -> tuple[int, int]:
    """Fork `argv` onto a new PTY sized (cols, rows); returns (pid, master fd).

    `pty.fork` rather than a `subprocess` + `openpty` pair because a tmux
    client needs the PTY to be its *controlling* terminal, which only the
    setsid-then-TIOCSCTTY sequence `pty.fork` performs gives it.
    """
    env = child_env(os.environ)
    for var in _INHERITED_TMUX_VARS:
        env.pop(var, None)
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvpe(argv[0], argv, env)
        finally:
            os._exit(1)
    _set_winsize(fd, cols, rows)
    return pid, fd


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0))


class TerminalSession:
    """One PTY-hosted tmux client, plus the grouped session it attached to.

    Owns exactly one child process. `close` is idempotent and reaps: a leaked
    client per page view is the failure mode this class exists to prevent.

    One invariant governs every descriptor here: **a descriptor number may be
    released only when nobody can still name it.** Closing frees the number
    for the next connection's PTY master, so a straggling use does not fail —
    it addresses a different admin's terminal. `_in_read`, `_release_fds` and
    `_wake_reader` are that invariant made explicit, and each is pinned by a
    test.

    **Precondition: at most one reader.** `_in_read` is a boolean, not a
    count, so with two concurrent `read` calls the first to return would clear
    the claim while the second is still waiting, and `close` would free the
    numbers under it. One reader thread per session is the caller's job
    (`web.blueprints.terminal._pump_output` is that thread).
    """

    def __init__(self, pid: int, fd: int, socket: str | None, group: str):
        self.pid = pid
        self.fd = fd
        self._socket = socket
        self._group = group
        self._lock = threading.Lock()
        # Guards every use of a descriptor NUMBER, and — more importantly —
        # guards handing those numbers back. Closing frees them for the next
        # connection's PTY master, so a use that straddles a close would
        # address a different admin's terminal.
        self._fd_lock = threading.Lock()
        # `close` is the only writer: one byte ends the reader's wait at once,
        # rather than leaving it to notice on the next output or on a timer.
        self._wake_r, self._wake_w = os.pipe()
        # Set by the reader for as long as it may still NAME these descriptors
        # (it waits on them outside the lock, so a `_closed` check alone comes
        # too late — the wait is entered before it). While it is set, `close`
        # leaves the numbers allocated and the reader frees them on its way
        # out; the claim and the release are both taken under `_fd_lock`, so
        # there is no interleaving in which both sides skip the free or one
        # side waits on a number the other already recycled.
        self._in_read = False
        self._fds_released = False
        self._closed = False

    def read(self, size: int = 65536) -> bytes:
        """Pane output, or b"" once the client is gone."""
        with self._fd_lock:
            if self._closed:
                self._release_fds()
                return b""
            self._in_read = True
        try:
            return self._wait_and_read(size)
        finally:
            with self._fd_lock:
                self._in_read = False
                if self._closed:
                    self._release_fds()

    def _wait_and_read(self, size: int) -> bytes:
        """Wait for output, then take it. The wait is outside `_fd_lock` — a
        pane can be silent indefinitely, and holding the lock across that
        would block the keystrokes and resizes coming the other way."""
        try:
            ready, _, _ = select.select([self.fd, self._wake_r], [], [])
        except OSError:
            return b""
        if self._wake_r in ready:
            return b""
        try:
            return os.read(self.fd, size)
        except OSError:
            return b""

    def _wake_reader(self) -> None:
        """End a parked reader's wait, if the pipe is still ours to write to.

        The guard and the write are one critical section on purpose. `close`
        is no longer the only releaser — a reader entering `read` after
        `_closed` is set hands the descriptors back itself — so testing
        `_closed` and then writing outside the lock would let that reader free
        `_wake_w` in between. The write would still SUCCEED, into whatever the
        OS had since handed that number to: a NUL typed into the next admin's
        pane, or a byte into some unrelated open file.
        """
        with self._fd_lock:
            if self._fds_released:
                return
            try:
                os.write(self._wake_w, b"\0")
            except OSError:
                pass

    def _release_fds(self) -> None:
        """Hand back the three descriptor numbers, once. Caller holds
        `_fd_lock`."""
        if self._fds_released:
            return
        self._fds_released = True
        for fd in (self.fd, self._wake_r, self._wake_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def write(self, data: bytes) -> None:
        if self._use_fd(os.write, data):
            return
        self.close()

    def resize(self, cols: int, rows: int) -> None:
        """Apply the browser's geometry. The kernel raises SIGWINCH for the
        tty's foreground group, which is how the tmux client learns of it —
        no `refresh-client` round trip, and correct even mid-redraw."""
        if self._use_fd(_set_winsize, cols, rows):
            return
        self.close()

    def _use_fd(self, op, *args) -> bool:
        """Run `op(fd, *args)` under the fd lock; False if it failed or the
        session is already closed.

        The failure is reported rather than handled here because the caller's
        response is `close()`, which takes the same lock — closing from inside
        would deadlock on a non-reentrant lock.
        """
        with self._fd_lock:
            if self._closed:
                return True
            try:
                op(self.fd, *args)
                return True
            except OSError:
                return False

    def close(self) -> None:
        """Reap the child and end the session. Idempotent.

        Does NOT guarantee the descriptors are free on return: a reader still
        mid-wait owns them, and releases them on its way out.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._wake_reader()
        try:
            self._kill_and_reap()
        finally:
            # In a finally: a kill that fails for an unexpected reason must
            # not strand three descriptors with `_closed` already set, since
            # nothing comes back to retry.
            with self._fd_lock:
                if not self._in_read:
                    self._release_fds()
            _tmux(self._socket, "kill-session", "-t", self._group)
            log.write("bridge_terminal_closed", group=self._group,
                      pid=self.pid)

    def _kill_and_reap(self) -> None:
        """SIGKILL, then a blocking `waitpid`. Descriptors are freed by the
        caller, which knows whether the reader still needs them.

        A tmux *client* is disposable — the server, the session and the
        operator's own attachment all outlive it — so there is nothing to
        shut down gracefully, and SIGKILL makes the wait terminate on an
        event rather than on a timer.
        """
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            return
        try:
            os.waitpid(self.pid, 0)
        except (ChildProcessError, OSError):
            pass


def open_terminal(pane_row: dict, cols: int = 80,
                  rows: int = 24) -> TerminalSession | None:
    """Attach a fresh PTY-hosted client to `pane_row`'s pane, or None.

    None on any tmux-side failure; the caller closes the socket. The pane must
    already have been resolved through `store.get_reachable_pane` and checked
    by `delivery._verify_identity` — this function re-checks nothing, because
    a pane id alone cannot tell a recycled pane from the registered one.
    """
    socket, pane_id = pane_row.get("tmux_socket"), pane_row["pane_id"]
    location = _pane_location(socket, pane_id)
    if location is None:
        log.write("bridge_terminal_refused", pane_id=pane_id,
                  detail="pane not found")
        return None
    target, window = location
    group = f"regin-term-{secrets.token_hex(4)}"
    made = _tmux(socket, "new-session", "-d", "-t", target, "-s", group)
    if made.returncode != 0:
        log.write("bridge_terminal_refused", pane_id=pane_id,
                  detail=f"grouped session failed: {made.stderr.strip()}")
        return None
    # The grouped session starts on the target's current window, which is not
    # necessarily the one the operator's pane is in.
    _tmux(socket, "select-window", "-t", f"{group}:{window}")
    argv = ["tmux", *(["-S", socket] if socket else []), "attach", "-t", group]
    try:
        pid, fd = _spawn_pty(argv, cols, rows)
    except OSError as exc:
        _tmux(socket, "kill-session", "-t", group)
        log.write("bridge_terminal_refused", pane_id=pane_id,
                  detail=f"pty spawn failed: {exc}")
        return None
    log.write("bridge_terminal_opened", pane_id=pane_id, group=group, pid=pid)
    return TerminalSession(pid, fd, socket, group)
