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

from lib.agent_bridge import delivery, store
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
    """Mock subprocess.run so `_tmux` builds real argv we can inspect.

    `capture` may be a list, in which case successive capture-pane calls read
    successive entries (the last repeats) — what a pane whose composer CHANGES
    between the pre-type baseline read and the ack poll needs.
    """
    calls: list[list[str]] = []
    captures = [0]

    def _next_capture():
        entry = capture or ""
        if isinstance(capture, list):
            i = min(captures[0], len(capture) - 1)
            captures[0] += 1
            entry = capture[i]
        # A (rc, stdout) entry models a capture-pane that FAILED.
        return entry if isinstance(entry, tuple) else (0, entry)

    def _fake_run(cmd, **_kw):
        calls.append(list(cmd))
        if "display-message" in cmd:
            line = f"{server_pid}\t{pane_pid}\t{command}\t{in_mode}"
            return subprocess.CompletedProcess(cmd, identity_rc,
                                               stdout=line, stderr="")
        if "capture-pane" in cmd:
            rc, out = _next_capture()
            return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return calls


def _pane_row(**over):
    row = {"pane_id": "%7", "tmux_socket": None,
           "tmux_server_pid": 111, "pane_pid": 222}
    row.update(over)
    return row


def _set_row(monkeypatch, row, *, answered=True):
    """Stub both views of the pane registry.

    `answered=False` models a store that could not be QUERIED (locked DB,
    drifted schema) — which `resolve_reachable_pane` reports separately and a
    bare row/None cannot express.
    """
    monkeypatch.setattr(delivery.store, "resolve_reachable_pane",
                        lambda _tid: (row, answered))
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


# ── 8b. ack survives the composer's line-wrap (reflow) ───────
def _cu_sent(calls):
    return any(c[-1] == "C-u" for c in calls)


def test_ack_matches_wrapped_echo(monkeypatch):
    # Claude soft-wraps a long line, injecting a newline + `│` gutter mid-text.
    # The ack's needle (text[:30]) straddles that boundary; a raw substring
    # match misses it (RED before the reflow-tolerant compare), so the good
    # send was reported "not visible". Normalized, it matches → submits.
    text = "please refactor the delivery layer now"
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="> please refactor the\n│   delivery layer now")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", text)

    assert res.delivered is True
    assert _enter_sent(calls)  # actually submitted
    assert not _cu_sent(calls)  # happy path never clears the line


# ── 8c. ack survives the composer collapsing bulk input to a chip ──
# Deliberately NON-repeating: with a repeated phrase the head needle appears
# inside every window of the text, which would let the tail test pass on the
# head rule alone and pin nothing.
_LONG = " ".join(f"clause {i:02d} of one long single-line prompt body"
                 for i in range(24))  # ~1080 chars


def test_ack_accepts_a_new_paste_chip_as_the_echo(monkeypatch):
    # Past ~880 chars the composer draws `[Pasted text #N]` INSTEAD of the
    # text, so the literal needle can never match (RED: nine real refusals of
    # a 942-char prompt whose keystrokes had landed). A chip numbered above
    # the pre-type baseline is the echo.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=["❯ ", "❯ [Pasted text #43]"])
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", _LONG)

    assert res.delivered is True
    assert _enter_sent(calls)
    assert not _cu_sent(calls)  # nothing to clear — this is the happy path


def test_ack_ignores_a_paste_chip_that_predates_the_send(monkeypatch):
    # A chip already staged in the composer (or still in scrollback) is not
    # evidence OUR burst arrived. The counter is monotonic, so "unchanged"
    # means nothing new landed: refuse, clear, and never submit.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="❯ [Pasted text #43]")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", _LONG)

    assert res.delivered is False
    assert "not visible" in res.detail
    assert not _enter_sent(calls)
    assert _cu_sent(calls)


def test_ack_paste_baseline_is_read_before_the_literal_send(monkeypatch):
    # The baseline must be captured BEFORE typing or our own chip is already
    # in it and can never exceed itself.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=["❯ ", "❯ [Pasted text #1]"])
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", _LONG).delivered is True
    cap_idx = next(i for i, c in enumerate(calls) if "capture-pane" in c)
    type_idx = next(i for i, c in enumerate(calls) if "-l" in c and "--" in c)
    assert cap_idx < type_idx


def test_ack_matches_a_horizontally_scrolled_composer_by_its_tail(monkeypatch):
    # Third rendering of a long line: the composer scrolls instead of wrapping
    # or collapsing, so ONLY the end is on screen and the head needle is off
    # the left edge. Observed live on a fresh pane at 861-1189 chars.
    scrolled = "❯ " + _LONG[-40:]
    assert _LONG[:30] not in scrolled  # else the OLD head rule would ack it
    calls = _install_tmux(monkeypatch, command="claude", capture=scrolled)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", _LONG)

    assert res.delivered is True
    assert _enter_sent(calls)


def test_ack_still_fails_when_neither_end_echoes(monkeypatch):
    # The tail needle must not become a blanket pass: a pane showing something
    # else entirely (a modal dialog swallowing the keystrokes) still refuses.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="Do you want to proceed?\n❯ 1. Yes\n  2. No")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", _LONG)

    assert res.delivered is False
    assert not _enter_sent(calls)


def test_ack_unreadable_baseline_disables_the_chip_rule(monkeypatch):
    # `_tmux` flattens EVERY failure to rc=1 + empty stdout, so a baseline read
    # that failed would compute a zero high-water mark and promote a stale chip
    # already in the window to "newer than before we typed" — an Enter on text
    # that never reached the composer. A failed read means UNKNOWN: chip rule
    # off, literal echo only, and this pane never echoes it.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=[(1, ""), "❯ [Pasted text #43]"])
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", _LONG)

    assert res.delivered is False
    assert not _enter_sent(calls)


def test_ack_reads_a_wrapped_paste_chip(monkeypatch):
    # On a narrow pane the composer wraps the chip itself; the counter must
    # still be read, or the original bug returns for narrow panes.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=["❯ ", "❯ [Pasted\n│ text #43]"])
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", _LONG).delivered is True
    assert _enter_sent(calls)


def test_menu_free_text_ack_never_accepts_a_paste_chip(monkeypatch):
    # A menu's free-text entry is not the composer and does not collapse; the
    # chip rule must not leak there and blind-submit an unechoed answer.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=["❯ ", "❯ [Pasted text #43]"])
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 0, free_text=_LONG)

    assert res.delivered is False
    assert "not visible" in res.detail
    assert not _enter_sent(calls)


