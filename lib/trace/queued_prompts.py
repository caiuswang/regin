"""Currently-queued user prompts, derived live from the transcript.

Prompts typed while the agent is busy are queued by Claude Code and fire NO
`UserPromptSubmit` hook, so the trace can't show them via the normal span
path. But Claude Code does record them in the transcript as `queue-operation`
entries — `enqueue` (with `content`) when queued, `remove` (FIFO, no content)
when dequeued. We read those to surface what's *currently* waiting.

This is intentionally EPHEMERAL: queued prompts are returned as a derived
field on the live poll, never persisted as spans. A queued prompt's permanent
record is the real prompt it becomes once processed; "queued" is a transient
state, so deriving it fresh each poll avoids any append-only retire problem
(when an item is dequeued the next poll simply omits it).
"""

from __future__ import annotations

import json

# Auto-injected system queue items (background-task completions, monitor
# events) — already represented as task.notification spans once processed, so
# they'd be noise as "queued user prompt" cards.
_SYSTEM_MARKER = '<task-notification>'


def _parse_queue_ops(path: str) -> tuple[list, int]:
    """Return (enqueues, remove_count) from a transcript's queue-operation
    entries. `enqueues` is [(timestamp, content), ...] in arrival order."""
    enqueues: list[tuple] = []
    removes = 0
    try:
        with open(path) as f:
            for line in f:
                # cheap prefilter — queue-ops are a tiny fraction of lines
                if '"queue-operation"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if e.get('type') != 'queue-operation':
                    continue
                op = e.get('operation')
                if op == 'enqueue':
                    enqueues.append((e.get('timestamp'), e.get('content')))
                elif op == 'remove':
                    removes += 1
    except OSError:
        return [], 0
    return enqueues, removes


def current_queued_prompts(trace_id: str) -> list[dict]:
    """`[{content, enqueued_at}]` for the prompts still in the queue, oldest
    first. Empty when nothing is queued or the transcript is unreadable."""
    from lib.trace.live_rescan import _find_main_transcript
    path = _find_main_transcript(trace_id)
    if not path:
        return []
    enqueues, removes = _parse_queue_ops(path)
    # FIFO: the first `removes` enqueues were dequeued; the rest still wait.
    # Account on the FULL stream (system items occupy queue slots too), THEN
    # drop system items from what we surface.
    still_queued = enqueues[removes:] if removes < len(enqueues) else []
    out: list[dict] = []
    for ts, content in still_queued:
        if not isinstance(content, str) or not content.strip():
            continue
        if _SYSTEM_MARKER in content:
            continue
        out.append({'content': content, 'enqueued_at': ts})
    return out
