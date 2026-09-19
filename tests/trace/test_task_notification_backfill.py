"""A task notification the hook never saw must still reach the trace.

`task.notification` spans had exactly ONE writer: the UserPromptSubmit hook.
A notification injected as a queued item when a session resumes does not fire
that hook, so the fact was lost for good — which is how two shells the harness
had already killed kept reporting `running`, elapsed ticking upward, on a
session that was still very much alive.

The transcript holds the entry either way, so it is the second writer — the
same hook/transcript pairing every other span family already has.
"""

from __future__ import annotations

import json

import pytest

from lib.trace import repair
from lib.trace.task_notification import notification_span_id

ORPHAN_TEXT = (
    '<task-notification>\n'
    '<task-id>bmbknevn9</task-id>\n'
    '<task-id>bsk9mt0d6</task-id>\n'
    '<task-id>__orphan_summary__:shell</task-id>\n'
    '<status>stopped</status>\n'
    '<summary>2 background shell command tasks did not finish.</summary>\n'
    '</task-notification>'
)


def _transcript(tmp_path, *texts):
    path = tmp_path / 't.jsonl'
    with path.open('w') as fh:
        for i, text in enumerate(texts):
            fh.write(json.dumps({
                'type': 'user', 'uuid': f'u{i}',
                'timestamp': '2026-09-19T12:14:00.000Z',
                'message': {'role': 'user', 'content': text},
            }) + '\n')
    return str(path)


@pytest.fixture
def posted(monkeypatch):
    spans = []
    monkeypatch.setattr('lib.hook_plugin.post_span',
                        lambda **kw: spans.append(kw) or True)
    return spans


def test_the_span_id_matches_what_the_hook_would_have_written():
    """Both writers must land on ONE row; the store upserts on (trace, span)."""
    assert notification_span_id('bmbknevn9') == 'task-bmbknevn9'


def test_an_unseen_notification_is_backfilled(tmp_path, posted, monkeypatch):
    monkeypatch.setattr(repair, '_existing_notification_span_ids', lambda t: set())

    n = repair.backfill_task_notifications('t', _transcript(tmp_path, ORPHAN_TEXT))

    assert n == 1
    assert posted[0]['name'] == 'task.notification'
    assert posted[0]['attributes']['status'] == 'stopped'
    assert posted[0]['attributes']['task_ids'] == ['bmbknevn9', 'bsk9mt0d6']


def test_a_notification_already_in_the_trace_is_not_rewritten(tmp_path, posted, monkeypatch):
    monkeypatch.setattr(repair, '_existing_notification_span_ids',
                        lambda t: {'task-bmbknevn9'})

    n = repair.backfill_task_notifications('t', _transcript(tmp_path, ORPHAN_TEXT))

    assert n == 0
    assert posted == []


def test_an_ordinary_prompt_is_not_a_notification(tmp_path, posted, monkeypatch):
    monkeypatch.setattr(repair, '_existing_notification_span_ids', lambda t: set())

    n = repair.backfill_task_notifications(
        't', _transcript(tmp_path, 'please fix the background shell block'))

    assert n == 0
    assert posted == []
