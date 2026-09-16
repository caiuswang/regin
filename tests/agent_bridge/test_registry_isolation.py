"""The suite must never claim the developer's own tmux pane.

`bridge_registry` takes the pane triple from `os.environ`, so a test run
inside a real tmux pane registered `bridge_panes` rows for the replayed
fixture session ids against that pane. Occupancy is decided by the newest
reachable registration whose session has not ended, and a fixture id gets
no `sessions` row at all — so its absent `ended_at` reads as NULL, i.e. as
a live claimant, and it evicted the session actually sitting in the pane.
The trace view's follow-up composer and terminal node both hang off that
session's `bridge_reachable`, so both vanished for the length of whatever
Bash command was running when the suite fired.

Pinned here rather than in `test_pane_occupancy.py`: the store's
"unknown liveness counts as live" rule is deliberate (refusing to steer is
cheaper than typing into someone else's terminal) and stays as it is. What
had to stop is the suite manufacturing rows of that shape.
"""

from __future__ import annotations

import os

from hook_manager.core import HookPayload
from hook_manager.handlers import bridge_registry
from lib.orm.engine import get_connection


def _pane_rows() -> list:
    conn = get_connection()
    try:
        return conn.execute("SELECT trace_id FROM bridge_panes").fetchall()
    finally:
        conn.close()


def test_bridge_is_pinned_off_for_the_whole_suite():
    """Env, not a patched function: `test_replay` hands `os.environ.copy()`
    to `python -m hook_manager`, and only an env pin reaches that child."""
    assert bridge_registry._env_truthy('REGIN_BRIDGE') is False


def test_register_pane_writes_no_row_even_inside_a_real_tmux_pane(monkeypatch):
    monkeypatch.setenv('TMUX_PANE', '%143')
    monkeypatch.setenv('TMUX', '/private/tmp/tmux-501/default,1481,0')

    def _fail(*_a, **_k):
        raise AssertionError('queried tmux with the bridge pinned off')

    monkeypatch.setattr(bridge_registry, '_query_pane_identity', _fail)

    payload = HookPayload(event='SessionStart',
                          session_id='test-fixture-session-deadbeef',
                          cwd='/work')
    assert bridge_registry._register_pane(payload, healing=False) is False
    assert bridge_registry._register_pane(payload, healing=True) is False
    assert _pane_rows() == []


def test_replaying_a_session_start_claims_nothing(monkeypatch):
    """The whole handler, not just its helper — `handle_start` swallows
    exceptions, so a guard that moved below the tmux query would still
    return a well-formed response while writing the row."""
    monkeypatch.setenv('TMUX_PANE', '%143')
    monkeypatch.setenv('TMUX', '/private/tmp/tmux-501/default,1481,0')

    payload = HookPayload(event='SessionStart',
                          session_id='test-fixture-session-deadbeef',
                          cwd='/work')
    bridge_registry.handle_start(payload)
    bridge_registry.handle_turn(payload)
    assert _pane_rows() == []


def test_the_pin_is_what_stops_it(monkeypatch):
    """Negative control: with the pin lifted the registration goes through,
    so the tests above are measuring the guard and not a broken import."""
    monkeypatch.setenv('REGIN_BRIDGE', '1')
    monkeypatch.setenv('TMUX_PANE', '%143')
    monkeypatch.setenv('TMUX', '/private/tmp/tmux-501/default,1481,0')
    monkeypatch.setattr(bridge_registry, '_query_pane_identity',
                        lambda _pane: (1481, 49000, 'node'))
    monkeypatch.setattr(bridge_registry, '_command_allowed', lambda _c: True)
    # Process-global, so it outlives the test either way: clear it going in
    # AND coming out, or whichever test runs next silently short-circuits.
    claim = ('test-fixture-session-deadbeef', '%143')
    bridge_registry._registered_panes.discard(claim)
    try:
        payload = HookPayload(event='SessionStart',
                              session_id='test-fixture-session-deadbeef',
                              cwd='/work')
        assert bridge_registry._register_pane(payload, healing=False) is True
        assert [r['trace_id'] for r in _pane_rows()] == [
            'test-fixture-session-deadbeef']
    finally:
        bridge_registry._registered_panes.discard(claim)
