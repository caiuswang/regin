"""The pane terminal's WebSocket gate (`web.blueprints.terminal`).

Full shell access hangs off this route, so what is pinned here is the order
and the completeness of the refusals: the flag, the ticket, the admin role,
a reachable pane and the pane's identity, each resolving BEFORE anything is
spawned. `_authorize` is exercised directly — Flask's test client cannot
speak WebSocket, and routing an assertion about "did we spawn" through a
protocol upgrade would test the upgrade, not the gate.

The pane is seeded straight into `bridge_panes` and `settings.agent_bridge`
is the live singleton mutated with monkeypatch, both idioms from
`tests/agent_bridge/test_web_proxy.py`.
"""

from __future__ import annotations

import itertools
import json

import pytest

from lib import auth
from lib.agent_bridge import delivery, terminal
from lib.notifications import tickets
from lib.orm.engine import get_connection
from lib.settings import settings
from web.app import PUBLIC_API_ENDPOINTS
from web.blueprints import terminal as route

_TRACE = "T-term"


@pytest.fixture(autouse=True)
def _clean_tickets():
    tickets._tickets.clear()
    yield
    tickets._tickets.clear()


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "terminal_enabled", True)


@pytest.fixture
def disabled(monkeypatch):
    """Pin the flag OFF rather than trusting the ambient config: the developer
    running the suite may well have turned the terminal on for themselves, and
    a test that silently inverts with their settings is not a test."""
    monkeypatch.setattr(settings.agent_bridge, "terminal_enabled", False)


def _seed_pane(trace_id=_TRACE, pane_id="%7", reachable=1):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO bridge_panes (trace_id, pane_id, tmux_server_pid, "
            "pane_pid, tmux_socket, reachable, cwd) "
            "VALUES (?, ?, 111, 222, NULL, ?, '/work')",
            (trace_id, pane_id, reachable))
        conn.commit()
    finally:
        conn.close()


def _identity(monkeypatch, ok=True, detail="identity ok"):
    monkeypatch.setattr(
        delivery, "_verify_identity",
        lambda row: {"ok": ok, "in_mode": False, "read": True, "detail": detail})


_seq = itertools.count()


def _ticket_for(role):
    user = auth.register_user(f"u-{role}-{next(_seq)}", role, "pw123456",
                              role=role)
    return tickets.issue(user["id"])


# ── Refusals, each before any spawn ─────────────────────────────────

def test_the_flag_is_off_by_default():
    """The DEFAULT on the model, not whatever this machine has configured."""
    from lib.settings import AgentBridgeConfig
    assert AgentBridgeConfig().terminal_enabled is False


def test_refuses_when_the_flag_is_off(tmp_db, disabled):
    row, refusal = route._authorize(_TRACE, _ticket_for("admin"))
    assert row is None and refusal == "terminal disabled"


def test_a_disabled_terminal_does_not_even_spend_the_ticket(tmp_db, disabled):
    ticket = _ticket_for("admin")
    route._authorize(_TRACE, ticket)
    assert tickets.outstanding() == 1


def test_refuses_an_absent_ticket(tmp_db, enabled):
    row, refusal = route._authorize(_TRACE, "")
    assert row is None and refusal == "invalid ticket"


def test_refuses_a_ticket_that_was_already_spent(tmp_db, enabled, monkeypatch):
    _seed_pane()
    _identity(monkeypatch)
    ticket = _ticket_for("admin")
    assert route._authorize(_TRACE, ticket)[1] is None
    assert route._authorize(_TRACE, ticket)[1] == "invalid ticket"


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_refuses_every_role_below_admin(tmp_db, enabled, monkeypatch, role):
    """Steering a message needs `require_editor`; a raw terminal is strictly
    more powerful, so it takes one rung more."""
    _seed_pane()
    _identity(monkeypatch)
    row, refusal = route._authorize(_TRACE, _ticket_for(role))
    assert row is None and refusal == "admin role required"


def test_refuses_a_ticket_whose_user_is_gone(tmp_db, enabled):
    user = auth.register_user("ghost", "ghost", "pw123456", role="admin")
    ticket = tickets.issue(user["id"])
    auth.delete_user(user["id"])
    assert route._authorize(_TRACE, ticket)[1] == "admin role required"


