"""F180 #8 — an ACP run ending after its report never clears its queue
silently: every steer still queued is written back to its bridge row as a
drop, so the operator sees it and can restore it."""

from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

from lib.agent_acp.launcher import AcpRunHandle
from lib.agent_bridge import store as bridge_store


def _handle(trace_id: str) -> AcpRunHandle:
    return AcpRunHandle(trace_id, Future(), SimpleNamespace(close=lambda: None),
                        trace_id)


def _row(trace_id, row_id):
    return next(r for r in bridge_store.list_bridge_messages(
        session_id=trace_id) if r["id"] == row_id)


def test_acp_end_after_turn_drops_nothing_silently(tmp_db):
    handle = _handle("acp-1")
    row_id = bridge_store.record_bridge_message("acp-1", "late steer",
                                                "web:test")
    assert handle.enqueue("late steer", row_id=row_id) is True

    assert handle.end_after_turn() == (True, "ending after this turn")

    row = _row("acp-1", row_id)
    assert row["delivered"] == 0
    assert row["delivery_detail"].startswith("dropped:")
    assert handle.queued_prompts() == []
    assert [r["id"] for r in bridge_store.list_dropped_steers("acp-1")] == [
        row_id]


def test_a_sealed_handle_refuses_a_steer_until_unsealed(tmp_db):
    handle = _handle("acp-2")

    assert handle.seal_if_idle() == []
    assert handle.enqueue("too late") is False and handle.sealed is True

    handle.unseal()
    assert handle.enqueue("in time") is True
    assert [q["text"] for q in handle.seal_if_idle()] == ["in time"]
    assert handle.sealed is False


def test_an_edit_keeps_the_row_a_drop_is_written_to(tmp_db):
    handle = _handle("acp-3")
    row_id = bridge_store.record_bridge_message("acp-3", "typo", "web:test")
    handle.enqueue("typo", row_id=row_id)
    handle.edit_queued(handle.queued_prompts()[0]["id"], "fixed")

    handle.end_after_turn()

    assert _row("acp-3", row_id)["delivery_detail"].startswith("dropped:")
