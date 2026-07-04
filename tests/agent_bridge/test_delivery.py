"""Agent-bridge delivery engine (`lib/agent_bridge/delivery.py`).

Every guard is exercised against a mocked tmux — the real send-keys path is
the verifier's live selftest, not a unit test. The invariants pinned here:

  * structured refusal (never an exception) on disabled / no-row / stale id
    / non-claude command / ack failure / rate limit,
  * the registered `tmux_socket` is threaded into EVERY tmux argv (`-S`),
    and omitted when NULL,
  * sanitization strips control/ANSI/newline before the literal send,
  * the per-trace_id rate limit is independent across trace_ids.
"""

from __future__ import annotations

import subprocess

import pytest

from lib.agent_bridge import delivery
from lib.settings import settings


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Fresh rate-limit history + a permissive enabled config per test."""
    delivery._HISTORY.clear()
    cfg = settings.agent_bridge
    monkeypatch.setattr(cfg, "enabled", True)
    monkeypatch.setattr(cfg, "rate_limit_per_minute", 30)
    monkeypatch.setattr(cfg, "max_text_len", 4000)
    monkeypatch.setattr(cfg, "allowed_pane_commands",
                        ["claude", "claude.exe", "node"])
    # No real sleeps in the ack path.
    monkeypatch.setattr(delivery.time, "sleep", lambda *_a, **_k: None)
    yield


def _install_tmux(monkeypatch, *, server_pid=111, pane_pid=222,
                  command="claude", in_mode="0", capture=None,
                  identity_rc=0):
    """Mock subprocess.run so `_tmux` builds real argv we can inspect."""
    calls: list[list[str]] = []

    def _fake_run(cmd, **_kw):
        calls.append(list(cmd))
        if "display-message" in cmd:
            line = f"{server_pid}\t{pane_pid}\t{command}\t{in_mode}"
            return subprocess.CompletedProcess(cmd, identity_rc,
                                               stdout=line, stderr="")
        if "capture-pane" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=capture or "",
                                               stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return calls


def _pane_row(**over):
    row = {"pane_id": "%7", "tmux_socket": None,
           "tmux_server_pid": 111, "pane_pid": 222}
    row.update(over)
    return row


def _set_row(monkeypatch, row):
    monkeypatch.setattr(delivery.store, "get_reachable_pane",
                        lambda _tid: row)


def _literal_sends(calls):
    """The send-keys -l -- <text> argv(s)."""
    return [c for c in calls if "-l" in c and "--" in c]


def _enter_sent(calls):
    return any(c[-1] == "Enter" for c in calls)


# ── 1. disabled ──────────────────────────────────────────────
def test_disabled_bridge_refuses_without_tmux(monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    calls = _install_tmux(monkeypatch)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", "hello")

    assert res.delivered is False
    assert "disabled" in res.detail
    assert calls == []  # no tmux subprocess at all


# ── 2. no reachable row ──────────────────────────────────────
def test_no_reachable_row_refuses(monkeypatch):
    calls = _install_tmux(monkeypatch)
    _set_row(monkeypatch, None)

    res = delivery.deliver("t1", "hello")

    assert res.delivered is False
    assert "no reachable session" in res.detail
    assert calls == []


# ── 3. identity mismatch (staleness guard) ───────────────────
def test_server_pid_mismatch_refuses(monkeypatch):
    calls = _install_tmux(monkeypatch, server_pid=999)  # live 999 != row 111
    _set_row(monkeypatch, _pane_row(tmux_server_pid=111))

    res = delivery.deliver("t1", "hello")

    assert res.delivered is False
    assert "stale" in res.detail and "server pid" in res.detail
    assert not _literal_sends(calls)  # refused before typing


def test_pane_pid_mismatch_refuses(monkeypatch):
    calls = _install_tmux(monkeypatch, pane_pid=888)  # live 888 != row 222
    _set_row(monkeypatch, _pane_row(pane_pid=222))

    res = delivery.deliver("t1", "hello")

    assert res.delivered is False
    assert "stale" in res.detail and "pane pid" in res.detail
    assert not _literal_sends(calls)


# ── 4. command not allowlisted (shell-exec guard) ────────────
def test_non_claude_command_refuses(monkeypatch):
    calls = _install_tmux(monkeypatch, command="fish")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", "hello")

    assert res.delivered is False
    assert "fish" in res.detail and "refused" in res.detail
    assert not _literal_sends(calls)  # the keystrokes never reach the shell


# ── 5. socket threading ──────────────────────────────────────
def test_socket_threaded_into_every_tmux_call(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude", capture="hello there")
    _set_row(monkeypatch, _pane_row(tmux_socket="/tmp/alt"))

    res = delivery.deliver("t1", "hello there")

    assert res.delivered is True
    assert calls  # some tmux ran
    for c in calls:
        assert c[:3] == ["tmux", "-S", "/tmp/alt"]


def test_null_socket_omits_dash_s(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude", capture="hello there")
    _set_row(monkeypatch, _pane_row(tmux_socket=None))

    res = delivery.deliver("t1", "hello there")

    assert res.delivered is True
    for c in calls:
        # No socket flag: argv[1] is the subcommand, not "-S". (capture-pane
        # carries its own "-S -40" start-line arg later in the argv, so we
        # check only the socket position, not the whole argv.)
        assert c[0] == "tmux" and c[1] != "-S"


# ── 6. copy-mode cancel ──────────────────────────────────────
def test_copy_mode_cancelled_before_literal_send(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude", in_mode="1",
                          capture="hello")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", "hello")

    assert res.delivered is True
    cancel_idx = next(i for i, c in enumerate(calls) if "cancel" in c)
    send_idx = next(i for i, c in enumerate(calls) if "-l" in c and "--" in c)
    assert cancel_idx < send_idx
    assert calls[cancel_idx][-2:] == ["-X", "cancel"]


# ── 7. ack failure ───────────────────────────────────────────
def test_ack_failure_does_not_submit(monkeypatch):
    # capture-pane never shows the typed text → not delivered, no Enter.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="a totally different screen")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", "hello world")

    assert res.delivered is False
    assert "not visible" in res.detail
    assert not _enter_sent(calls)


# ── 8. happy path ────────────────────────────────────────────
def test_happy_path_delivers_and_submits(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude.exe",
                          capture="prompt> status update please")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", "status update please")

    assert res.delivered is True
    assert len(_literal_sends(calls)) == 1  # exactly one literal send
    assert _enter_sent(calls)


# ── 9. sanitize ──────────────────────────────────────────────
def test_sanitize_strips_control_ansi_newline(monkeypatch):
    raw = "line one\nline\x1b[31mtwo\x03 end"
    # capture must contain the sanitized text for the ack to pass.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="prompt> line one linetwo end")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", raw)

    payload = _literal_sends(calls)[0][-1]
    assert "\n" not in payload and "\x1b" not in payload and "\x03" not in payload
    assert "[31m" not in payload
    assert payload == "line one linetwo end"
    assert res.delivered is True


# ── 10a. drifted schema: deliver() honors no-raise contract ──
# The slice-1 9-column shape, missing tmux_socket — the SELECT in
# store.get_reachable_pane names that column, so a raw execute raises
# OperationalError on this table. deliver() must still refuse, not raise.
_OLD_BRIDGE_PANES_DDL = """
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


