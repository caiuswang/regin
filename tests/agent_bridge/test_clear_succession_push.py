"""The `/clear` succession reaches the stranded page as a pushed frame.

The page an operator holds through a `/clear` cannot discover its own
successor: the heir's spans land under a trace it is not watching, and the
trace view suspends its poll once the cleared session stops changing. So the
succession has to arrive as an event.

Two independent hook processes write the two facts the walk needs — the
predecessor's `ended_reason = 'clear'` (span ingest) and the heir's
`bridge_panes` row (SessionStart) — in either order. Neither site can know it
is the second, so both announce and the walk decides. What is pinned here is
that the LATER one always raises exactly one frame and the earlier one raises
none, whichever way round they land.
"""

from __future__ import annotations

import pytest

from lib.agent_bridge import store, succession
from lib.notifications import hub
from lib.orm.engine import get_connection

from tests.agent_bridge.test_clear_successor import _seed, _seed_session


@pytest.fixture
def frames(monkeypatch):
    """Capture what reached the hub, with a subscriber present."""
    captured = []
    monkeypatch.setattr(hub, "subscriber_count", lambda: 1)
    monkeypatch.setattr(
        hub, "broadcast_event",
        lambda name, payload: captured.append((name, payload)))
    return captured


@pytest.fixture
def bridge_on(monkeypatch):
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_bridge, "enabled", True)


def _seed_cleared_chain():
    """`T-old` cleared into `T-new`, both claiming pane %7."""
    _seed("T-old", pane_id="%7", at="2026-08-01 10:00:00")
    _seed_session("T-old", ended_at="2026-08-01 10:30:00",
                  ended_reason="clear")
    _seed("T-new", pane_id="%7", at="2026-08-01 10:30:01")
    _seed_session("T-new")


def test_announce_names_the_predecessor_not_the_heir(frames, bridge_on):
    """The frame is addressed to the id the stranded page is mounted on.

    Naming the heir would be useless: no page is on it yet, which is the whole
    problem the frame exists to fix.
    """
    _seed_cleared_chain()
    assert succession.announce("T-old") == "T-new"
    assert frames == [(hub.SUCCESSOR_EVENT,
                       {"trace_id": "T-old", "successor_trace_id": "T-new"})]


def test_the_earlier_of_the_two_writes_announces_nothing(frames, bridge_on):
    """The predecessor is marked cleared but the heir has not claimed the pane
    yet — the ingest-first ordering. Silence, not a frame naming nobody."""
    _seed("T-old", pane_id="%7", at="2026-08-01 10:00:00")
    _seed_session("T-old", ended_at="2026-08-01 10:30:00",
                  ended_reason="clear")
    assert succession.announce("T-old") is None
    assert frames == []


def test_the_heir_registering_second_still_gets_the_frame(frames, bridge_on):
    """The registration-last ordering, driven the way the hook drives it:
    the heir knows only its own id and walks BACK to find who it replaced."""
    _seed_cleared_chain()
    predecessor = store.cleared_predecessor("T-new")
    assert predecessor == "T-old"
    assert succession.announce(predecessor) == "T-new"
    assert [p["successor_trace_id"] for _, p in frames] == ["T-new"]


def test_a_session_that_ended_some_other_way_announces_nothing(frames,
                                                               bridge_on):
    """The guard that keeps a stale card out of a stranger's terminal is the
    same one that decides whether to move a page onto it."""
    _seed("T-old", pane_id="%7", at="2026-08-01 10:00:00")
    _seed_session("T-old", ended_at="2026-08-01 10:30:00",
                  ended_reason="logout")
    _seed("T-new", pane_id="%7", at="2026-08-01 10:30:01")
    _seed_session("T-new")
    assert succession.announce("T-old") is None
    assert frames == []


def test_no_frame_when_the_bridge_is_disabled(frames, monkeypatch):
    """With the bridge off there is no terminal the heir could be typed into,
    so moving the operator onto it would stand them somewhere just as mute."""
    from lib.settings import settings
    monkeypatch.setattr(settings.agent_bridge, "enabled", False)
    _seed_cleared_chain()
    assert succession.announce("T-old") is None
    assert frames == []


def test_no_walk_is_paid_for_when_nobody_is_listening(monkeypatch, bridge_on):
    """This runs inside an ingest a hook is blocking on: with no open stream
    the queries must not be issued at all."""
    monkeypatch.setattr(hub, "subscriber_count", lambda: 0)
    _seed_cleared_chain()

    def explode(*_a, **_k):
        raise AssertionError("resolved the successor with nobody watching")

    monkeypatch.setattr(store, "clear_successor", explode)
    assert succession.announce("T-old") is None


def test_an_announcement_failure_never_reaches_the_caller(frames, bridge_on,
                                                          monkeypatch):
    """Best-effort by contract — the callers are a span ingest and an
    interactive SessionStart."""
    _seed_cleared_chain()
    monkeypatch.setattr(
        hub, "broadcast_event",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("stream gone")))
    assert succession.announce("T-old") is None


def test_cleared_predecessor_ignores_an_unrelated_pane():
    """A row on another pane is not this session's predecessor, however
    recently it was cleared."""
    _seed("T-elsewhere", pane_id="%9", at="2026-08-01 10:00:00")
    _seed_session("T-elsewhere", ended_at="2026-08-01 10:30:00",
                  ended_reason="clear")
    _seed("T-new", pane_id="%7", at="2026-08-01 10:30:01")
    _seed_session("T-new")
    assert store.cleared_predecessor("T-new") is None


def test_cleared_predecessor_picks_the_nearest_earlier_claim():
    """A pane an operator keeps open accumulates a row per session that ever
    ran in it; the chain is walked forward from the IMMEDIATE predecessor, so
    a distant cleared ancestor must not be the starting point."""
    _seed("T-ancient", pane_id="%7", at="2026-08-01 08:00:00")
    _seed_session("T-ancient", ended_at="2026-08-01 08:30:00",
                  ended_reason="clear")
    _seed("T-old", pane_id="%7", at="2026-08-01 10:00:00")
    _seed_session("T-old", ended_at="2026-08-01 10:30:00",
                  ended_reason="clear")
    _seed("T-new", pane_id="%7", at="2026-08-01 10:30:01")
    _seed_session("T-new")
    assert store.cleared_predecessor("T-new") == "T-old"


def test_no_predecessor_for_a_pane_this_session_opened():
    """The first claude in a pane replaced nobody."""
    _seed("T-new", pane_id="%7", at="2026-08-01 10:30:01")
    _seed_session("T-new")
    assert store.cleared_predecessor("T-new") is None
    conn = get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM bridge_panes").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_failing_gate_is_swallowed_like_a_failing_broadcast(bridge_on,
                                                              monkeypatch):
    """The gates are the caller's first contact with this module, so they have
    to sit inside the guard too — a raise there would reach the span ingest
    that answers a blocking hook and turn a fan-out detail into a 500."""
    _seed_cleared_chain()
    monkeypatch.setattr(
        hub, "subscriber_count",
        lambda: (_ for _ in ()).throw(RuntimeError("hub is gone")))
    assert succession.announce("T-old") is None
