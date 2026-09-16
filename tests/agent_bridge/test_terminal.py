"""The PTY tier: spawn, resize, and — above all — reap.

Nothing here talks to a real tmux. A mocked tmux would prove nothing about
the property that matters (one leaked client per page view is the failure
mode), so the reap tests drive `_spawn_pty` with an ordinary long-lived
command and assert against the real process table.
"""

from __future__ import annotations

import os
import signal
import struct
import fcntl
import termios
import threading

import pytest

from lib.agent_bridge import terminal


def _alive(pid: int) -> bool:
    """True while `pid` is a live, unreaped child of this process."""
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return False
    return done == 0


def _winsize(fd: int) -> tuple[int, int]:
    packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
    rows, cols, _, _ = struct.unpack("HHHH", packed)
    return cols, rows


@pytest.fixture
def session():
    """A `TerminalSession` over `cat` — a real child on a real PTY."""
    pid, fd = terminal._spawn_pty(["cat"], 80, 24)
    term = terminal.TerminalSession(pid, fd, None, "regin-term-test")
    yield term
    term.close()


# ── Spawn ───────────────────────────────────────────────────────────

def test_spawn_applies_the_requested_geometry():
    pid, fd = terminal._spawn_pty(["cat"], 120, 40)
    try:
        assert _winsize(fd) == (120, 40)
    finally:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        os.close(fd)


def test_spawn_drops_the_inherited_tmux_environment(monkeypatch):
    """A tmux client refuses to start inside another tmux, and `regin serve`
    is routinely run from a pane."""
    monkeypatch.setenv("TMUX", "/tmp/sock,123,0")
    monkeypatch.setenv("TMUX_PANE", "%9")
    pid, fd = terminal._spawn_pty(
        ["sh", "-c", "printf '[%s][%s]' \"$TMUX\" \"$TMUX_PANE\""], 80, 24)
    try:
        assert b"[][]" in _read_all(fd)
    finally:
        os.waitpid(pid, 0)
        os.close(fd)


def _read_all(fd: int) -> bytes:
    out = b""
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out


# ── Round trip + resize ─────────────────────────────────────────────

def test_bytes_go_both_ways(session):
    session.write(b"regin\n")
    assert b"regin" in session.read()


def test_resize_reaches_the_child(session):
    session.resize(132, 50)
    assert _winsize(session.fd) == (132, 50)


def test_resize_after_close_is_inert(session):
    session.close()
    session.resize(132, 50)


# ── Reap ────────────────────────────────────────────────────────────

def test_close_reaps_the_child(session):
    pid = session.pid
    assert _alive(pid)
    session.close()
    assert not _alive(pid)


def test_close_is_idempotent(session):
    session.close()
    session.close()


def test_close_kills_the_grouped_session(monkeypatch, session):
    calls = []
    monkeypatch.setattr(terminal, "_tmux",
                        lambda socket, *args: calls.append((socket, args)))
    session.close()
    assert calls == [(None, ("kill-session", "-t", "regin-term-test"))]


def test_read_after_close_is_empty(session):
    session.close()
    assert session.read() == b""


# ── open_terminal ───────────────────────────────────────────────────

def _stub_tmux(monkeypatch, responses):
    """Replay `responses` (keyed by the tmux subcommand) and record the calls."""
    import subprocess
    calls = []

    def fake(socket, *args):
        calls.append(args)
        rc, out = responses.get(args[0], (0, ""))
        return subprocess.CompletedProcess(list(args), rc, out, "")

    monkeypatch.setattr(terminal, "_tmux", fake)
    return calls


def test_open_terminal_refuses_a_pane_tmux_cannot_find(monkeypatch):
    _stub_tmux(monkeypatch, {"display-message": (1, "")})
    assert terminal.open_terminal({"pane_id": "%1", "tmux_socket": None}) is None


def test_open_terminal_refuses_when_the_grouped_session_fails(monkeypatch):
    calls = _stub_tmux(monkeypatch, {
        "display-message": (0, "work\t2\n"), "new-session": (1, "")})
    assert terminal.open_terminal({"pane_id": "%1", "tmux_socket": None}) is None
    assert [c[0] for c in calls] == ["display-message", "new-session"]


