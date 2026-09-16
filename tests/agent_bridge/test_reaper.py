"""What a dead tmux server leaves behind (`lib/agent_bridge/reaper.py`).

A session the operator started in a pane has no `agent_runs` row, so the SDK
boot sweep settles none of them: when the tmux server dies with the machine,
every one of those `sessions` rows keeps `status='active'` / `ended_at IS NULL`
forever and sits in the UI as permanently Live.

Pinned here: which rows the tmux-tier sweep closes, which it must not, and —
the load-bearing half — that a probe it could not make settles NOTHING. `_tmux`
reports a missing binary, a timeout and a dead socket alike as returncode 1, so
a sweep reading that code alone would empty the session list of every install
without tmux on PATH.
"""

from __future__ import annotations

import subprocess

import pytest

from lib.agent_bridge import reaper
from lib.orm.engine import get_connection

_TRACE = "bridge-reap-0001"
_OTHER = "bridge-reap-0002"
_SEEN = "2026-02-03T04:05:06"
_DEAD_PID = 1481
_LIVE_PID = 9042
_SOCK = "/private/tmp/tmux-501/default"


def _seed_session(trace_id: str, **over) -> None:
    from lib.orm import SessionLocal
    from lib.orm.models.trace import Session as SessionModel

    fields = {"trace_id": trace_id, "started_at": _SEEN, "last_seen": _SEEN,
              "status": "active", "is_test": 0}
    fields.update(over)
    with SessionLocal() as session:
        session.add(SessionModel(**fields))
        session.commit()


def _seed_pane(trace_id: str, *, pane_id="%0", server_pid=_DEAD_PID,
               socket=_SOCK, reachable=1) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO bridge_panes (trace_id, pane_id, tmux_server_pid, "
            "pane_pid, tmux_socket, reachable, cwd, created_at, updated_at) "
            "VALUES (?, ?, ?, 222, ?, ?, '/work', ?, ?)",
            (trace_id, pane_id, server_pid, socket, reachable, _SEEN, _SEEN))
        conn.commit()
    finally:
        conn.close()


def _row(trace_id: str):
    from sqlmodel import select

    from lib.orm import SessionLocal
    from lib.orm.models.trace import Session as SessionModel

    with SessionLocal() as session:
        return session.exec(
            select(SessionModel).where(
                SessionModel.trace_id == trace_id)).first()


def _install_tmux(monkeypatch, reply):
    """Mock subprocess.run so `_tmux` builds real argv we can inspect.

    `reply(socket)` returns the CompletedProcess for that socket, or raises to
    model a probe that never completed (missing binary, timeout) — the wrapper's
    own except path then does the collapsing to returncode 1, as in production.
    """
    calls: list[list[str]] = []

    def _fake_run(cmd, **_kw):
        calls.append(list(cmd))
        socket = cmd[cmd.index("-S") + 1] if "-S" in cmd else None
        return reply(socket)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return calls


def _server(pid: int):
    return lambda _sock: subprocess.CompletedProcess(
        [], 0, stdout=f"{pid}\n", stderr="")


def _no_server(_sock):
    return subprocess.CompletedProcess(
        [], 1, stdout="", stderr=f"no server running on {_SOCK}\n")


# ── 1. a server that answers with a DIFFERENT pid ────────────
def test_a_pane_on_a_replaced_server_is_settled(tmp_db, monkeypatch):
    """A socket hosts one server, so a row naming another pid is on a server
    that is gone — the shape a reboot leaves when tmux comes back up."""
    _seed_session(_TRACE)
    _seed_pane(_TRACE)
    _install_tmux(monkeypatch, _server(_LIVE_PID))

    assert reaper.settle_dead_tmux_sessions() == 1

    row = _row(_TRACE)
    assert row.status == "ended"
    assert row.ended_at == _SEEN
    assert row.ended_reason == "tmux server gone"


# ── 2. the server that is actually still there ───────────────
def test_a_pane_on_the_live_server_is_left_alone(tmp_db, monkeypatch):
    """Its `claude` is sitting in that pane right now."""
    _seed_session(_TRACE)
    _seed_pane(_TRACE, server_pid=_LIVE_PID)
    _install_tmux(monkeypatch, _server(_LIVE_PID))

    assert reaper.settle_dead_tmux_sessions() == 0

    row = _row(_TRACE)
    assert row.status == "active"
    assert row.ended_at is None


# ── 3. no server on the socket at all ────────────────────────
def test_no_server_running_settles_every_row_on_that_socket(tmp_db,
                                                            monkeypatch):
    """The operator's own case: the socket file outlives the server that made
    it, and tmux says so in as many words."""
    for trace_id in (_TRACE, _OTHER):
        _seed_session(trace_id)
        _seed_pane(trace_id, pane_id=f"%{trace_id[-1]}")
    _install_tmux(monkeypatch, _no_server)

    assert reaper.settle_dead_tmux_sessions() == 2
    assert all(_row(t).status == "ended" for t in (_TRACE, _OTHER))