def test_ack_failure_clears_the_typed_line(monkeypatch):
    # Genuine ack failure: the keystrokes landed but never echoed. The line
    # MUST be cleared (C-u) so the client's preserved-draft retry doesn't type
    # on top and submit a duplicated / concatenated prompt — and Enter is still
    # never sent.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="a totally different screen")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", "hello world")

    assert res.delivered is False
    assert _cu_sent(calls)
    assert not _enter_sent(calls)
    # The clear happens AFTER the literal type (clears what was typed) and
    # BEFORE any Enter — the sequence a retry relies on.
    type_idx = next(i for i, c in enumerate(calls) if "-l" in c and "--" in c)
    cu_idx = next(i for i, c in enumerate(calls) if c[-1] == "C-u")
    assert type_idx < cu_idx


def _keys_before_literal(calls):
    """The bare send-keys argv issued before the literal type, last arg only."""
    literal = next(i for i, c in enumerate(calls) if "-l" in c and "--" in c)
    return [c[-1] for c in calls[:literal]
            if "send-keys" in c and "-l" not in c and "-X" not in c]


def test_command_resets_composer_into_insert_mode_before_typing(monkeypatch):
    # The Escape that cancels a turn also drops a vim-mode composer out of
    # INSERT, where literal text runs as motions — "/exit" left only its
    # trailing `t` in the pane and the session never quit. The mode cannot be
    # read back (claude renders no NORMAL marker, same as vim being off), so
    # the reset must be correct blind: C-u clears, `i` re-enters INSERT (or
    # types one stray char when vim is off), C-u clears that.
    calls = _install_tmux(monkeypatch, command="claude", capture="> /exit")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", "/exit", as_command=True)

    assert res.delivered is True
    assert _keys_before_literal(calls) == ["C-u", "i", "C-u"]
    assert _enter_sent(calls)


def test_command_is_terminated_so_the_menu_cannot_eat_the_enter(monkeypatch):
    # Typing a slash command opens claude's autocomplete, and Enter then runs
    # the HIGHLIGHTED entry — fuzzy ranking, so not necessarily the command
    # typed: a live run submitted /python-complexity for "/exit" ("compl-EXIT-y").
    # The trailing space completes the token and dismisses the menu.
    calls = _install_tmux(monkeypatch, command="claude", capture="> /exit")
    _set_row(monkeypatch, _pane_row())

    delivery.deliver("t1", "/exit", as_command=True)

    assert [c[-1] for c in _literal_sends(calls)] == ["/exit "]


def test_a_half_sent_reset_refuses_to_type_on_the_residue(monkeypatch):
    # The reset is only empty at its LAST key, so a failed send can leave the
    # stray `i` in the composer. Typing on top would submit `i/exit`.
    def _fake_run(cmd, **_kw):
        if "display-message" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="111\t222\tclaude\t0",
                                               stderr="")
        rc = 1 if cmd[-1] in ("C-u", "i") else 0
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", "/exit", as_command=True)

    assert res.delivered is False
    assert "reset failed" in res.detail


def test_ordinary_delivery_types_the_message_verbatim(monkeypatch):
    # Steering is not a command: no reset (the composer may hold a human's
    # draft) and no terminator (it would alter the message). The body itself
    # rides inside bracketed-paste markers, and only the body.
    calls = _install_tmux(monkeypatch, command="claude", capture="> hi there")
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", "hi there").delivered is True
    assert _keys_before_literal(calls) == []
    assert [c[-1] for c in _literal_sends(calls)] == ["\x1b[200~hi there\x1b[201~"]


def test_ack_failure_reset_ends_in_insert_mode(monkeypatch):
    # A bare C-u is ignored by a NORMAL-mode composer, so the residue the
    # clear exists to remove would survive it. The recovery uses the same
    # blind reset, which clears in either mode.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="a totally different screen")
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", "hello world").delivered is False
    literal = next(i for i, c in enumerate(calls) if "-l" in c and "--" in c)
    assert [c[-1] for c in calls[literal + 1:] if "send-keys" in c] \
        == ["C-u", "i", "C-u"]


def test_ack_bar_only_needle_fails_closed_no_blind_submit(monkeypatch):
    # A needle made only of whitespace / vertical bars normalizes to empty.
    # It must NOT short-circuit the ack into a blind submit (an empty needle
    # substring-matches any capture) — refuse, clear the line, send no Enter.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="a totally different screen with no bars")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", "|| | ||")

    assert res.delivered is False
    assert not _enter_sent(calls)  # nothing submitted un-echoed
    assert _cu_sent(calls)         # residue cleared like any ack failure


# ── 9. sanitize ──────────────────────────────────────────────
def test_sanitize_strips_control_and_ansi_but_keeps_line_breaks(monkeypatch):
    # ANSI drives the TUI and a stray control byte can interrupt the agent, so
    # both still go. The NEWLINE stays: it is the message's structure, and the
    # bracketed paste inserts it instead of submitting on it.
    raw = "line one\nline\x1b[31mtwo\x03 end"
    # capture must contain the sanitized text for the ack to pass; the ack
    # normalizes whitespace on both sides, so the wrapped echo matches.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="prompt> line one linetwo end")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", raw)

    payload = _literal_sends(calls)[0][-1]
    assert "\x1b[31m" not in payload and "\x03" not in payload
    assert payload == "\x1b[200~line one\nlinetwo end\x1b[201~"
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


def test_drifted_schema_is_indeterminate_not_an_empty_registry(monkeypatch):
    """Same drifted table, asked the other question.

    `deliver` only needs "refuse and do not raise", so None is right for it.
    A shutdown caller needs to know regin never got an answer — reported as an
    empty registry, a locked or drifted store lets the UI tick off a session
    that may still be running.
    """
    _reshape_to_old_bridge_panes()
    _install_tmux(monkeypatch)

    row, answered = store.resolve_reachable_pane("t1")
    assert row is None
    assert answered is False
    assert delivery.session_liveness("t1") == delivery.INDETERMINATE


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
    # Free-text at index 3 (the "Type something." entry): Down×3, then type
    # DIRECTLY (no opening Enter — that would decline the question), ack, then a
    # single Enter to submit the custom answer.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="prompt> my own answer")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 3, "my own answer")

    assert res.delivered is True
    assert len(_downs(calls)) == 3
    assert _literal_sends(calls)[0][-1] == "my own answer"
    assert len(_enters(calls)) == 1  # submit only — NO field-opening Enter


