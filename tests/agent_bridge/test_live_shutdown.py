"""Graceful shutdown of a still-live session (`lib/live_session.py`) and its
two HTTP controls (`bridge.api_session_live_state` / `api_session_shutdown`).

Close and Delete in the trace list are store writes that say nothing to the
process behind the row, so a live agent kept running and reappeared as a new
partial trace. These pin the missing half:

  tiers     — SDK ownership is asked FIRST (a regin-launched child runs with
              REGIN_BRIDGE=0 and can never have a pane row), a *starting* run
              reports its tier with live=False (only a registered run can be
              stopped), tmux answers only when the pane probe confirms it,
  never-raise— a probe that blows up reads as "nothing reachable", because the
              caller uses the answer to word a confirmation dialog,
  sdk close — `stop_run` once, tmux untouched,
  tmux close— Escape BEFORE /exit (a command submitted mid-turn is queued as a
              steering message), and a refused Escape stops the sequence;
              /exit is typed AS A COMMAND, not as a message (that Escape can
              leave a vim-mode composer in NORMAL, where only its trailing `t`
              lands, and an open autocomplete menu steals the Enter for its
              highlighted entry), and the exit is CONFIRMED — a pane still
              running claude reports closed=False rather than settling the row
              on a keystroke,
  no tier   — closed=False is the ordinary answer, not an error: the caller
              settles the row anyway,
  gate      — both routes are outside PUBLIC_API_ENDPOINTS and editor-gated;
              ending a run outranks every editor mutation, so viewers 403.

Nothing here touches tmux or the SDK: both layers are monkeypatched, the
idiom from `test_web_proxy.py`.
"""

from __future__ import annotations

import pytest

from lib import agent_sdk, live_session
from lib.agent_bridge import delivery


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch):
    """The real pauses are pane-settling and exit-watching time."""
    monkeypatch.setattr(live_session, "_SETTLE_SEC", 0)
    monkeypatch.setattr(live_session, "_EXIT_POLL_SEC", 0)


def _sdk(monkeypatch, *, owned=False, starting=False):
    monkeypatch.setattr(agent_sdk, "is_starting", lambda t: starting)
    monkeypatch.setattr(agent_sdk, "is_sdk_owned", lambda t: owned)


def _tmux_live(monkeypatch, live=True, after=delivery.GONE):
    """`session_liveness` for the tier probe, then for the post-`/exit` watch.

    The first reading answers `manageability`; every later one is `_await_exit`
    watching the process go, so `after` models what happened to the quit —
    GONE (it took), LIVE (claude ignored it) or UNKNOWN (the probe could not
    answer, which must NOT read as an exit).
    """
    reads: list[str] = []

    def _liveness(trace_id):
        reads.append(trace_id)
        if len(reads) == 1:
            return delivery.LIVE if live else delivery.GONE
        return after

    monkeypatch.setattr(delivery, "session_liveness", _liveness)


def _record_tmux(monkeypatch, *, escape=True, exit_ok=True):
    """Replace both tmux legs with recorders; returns the ordered call log."""
    calls: list[tuple[str, str]] = []

    def _key(trace_id, key):
        calls.append(("key", key))
        return delivery.DeliveryResult(escape, "escape detail")

    def _deliver(trace_id, text, as_command=False):
        calls.append(("text", text, as_command))
        return delivery.DeliveryResult(exit_ok, "exit detail")

    monkeypatch.setattr(delivery, "deliver_key", _key)
    monkeypatch.setattr(delivery, "deliver", _deliver)
    return calls


# ── tier resolution ──────────────────────────────────────────


def test_starting_run_reports_tier_but_not_live(monkeypatch):
    """`is_sdk_owned` covers reserved ids, but only a registered run can be
    stopped — promising a shutdown would send the caller at a control that
    can only answer 'no live agent session'."""
    _sdk(monkeypatch, owned=True, starting=True)
    state = live_session.manageability("T-1")
    assert state["tier"] == "sdk"
    assert state["live"] is False
    assert state["starting"] is True


def test_sdk_ownership_is_asked_before_tmux(monkeypatch):
    """A regin-launched child runs REGIN_BRIDGE=0 and can never have a pane
    row, so the pane probe must not even be consulted for an owned run."""
    _sdk(monkeypatch, owned=True)

    def _boom(trace_id):
        raise AssertionError("pane probe consulted for an SDK-owned run")

    monkeypatch.setattr(delivery, "session_liveness", _boom)
    assert live_session.manageability("T-1")["tier"] == "sdk"