def _reshape_to_old_bridge_panes():
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        conn.execute("DROP INDEX IF EXISTS idx_bridge_panes_reachable")
        conn.execute("DROP TABLE IF EXISTS bridge_panes")
        conn.execute(_OLD_BRIDGE_PANES_DDL)
        conn.execute(
            "INSERT INTO bridge_panes "
            "(trace_id, pane_id, tmux_server_pid, pane_pid, reachable) "
            "VALUES ('t1', '%7', 111, 222, 1)"
        )
        conn.commit()
    finally:
        conn.close()


def test_drifted_schema_refuses_without_raising(monkeypatch):
    # Real get_reachable_pane (not stubbed) hits a pre-migration table.
    _reshape_to_old_bridge_panes()
    calls = _install_tmux(monkeypatch)

    res = delivery.deliver("t1", "hello")  # must not raise

    assert res.delivered is False
    assert "no reachable session" in res.detail
    assert calls == []  # refused before any tmux subprocess


# ── 10. rate limit ───────────────────────────────────────────
def test_rate_limit_is_per_trace_id(monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "rate_limit_per_minute", 2)
    _install_tmux(monkeypatch, command="claude", capture="hi")
    _set_row(monkeypatch, _pane_row())

    r1 = delivery.deliver("busy", "hi")
    r2 = delivery.deliver("busy", "hi")
    r3 = delivery.deliver("busy", "hi")
    other = delivery.deliver("calm", "hi")

    assert r1.delivered is True and r2.delivered is True
    assert r3.delivered is False and "rate limit" in r3.detail
    assert other.delivered is True  # a different trace_id is unaffected