def test_open_terminal_groups_rather_than_attaching_to_the_target(monkeypatch):
    """A separate session, so the browser gets its own client, its own current
    window and its own lifecycle — closing it kills a session of ours rather
    than detaching a client from the operator's."""
    calls = _stub_tmux(monkeypatch, {"display-message": (0, "work\t2\n")})
    spawned = {}
    real_spawn = terminal._spawn_pty

    def fake_spawn(argv, cols, rows):
        spawned["argv"] = argv
        return real_spawn(["cat"], cols, rows)

    monkeypatch.setattr(terminal, "_spawn_pty", fake_spawn)
    term = terminal.open_terminal({"pane_id": "%1", "tmux_socket": None}, 90, 30)
    assert term is not None
    group = term._group
    try:
        new_session = next(c for c in calls if c[0] == "new-session")
        assert new_session == ("new-session", "-d", "-t", "work", "-s", group)
        assert ("select-window", "-t", f"{group}:2") in calls
        assert spawned["argv"] == ["tmux", "attach", "-t", group]
    finally:
        term.close()


def test_open_terminal_kills_the_group_when_the_pty_will_not_spawn(monkeypatch):
    calls = _stub_tmux(monkeypatch, {"display-message": (0, "work\t2\n")})

    def boom(argv, cols, rows):
        raise OSError("no ptys left")

    monkeypatch.setattr(terminal, "_spawn_pty", boom)
    assert terminal.open_terminal({"pane_id": "%1", "tmux_socket": None}) is None
    assert [c[0] for c in calls][-1] == "kill-session"


# ── Exit paths the socket does not drive ────────────────────────────

def test_close_reaps_a_child_that_already_exited_on_its_own():
    """The client can end first — `exit`, a killed tmux server — and the
    socket only learns of it through an empty read."""
    pid, fd = terminal._spawn_pty(["sh", "-c", "exit 0"], 80, 24)
    term = terminal.TerminalSession(pid, fd, None, "regin-term-test")
    while term.read():
        pass
    term.close()
    assert not _alive(pid)


