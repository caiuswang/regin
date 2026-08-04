"""Chip lifecycle on `bridge_messages` (`kind` + `state`).

The /live "queued" chip feed is no longer a time-windowed scan of the audit
log: a row's `kind` decides whether it can ever be a chip (steers only), and
`state` advances one-way on observed events — consumed / closed / dismissed —
so nothing lingers by timer and nothing audit-only ("selected …" answers,
permission decisions) ever renders as queued.
"""

from __future__ import annotations

import pytest

from lib.agent_bridge import store


pytestmark = pytest.mark.usefixtures("tmp_db")


def _row(row_id):
    rows = store.list_bridge_messages(limit=200)
    return next(r for r in rows if r["id"] == row_id)


def _state(row_id):
    from lib.orm.engine import get_connection
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT kind, state FROM bridge_messages WHERE id = ?", (row_id,))
        return dict(cur.fetchone())
    finally:
        conn.close()


def test_record_kind_and_birth_state():
    tmux_steer = store.record_bridge_message("T-1", "/exit", "web",
                                             pending=True)
    sdk_steer = store.record_bridge_message("T-1", "steer sdk", "web")
    answer = store.record_bridge_message("T-1", "selected Yes", "web",
                                         kind="answer")
    decision = store.record_bridge_message("T-1", "allowed the pending request",
                                           "web", kind="decision")
    assert _state(tmux_steer) == {"kind": "steer", "state": "pending"}
    # An SDK-tier steer's display truth is the runner's queue, an answer's /
    # decision's is nothing at all — all born closed, never chip-eligible.
    assert _state(sdk_steer) == {"kind": "steer", "state": "closed"}
    assert _state(answer) == {"kind": "answer", "state": "closed"}
    assert _state(decision) == {"kind": "decision", "state": "closed"}


def test_list_pending_steers_serves_delivered_pending_oldest_first():
    first = store.record_bridge_message("T-2", "first", "web", pending=True)
    second = store.record_bridge_message("T-2", "second", "web", pending=True)
    undelivered = store.record_bridge_message("T-2", "refused", "web",
                                              pending=True)
    answer = store.record_bridge_message("T-2", "selected Yes", "web",
                                         kind="answer")
    store.mark_delivered(first, True, "ok")
    store.mark_delivered(second, True, "ok")
    store.mark_delivered(undelivered, False, "pane gone")
    store.mark_delivered(answer, True, "ok")
    served = store.list_pending_steers("T-2")
    assert [r["id"] for r in served] == [first, second]
    assert [r["body"] for r in served] == ["first", "second"]


def test_settle_is_one_way_and_idempotent():
    row = store.record_bridge_message("T-3", "ran", "web", pending=True)
    store.mark_delivered(row, True, "ok")
    store.settle_steers([row], "consumed")
    assert _state(row)["state"] == "consumed"
    # A settled row never re-enters the machine: a later close, a repeat
    # settle, and a dismiss are all no-ops against it.
    store.settle_steers([row], "closed")
    assert _state(row)["state"] == "consumed"
    assert store.dismiss_steer("T-3", row) is False


def test_dismiss_is_scoped_to_the_session():
    row = store.record_bridge_message("T-4", "never ran", "web", pending=True)
    store.mark_delivered(row, True, "ok")
    assert store.dismiss_steer("someone-else", row) is False
    assert store.dismiss_steer("T-4", row) is True
    assert _state(row)["state"] == "dismissed"
    assert store.list_pending_steers("T-4") == []
    # Refusal, not error, the second time — the chip was already gone.
    assert store.dismiss_steer("T-4", row) is False


def test_dismiss_route_retires_the_chip(flask_client):
    row = store.record_bridge_message("T-5", "swallowed by an overlay", "web",
                                      pending=True)
    store.mark_delivered(row, True, "ok")
    res = flask_client.delete(f"/api/agent-runs/T-5/queue/b{row}").get_json()
    assert res["removed"] is True
    assert store.list_pending_steers("T-5") == []
    stale = flask_client.delete(f"/api/agent-runs/T-5/queue/b{row}").get_json()
    assert stale["removed"] is False