def test_tmux_tier_when_pane_probe_confirms(monkeypatch):
    _sdk(monkeypatch)
    _tmux_live(monkeypatch, True)
    state = live_session.manageability("T-1")
    assert state["tier"] == "tmux"
    assert state["live"] is True


def test_no_tier_when_nothing_holds_the_session(monkeypatch):
    _sdk(monkeypatch)
    _tmux_live(monkeypatch, False)
    state = live_session.manageability("T-1")
    assert state["tier"] is None
    assert state["live"] is False


def test_broken_pane_probe_reads_as_unreachable(monkeypatch):
    """The answer words a confirmation dialog; a probe that raised would
    block the very action it describes."""
    def _boom(trace_id):
        raise RuntimeError("tmux gone")

    _sdk(monkeypatch)
    monkeypatch.setattr(delivery, "session_liveness", _boom)
    assert live_session.manageability("T-1")["tier"] is None


# ── graceful close ───────────────────────────────────────────


def test_sdk_close_stops_the_run_and_leaves_tmux_alone(monkeypatch):
    stops: list[str] = []
    _sdk(monkeypatch, owned=True)
    monkeypatch.setattr(agent_sdk, "stop_run",
                        lambda t: (stops.append(t), (True, "stopping"))[1])
    calls = _record_tmux(monkeypatch)

    result = live_session.graceful_close("T-1")

    assert stops == ["T-1"]
    assert calls == []
    assert result == {"tier": "sdk", "live": True, "closed": True,
                      "interrupted": False, "detail": "stopping"}


def test_tmux_close_cancels_the_turn_before_typing_exit(monkeypatch):
    """/exit submitted mid-turn is queued as a steering message, so the
    session would stay up with the quit text in its transcript."""
    _sdk(monkeypatch)
    _tmux_live(monkeypatch)
    calls = _record_tmux(monkeypatch)

    result = live_session.graceful_close("T-1")

    assert calls == [("key", "Escape"), ("text", "/exit", True)]
    assert result["tier"] == "tmux"
    assert result["closed"] is True


def test_exit_is_typed_as_a_command_not_as_a_message(monkeypatch):
    """Two hazards ride on that Escape: it drops a vim-mode composer out of
    INSERT (where `/exit` runs as motions, leaving only `t`), and a typed
    slash command hands Enter to the autocomplete menu's highlighted entry.
    `as_command` is what handles both — see `delivery._type_and_ack`."""
    _sdk(monkeypatch)
    _tmux_live(monkeypatch)
    calls = _record_tmux(monkeypatch)

    live_session.graceful_close("T-1")

    assert [c for c in calls if c[0] == "text"] == [("text", "/exit", True)]


def test_a_session_that_ignored_exit_is_not_reported_closed(monkeypatch):
    """Delivery can only say the keys were submitted. The operator was
    promised the session would END, so a pane still running claude after the
    watch window is a failure — otherwise the row settles while the agent
    keeps emitting spans as a new partial trace."""
    _sdk(monkeypatch)
    _tmux_live(monkeypatch, after=delivery.LIVE)
    _record_tmux(monkeypatch)

    result = live_session.graceful_close("T-1")

    assert result["closed"] is False
    assert result["interrupted"] is True
    assert "still running claude" in result["detail"]


def test_an_unanswerable_probe_is_not_read_as_an_exit(monkeypatch):
    """A tmux call that timed out says nothing about the process. Treating
    that silence as "it exited" settles the row while the agent keeps
    running — exactly the bug the confirmation exists to catch — so only a
    positive GONE closes it."""
    _sdk(monkeypatch)
    _tmux_live(monkeypatch, after=delivery.UNKNOWN)
    _record_tmux(monkeypatch)

    result = live_session.graceful_close("T-1")

    assert result["closed"] is False
    assert "could not confirm" in result["detail"]


def test_a_probe_that_raises_mid_watch_is_not_read_as_an_exit(monkeypatch):
    """`manageability` may never raise, so the watch degrades exceptions to
    UNKNOWN rather than to 'gone'."""
    _sdk(monkeypatch)
    reads: list[str] = []

    def _liveness(trace_id):
        reads.append(trace_id)
        if len(reads) == 1:
            return delivery.LIVE
        raise RuntimeError("tmux socket died")

    monkeypatch.setattr(delivery, "session_liveness", _liveness)
    _record_tmux(monkeypatch)

    assert live_session.graceful_close("T-1")["closed"] is False


