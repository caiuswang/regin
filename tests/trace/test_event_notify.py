"""Unit tests for interaction-event pushes (lib.agent_messages.event_notify).

Permission prompts and plan-ready events route through `record_message`
(inbox + push fan-out) only when their opt-in toggle is on, are de-duped
against a double-firing event, and resolve cleanly. `record_message` is
captured so nothing hits the DB/network.
"""

from __future__ import annotations

import pytest

from lib.agent_messages import event_notify
from lib.settings import settings


@pytest.fixture
def recorded(monkeypatch):
    """Capture record_message calls; control live_keyed_message lookups."""
    calls: list[dict] = []
    live: dict = {}  # key -> serialized message the store would return

    def _record(**kw):
        calls.append(kw)
        live[kw["msg_key"]] = {"body": kw["body"], "span_id": kw.get("span_id")}
        return {"id": len(calls), **kw}

    def _live(trace_id, key):
        return live.get(key)

    dismissed: list[tuple] = []

    def _dismiss(trace_id, key):
        live.pop(key, None)
        dismissed.append((trace_id, key))
        return 1

    from lib.agent_messages import store
    monkeypatch.setattr(store, "record_message", _record)
    monkeypatch.setattr(store, "live_keyed_message", _live)
    monkeypatch.setattr(store, "dismiss_keyed", _dismiss)
    return {"calls": calls, "dismissed": dismissed}


def _on(monkeypatch, **kw):
    for key, val in kw.items():
        monkeypatch.setattr(settings.agent_messages, key, val)


# ── Permission ──────────────────────────────────────────────

def test_permission_off_by_default_is_noop(recorded, monkeypatch):
    _on(monkeypatch, push_permission_events=False)
    assert event_notify.notify_permission_request(
        trace_id="s1", attrs={"tool_name": "Bash"}) is False
    assert recorded["calls"] == []


def test_permission_pushed_as_blocker(recorded, monkeypatch):
    _on(monkeypatch, push_permission_events=True)
    ok = event_notify.notify_permission_request(
        trace_id="s1",
        attrs={"tool_name": "Bash", "requested_permission": "run `rm -rf x`",
               "option_count": 2, "tool_use_id": "tu_1"})
    assert ok is True
    (call,) = recorded["calls"]
    assert call["msg_type"] == "blocker"
    assert call["msg_key"] == "permission-pending"
    assert call["span_id"] == "tu_1"
    assert "rm -rf x" in call["body"]
    assert "Bash" in call["title"]


def test_permission_double_fire_pushes_once(recorded, monkeypatch):
    _on(monkeypatch, push_permission_events=True)
    attrs = {"tool_name": "Bash", "requested_permission": "run x",
             "tool_use_id": "tu_1"}
    assert event_notify.notify_permission_request(trace_id="s1", attrs=attrs)
    # same prompt fires again (PreToolUse + PermissionRequest) → de-duped
    assert event_notify.notify_permission_request(trace_id="s1", attrs=attrs) is False
    assert len(recorded["calls"]) == 1


def test_distinct_prompts_each_push(recorded, monkeypatch):
    _on(monkeypatch, push_permission_events=True)
    event_notify.notify_permission_request(
        trace_id="s1", attrs={"tool_name": "Bash", "requested_permission": "a"})
    event_notify.notify_permission_request(
        trace_id="s1", attrs={"tool_name": "Write", "requested_permission": "b"})
    assert len(recorded["calls"]) == 2  # different body → supersede + push


def test_askuserquestion_formats_question_and_options(recorded, monkeypatch):
    _on(monkeypatch, push_permission_events=True)
    event_notify.notify_permission_request(trace_id="s1", attrs={
        "tool_name": "AskUserQuestion",
        "questions": [{"question": "Which DB?",
                       "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]})
    (call,) = recorded["calls"]
    assert "Which DB?" in call["body"]
    assert "• Postgres" in call["body"]
    assert call["title"] == "The agent is asking you a question"


def test_resolve_dismisses_when_enabled(recorded, monkeypatch):
    _on(monkeypatch, push_permission_events=True)
    event_notify.notify_permission_request(
        trace_id="s1", attrs={"tool_name": "Bash", "requested_permission": "a"})
    event_notify.resolve_permission("s1")
    assert recorded["dismissed"] == [("s1", "permission-pending")]


def test_resolve_noop_when_disabled(recorded, monkeypatch):
    _on(monkeypatch, push_permission_events=False)
    event_notify.resolve_permission("s1")
    assert recorded["dismissed"] == []


def test_no_trace_id_is_noop(recorded, monkeypatch):
    _on(monkeypatch, push_permission_events=True)
    assert event_notify.notify_permission_request(
        trace_id=None, attrs={"tool_name": "Bash"}) is False
    assert recorded["calls"] == []


# ── Plan ────────────────────────────────────────────────────

def test_plan_off_by_default_is_noop(recorded, monkeypatch):
    _on(monkeypatch, push_plan_events=False)
    assert event_notify.notify_plan_ready(trace_id="s1", plan_text="x") is False
    assert recorded["calls"] == []


def test_plan_pushed_as_warning_with_text(recorded, monkeypatch):
    _on(monkeypatch, push_plan_events=True)
    assert event_notify.notify_plan_ready(trace_id="s1", plan_text="  Do A then B  ")
    (call,) = recorded["calls"]
    assert call["msg_type"] == "warning"
    assert call["msg_key"] == "plan-pending"
    assert call["body"] == "Do A then B"
    assert call["title"] == "Plan ready for review"


def test_plan_without_text_uses_generic_body(recorded, monkeypatch):
    _on(monkeypatch, push_plan_events=True)
    event_notify.notify_plan_ready(trace_id="s1", plan_text=None)
    (call,) = recorded["calls"]
    assert "waiting for you to approve" in call["body"]


def test_plan_text_truncated(recorded, monkeypatch):
    _on(monkeypatch, push_plan_events=True)
    event_notify.notify_plan_ready(trace_id="s1", plan_text="x" * 5000)
    (call,) = recorded["calls"]
    assert call["body"].endswith("…")
    assert len(call["body"]) <= event_notify._PLAN_MAX + 1
