"""The pending card a held permission request shows on the web (F158 S2).

A request the regin-permit mod holds and the web then decides never fires a
classic `PermissionRequest` (P0 Q3), so the card is written when the hold
registers: the `pending-<tool_use_id>` row, carrying `gate_key(tool, input)`.
When the hold is released or dies, the native dialog's `PermissionRequest` —
which carries no `tool_use_id` — arrives late and must fold into that same card
by `gate_key`. These run the real producers through the real ingest and the
serve-time merge, and pin that the key the hold computes from tool.check's
`e.input` is the one PreToolUse computes from `tool_input`.
"""

from __future__ import annotations

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import permission_events, pre_tool_trace
from lib.agent_bridge.mod_hold import card_span
from lib.hook_plugin import build_span
from lib.trace.pending_spans import tool_pending_id
from lib.trace.merge import merge_spans

_SID = "sess-held-card"
_TU = "toolu_01HeldCard000000000000"
_INPUT = {"command": "rm -rf build", "description": "Clean the build dir",
          "timeout": 60000}


@pytest.fixture
def ingest(flask_client, monkeypatch):
    """Route every producer's `post_span` through the real ingest endpoint."""
    import lib.hook_plugin as hp

    def _post(**span):
        res = flask_client.post("/api/session-spans", json=build_span(**span))
        assert res.status_code == 200, res.get_json()
        return True

    monkeypatch.setattr(hp, "post_span", _post)
    return _post


def _pre_tool_use(tool, tool_input):
    pre_tool_trace.handle(HookPayload(
        event="PreToolUse", session_id=_SID, tool_name=tool,
        tool_input=tool_input, raw={"tool_use_id": _TU}))


def _permission_request(tool, tool_input):
    permission_events._emit_span(HookPayload(
        event="PermissionRequest", session_id=_SID, tool_name=tool,
        tool_input=tool_input, raw={}), "permission.request", status="PENDING")


def _hold_registers(ingest, tool, tool_input):
    ingest(**card_span(_SID, _TU, tool, tool_input))


def _rows():
    from lib.orm.engine import get_connection
    import json
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM session_spans WHERE trace_id = ? ORDER BY id", (_SID,))]
    finally:
        conn.close()
    for r in rows:
        r["attributes"] = json.loads(r["attributes"] or "{}")
    return rows


def _pending_cards():
    return [s for s in merge_spans(_rows()) if s.get("status_code") == "PENDING"]


@pytest.mark.parametrize("tool, tool_input", [
    ("Bash", _INPUT),                                   # PreToolUse writes a placeholder too
    ("Edit", {"file_path": "/tmp/x.py", "old_string": "a", "new_string": "b"}),  # it does not
])
def test_a_held_request_has_exactly_one_pending_card(ingest, tool, tool_input):
    _pre_tool_use(tool, tool_input)
    _hold_registers(ingest, tool, tool_input)
    assert [s["span_id"] for s in _pending_cards()] == [tool_pending_id(_TU)]

    # Released: the native dialog opens and its PermissionRequest lands late.
    _permission_request(tool, tool_input)
    assert [r["name"] for r in _rows()].count("permission.request") == 1

    cards = _pending_cards()
    assert len(cards) == 1
    assert cards[0]["attributes"]["tool_use_id"] == _TU


def test_the_hold_and_pre_tool_use_derive_one_gate_key(monkeypatch):
    import lib.hook_plugin as hp
    posted: list[dict] = []
    monkeypatch.setattr(hp, "post_span", lambda **span: posted.append(span) or True)

    # tool.check's `e.input` and PreToolUse's `tool_input` are the same JSON
    # object; key order differs between the two serializations in the wild.
    _pre_tool_use("Bash", dict(reversed(list(_INPUT.items()))))
    _permission_request("Bash", _INPUT)
    held = card_span(_SID, _TU, "Bash", _INPUT)

    pre, perm = posted
    assert pre["span_id"] == held["span_id"]
    assert held["attributes"]["gate_key"] == pre["attributes"]["gate_key"]
    assert held["attributes"]["gate_key"] == perm["attributes"]["gate_key"]