def test_answer_free_text_never_sends_declining_enter_before_typing(monkeypatch):
    # Regression: an Enter BEFORE the literal type declines the ask. The literal
    # send must come first, with no Enter between navigation and typing.
    calls = _install_tmux(monkeypatch, command="claude", capture="prompt> hi")
    _set_row(monkeypatch, _pane_row())

    delivery.deliver_answer("t1", 2, "hi")

    seq = [c for c in calls if c[-1] == "Enter" or ("-l" in c and "--" in c)]
    # The literal send must precede the only Enter.
    assert "-l" in seq[0] and seq[-1][-1] == "Enter"
    assert sum(1 for c in seq if c[-1] == "Enter") == 1


def test_answer_chat_present_navigates_and_dismisses(monkeypatch):
    # expect_chat: the "Chat about this" entry (index 4) is present, so the
    # menu is navigated to and dismissed with one Enter; no text typed.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="4. Type something.\n  5. Chat about this")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 4, expect_chat=True)

    assert res.delivered is True
    assert len(_downs(calls)) == 4
    assert len(_enters(calls)) == 1  # dismiss the menu
    assert not _literal_sends(calls)


def test_answer_chat_with_message_navigates_types_acks_submits(monkeypatch):
    # expect_chat + a message: navigate to the chat entry (index 4), Enter to
    # dismiss the menu, type the message into the reopened composer, ack, submit.
    # The capture must satisfy BOTH the chat-entry guard and the typed-text ack.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="5. Chat about this\nprompt> why these three?")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 4, "why these three?", expect_chat=True)

    assert res.delivered is True
    assert len(_downs(calls)) == 4
    assert _literal_sends(calls)[0][-1] == "\x1b[200~why these three?\x1b[201~"
    assert len(_enters(calls)) == 2  # dismiss the menu + submit the message


def test_answer_chat_absent_refuses_without_navigating(monkeypatch):
    # A build lacking the "Chat about this" entry must refuse, not over-walk
    # the menu and answer the wrong entry.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="1. Red\n  2. Green\n  3. Type something.")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 3, expect_chat=True)

    assert res.delivered is False
    assert "Chat about this" in res.detail
    assert _downs(calls) == [] and _enters(calls) == []


def test_answer_free_text_ack_failure_does_not_submit(monkeypatch):
    # The typed answer never echoes → not delivered, and NO Enter is ever sent
    # (free-text types directly onto the "Type something." entry — there is no
    # field-opening Enter, and the submit Enter is withheld on ack failure).
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="a different screen")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 1, "unseen answer")

    assert res.delivered is False
    assert "not visible" in res.detail
    assert len(_enters(calls)) == 0  # nothing submitted, nothing declined


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


# ── 11b. deliver_answers: multi-question tabbed-TUI walk + submit ────
# The tabbed TUI (empirically, claude v2.1.x): selecting an option checks its
# question tab AND auto-advances; the last select lands on the review/Submit
# tab, which needs one more Enter. A single fake capture string stands in for
# the pane, so every question text + "Submit answers" in it makes the focus
# guards pass; omit one to exercise a refusal.
def test_answers_walks_each_question_then_submits(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="PROBEQ1 which box\nPROBEQ2 which cache\nSubmit answers")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": 1, "confirm_text": "PROBEQ1 which box"},
        {"option_index": 2, "confirm_text": "PROBEQ2 which cache"},
    ])

    assert res.delivered is True and "submitted 2 answers" in res.detail
    assert len(_downs(calls)) == 3   # 1 (Q1 option) + 2 (Q2 option)
    assert len(_enters(calls)) == 3  # Q1 select + Q2 select + Submit


def test_answers_refuses_when_a_question_not_focused(monkeypatch):
    # Q2's text is not on-screen (a failed advance) → stop before answering it,
    # and never reach the Submit Enter.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="PROBEQ1 which box\nSubmit answers")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": 0, "confirm_text": "PROBEQ1 which box"},
        {"option_index": 0, "confirm_text": "PROBEQ2 which cache"},
    ])

    assert res.delivered is False
    assert "question 2 not focused" in res.detail
    assert len(_enters(calls)) == 1  # only Q1's select landed; no Submit


def test_answers_refuses_when_submit_screen_absent(monkeypatch):
    # Every question answered but the review/Submit tab never shows → do NOT
    # press a blind Enter that might do the wrong thing; refuse.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="PROBEQ1 which box\nPROBEQ2 which cache")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": 0, "confirm_text": "PROBEQ1 which box"},
        {"option_index": 0, "confirm_text": "PROBEQ2 which cache"},
    ])

    assert res.delivered is False
    assert "submit screen not shown" in res.detail
    assert len(_enters(calls)) == 2  # both selects, but no Submit Enter


def test_answers_free_text_question_types_and_acks(monkeypatch):
    # A per-question typed answer rides the "Type something." entry: navigate,
    # type, ack (capture shows it), Enter — then the next question, then submit.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="PROBEQ1\nPROBEQ2\nSubmit answers\nmy typed reply")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": 2, "text": "my typed reply", "confirm_text": "PROBEQ1"},
        {"option_index": 0, "confirm_text": "PROBEQ2"},
    ])

    assert res.delivered is True
    assert any(c[-1] == "my typed reply" for c in _literal_sends(calls))


def test_answers_invalid_shape_refuses_before_tmux(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude")
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver_answers("t1", []).delivered is False
    assert delivery.deliver_answers("t1", [{"option_index": -1}]).delivered is False
    assert delivery.deliver_answers("t1", "nope").delivered is False
    assert calls == []  # rejected before any tmux subprocess


# ── 11c. multi-SELECT questions: toggle the rows, then advance ───────
# A different widget from a single-select tab (empirically, claude v2.1.252):
# the options are checkboxes, Enter TOGGLES one and leaves the cursor put, and
# an extra unnumbered "Next"/"Submit" row at nav index option_count + 1 is what
# advances. The driver reads that widget rather than assuming it, so these
# fakes render a real one — `_install_tmux(capture=[...])` feeds successive
# capture-pane calls, which is how the pane's BEFORE and AFTER states differ.
# Every screen carries the question texts and "Submit answers" so the focus
# guards pass; the widget rows are what each test actually varies.
_TAIL = "PROBEQ2 which cache\nSubmit answers"


def _multi_screen(labels, *, checked=(), cursor=0, above="",
                  question="PROBEQ1 pick many"):
    """One rendered multi-select widget, in the real row order."""
    lines = ([above] if above else []) + [question]
    for i, label in enumerate(labels):
        mark = "✔" if i in checked else " "
        lines.append(f"{'❯ ' if i == cursor else '  '}{i + 1}. [{mark}] {label}")
    n = len(labels)
    lines.append(f"{'❯ ' if cursor == n else '  '}{n + 1}. [ ] Type something")
    lines.append("     Submit")
    lines.append(f"  {n + 2}. Chat about this")
    return "\n".join(lines + [_TAIL])


_ABC = ("A", "B", "C")


def test_multi_select_toggles_each_option_then_advances(monkeypatch):
    before = _multi_screen(_ABC)
    after = _multi_screen(_ABC, checked={0, 2}, cursor=2)
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=[before, before, after])
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": [0, 2], "option_count": 3,
         "confirm_text": "PROBEQ1 pick many"},
    ])

    assert res.delivered is True and "submitted 1 answers" in res.detail
    # 0→0 (0) + 0→2 (2) + cursor 2 → advance row 4 (2)
    assert len(_downs(calls)) == 4
    # two toggles + the advance row + the review tab's Submit answers
    assert len(_enters(calls)) == 4


