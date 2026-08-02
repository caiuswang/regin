"""Currently-queued user prompts, derived live from the transcript.

Prompts typed while the agent is busy are queued by Claude Code and fire NO
`UserPromptSubmit` hook, so the trace can't show them via the normal span
path. But Claude Code does record them in the transcript as `queue-operation`
entries, which we replay in order to reconstruct what's *currently* waiting:

- `enqueue` (with `content`) — a prompt joins the back of the queue.
- `dequeue` / `remove` — an item leaves the queue. Bare, it is a FIFO pop of
  the oldest item; carrying `content` it names the one item to drop, which is
  not necessarily the head. (`remove` is the older Claude Code name;
  `dequeue` the current one.)
- `popAll` — the whole queue is pulled back out at once, e.g. when the user
  pops the queued prompts back into the editor to edit them. Clears every
  pending item regardless of count.

Replaying in arrival order is what makes this correct: a counter of "removes"
can't express `popAll` (clear everything), and editing a queued message is
just `popAll` + a fresh `enqueue`, so there's no edit operation to special-case
— the in-order replay lands on the right final state on its own.

The op stream is not always balanced, and an unbalanced replay does not
self-correct: a leaked entry sits at the head forever, so every later bare pop
takes *it* instead of the real oldest item and the genuinely-queued prompts
are pinned behind it for the rest of the session. Two rules keep the replay
honest:

- a `<task-notification>` enqueue supersedes an earlier one for the same
  task id. Claude Code rewrites such a notification in place when the task's
  state changes (`<status>stopped</status>`) and logs only the new enqueue.
- a still-queued item is retired when the transcript shows its turn actually
  ran, whatever the ops claim (`_consumed_enqueue_ids`).

This is intentionally EPHEMERAL: queued prompts are returned as a derived
field on the live poll, never persisted as spans. A queued prompt's permanent
record is the real prompt it becomes once processed; "queued" is a transient
state, so deriving it fresh each poll avoids any append-only retire problem
(when an item is dequeued the next poll simply omits it).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import NamedTuple

# Single-pop removals: one item leaves the queue. `remove` is the legacy
# operation name, `dequeue` the current one.
_POP_ONE = {'dequeue', 'remove'}

# Auto-injected system queue items (background-task completions, monitor
# events) — already represented as task.notification spans once processed, so
# they'd be noise as "queued user prompt" cards.
_SYSTEM_MARKER = '<task-notification>'

_TASK_ID_RE = re.compile(r'<task-id>([^<]+)</task-id>')


class _Item(NamedTuple):
    """One enqueue event. `eid` is its arrival index, which survives the pop
    that removes it from the queue so `_consumed_enqueue_ids` can pair even
    already-popped events against processed turns."""

    eid: int
    ts: str | None
    content: object
    task_id: str | None


def _task_id(content) -> str | None:
    """Task id of a `<task-notification>` body, or None for a user prompt.

    Gated on the notification marker: supersede-by-id is only ever right for
    the auto-queued notifications Claude Code rewrites in place. A user prompt
    that merely quotes a `<task-id>` — asking about one, say — must not evict
    a queue entry.
    """
    if not isinstance(content, str) or _SYSTEM_MARKER not in content:
        return None
    match = _TASK_ID_RE.search(content)
    return match.group(1) if match else None


def _pop_one(queue: list, content) -> None:
    """Apply a single-item removal.

    A pop carrying content names its target, so the head is only right for the
    bare form. An unmatched target is a no-op rather than a head pop: parsing
    can begin mid-stream, and evicting a real item to account for one we never
    saw is what pins the rest of the queue.
    """
    if content:
        key = _norm(content)
        for i, item in enumerate(queue):
            if _norm(item.content) == key:
                del queue[i]
                return
        return
    if queue:
        del queue[0]


def _apply_op(queue: list, enqueued: list, entry: dict) -> None:
    op = entry.get('operation')
    if op == 'enqueue':
        content = entry.get('content')
        item = _Item(len(enqueued), entry.get('timestamp'), content,
                     _task_id(content))
        if item.task_id:
            queue[:] = [q for q in queue if q.task_id != item.task_id]
        queue.append(item)
        enqueued.append(item)
    elif op in _POP_ONE:
        _pop_one(queue, entry.get('content'))
    elif op == 'popAll':
        queue.clear()


def _replay_queue_ops(path: str) -> tuple[list, list]:
    """Replay a transcript's queue-operation entries in arrival order.

    Returns `(still_queued, enqueued)` — the surviving items oldest first, and
    every enqueue event including the popped ones. The full stream is
    replayed: system auto-queue items occupy queue slots too, so they must
    take part in FIFO accounting; callers drop them from what they surface
    afterwards.
    """
    queue: list = []
    enqueued: list = []
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
                _apply_op(queue, enqueued, e)
    except OSError:
        return [], []
    return queue, enqueued


def _norm(content) -> str:
    """Whitespace-collapsed body, matching sessions._steer_key so a bridge
    steer's optimistic copy dedupes against its consumed transcript turn."""
    return ' '.join(str(content or '').split())