def test_concurrent_closes_reap_exactly_once(session):
    """`_pump_output` and the route's `finally` both close, from different
    threads; a second `waitpid` on a reaped pid would raise."""
    errors = []

    def close():
        try:
            session.close()
        except Exception as exc:  # a raise here would escape into the pump
            errors.append(repr(exc))

    threads = [threading.Thread(target=close) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert errors == [] and not any(t.is_alive() for t in threads)


def test_write_to_a_dead_child_closes_instead_of_raising():
    pid, fd = terminal._spawn_pty(["sh", "-c", "exit 0"], 80, 24)
    term = terminal.TerminalSession(pid, fd, None, "regin-term-test")
    while term.read():
        pass
    term.write(b"x" * 4096)
    assert term._closed and not _alive(pid)


def test_a_blocked_reader_never_reads_a_recycled_descriptor():
    """`close()` frees the fd number for the next PTY. A reader still inside
    `os.read` when that happens would ship another session's bytes to this
    client, so the fd lock must span the blocking read — and `close()` must
    still return, which it does because its SIGKILL is what ends the read."""
    pid, fd = terminal._spawn_pty(["cat"], 80, 24)
    term = terminal.TerminalSession(pid, fd, None, "regin-term-test")
    reads = []
    entered = threading.Event()

    def reader_body():
        entered.set()
        reads.append(term.read())

    reader = threading.Thread(target=reader_body)
    reader.start()
    entered.wait(timeout=5)
    term.close()
    reader.join(timeout=5)
    assert not reader.is_alive() and reads == [b""]
    assert term.read() == b""


def test_no_fd_use_outlives_the_close_that_freed_it():
    """`read` is not the only reader of `fd`. `write` and `resize` touch it
    too, and `write`'s own error path calls `close()` while `_pump_input` is
    still looping — so the next frame would address a descriptor number the OS
    has since handed to another terminal's PTY master, typing one admin's
    keystrokes into another admin's pane.

    Asserted against the recycled descriptor itself: close one session, open
    another that lands on the same fd number, then drive the dead session and
    show the live one saw nothing.
    """
    pid, fd = terminal._spawn_pty(["cat"], 80, 24)
    dead = terminal.TerminalSession(pid, fd, None, "regin-term-test")
    dead.close()
    heir_pid, heir_fd = terminal._spawn_pty(["cat"], 80, 24)
    try:
        if heir_fd != fd:
            pytest.skip("the OS did not recycle the descriptor number")
        dead.write(b"keystrokes for the other session\n")
        dead.resize(200, 60)
        os.set_blocking(heir_fd, False)
        with pytest.raises(BlockingIOError):
            os.read(heir_fd, 4096)
        assert _winsize(heir_fd) == (80, 24)
    finally:
        os.kill(heir_pid, signal.SIGKILL)
        os.waitpid(heir_pid, 0)
        os.close(heir_fd)


def test_write_and_resize_take_the_same_lock_as_read(session):
    """The fd lock spans every use of `fd`, so a held lock blocks them."""
    session._fd_lock.acquire()
    try:
        done = []
        for target in (lambda: session.write(b"x"),
                       lambda: session.resize(90, 30)):
            t = threading.Thread(target=lambda: done.append(target()))
            t.start()
            t.join(timeout=0.5)
            assert t.is_alive(), "touched fd without the lock"
    finally:
        session._fd_lock.release()


def test_a_silent_pane_does_not_block_input_or_resize(session):
    """A reader waiting on a pane that is producing nothing must not hold the
    fd lock while it waits. It did once: `read` blocked inside the lock, so a
    quiet terminal deadlocked every keystroke and every resize coming the
    other way — the pane rendered, and then ignored the browser entirely."""
    reader = threading.Thread(target=session.read, daemon=True)
    reader.start()
    done = threading.Event()

    def drive():
        session.resize(132, 50)
        session.write(b"typed while the pane was silent\n")
        done.set()

    threading.Thread(target=drive, daemon=True).start()
    assert done.wait(timeout=5), "a silent reader blocked write/resize"
    assert _winsize(session.fd) == (132, 50)


def test_close_between_reads_does_not_strand_the_reader_on_a_recycled_fd():
    """The reader waits OUTSIDE the fd lock, so a `_closed` check alone comes
    too late — the wait is entered before it. If `close()` lands in the gap
    after one read returns and before the next wait begins, the freed numbers
    can already belong to something else, and the reader parks on it forever:
    the socket is never told, and the route never reaps."""
    pid, fd = terminal._spawn_pty(["sh", "-c", "printf hello; sleep 30"], 80, 24)
    term = terminal.TerminalSession(pid, fd, None, "regin-term-test")
    assert term.read()
    term.close()
    hog_r, hog_w = os.pipe()
    try:
        assert hog_r in (fd, term._wake_r, term._wake_w), \
            "the numbers were not recycled; the test proves nothing"
        done = threading.Event()
        threading.Thread(target=lambda: (term.read(), done.set()),
                         daemon=True).start()
        assert done.wait(timeout=5), "reader parked on a recycled descriptor"
    finally:
        os.close(hog_r)
        os.close(hog_w)


def test_a_kill_that_fails_still_hands_back_the_descriptors(monkeypatch, session):
    """`_closed` is already set by then, so nothing comes back to retry."""
    def refuse(pid, sig):
        raise OSError("no such process table entry")

    monkeypatch.setattr(os, "kill", refuse)
    session.close()
    monkeypatch.undo()
    for fd in (session.fd, session._wake_r, session._wake_w):
        with pytest.raises(OSError):
            os.fstat(fd)
    os.kill(session.pid, signal.SIGKILL)
    os.waitpid(session.pid, 0)


def test_the_wake_byte_never_lands_in_a_handed_back_descriptor(monkeypatch):
    """`close()` is not the only releaser: a reader entering `read()` after
    `_closed` is set frees the descriptors itself. So testing `_closed` and
    then writing the wake byte outside the lock lets that reader free
    `_wake_w` in between — and the write still SUCCEEDS, into whatever number
    the OS has since handed out. A NUL into the next admin's pane."""
    pid, fd = terminal._spawn_pty(["cat"], 80, 24)
    term = terminal.TerminalSession(pid, fd, None, "regin-term-test")
    term._closed = True
    assert term.read() == b""
    assert term._fds_released

    written = []
    monkeypatch.setattr(os, "write",
                        lambda fd, data: written.append((fd, data)))
    term._wake_reader()
    assert written == [], "wrote into a descriptor already handed back"
    monkeypatch.undo()
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