def test_multi_select_walks_a_mixed_ask_with_a_single_select_tab(monkeypatch):
    """The two widgets are driven differently but submit together: Q1 toggles
    and Enters its own advance row, Q2 is one pick that auto-advances, and the
    review tab still needs the final Submit."""
    before = _multi_screen(("A", "B"))
    after = _multi_screen(("A", "B"), checked={0, 1}, cursor=1)
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=[before, before, after])
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": [0, 1], "option_count": 2,
         "confirm_text": "PROBEQ1 pick many"},
        {"option_index": 2, "confirm_text": "PROBEQ2 which cache"},
    ])

    assert res.delivered is True and "submitted 2 answers" in res.detail
    # Q1: 0 + 1 (toggles) + 2 (cursor 1 → advance row 3); Q2: 2 (plain pick)
    assert len(_downs(calls)) == 5
    # 2 toggles + Q1 advance + Q2 select + final Submit
    assert len(_enters(calls)) == 5


def test_multi_select_refuses_when_widget_is_not_a_checkbox_list(monkeypatch):
    """A single-select menu is where these same Enters would SELECT and
    advance, answering a question the operator never staged. The parse refuses
    it before the first one."""
    single = ("PROBEQ1 pick many\n❯ 1. Yes\n  2. No\n  3. Type something.\n"
              "  4. Chat about this\n" + _TAIL)
    calls = _install_tmux(monkeypatch, command="claude", capture=single)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": [0], "option_count": 2,
         "confirm_text": "PROBEQ1 pick many"},
    ])

    assert res.delivered is False
    assert "not multi-select" in res.detail
    assert _enters(calls) == []


def test_multi_select_refuses_when_the_ticked_rows_are_not_the_ones_asked_for(
        monkeypatch):
    """A toggle that did not land must not be followed by the advance Enter:
    submitting there would answer with a subset the operator never chose. The
    ask stays parked and re-answerable instead."""
    before = _multi_screen(("A", "B"))
    after = _multi_screen(("A", "B"), checked={0}, cursor=1)  # B never took
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=[before, before, after])
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": [0, 1], "option_count": 2,
         "confirm_text": "PROBEQ1 pick many"},
    ])

    assert res.delivered is False
    assert "[0] ticked, wanted [0, 1]" in res.detail
    assert len(_enters(calls)) == 2  # the two toggles only — no advance, no submit


def test_a_stale_tick_elsewhere_on_screen_cannot_mask_a_dropped_toggle(
        monkeypatch):
    """The post-check reads WHICH rows are ticked inside the widget, not how
    many `[✔]` are on screen. A count would see the stale mark above the
    question, reach two, and submit a half-toggled answer."""
    stale = "  an earlier ask: 1. [✔] Alpha"
    before = _multi_screen(("A", "B"), above=stale)
    after = _multi_screen(("A", "B"), checked={0}, cursor=1, above=stale)
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=[before, before, after])
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": [0, 1], "option_count": 2,
         "confirm_text": "PROBEQ1 pick many"},
    ])

    assert res.delivered is False
    assert "[0] ticked, wanted [0, 1]" in res.detail


def test_a_box_already_ticked_is_left_alone_rather_than_toggled_off(monkeypatch):
    """Blindly Entering every requested row UNTICKS one a human already
    ticked — the agent would receive the opposite of what was picked. Only the
    difference against the live state is sent, which is also what makes a retry
    after a partial failure converge instead of undoing itself."""
    before = _multi_screen(("A", "B", "C"), checked={0})
    after = _multi_screen(("A", "B", "C"), checked={0, 1}, cursor=1)
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=[before, before, after])
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": [0, 1], "option_count": 3,
         "confirm_text": "PROBEQ1 pick many"},
    ])

    assert res.delivered is True
    # ONE toggle (row 1 only) + the advance row + the final Submit.
    assert len(_enters(calls)) == 3


def test_multi_select_navigates_from_the_measured_cursor(monkeypatch):
    """The cursor is not assumed to be at row 0 — a refused earlier attempt
    leaves it parked wherever it stopped, and starting from 0 there would
    toggle the wrong rows and then Enter the advance row early."""
    before = _multi_screen(("A", "B", "C"), cursor=2)
    after = _multi_screen(("A", "B", "C"), checked={0}, cursor=0)
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=[before, before, after])
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": [0], "option_count": 3,
         "confirm_text": "PROBEQ1 pick many"},
    ])

    assert res.delivered is True
    ups = [c for c in calls if c[-1] == "Up"]
    assert len(ups) == 2                 # cursor 2 → row 0, measured
    assert len(_downs(calls)) == 4       # cursor 0 → advance row 4


def test_multi_select_refuses_when_the_pane_shows_a_different_option_count(
        monkeypatch):
    """`option_count` locates the advance row. A stale card naming a count the
    live question does not have would drive Enter at an arbitrary row, so the
    measured widget wins and the mismatch refuses."""
    screen = _multi_screen(("A", "B"))
    calls = _install_tmux(monkeypatch, command="claude", capture=screen)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answers("t1", [
        {"option_index": [0], "option_count": 5,
         "confirm_text": "PROBEQ1 pick many"},
    ])

    assert res.delivered is False
    assert "pane shows 2 options, not 5" in res.detail
    assert _enters(calls) == []