def _turn_text(message) -> str:
    """Plain text of a user turn's `message.content` — a raw string as-is, or
    the joined text blocks of the block-array form (image/tool blocks skipped).
    Empty for anything without extractable text."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts = [b.get('text', '') for b in message
                 if isinstance(b, dict) and b.get('type') == 'text']
        return ' '.join(p for p in parts if p)
    return ''


def _consumed_enqueue_ids(enqueued: list, consumed: dict) -> set:
    """Ids of enqueue events whose prompt the transcript has since processed.

    Paired oldest-first per body, one turn per event: two identical queued
    prompts need two processed turns to both retire, so a consumed "continue"
    never retires its still-waiting twin. An event with no timestamp, or whose
    body has no processed turn at or after it, keeps its place in the queue.

    Timestamps are compared as strings, which orders the uniform `…Z` form
    every observed transcript uses; a mixed-offset stream would pair wrongly
    and under-retire.
    """
    by_text: dict = defaultdict(list)
    for item in enqueued:
        by_text[_norm(item.content)].append(item)
    done = set()
    for text, items in by_text.items():
        # A turn older than the oldest unpaired event can't belong to any
        # later one either, so the cursor only moves forward.
        turns = sorted(t for t in consumed.get(text, ()) if t)
        i = 0
        for item in items:
            if not item.ts:
                # Unorderable on its own, but it must not consume the cursor:
                # its siblings can still be paired.
                continue
            while i < len(turns) and turns[i] < item.ts:
                i += 1
            if i == len(turns):
                break
            done.add(item.eid)
            i += 1
    return done


def _consumed_turns(path: str) -> dict:
    """Normalized body → timestamps of the user turns the transcript has
    already PROCESSED (non-meta `type:user`). Empty when unreadable."""
    turns: dict = defaultdict(list)
    try:
        with open(path) as f:
            for line in f:
                # cheap prefilter on the value token — matches both compact
                # transcripts and any pretty-printed spacing around the colon
                if '"user"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if e.get('type') != 'user' or e.get('isMeta'):
                    continue
                text = _norm(_turn_text((e.get('message') or {}).get('content')))
                if text:
                    turns[text].append(e.get('timestamp'))
    # A corrupt/partial-write transcript (invalid UTF-8 mid-iteration) raises
    # UnicodeDecodeError out of `for line in f`, not just json.loads; degrade to
    # empty like an unreadable file rather than 500-ing the unguarded live poll.
    except (OSError, UnicodeDecodeError):
        return {}
    return turns


def consumed_prompt_texts(trace_id: str) -> set[str]:
    """Normalized bodies of user prompts the transcript has already PROCESSED
    — the counterpart to `current_queued_prompts` (still-pending). A bridge
    steer surfaced optimistically must retire the moment it appears here: a
    dequeued/consumed steer leaves the pending queue, so a still-queued-only
    dedup would let its chip linger for the whole window."""
    from lib.trace.live_rescan import _find_main_transcript
    path = _find_main_transcript(trace_id)
    if not path:
        return set()
    return set(_consumed_turns(path))


def current_queued_prompts(trace_id: str) -> list[dict]:
    """`[{content, enqueued_at}]` for the prompts still in the queue, oldest
    first. Empty when nothing is queued or the transcript is unreadable."""
    from lib.trace.live_rescan import _find_main_transcript
    path = _find_main_transcript(trace_id)
    if not path:
        return []
    still_queued, enqueued = _replay_queue_ops(path)
    if still_queued:
        ran = _consumed_enqueue_ids(enqueued, _consumed_turns(path))
        still_queued = [q for q in still_queued if q.eid not in ran]
    out: list[dict] = []
    for item in still_queued:
        if not isinstance(item.content, str) or not item.content.strip():
            continue
        if _SYSTEM_MARKER in item.content:
            continue
        out.append({'content': item.content, 'enqueued_at': item.ts})
    return out
