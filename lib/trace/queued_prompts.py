"""Currently-queued user prompts, derived live from the transcript.

Prompts typed while the agent is busy are queued by Claude Code and fire NO
`UserPromptSubmit` hook, so the trace can't show them via the normal span
path. But Claude Code does record them in the transcript as `queue-operation`
entries, which we replay in order to reconstruct what's *currently* waiting:

- `enqueue` (with `content`) — a prompt joins the back of the queue.
- `dequeue` / `remove` (no content) — the oldest prompt leaves the queue to be
  processed. (`remove` is the older Claude Code name; `dequeue` the current
  one — both are a single FIFO pop.)
- `popAll` — the whole queue is pulled back out at once, e.g. when the user
  pops the queued prompts back into the editor to edit them. Clears every
  pending item regardless of count.

Replaying in arrival order is what makes this correct: a counter of "removes"
can't express `popAll` (clear everything), and editing a queued message is
just `popAll` + a fresh `enqueue`, so there's no edit operation to special-case
— the in-order replay lands on the right final state on its own.

This is intentionally EPHEMERAL: queued prompts are returned as a derived
field on the live poll, never persisted as spans. A queued prompt's permanent
record is the real prompt it becomes once processed; "queued" is a transient
state, so deriving it fresh each poll avoids any append-only retire problem
(when an item is dequeued the next poll simply omits it).
"""

from __future__ import annotations

import json
from collections import deque

# Single-pop removals: oldest item leaves the queue. `remove` is the legacy
# operation name, `dequeue` the current one.
_POP_ONE = {'dequeue', 'remove'}

# Auto-injected system queue items (background-task completions, monitor
# events) — already represented as task.notification spans once processed, so
# they'd be noise as "queued user prompt" cards.
_SYSTEM_MARKER = '<task-notification>'


def _replay_queue_ops(path: str) -> list[tuple]:
    """Replay a transcript's queue-operation entries in arrival order and
    return the still-queued items as [(timestamp, content), ...], oldest
    first. The full stream is replayed — system auto-queue items occupy queue
    slots too, so they must take part in FIFO accounting; callers drop them
    from what they surface afterwards."""
    queue: deque[tuple] = deque()
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
                    queue.append((e.get('timestamp'), e.get('content')))
                elif op in _POP_ONE:
                    if queue:  # no-op if we began parsing mid-stream
                        queue.popleft()
                elif op == 'popAll':
                    queue.clear()
    except OSError:
        return []
    return list(queue)


def current_queued_prompts(trace_id: str) -> list[dict]:
    """`[{content, enqueued_at}]` for the prompts still in the queue, oldest
    first. Empty when nothing is queued or the transcript is unreadable."""
    from lib.trace.live_rescan import _find_main_transcript
    path = _find_main_transcript(trace_id)
    if not path:
        return []
    still_queued = _replay_queue_ops(path)
    out: list[dict] = []
    for ts, content in still_queued:
        if not isinstance(content, str) or not content.strip():
            continue
        if _SYSTEM_MARKER in content:
            continue
        out.append({'content': content, 'enqueued_at': ts})
    return out
