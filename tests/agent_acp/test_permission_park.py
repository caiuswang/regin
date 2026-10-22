"""D310 — a permission the agent still asks for under bypassPermissions parks
for the operator instead of being answered `cancelled`.

Answering `cancelled` ended codebuddy's turn halfway (`git worktree remove
--force`), with no Allow/Deny anywhere. The request now waits on the run's
handle, shows as an answerable blocker, and `bridge-decide`'s
`resolve_permission` hands the operator's choice back over the wire.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from lib import agent_sdk
from lib.agent_acp.launcher import launch_run
from lib.agent_acp.permissions import CANCELLED, selected_outcome
from lib.agent_messages import blockers

FAKE = str(Path(__file__).parent / "fake_acp_server.py")
WAIT = 20
CALL = "call_perm_1"


@pytest.fixture
def spans(monkeypatch):
    posted: list[dict] = []
    monkeypatch.setattr("lib.hook_plugin.post_span",
                        lambda **span: posted.append(span) or True)
    monkeypatch.setattr(
        "lib.agent_messages.event_notify.notify_permission_request",
        lambda **_kw: True)
    monkeypatch.setattr("lib.agent_messages.event_notify.resolve_permission",
                        lambda _trace_id: None)
    return posted


@pytest.fixture
def run(tmp_path, tmp_db, spans):
    record = tmp_path / "wire.jsonl"
    handle = launch_run("go", cwd=str(tmp_path), cmd=[sys.executable, FAKE],
                        env={"FAKE_ACP_PERMISSION": "1",
                             "FAKE_ACP_RECORD": str(record)})
    _until(lambda: handle.permissions.pending())
    yield handle, record
    handle.close()


def _until(cond, timeout=WAIT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.05)
    pytest.fail("condition never held")


def _answer(record: Path) -> dict:
    _until(lambda: record.exists() and any(
        json.loads(line).get("id") == "perm-1"
        for line in record.read_text().splitlines()))
    return next(json.loads(line) for line in record.read_text().splitlines()
                if json.loads(line).get("id") == "perm-1")["result"]


def test_the_request_parks_as_an_answerable_blocker(run):
    handle, _record = run
    assert agent_sdk.run_phase(handle.trace_id) == "waiting-permission"
    [card] = [c for c in blockers.live_blockers()
              if c["trace_id"] == handle.trace_id]
    assert (card["answerable"], card["span_id"], card["tool_name"]) == (
        "decision", CALL, "Bash")


def test_the_park_is_on_the_trace_as_a_pending_request(run, spans):
    [request] = [s for s in spans if s["name"] == "permission.request"]
    assert request["status_code"] == "PENDING"
    assert request["attributes"]["tool_use_id"] == CALL


def test_allow_selects_the_once_option_and_the_turn_finishes(run, spans):
    handle, record = run
    assert agent_sdk.resolve_permission(
        handle.trace_id, {"behavior": "allow"}, tool_use_id=CALL) == (
        True, "decision delivered")
    assert _answer(record) == {"outcome": {"outcome": "selected",
                                           "optionId": "allow"}}
    _until(lambda: handle.turns)
    assert handle.turns[0].status == "exited"
    assert not handle.permissions.pending()
    assert spans[-1]["name"] == "permission.request"
    assert spans[-1]["status_code"] == "OK"


def test_deny_selects_reject(run, spans):
    handle, record = run
    agent_sdk.resolve_permission(handle.trace_id,
                                 {"behavior": "deny", "reason": "no"})
    assert _answer(record) == {"outcome": {"outcome": "selected",
                                           "optionId": "reject"}}
    assert spans[-1]["name"] == "permission.denied"


def test_interrupt_answers_cancelled_and_keeps_the_session(run):
    handle, record = run
    assert agent_sdk.interrupt_run(handle.trace_id)[0] is True
    assert _answer(record) == CANCELLED
    _until(lambda: handle.turns)
    assert not handle.is_closed


def test_close_answers_a_parked_request_cancelled(run):
    handle, record = run
    handle.close()
    assert not handle.permissions.pending()
    assert handle.permissions.ask({"toolCall": {"toolCallId": "late"}}) \
        == CANCELLED


@pytest.mark.parametrize("options, behavior, expected", [
    ([{"kind": "allow", "optionId": "allow_once"},
      {"kind": "allow", "optionId": "allow_session"},
      {"kind": "reject", "optionId": "deny"}], "allow", "allow_once"),
    ([{"kind": "allow", "optionId": "allow_once"},
      {"kind": "reject", "optionId": "deny"}], "deny", "deny"),
    ([{"kind": "allow_always", "optionId": "always"},
      {"kind": "allow_once", "optionId": "once"}], "allow", "once"),
])
def test_selected_outcome_picks_the_once_option(options, behavior, expected):
    assert selected_outcome(options, behavior)["outcome"]["optionId"] == expected


def test_no_option_for_that_side_is_cancelled():
    assert selected_outcome([{"kind": "allow_once", "optionId": "a"}],
                            "deny") == CANCELLED
