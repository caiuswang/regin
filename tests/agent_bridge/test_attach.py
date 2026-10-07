"""Taking over a conversation the CLI holds off-pane (`lib/agent_bridge/attach.py`).

The module's whole job is to turn a session the bridge cannot see into an
ordinary pane-backed one, so what these pin is the seam: what it refuses to
touch, what it writes to `bridge_panes`, and that a failure never leaves a pane
behind. tmux itself is stubbed — a unit test has no business spawning a server,
and the real thing was exercised end to end against a live `claude --bg`
session before this landed.
"""

from __future__ import annotations

import pytest

from lib.agent_bridge import attach
from lib.settings import settings

_TRACE = "3183f301-33fe-4074-a35c-b179b938e7b3"
_HANDLE = "3183f301"


@pytest.fixture
def cli_holds(monkeypatch):
    """What the CLI reports it is holding, `{session_id: handle}`."""
    rows: dict[str, str] = {}

    class _Provider:
        def live_sessions(self):
            return rows

    monkeypatch.setattr("lib.providers.get_active_provider",
                        lambda *a, **k: _Provider())
    return rows


@pytest.fixture
def tmux(monkeypatch):
    """Stub tmux. `calls` is the verb sequence; `command` is what the pane
    reports as its foreground process."""
    state = type("S", (), {"calls": [], "command": "claude", "alive": False,
                           "new_session_rc": 0, "argv": ()})()

    class _R:
        def __init__(self, rc=0, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def _fake(*args):
        state.calls.append(args[0])
        if args[0] == "new-session":
            state.argv = args
            if state.new_session_rc == 0:
                state.alive = True
            return _R(state.new_session_rc, err="refused")
        if args[0] == "list-panes":
            if not state.alive:
                return _R(1)
            return _R(0, f"%7\t4242\t4243\t{state.command}\n")
        if args[0] == "kill-session":
            state.alive = False
            return _R(0)
        if args[0] == "display-message":
            return _R(0, "/tmp/tmux-501/regin-bridge\n")
        return _R(0)

    monkeypatch.setattr(attach, "_tmux", _fake)
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)
    return state


def _row(trace_id):
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT pane_id, tmux_server_pid, pane_pid, tmux_socket, reachable "
            "FROM bridge_panes WHERE trace_id = ?", (trace_id,)).fetchone()
    finally:
        conn.close()


def test_a_session_the_cli_does_not_hold_is_refused(tmp_db, cli_holds, tmux):
    """Nothing to attach to. Refused structurally rather than by exception:
    this answers a button."""
    result = attach.take_over(_TRACE)

    assert result.attached is False
    assert "not holding" in result.detail
    assert "new-session" not in tmux.calls


def test_a_session_the_cli_will_not_hand_over_is_refused(tmp_db, cli_holds,
                                                         tmux):
    """A claude someone is driving in their own terminal reports no handle, and
    is not something a second client may open."""
    cli_holds[_TRACE] = ""

    assert attach.take_over(_TRACE).attached is False
    assert "new-session" not in tmux.calls


def test_taking_over_claims_the_pane_on_regins_own_socket(tmp_db, cli_holds,
                                                          tmux):
    """The row is what makes every downstream bridge verb work, and it must be
    indistinguishable from one the hook wrote."""
    cli_holds[_TRACE] = _HANDLE

    result = attach.take_over(_TRACE)

    assert (result.attached, result.pane_id) == (True, "%7")
    pane_id, server_pid, pane_pid, socket, reachable = _row(_TRACE)
    assert (pane_id, server_pid, pane_pid, reachable) == ("%7", 4242, 4243, 1)
    assert socket.endswith("regin-bridge")


def test_the_taken_over_pane_has_the_autoupdater_off(tmp_db, cli_holds, tmux):
    cli_holds[_TRACE] = _HANDLE

    attach.take_over(_TRACE)

    argv = list(tmux.argv)
    assert argv[argv.index("DISABLE_AUTOUPDATER=1") - 1] == "-e"
    assert argv.index("DISABLE_AUTOUPDATER=1") < argv.index("claude")


def test_taking_over_twice_reuses_the_pane(tmp_db, cli_holds, tmux):
    """The operator pressing the button again, or two tabs open on one
    session, must not accumulate panes."""
    cli_holds[_TRACE] = _HANDLE

    attach.take_over(_TRACE)
    tmux.calls.clear()
    assert attach.take_over(_TRACE).attached is True
    assert "new-session" not in tmux.calls


def test_a_pane_that_fell_back_to_a_shell_is_refused_and_torn_down(
        tmp_db, cli_holds, tmux):
    """The guard the hook applies, for the same reason: a delivered message
    typed at a shell runs as a command. And the pane this module created is
    this module's to clean up."""
    cli_holds[_TRACE] = _HANDLE
    tmux.command = "bash"

    result = attach.take_over(_TRACE)

    assert result.attached is False
    assert "bash" in result.detail
    assert "kill-session" in tmux.calls
    assert _row(_TRACE) is None


def test_a_tmux_that_refuses_leaves_no_claim(tmp_db, cli_holds, tmux):
    cli_holds[_TRACE] = _HANDLE
    tmux.new_session_rc = 1

    assert attach.take_over(_TRACE).attached is False
    assert _row(_TRACE) is None


def test_releasing_retires_the_claim(tmp_db, cli_holds, tmux):
    """Release drops regin's pane; it never touches the conversation, which is
    what makes taking over reversible."""
    cli_holds[_TRACE] = _HANDLE
    attach.take_over(_TRACE)

    assert attach.release(_TRACE) is True
    assert _row(_TRACE)[4] == 0


def test_the_bridge_being_off_refuses_before_anything_runs(tmp_db, cli_holds,
                                                           tmux, monkeypatch):
    """The flag authorizes keystroke injection into a terminal, and this
    creates the terminal it would be injected into."""
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    cli_holds[_TRACE] = _HANDLE

    assert attach.take_over(_TRACE).attached is False
    assert tmux.calls == []