def test_one_probe_per_socket_not_per_row(tmp_db, monkeypatch):
    """A socket hosts one server; asking once per registered pane would spawn
    a subprocess per row of a table that only grows."""
    for trace_id in (_TRACE, _OTHER):
        _seed_session(trace_id)
        _seed_pane(trace_id, pane_id=f"%{trace_id[-1]}")
    calls = _install_tmux(monkeypatch, _server(_LIVE_PID))

    reaper.settle_dead_tmux_sessions()

    assert len(calls) == 1


# ── 4. probes that never answered ────────────────────────────
def _raise(exc):
    def _reply(_sock):
        raise exc
    return _reply


@pytest.mark.parametrize("reply, why", [
    (_raise(FileNotFoundError("tmux")), "tmux is not installed"),
    (_raise(subprocess.TimeoutExpired("tmux", 3.0)), "the probe timed out"),
    (lambda _s: subprocess.CompletedProcess([], 0, stdout="tmux 3.4\n",
                                            stderr=""),
     "stdout was not a pid"),
    (lambda _s: subprocess.CompletedProcess([], 1, stdout="",
                                            stderr="error connecting to /x\n"),
     "the failure was not a report of absence"),
])
def test_an_unanswered_probe_settles_nothing(tmp_db, monkeypatch, reply, why):
    """`_tmux` gives a missing binary and a dead socket the same returncode.
    Treating that code as proof of death would end every session in the
    database on any machine without tmux on PATH."""
    _seed_session(_TRACE)
    _seed_pane(_TRACE)
    _install_tmux(monkeypatch, reply)

    assert reaper.settle_dead_tmux_sessions() == 0, why
    assert _row(_TRACE).status == "active", why


# ── 5. a session this process is running ─────────────────────
def test_a_session_a_live_run_names_is_spared(tmp_db, monkeypatch):
    """A run regin owns outranks an inference drawn from a pane registration:
    the pane may be stale while the SDK runner behind it is very much alive."""
    from lib.agent_sdk import store as sdk_store

    _seed_session(_TRACE)
    _seed_pane(_TRACE)
    sdk_store.upsert_run(_TRACE, status="running")
    _install_tmux(monkeypatch, _no_server)

    assert reaper.settle_dead_tmux_sessions() == 0

    row = _row(_TRACE)
    assert row.status == "active"
    assert row.ended_at is None


# ── 6. idempotence ───────────────────────────────────────────
def test_a_second_sweep_settles_nothing(tmp_db, monkeypatch):
    """It runs at every boot; the second one must be a no-op, not a re-stamp."""
    _seed_session(_TRACE)
    _seed_pane(_TRACE)
    _install_tmux(monkeypatch, _no_server)

    assert reaper.settle_dead_tmux_sessions() == 1
    assert reaper.settle_dead_tmux_sessions() == 0
    assert _row(_TRACE).ended_at == _SEEN


def test_a_row_already_calling_itself_ended_is_not_restamped(tmp_db,
                                                             monkeypatch):
    """`status='ended'` with a NULL `ended_at` is a verdict written by the
    process that was there — half-recorded, but still outranking this one."""
    _seed_session(_TRACE, status="ended", ended_reason="clear")
    _seed_pane(_TRACE)
    _install_tmux(monkeypatch, _no_server)

    assert reaper.settle_dead_tmux_sessions() == 0

    row = _row(_TRACE)
    assert row.ended_reason == "clear"
    assert row.ended_at is None


# ── 7. a registry that cannot be read ────────────────────────
def test_a_missing_bridge_panes_table_returns_zero(tmp_db, monkeypatch):
    """This runs at boot on whatever schema the install happens to be on; a
    pre-migration DB must cost the sweep, not the server."""
    conn = get_connection()
    try:
        conn.execute("DROP TABLE bridge_panes")
        conn.commit()
    finally:
        conn.close()
    calls = _install_tmux(monkeypatch, _no_server)

    assert reaper.settle_dead_tmux_sessions() == 0
    assert calls == []


def test_an_unreadable_agent_runs_table_settles_nothing(tmp_db, monkeypatch):
    """The live-run guard failing open would end a session this process is
    running — the sweep's own bug, inverted."""
    _seed_session(_TRACE)
    _seed_pane(_TRACE)
    conn = get_connection()
    try:
        conn.execute("DROP TABLE agent_runs")
        conn.commit()
    finally:
        conn.close()
    _install_tmux(monkeypatch, _no_server)

    assert reaper.settle_dead_tmux_sessions() == 0
    assert _row(_TRACE).status == "active"


def test_a_retired_pane_claim_is_not_evidence_about_its_session(tmp_db,
                                                                monkeypatch):
    """`reachable=0` says the claim stopped being true, so the row must not
    settle the session it names.

    SessionEnd retires a claim, and so does a handover to the SDK tier — whose
    child registers no pane of its own. Sweeping retired rows too meant a
    session resumed off a terminal whose tmux server had since died was
    settled 'tmux server gone' while the run driving it was alive, leaving the
    trace page with no composer and no Resume.
    """
    _seed_session(_TRACE)
    _seed_pane(_TRACE, reachable=0)
    _install_tmux(monkeypatch, _no_server)

    assert reaper.settle_dead_tmux_sessions() == 0

    row = _row(_TRACE)
    assert row.status == "active"
    assert row.ended_at is None