def test_multi_select_rejects_an_unusable_shape_before_tmux(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude")
    _set_row(monkeypatch, _pane_row())
    bad = [
        {"option_index": [0]},                                  # no option_count
        {"option_index": [], "option_count": 2},                # nothing chosen
        {"option_index": [2], "option_count": 2},               # index past the list
        {"option_index": [0, 1, 2], "option_count": 2},         # more than there are
        {"option_index": [0], "option_count": 0},               # empty question
        {"option_index": ["a"], "option_count": 2},             # not an index
        {"option_index": [True], "option_count": 2},            # bool, not an index
        {"option_index": [0], "option_count": True},            # bool count
        {"option_index": [0], "option_count": 2, "text": "hi"},  # toggle + free text
    ]
    for entry in bad:
        assert delivery.deliver_answers("t1", [entry]).delivered is False, entry
    assert calls == []


# ── 12. deliver_decision_option: structured permission/plan decide ──

def test_decision_option_navigates_and_submits(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_decision_option("t1", 1)

    assert res.delivered is True
    assert len(_downs(calls)) == 1
    assert len(_enters(calls)) == 1
    assert not _literal_sends(calls)


def test_decision_option_out_of_range_refuses_before_tmux(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_decision_option("t1", -1)

    assert res.delivered is False and "out of range" in res.detail
    assert calls == []


def test_decision_option_disabled_refuses_without_tmux(monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    calls = _install_tmux(monkeypatch)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_decision_option("t1", 0)

    assert res.delivered is False and "disabled" in res.detail
    assert calls == []


def test_decision_option_stale_identity_refuses(monkeypatch):
    calls = _install_tmux(monkeypatch, server_pid=999)
    _set_row(monkeypatch, _pane_row(tmux_server_pid=111))

    res = delivery.deliver_decision_option("t1", 0)

    assert res.delivered is False and "stale" in res.detail
    assert _downs(calls) == [] and _enters(calls) == []


# ── 13. read_live_menu / deliver_live_menu_decision: parsed-pane decide ──

_PLAN_MENU = (
    "Would you like to proceed?\n"
    "\n"
    " ❯ 1. Yes, auto-accept edits\n"
    "   2. Yes, manually approve edits\n"
    "   3. No, refine with Ultraplan on Claude Code on the web\n"
    "   4. Tell Claude what to change\n"
)


def test_read_live_menu_parses_a_real_capture(monkeypatch):
    _install_tmux(monkeypatch, command="claude", capture=_PLAN_MENU)
    _set_row(monkeypatch, _pane_row())

    menu, detail = delivery.read_live_menu("t1")

    assert detail == "ok"
    assert menu is not None
    assert menu.options[0] == "Yes, auto-accept edits"
    assert menu.cursor_index == 0


def test_read_live_menu_refuses_when_unparseable(monkeypatch):
    _install_tmux(monkeypatch, command="claude", capture="nothing menu-like here")
    _set_row(monkeypatch, _pane_row())

    menu, detail = delivery.read_live_menu("t1")

    assert menu is None
    assert "could not reliably read" in detail


def test_read_live_menu_never_types_or_sends_enter(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude", capture=_PLAN_MENU)
    _set_row(monkeypatch, _pane_row())

    delivery.read_live_menu("t1")

    assert not _literal_sends(calls)
    assert not _enter_sent(calls)


def test_read_live_menu_stale_identity_refuses(monkeypatch):
    _install_tmux(monkeypatch, server_pid=999, capture=_PLAN_MENU)
    _set_row(monkeypatch, _pane_row(tmux_server_pid=111))

    menu, detail = delivery.read_live_menu("t1")

    assert menu is None and "stale" in detail


def test_live_menu_decision_navigates_from_the_measured_cursor(monkeypatch):
    # Cursor is on option 0 (index 0); picking index 3 must send Down×3.
    calls = _install_tmux(monkeypatch, command="claude", capture=_PLAN_MENU)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_live_menu_decision("t1", 3)

    assert res.delivered is True
    assert "Tell Claude what to change" in res.detail
    assert len(_downs(calls)) == 3
    assert len(_enters(calls)) == 1


def test_live_menu_decision_can_navigate_upward(monkeypatch):
    # Cursor on option 1 (index 1), picking index 0 must send Up×1, not Down.
    menu = _PLAN_MENU.replace("❯ 1.", "  1.").replace("2. Yes, manually",
                                                            "❯ 2. Yes, manually")
    calls = _install_tmux(monkeypatch, command="claude", capture=menu)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_live_menu_decision("t1", 0)

    assert res.delivered is True
    ups = [c for c in calls if c[-1] == "Up"]
    assert len(ups) == 1
    assert _downs(calls) == []


def test_live_menu_decision_refuses_rather_than_guess_when_unparseable(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude", capture="stale scrollback")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_live_menu_decision("t1", 0)

    assert res.delivered is False
    assert "resolve it in the terminal" in res.detail
    assert _downs(calls) == [] and _enters(calls) == []


def test_live_menu_decision_out_of_range_for_the_fresh_parse_refuses(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude", capture=_PLAN_MENU)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_live_menu_decision("t1", 99)

    assert res.delivered is False and "out of range" in res.detail
    assert _enters(calls) == []


def test_live_menu_decision_disabled_refuses_without_tmux(monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    calls = _install_tmux(monkeypatch, capture=_PLAN_MENU)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_live_menu_decision("t1", 0)

    assert res.delivered is False and "disabled" in res.detail
    assert calls == []


def test_live_menu_decision_matching_label_proceeds(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude", capture=_PLAN_MENU)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_live_menu_decision(
        "t1", 0, expect_label="Yes, auto-accept edits")

    assert res.delivered is True
    assert len(_enters(calls)) == 1


def test_live_menu_decision_mismatched_label_refuses_without_acting(monkeypatch):
    # TOCTOU guard: the operator staged a pick against an earlier read whose
    # label no longer matches what's on screen now — a DIFFERENT dialog with
    # enough rows would otherwise pass the bare range check.
    calls = _install_tmux(monkeypatch, command="claude", capture=_PLAN_MENU)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_live_menu_decision(
        "t1", 0, expect_label="Some other option entirely")

    assert res.delivered is False
    assert "menu changed" in res.detail
    assert _downs(calls) == [] and _enters(calls) == []


def test_live_menu_decision_no_expect_label_skips_the_check(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude", capture=_PLAN_MENU)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_live_menu_decision("t1", 1)  # no expect_label

    assert res.delivered is True
    assert len(_downs(calls)) == 1


# ── capture_screen: read-only snapshot, screen-only by default ──


def _capture_argv(calls):
    return next(c for c in calls if "capture-pane" in c)


def test_capture_screen_default_omits_scrollback_flag(monkeypatch):
    """No `lines` → just the current screen: no `-S` at all, not `-S -N`
    for some large default N."""
    calls = _install_tmux(monkeypatch, capture="the screen\n")
    _set_row(monkeypatch, _pane_row())

    res = delivery.capture_screen("t1")

    assert res.ok is True and res.text == "the screen\n"
    argv = _capture_argv(calls)
    assert "-S" not in argv


def test_capture_screen_with_lines_requests_scrollback(monkeypatch):
    calls = _install_tmux(monkeypatch, capture="history\n")
    _set_row(monkeypatch, _pane_row())

    res = delivery.capture_screen("t1", lines=40)

    assert res.ok is True
    argv = _capture_argv(calls)
    assert argv[argv.index("-S") + 1] == "-40"


def test_capture_screen_never_types_or_sends_enter(monkeypatch):
    calls = _install_tmux(monkeypatch, capture="ok\n")
    _set_row(monkeypatch, _pane_row())

    delivery.capture_screen("t1")

    assert not _literal_sends(calls)
    assert not _enter_sent(calls)


def test_capture_screen_disabled_refuses_without_tmux(monkeypatch):
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    calls = _install_tmux(monkeypatch)
    _set_row(monkeypatch, _pane_row())

    res = delivery.capture_screen("t1")

    assert res.ok is False and "disabled" in res.detail
    assert calls == []


def test_capture_screen_stale_identity_refuses(monkeypatch):
    calls = _install_tmux(monkeypatch, server_pid=999)  # live 999 != row 111
    _set_row(monkeypatch, _pane_row(tmux_server_pid=111))

    res = delivery.capture_screen("t1")

    assert res.ok is False and "stale" in res.detail
    assert not any("capture-pane" in c for c in calls)


# ── 9. is this session live in a terminal (the resume gate) ──
def test_session_is_live_when_its_pane_still_runs_claude(monkeypatch):
    _install_tmux(monkeypatch, command="claude")
    _set_row(monkeypatch, _pane_row())

    assert delivery.session_is_live("t1") is True


def test_session_is_not_live_once_the_pane_is_back_to_a_shell(monkeypatch):
    """The registry row outlives the CLI — a pane keeps its last session until
    the next one claims it — so the foreground command is the deciding fact.
    Reading the row alone would hide a resumable session forever."""
    _install_tmux(monkeypatch, command="fish")
    _set_row(monkeypatch, _pane_row())

    assert delivery.session_is_live("t1") is False


def test_session_is_not_live_when_it_holds_no_pane(monkeypatch):
    calls = _install_tmux(monkeypatch)
    _set_row(monkeypatch, None)

    assert delivery.session_is_live("t1") is False
    assert calls == []


def test_liveness_reads_gone_only_when_the_pane_was_actually_queried(
        monkeypatch):
    """`session_is_live` collapses "not our claude" and "could not ask" into
    False — right for a gate that fails closed, fatal for confirming a
    shutdown, where "the probe broke" would read as "the process exited"."""
    _install_tmux(monkeypatch, command="fish")
    _set_row(monkeypatch, _pane_row())

    assert delivery.session_liveness("t1") == delivery.GONE


def test_liveness_is_indeterminate_when_the_pane_cannot_be_queried(monkeypatch):
    # A tmux call that failed or timed out (loaded box, dead socket) answers
    # nothing about the process behind the row. A row EXISTS, so this is not
    # the settled "nothing to ask" of UNKNOWN — the session may still be up.
    _install_tmux(monkeypatch, identity_rc=1)
    _set_row(monkeypatch, _pane_row())

    assert delivery.session_liveness("t1") == delivery.INDETERMINATE


def test_liveness_is_indeterminate_when_the_identity_will_not_parse(monkeypatch):
    """tmux exited cleanly but the fields are unusable — a format the running
    tmux does not support, or a pane read mid-teardown. Nothing was learned
    about the process, so this must not read as the positive 'gone' that lets
    a shutdown settle the trace row."""
    _install_tmux(monkeypatch, server_pid="not-a-pid")
    _set_row(monkeypatch, _pane_row())

    assert delivery.session_liveness("t1") == delivery.INDETERMINATE
    assert delivery.session_is_live("t1") is False


def test_liveness_is_unknown_without_a_row_or_a_bridge(monkeypatch):
    _install_tmux(monkeypatch)
    _set_row(monkeypatch, None)
    assert delivery.session_liveness("t1") == delivery.UNKNOWN

    _set_row(monkeypatch, _pane_row())
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    assert delivery.session_liveness("t1") == delivery.UNKNOWN


def test_liveness_is_indeterminate_when_the_registry_will_not_answer(monkeypatch):
    """A store that could not be queried is not an empty registry.

    `get_reachable_pane` reports both as None so delivery can stay fail-closed,
    but a caller deciding whether it may report a session STOPPED needs them
    apart: a locked DB read as "nothing is registered" is how a still-running
    agent gets a green tick.
    """
    _install_tmux(monkeypatch)
    _set_row(monkeypatch, None, answered=False)

    assert delivery.session_liveness("t1") == delivery.INDETERMINATE
    # The fail-closed view is unchanged: delivery still refuses, never raises.
    assert delivery.session_is_live("t1") is False


def test_session_is_not_live_when_the_pane_id_was_recycled(monkeypatch):
    """A tmux server restart reissues pane ids: `%7` under the new server is a
    different pane, and whatever runs there is not this session."""
    _install_tmux(monkeypatch, server_pid=999)
    _set_row(monkeypatch, _pane_row(tmux_server_pid=111))

    assert delivery.session_is_live("t1") is False


def test_liveness_is_unknown_rather_than_asserted_with_the_bridge_off(
        monkeypatch):
    """No registry, no evidence — and refusing every resume on a hunch costs
    more than the collision it would avoid."""
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    calls = _install_tmux(monkeypatch)
    _set_row(monkeypatch, _pane_row())

    assert delivery.session_is_live("t1") is False
    assert calls == []


# ── is_slash_command: the routing gate for as_command sends ─────────────


@pytest.mark.parametrize("text", [
    "/exit",
    "/compact",
    "/goal-verified-treenav fix the pane",
    "/multi-agent-capture:task-1",
    "/goal fix the bug in tts.py",
])
def test_command_shaped_text_is_a_slash_command(text):
    assert delivery.is_slash_command(text) is True


@pytest.mark.parametrize("text", [
    "steer left",
    "/Users/x/app.py crashes on boot",   # path: second slash ends the token
    "/ exit",                            # bare slash never opens a command
    "run /exit for me",                  # slash not leading
    "",
])
def test_non_command_text_is_not_a_slash_command(text):
    assert delivery.is_slash_command(text) is False


# ── ends_with_mention: the trailing-@ picker eats the Enter too ──────────


@pytest.mark.parametrize("text", [
    "hello @/Users/taowang/regin/",   # trace 212ebe7f: two sends, never submitted
    "check @src/app.py",
    "@/Users/x/y",                    # mention is the whole message
    "what changed @",                 # bare @ opens the picker unfiltered
])
def test_trailing_mention_is_detected(text):
    assert delivery.ends_with_mention(text) is True


@pytest.mark.parametrize("text", [
    "read @/Users/x/y then fix it",   # space after the token closed the picker
    "mail me at foo@bar.com",         # @ not at a token boundary
    "steer left",
    "",
])
def test_non_trailing_mention_is_not_detected(text):
    assert delivery.ends_with_mention(text) is False


def test_trailing_mention_is_terminated_so_the_picker_cannot_eat_the_enter(
        monkeypatch):
    # Probed live: `hello @/Users/taowang/regin/` left claude's file picker
    # open over the composer, so the trailing Enter selected a completion
    # instead of submitting. The send still ACKED (the text IS in the
    # composer), so trace 212ebe7f sat silent on two "delivered" prompts.
    text = "hello @/Users/taowang/regin/"
    calls = _install_tmux(monkeypatch, command="claude", capture=f"> {text}")
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", text).delivered is True
    assert [c[-1] for c in _literal_sends(calls)] \
        == [f"\x1b[200~{text} \x1b[201~"]
    assert _enter_sent(calls)


def test_chat_answer_ending_in_a_mention_is_terminated(monkeypatch):
    # The chat path types into the SAME real composer as a steer, so it has
    # the identical swallowed-Enter hazard — and it does not route through
    # `_type_and_ack`, so it needs the terminator applied at its own send.
    assert delivery._typed_payload("chat me @/Users/x/", False) \
        == "chat me @/Users/x/ "


def test_inline_answer_field_is_left_verbatim(monkeypatch):
    # The non-chat free-text branch types into the ask's own inline widget,
    # not the composer; its picker behaviour is unprobed, so it must not be
    # silently altered.
    assert delivery._typed_payload("plain answer", False) == "plain answer"


def test_mention_terminator_does_not_reset_a_human_draft(monkeypatch):
    # The terminator is not `as_command`: a steer must still leave the
    # composer's existing draft alone.
    text = "look at @/Users/x/"
    calls = _install_tmux(monkeypatch, command="claude", capture=f"> {text}")
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", text).delivered is True
    assert _keys_before_literal(calls) == []


# ── 8e. the composer is restored to INSERT before an ordinary steer ──
_RULE = "─" * 40


def _boxed(line):
    """A composer box `screen_state.parse_screen` will vouch for."""
    return "\n".join([_RULE, f"❯ {line}", _RULE])


def test_empty_composer_is_reset_before_an_ordinary_steer(monkeypatch):
    # claude's vim mode leaves the composer in NORMAL after the Esc that
    # interrupts a turn, where typed text runs as motions. A body opening with
    # `S` (substitute-line, which itself enters INSERT) lost exactly that one
    # character: the ack matched on the tail and the agent answered a prompt
    # missing its first letter. An empty composer has no draft to protect, so
    # the INSERT-restoring reset runs there too — not just for commands.
    text = "So the delivery layer needs a second look before we ship it"
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=[_boxed(""), _boxed(text)])
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", text).delivered is True
    assert _cu_sent(calls)
    assert _enter_sent(calls)


def test_a_drafted_composer_is_never_reset(monkeypatch):
    # The reset CLEARS the line, so a human's half-typed draft outranks the
    # vim guard: type on top of it rather than destroy it.
    text = "please refactor the delivery layer now"
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=[_boxed("half a thought"), _boxed(text)])
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", text).delivered is True
    assert not _cu_sent(calls)


def test_an_unreadable_composer_is_never_reset(monkeypatch):
    # "Don't know" has to mean "leave the line alone" — the reset is
    # destructive, so an unparsable screen must not license it.
    text = "please refactor the delivery layer now"
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=[(1, ""), _boxed(text)])
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", text).delivered is True
    assert not _cu_sent(calls)


# ── 22. the composer's 1024-byte input burst ─────────────────
# Measured against Claude Code v2.1.259 (trace aac4dd29, bridge_messages
# 917/918): a 1448-byte body typed as one `send-keys -l` reached the composer
# as a byte-exact suffix FROM OFFSET 1024 — the head silently gone, and the
# send ACKed anyway because the surviving tail is what the echo matched.
# Chunking the literal send does not fix it; bracketed paste does, byte-exactly
# up to `max_text_len`. These pin the paste path so a future "simplification"
# back to a raw burst has to argue with the measurement.
_BURST_OVERFLOW = "A" * 1600


def test_a_body_over_the_input_burst_is_pasted_whole_in_one_send(monkeypatch):
    calls = _install_tmux(monkeypatch, command="claude",
                          capture=f"> {_BURST_OVERFLOW}")
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", _BURST_OVERFLOW).delivered is True
    sends = [c[-1] for c in _literal_sends(calls)]
    assert sends == [f"\x1b[200~{_BURST_OVERFLOW}\x1b[201~"]
    # The head is what the raw burst lost — it must be in the payload.
    assert sends[0][6:].startswith("A" * 1600)


def test_a_multiline_prompt_keeps_its_line_breaks(monkeypatch):
    # The reported defect: pasted source arrived as one flattened line. Inside
    # the markers a newline INSERTS, so nothing submits half a message.
    text = "fix this:\n\n    public int findKth(int[] a) {\n        return 0;\n    }"
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="> fix this: public int findKth(int[] a) {")
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", text).delivered is True
    payload = _literal_sends(calls)[0][-1]
    assert payload == f"\x1b[200~{text}\x1b[201~"
    assert payload.count("\n") == 4
    # One Enter, sent after the ack — the newlines did not submit anything.
    assert len([c for c in calls if c[-1] == "Enter"]) == 1


def test_a_slash_command_is_typed_not_pasted(monkeypatch):
    # A command is one short line the burst limit cannot reach, and it is typed
    # precisely so the autocomplete menu opens for `_MENU_TERMINATOR` to close.
    # Pasting it would suppress the menu the terminator is aimed at.
    calls = _install_tmux(monkeypatch, command="claude", capture="> /clear")
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", "/clear", as_command=True).delivered is True
    payload = _literal_sends(calls)[0][-1]
    assert payload == "/clear "
    assert "\x1b[200~" not in payload


def test_a_multiline_command_body_is_still_flattened(monkeypatch):
    # `as_command` keeps the single-line sanitizer: a command with a newline in
    # it is not a command, and the composer would run only its first line.
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="> /clear then this")
    _set_row(monkeypatch, _pane_row())

    delivery.deliver("t1", "/clear\nthen this", as_command=True)
    assert _literal_sends(calls)[0][-1] == "/clear then this "


def test_sanitize_body_keeps_breaks_and_sanitize_text_flattens_them():
    raw = "one\r\ntwo\rthree\tfour\x07 five"
    assert delivery.sanitize_body(raw) == "one\ntwo\nthree\tfour five"
    assert delivery.sanitize_text(raw) == "one two three four five"
    # Both still strip ANSI, and both still cap.
    assert delivery.sanitize_body("\x1b[31mred\nblue") == "red\nblue"
    assert len(delivery.sanitize_body("x" * 9000)) \
        == settings.agent_bridge.max_text_len


def test_multiline_paste_chip_is_recognised_by_the_ack():
    # A multi-line paste draws `[Pasted text #2 +48 lines]`, not the bare
    # `[Pasted text #2]` of a single-line one. Anchoring on the closing bracket
    # made every pasted diff/source body fail the ack and get cleared unsent —
    # caught live, delivering the user's 48-line Java body through deliver().
    assert delivery._paste_chip_high_water("[Pasted text #2 +48 lines]") == 2
    assert delivery._paste_chip_high_water("[Pasted text #7]") == 7
    assert delivery._paste_chip_high_water("no chip here") == 0


def test_a_pasted_multiline_body_acks_on_the_chip_not_the_echo(monkeypatch):
    # The composer collapses the paste, so neither head nor tail echoes. The
    # chip counter rising above the pre-type baseline is the only proof left.
    text = "line one\nline two\nline three"
    calls = _install_tmux(
        monkeypatch, command="claude",
        capture=["> ", "> [Pasted text #1 +2 lines]"])
    _set_row(monkeypatch, _pane_row())

    assert delivery.deliver("t1", text).delivered is True
    assert _literal_sends(calls)[0][-1] == f"\x1b[200~{text}\x1b[201~"
    assert _enter_sent(calls)


def test_a_body_cannot_break_out_of_the_paste_and_inject_keystrokes():
    # The wrapper is the one place delivery emits ESC on purpose, so a body
    # carrying its own `ESC[201~` would end the paste early and let the rest
    # arrive as live keystrokes. It cannot: ESC is stripped twice over — by
    # `_ANSI_RE`, then by the control sweep — before the markers go on.
    raw = "break \x1b[201~ out \x1b[200~ again"
    clean = delivery.sanitize_body(raw)
    assert "\x1b" not in clean
    payload = delivery._pasted_payload(clean)
    assert payload.count("\x1b[200~") == 1 and payload.count("\x1b[201~") == 1
    assert payload.endswith("\x1b[201~")


def test_chat_answer_acks_on_the_paste_chip_like_a_steer(monkeypatch):
    # The chat branch types into the REAL composer, so its payload is a paste
    # and a long one collapses to a chip that echoes neither head nor tail.
    # Without the chip baseline the ack fails and the answer is never
    # submitted — a silent non-delivery. Captures: chat-entry guard, pre-type
    # baseline, then the chip that proves the paste landed.
    calls = _install_tmux(
        monkeypatch, command="claude",
        capture=["5. Chat about this\n> ", "> ",
                 "> [Pasted text #1 +3 lines]"])
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 4, "a much longer chat reply",
                                  expect_chat=True)

    assert res.delivered is True
    assert len(_enters(calls)) == 2  # dismiss the menu + submit the message


def test_chat_answer_ack_failure_clears_the_composer(monkeypatch):
    # Un-acked chat text sits in the real composer; a retry would type on top
    # and submit two concatenated messages. The inline field is not the
    # composer, so only chat clears (see `test_inline_answer_field_...`).
    calls = _install_tmux(monkeypatch, command="claude",
                          capture="5. Chat about this\nnothing echoed here")
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver_answer("t1", 4, "never echoes", expect_chat=True)

    assert res.delivered is False
    literal = next(i for i, c in enumerate(calls) if "-l" in c and "--" in c)
    assert [c[-1] for c in calls[literal + 1:] if "send-keys" in c] \
        == ["C-u", "i", "C-u"]
    assert not any(c[-1] == "Enter" for c in calls[literal:])


def test_chip_regex_ignores_prose_that_merely_quotes_a_chip():
    # An unterminated mention poisons the pre-type BASELINE to a number the
    # real counter will never exceed, silently disabling the chip ack.
    assert delivery._paste_chip_high_water("said [Pasted text #99 was odd") == 0
    assert delivery._paste_chip_high_water("[Pasted text #4 +1 line]") == 4


def test_chip_read_is_reflow_tolerant_and_still_rejects_prose():
    # The composer wraps the chip itself on a narrow pane, including right
    # before the closing bracket. A baseline that missed a wrapped STALE chip
    # while the ack saw it would read as `N > 0` and blind-submit text that
    # never landed — the reader normalizes so both sides see the same number.
    assert delivery._paste_chip_high_water("> [Pasted text #2 +48 lines\n  | ]") == 2
    assert delivery._paste_chip_high_water("> [Pasted text #7\n  | ]") == 7
    assert delivery._paste_chip_high_water("> [Pasted text #2 +48\n  | lines]") == 2
    assert delivery._paste_chip_high_water("said [Pasted text #99 was odd") == 0


def test_a_stale_wrapped_chip_does_not_ack_a_send_that_never_landed(monkeypatch):
    # Same chip, wrapped, present BEFORE and AFTER the type. It must not read
    # as 0-then-5, which would submit unseen text.
    stale = "> [Pasted text #5 +9 lines\n  | ]"
    calls = _install_tmux(monkeypatch, command="claude", capture=stale)
    _set_row(monkeypatch, _pane_row())

    res = delivery.deliver("t1", "text that never echoes")

    assert res.delivered is False
    assert not _enter_sent(calls)