def test_refuses_an_unreachable_pane(tmp_db, enabled):
    _seed_pane(reachable=0)
    row, refusal = route._authorize(_TRACE, _ticket_for("admin"))
    assert row is None and refusal == "no reachable pane"


def test_refuses_a_session_that_never_registered(tmp_db, enabled):
    row, refusal = route._authorize("no-such-session", _ticket_for("admin"))
    assert row is None and refusal == "no reachable pane"


def test_refuses_a_pane_whose_identity_no_longer_matches(tmp_db, enabled,
                                                         monkeypatch):
    """A `bridge_panes` row is keyed by trace_id and never retired, so the id
    can name a pane some later claude took over."""
    _seed_pane()
    _identity(monkeypatch, ok=False, detail="pane '%7' not found")
    row, refusal = route._authorize(_TRACE, _ticket_for("admin"))
    assert row is None and refusal == "pane '%7' not found"


def test_authorizes_an_admin_on_a_verified_pane(tmp_db, enabled, monkeypatch):
    _seed_pane()
    _identity(monkeypatch)
    row, refusal = route._authorize(_TRACE, _ticket_for("admin"))
    assert refusal is None and row["pane_id"] == "%7"


def test_no_refusal_path_ever_reaches_the_pty(tmp_db, disabled, monkeypatch):
    """The one property every refusal shares."""
    monkeypatch.setattr(terminal, "open_terminal",
                        lambda *a, **kw: pytest.fail("spawned on a refusal"))
    cases = [
        (_TRACE, _ticket_for("admin")),          # flag off
        (_TRACE, "bogus"),
        ("no-such-session", _ticket_for("admin")),
    ]
    for trace_id, ticket in cases:
        assert route._authorize(trace_id, ticket)[0] is None


# ── Transport wiring ────────────────────────────────────────────────

def test_the_route_is_allowlisted_past_the_header_gate():
    """A WebSocket cannot send an Authorization header; the ticket is the
    credential, exactly as for the badge stream."""
    assert "bridge_terminal.api_session_terminal" in PUBLIC_API_ENDPOINTS


def test_the_route_is_registered(flask_client):
    rules = {r.endpoint: r.rule
             for r in flask_client.application.url_map.iter_rules()}
    assert rules["bridge_terminal.api_session_terminal"] == \
        "/api/sessions/<trace_id>/terminal"


# ── Frame protocol ──────────────────────────────────────────────────

class _FakeTerm:
    def __init__(self):
        self.written = []
        self.sizes = []

    def write(self, data):
        self.written.append(data)

    def resize(self, cols, rows):
        self.sizes.append((cols, rows))


class _FakeWs:
    def __init__(self, frames):
        self._frames = list(frames)

    def receive(self):
        return self._frames.pop(0) if self._frames else None


def test_binary_frames_are_raw_stdin():
    term, ws = _FakeTerm(), _FakeWs([b"\x03", b"ls\r"])
    route._pump_input(ws, term)
    assert term.written == [b"\x03", b"ls\r"] and term.sizes == []


def test_a_text_frame_resizes_and_never_reaches_stdin():
    term = _FakeTerm()
    route._pump_input(_FakeWs([json.dumps({"type": "resize", "cols": 100,
                                           "rows": 40})]), term)
    assert term.sizes == [(100, 40)] and term.written == []


@pytest.mark.parametrize("payload", [
    "not json", "[]", '{"type": "exec", "cmd": "rm -rf /"}', '{"cols": 10}'])
def test_unknown_text_frames_are_dropped(payload):
    term = _FakeTerm()
    route._pump_input(_FakeWs([payload]), term)
    assert term.sizes == [] and term.written == []


@pytest.mark.parametrize("raw,expected", [
    (0, 80), (-5, 80), ("120", 80), (None, 80), (True, 80),
    (10_000, route._MAX_DIMENSION), (120, 120)])
def test_client_geometry_is_clamped(raw, expected):
    assert route._dimension(raw, 80) == expected

