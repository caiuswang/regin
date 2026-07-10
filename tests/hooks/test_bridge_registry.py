"""SessionStart agent-bridge pane registry (bridge_registry handler).

Pins the guard order (REGIN_BRIDGE → $TMUX_PANE → tmux query → UPSERT):
with the flag off the handler must be a pure no-op — no subprocess, no
row — and every tmux/DB failure path must fail soft while still
returning the suppress-output response. The UPSERT must overwrite ALL
columns on resume so no stale coordinate survives a re-registration.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import bridge_registry


def _payload(session_id="s-bridge", cwd="/tmp/proj"):
    raw = {"cwd": cwd, "session_id": session_id, "source": "startup"}
    return HookPayload(event="SessionStart", cwd=cwd,
                       session_id=session_id, raw=raw)


def _forbid_subprocess(monkeypatch):
    def _boom(*_a, **_kw):
        pytest.fail("subprocess.run must not be called on this path")
    monkeypatch.setattr(bridge_registry.subprocess, "run", _boom)


def _mock_tmux(monkeypatch, *, server_pid=4242, pane_pid=777,
               returncode=0, stdout=None, raise_exc=None):
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, "kwargs": kwargs})
        if raise_exc is not None:
            raise raise_exc
        out = stdout if stdout is not None else f"{server_pid}\t{pane_pid}\n"
        return SimpleNamespace(returncode=returncode, stdout=out, stderr="")

    monkeypatch.setattr(bridge_registry.subprocess, "run", _fake_run)
    return calls


def _rows():
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        return [dict(r) for r in
                conn.execute("SELECT * FROM bridge_panes").fetchall()]
    finally:
        conn.close()


def test_flag_unset_is_pure_noop(monkeypatch):
    monkeypatch.delenv("REGIN_BRIDGE", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%3")
    _forbid_subprocess(monkeypatch)

    resp = bridge_registry.handle_start(_payload())

    assert resp is not None and resp.suppress_output is True
    assert _rows() == []


def test_flag_set_but_no_tmux_pane_records_nothing(monkeypatch):
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.delenv("TMUX_PANE", raising=False)
    _forbid_subprocess(monkeypatch)

    resp = bridge_registry.handle_start(_payload())

    assert resp is not None and resp.suppress_output is True
    assert _rows() == []


def test_registers_identity_triple_with_reachable(monkeypatch):
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setenv("TMUX_PANE", "%7")
    # $TMUX = "<socket_path>,<server_pid>,<session_id>"; the first
    # comma-field is the absolute socket path the delivery leg needs.
    monkeypatch.setenv("TMUX", "/tmp/sock,123,0")
    calls = _mock_tmux(monkeypatch, server_pid=4242, pane_pid=777)

    resp = bridge_registry.handle_start(_payload(cwd="/tmp/proj"))

    assert resp.suppress_output is True
    # Exactly one subprocess, timeout-guarded, targeting the pane id.
    assert len(calls) == 1
    assert calls[0]["cmd"][:2] == ["tmux", "display-message"]
    assert "%7" in calls[0]["cmd"]
    assert calls[0]["kwargs"].get("timeout") == pytest.approx(2.0)

    rows = _rows()
    assert len(rows) == 1
    expected = {"trace_id": "s-bridge", "pane_id": "%7",
                "tmux_server_pid": 4242, "pane_pid": 777,
                "tmux_socket": "/tmp/sock",
                "reachable": 1, "cwd": "/tmp/proj"}
    assert {k: rows[0][k] for k in expected} == expected
    assert rows[0]["updated_at"]


def test_missing_tmux_env_stores_null_socket(monkeypatch):
    # Inside tmux (TMUX_PANE present) but $TMUX unset (default socket, or an
    # env that never exported it) → tmux_socket persists as NULL; delivery
    # then omits -S and targets the default socket.
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setenv("TMUX_PANE", "%8")
    monkeypatch.delenv("TMUX", raising=False)
    _mock_tmux(monkeypatch, server_pid=99, pane_pid=88)

    resp = bridge_registry.handle_start(_payload())

    assert resp.suppress_output is True
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["tmux_socket"] is None


def test_resume_upserts_single_row_with_fresh_coordinates(monkeypatch):
    monkeypatch.setenv("REGIN_BRIDGE", "1")

    # First registration.
    monkeypatch.setenv("TMUX_PANE", "%0")
    _mock_tmux(monkeypatch, server_pid=1000, pane_pid=11)
    bridge_registry.handle_start(_payload(cwd="/tmp/old"))

    # Resume after a tmux server restart: same session id, everything else new.
    monkeypatch.setenv("TMUX_PANE", "%5")
    _mock_tmux(monkeypatch, server_pid=2000, pane_pid=22)
    bridge_registry.handle_start(_payload(cwd="/tmp/new"))

    rows = _rows()
    assert len(rows) == 1  # UPSERT, not a second row
    row = rows[0]
    # ALL mutable columns overwritten — nothing stale from the first write.
    assert row["pane_id"] == "%5"
    assert row["tmux_server_pid"] == 2000
    assert row["pane_pid"] == 22
    assert row["cwd"] == "/tmp/new"
    assert row["reachable"] == 1


def test_tmux_nonzero_exit_records_nothing(monkeypatch):
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setenv("TMUX_PANE", "%9")
    _mock_tmux(monkeypatch, returncode=1, stdout="")

    resp = bridge_registry.handle_start(_payload())

    assert resp is not None and resp.suppress_output is True
    assert _rows() == []


def test_tmux_timeout_records_nothing(monkeypatch):
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setenv("TMUX_PANE", "%9")
    _mock_tmux(monkeypatch,
               raise_exc=subprocess.TimeoutExpired(cmd="tmux", timeout=2.0))

    resp = bridge_registry.handle_start(_payload())

    assert resp is not None and resp.suppress_output is True
    assert _rows() == []


def test_tmux_binary_missing_records_nothing(monkeypatch):
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setenv("TMUX_PANE", "%9")
    _mock_tmux(monkeypatch, raise_exc=FileNotFoundError("tmux"))

    resp = bridge_registry.handle_start(_payload())

    assert resp is not None and resp.suppress_output is True
    assert _rows() == []


def _turn_payload(session_id="s-bridge", cwd="/tmp/proj"):
    raw = {"cwd": cwd, "session_id": session_id}
    return HookPayload(event="UserPromptSubmit", cwd=cwd,
                       session_id=session_id, raw=raw)


def test_turn_noop_when_flag_unset(monkeypatch):
    bridge_registry._registered_panes.clear()
    monkeypatch.delenv("REGIN_BRIDGE", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%3")
    _forbid_subprocess(monkeypatch)

    resp = bridge_registry.handle_turn(_turn_payload())

    assert resp is not None and resp.suppress_output is True
    assert _rows() == []


def test_turn_heals_missed_session_start(monkeypatch):
    # SessionStart fired while the tmux query was failing → no row, nothing
    # cached. The session would otherwise never get a /live composer.
    bridge_registry._registered_panes.clear()
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setenv("TMUX_PANE", "%7")
    _mock_tmux(monkeypatch, returncode=1, stdout="")
    bridge_registry.handle_start(_payload())
    assert _rows() == []  # SessionStart registered nothing

    # Next turn, tmux is healthy → the pane self-registers.
    _mock_tmux(monkeypatch, server_pid=4242, pane_pid=777)
    resp = bridge_registry.handle_turn(_turn_payload())

    assert resp.suppress_output is True
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["pane_id"] == "%7"
    assert rows[0]["reachable"] == 1


def test_turn_is_deduped_after_registration(monkeypatch):
    # Once a pane is registered this process, later turns must NOT re-run the
    # tmux subprocess — the heal costs one query per pane, not one per turn.
    bridge_registry._registered_panes.clear()
    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setenv("TMUX_PANE", "%7")
    _mock_tmux(monkeypatch, server_pid=4242, pane_pid=777)
    bridge_registry.handle_start(_payload())
    assert len(_rows()) == 1

    _forbid_subprocess(monkeypatch)
    resp = bridge_registry.handle_turn(_turn_payload())  # cached → no subprocess

    assert resp.suppress_output is True
    assert len(_rows()) == 1


# The slice-1 pre-migration shape: 9 columns, NO tmux_socket. Matches the
# original bridge_panes DDL before the delivery slice added the socket.
_PRE_MIGRATION_DDL = """
CREATE TABLE bridge_panes (
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


def _reshape_bridge_panes(ddl: str) -> None:
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.execute("DROP INDEX IF EXISTS idx_bridge_panes_reachable")
        conn.execute("DROP TABLE IF EXISTS bridge_panes")
        conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()


def _pane_columns() -> set[str]:
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(bridge_panes)")}
    finally:
        conn.close()


def test_ensure_schema_backfills_tmux_socket_on_old_shape(monkeypatch):
    # Reshape to the slice-1 9-column table (no tmux_socket), the exact
    # drift that made the socket-aware UPSERT/SELECT raise OperationalError.
    _reshape_bridge_panes(_PRE_MIGRATION_DDL)
    assert "tmux_socket" not in _pane_columns()  # before
    monkeypatch.setattr(bridge_registry, "_schema_ready", False)

    bridge_registry.ensure_schema()

    assert "tmux_socket" in _pane_columns()  # after: additive ALTER ran


def test_backfilled_old_db_stores_socket_end_to_end(monkeypatch):
    # An old DB, migrated by ensure_schema, then written through the real
    # registration path with $TMUX set — the socket must land in the row.
    _reshape_bridge_panes(_PRE_MIGRATION_DDL)
    monkeypatch.setattr(bridge_registry, "_schema_ready", False)

    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setenv("TMUX_PANE", "%4")
    monkeypatch.setenv("TMUX", "/tmp/oldsock,321,0")
    _mock_tmux(monkeypatch, server_pid=808, pane_pid=90)

    resp = bridge_registry.handle_start(_payload())

    assert resp is not None and resp.suppress_output is True
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["tmux_socket"] == "/tmp/oldsock"
    assert rows[0]["pane_id"] == "%4"


def test_ensure_schema_is_idempotent_on_current_shape(monkeypatch):
    # Running twice against an already-correct table must not error or
    # double-add (PRAGMA guard).
    monkeypatch.setattr(bridge_registry, "_schema_ready", False)
    bridge_registry.ensure_schema()
    monkeypatch.setattr(bridge_registry, "_schema_ready", False)
    bridge_registry.ensure_schema()  # no OperationalError on re-add
    cols = _pane_columns()
    assert "tmux_socket" in cols


def test_ensure_schema_creates_table_on_bare_db(monkeypatch):
    # Simulate a DB that predates the bridge: drop the table schema.sql
    # seeded and force ensure_schema to run again in this process.
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.execute("DROP INDEX IF EXISTS idx_bridge_panes_reachable")
        conn.execute("DROP TABLE bridge_panes")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(bridge_registry, "_schema_ready", False)

    monkeypatch.setenv("REGIN_BRIDGE", "1")
    monkeypatch.setenv("TMUX_PANE", "%2")
    _mock_tmux(monkeypatch, server_pid=555, pane_pid=66)

    resp = bridge_registry.handle_start(_payload())

    assert resp is not None and resp.suppress_output is True
    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["pane_id"] == "%2"
    assert rows[0]["tmux_server_pid"] == 555
    assert rows[0]["pane_pid"] == 66
    assert rows[0]["reachable"] == 1