def test_refused_escape_does_not_type_exit(monkeypatch):
    _sdk(monkeypatch)
    _tmux_live(monkeypatch)
    calls = _record_tmux(monkeypatch, escape=False)

    result = live_session.graceful_close("T-1")

    assert calls == [("key", "Escape")]
    assert result["closed"] is False
    assert "interrupt refused" in result["detail"]


def test_refused_exit_reports_the_delivery_detail(monkeypatch):
    _sdk(monkeypatch)
    _tmux_live(monkeypatch)
    _record_tmux(monkeypatch, exit_ok=False)

    result = live_session.graceful_close("T-1")

    assert result["closed"] is False
    assert "exit detail" in result["detail"]


def test_a_cancelled_turn_that_stayed_open_is_flagged(monkeypatch):
    """The operator is owed the difference between 'we never touched your
    pane' and 'we killed the turn and it is still running'."""
    _sdk(monkeypatch)
    _tmux_live(monkeypatch)
    _record_tmux(monkeypatch, exit_ok=False)

    assert live_session.graceful_close("T-1")["interrupted"] is True


def test_a_refused_escape_leaves_the_pane_untouched(monkeypatch):
    _sdk(monkeypatch)
    _tmux_live(monkeypatch)
    _record_tmux(monkeypatch, escape=False)

    assert live_session.graceful_close("T-1")["interrupted"] is False


def test_unreachable_session_reports_nothing_interrupted(monkeypatch):
    _sdk(monkeypatch)
    _tmux_live(monkeypatch, False)

    assert live_session.graceful_close("T-1")["interrupted"] is False


def test_unreachable_session_is_a_clean_no_op(monkeypatch):
    """Not a failure — this is exactly the interrupted session the manual
    close was built for, and the caller settles the row regardless."""
    _sdk(monkeypatch)
    _tmux_live(monkeypatch, False)
    calls = _record_tmux(monkeypatch)

    result = live_session.graceful_close("T-1")

    assert calls == []
    assert result["tier"] is None
    assert result["closed"] is False


def test_starting_run_is_not_stopped(monkeypatch):
    """live=False, so the shutdown must not reach `stop_run` at all."""
    _sdk(monkeypatch, owned=True, starting=True)

    def _boom(trace_id):
        raise AssertionError("stop_run called for a starting run")

    monkeypatch.setattr(agent_sdk, "stop_run", _boom)
    assert live_session.graceful_close("T-1")["closed"] is False


# ── HTTP gate ────────────────────────────────────────────────


@pytest.mark.parametrize("method,path", [
    ("get", "/api/sessions/T-1/live-state"),
    ("post", "/api/sessions/T-1/shutdown"),
])
def test_anonymous_401(anon_client, method, path):
    assert getattr(anon_client, method)(path).status_code == 401


@pytest.mark.parametrize("method,path", [
    ("get", "/api/sessions/T-1/live-state"),
    ("post", "/api/sessions/T-1/shutdown"),
])
def test_viewer_role_403(flask_client, monkeypatch, method, path):
    """Ending a live agent outranks every editor-gated mutation."""
    from lib.auth import create_token

    def _boom(trace_id):
        raise AssertionError("reached the tier probe past the auth gate")

    monkeypatch.setattr(agent_sdk, "is_starting", _boom)
    viewer = {"Authorization":
              f"Bearer {create_token(2, 'viewer-tester', 'viewer')}"}
    resp = getattr(flask_client, method)(path, headers=viewer)
    assert resp.status_code == 403


def test_live_state_serves_the_probe(flask_client, monkeypatch):
    _sdk(monkeypatch, owned=True)
    body = flask_client.get("/api/sessions/T-1/live-state").get_json()
    assert body["tier"] == "sdk"
    assert body["live"] is True


def test_shutdown_serves_the_close_result(flask_client, monkeypatch):
    _sdk(monkeypatch, owned=True)
    monkeypatch.setattr(agent_sdk, "stop_run", lambda t: (True, "stopping"))
    body = flask_client.post("/api/sessions/T-1/shutdown").get_json()
    assert body == {"tier": "sdk", "live": True, "closed": True,
                    "interrupted": False, "detail": "stopping"}