# ── 11. deliver_answer: AskUserQuestion select-TUI driving ────
def _downs(calls):
    return [c for c in calls if c[-1] == "Down"]


def _enters(calls):
    return [c for c in calls if c[-1] == "Enter"]


def test_answer_option_navigates_and_submits(monkeypatch):
    # Pick option index 2: Down×2 then a single Enter, no literal typing.
    calls = _install_tmux(monkeypatch, command="claude")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 2)

    assert res.delivered is True
    assert len(_downs(calls)) == 2
    assert len(_enters(calls)) == 1  # exactly one submission
    assert not _literal_sends(calls)  # a plain pick never types


def test_answer_option_zero_sends_no_down(monkeypatch):
    # The cursor starts on option 0 — no navigation, just Enter.
    calls = _install_tmux(monkeypatch, command="claude")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 0)

    assert res.delivered is True
    assert _downs(calls) == []
    assert len(_enters(calls)) == 1


def test_answer_free_text_navigates_types_acks_submits(monkeypatch):
    # Free-text at index 3 (the "Type something." entry): Down×3, Enter to
    # open the field, literal type, ack, then a final Enter.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="prompt> my own answer")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 3, "my own answer")

    assert res.delivered is True
    assert len(_downs(calls)) == 3
    assert _literal_sends(calls)[0][-1] == "my own answer"
    assert len(_enters(calls)) == 2  # open the field + submit


def test_answer_free_text_ack_failure_does_not_submit(monkeypatch):
    # The typed answer never echoes → not delivered, and the SUBMIT Enter is
    # withheld (only the field-opening Enter fires).
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="a different screen")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 1, "unseen answer")

    assert res.delivered is False
    assert "not visible" in res.detail
    assert len(_enters(calls)) == 1  # field opened, but never submitted


def test_answer_out_of_range_refuses_before_tmux(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude")
    _set_row(monkeypatch, _pane_row())

    lo = delivery.deliver_answer("t1", -1)
    hi = delivery.deliver_answer("t1", 999)

    assert lo.delivered is False and "out of range" in lo.detail
    assert hi.delivered is False and "out of range" in hi.detail
    assert calls == []  # refused before any tmux subprocess


def test_answer_empty_free_text_refuses(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 0, "   \x1b[0m ")  # empty after sanitize

    assert res.delivered is False
    assert "empty answer" in res.detail
    assert calls == []


def test_answer_stale_identity_refuses(monkeypatch):
    calls = _install_tmux(monkeypatch, server_pid=999)  # live 999 != row 111
    _set_row(monkeypatch, _pane_row(tmux_server_pid=111))

    res = delivery.deliver_answer("t1", 1)

    assert res.delivered is False
    assert "stale" in res.detail
    assert _downs(calls) == [] and _enters(calls) == []  # never drove the TUI
