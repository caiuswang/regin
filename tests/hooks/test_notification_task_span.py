"""Regression: Kimi's `Notification` hook must produce `task.notification` spans.

Kimi fires a structured Notification per background task (source_kind
``background_task``, ``source_id``/``title``/``body``); nothing subscribed the
event, so its background tasks had no completion card in the conversation feed.
Claude never fires a task-shaped Notification — it injects a
``<task-notification>`` prompt that `prompt_trace` turns into the same span —
so its idle/permission notifications must keep emitting nothing, and the span
id must stay the `task-<id[:13]>` key both producers agree on.
"""

from __future__ import annotations

import pytest

from hook_manager.core import HookPayload
from hook_manager.handlers import misc_events
from lib import hook_plugin


@pytest.fixture
def _captured(monkeypatch):
    spans: list[dict] = []
    monkeypatch.setattr(hook_plugin, 'post_span', lambda **kw: spans.append(kw))
    return spans


# Verbatim from ~/.kimi-code/hook-payloads.jsonl, session
# session_113dcdf9-f20c-488a-a53a-619671b96ad2.
_KIMI_NOTIFICATION = {
    "hook_event_name": "Notification",
    "session_id": "session_113dcdf9-f20c-488a-a53a-619671b96ad2",
    "cwd": "/Users/taowang/regin",
    "sink": "context",
    "notification_type": "task.completed",
    "title": "Background process completed",
    "body": "Run header-collapse e2e test completed.",
    "severity": "info",
    "source_kind": "background_task",
    "source_id": "bash-9ecj8jbe",
    "agent_type": "kimi",
}

_CLAUDE_IDLE_NOTIFICATION = {
    "hook_event_name": "Notification",
    "session_id": "4a03a151-691f-43c5-bcc2-8e8f04bde3e1",
    "cwd": "/Users/taowang/regin",
    "message": "Claude is waiting for your input",
    "notification_type": "idle_prompt",
    "agent_type": "claude",
}

_CLAUDE_PERMISSION_NOTIFICATION = dict(
    _CLAUDE_IDLE_NOTIFICATION,
    message="Claude needs your permission",
    notification_type="permission_prompt",
)


def _payload(raw: dict) -> HookPayload:
    return HookPayload.from_stdin_json("Notification", raw)


def test_kimi_background_task_notification_emits_span(_captured):
    misc_events.notification(_payload(_KIMI_NOTIFICATION))

    assert len(_captured) == 1
    span = _captured[0]
    assert span['name'] == 'task.notification'
    assert span['trace_id'] == 'session_113dcdf9-f20c-488a-a53a-619671b96ad2'
    assert span['span_id'] == 'task-bash-9ecj8jbe'
    attrs = span['attributes']
    assert attrs['task_id'] == 'bash-9ecj8jbe'
    assert attrs['status'] == 'completed'
    assert attrs['summary'] == 'Run header-collapse e2e test completed.'
    assert attrs['text'] == (
        'Background process completed\nRun header-collapse e2e test completed.'
    )


def test_repeat_delivery_upserts_the_same_row(_captured):
    payload = _payload(_KIMI_NOTIFICATION)
    misc_events.notification(payload)
    misc_events.notification(payload)

    assert len(_captured) == 2
    # Ingest keys on (trace_id, span_id) with ON CONFLICT DO UPDATE, so a
    # deterministic id is what makes the redelivery an upsert, not a duplicate.
    rows = {(s['trace_id'], s['span_id']): s for s in _captured}
    assert len(rows) == 1


def test_failed_task_notification_maps_to_failed_status(_captured):
    misc_events.notification(_payload(dict(
        _KIMI_NOTIFICATION,
        notification_type='task.failed',
        severity='error',
        body='Run e2e suite failed with exit code 1.',
    )))

    assert _captured[0]['attributes']['status'] == 'failed'


@pytest.mark.parametrize('raw', [
    _CLAUDE_IDLE_NOTIFICATION,
    _CLAUDE_PERMISSION_NOTIFICATION,
])
def test_claude_notifications_emit_nothing(_captured, raw):
    response = misc_events.notification(_payload(raw))

    assert _captured == []
    assert response.suppress_output is True


def test_handler_is_registered_for_the_notification_event():
    from hook_manager.registry import REGISTRY

    from hook_manager import registry

    wired = [h for h in REGISTRY if 'Notification' in h.events]
    assert 'task_notification' in [h.name for h in wired]
    by_name = {h.name: h for h in wired}
    # Compared against the registry's own binding, not the raw module
    # function: handler modules are reached through a lazy import proxy.
    assert by_name['task_notification'].fn is registry.misc_events.notification


@pytest.mark.parametrize('raw, emitted', [
    (_KIMI_NOTIFICATION, 1),
    (_CLAUDE_IDLE_NOTIFICATION, 0),
])
def test_runner_dispatches_notification_without_leaking_context(
    _captured, raw, emitted,
):
    import io
    import json

    from hook_manager import runner
    from hook_manager.registry import REGISTRY

    handlers = [h for h in REGISTRY if h.name == 'task_notification']
    out = io.StringIO()
    code = runner.run('Notification', handlers, json.dumps(raw), out)

    assert code == 0
    assert 'additionalContext' not in out.getvalue()
    assert len(_captured) == emitted
