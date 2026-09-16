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
import os
import re
from collections import OrderedDict, defaultdict
from typing import NamedTuple

# Single-pop removals: one item leaves the queue. `remove` is the legacy
# operation name, `dequeue` the current one.
_POP_ONE = {'dequeue', 'remove'}

# Auto-injected system queue items (background-task completions, monitor
# events) — already represented as task.notification spans once processed, so
# they'd be noise as "queued user prompt" cards.
_SYSTEM_MARKER = '<task-notification>'

_TASK_ID_RE = re.compile(r'<task-id>([^<]+)</task-id>')

# An EXECUTED slash command is logged as `<command-name>` XML, never as the
# literal line the sender typed — so a queued or bridge-delivered "/exit" can
# only retire against its executed turn if that turn also registers the
# reconstructed command line. Without it the chip outlives the command for
# the rest of its window (observed with /exit: the delivery that killed the
# session haunted its own trace as a queued prompt).
_COMMAND_NAME_RE = re.compile(r'<command-name>\s*([^<]+?)\s*</command-name>')
_COMMAND_ARGS_RE = re.compile(r'<command-args>\s*([^<]*?)\s*</command-args>')


def _command_line(text: str) -> str:
    """The typed form of a command-XML user turn ('/exit', '/goal fix x'),
    or '' when the turn is not a command record."""
    name = _COMMAND_NAME_RE.search(text)
    if not name:
        return ''
    args = _COMMAND_ARGS_RE.search(text)
    return _norm(f"{name.group(1)} {args.group(1) if args else ''}")


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


# Fold state per transcript: `{offset, queue, enqueued}`. The replay below is
# a left fold over the file's lines, and the transcript is append-only, so the
# bytes already folded can never change their contribution — resuming from the
# offset is the same answer as re-reading from zero. It matters because this
# runs on every poll of a live session, against a transcript that reaches tens
# of megabytes: re-reading it whole was the single most expensive thing left in
# the reconcile, and a mtime check could not have helped, because the file
# grows on exactly the ticks that matter.
#
# Bounded to the few sessions a viewer has open; the state per entry is the
# queue itself plus its enqueue log, both of which scale with the session's
# queue traffic rather than with the transcript.
_MAX_REPLAY_STATES = 8
_replay_states: OrderedDict = OrderedDict()


def _fold_line(raw: bytes, queue: list, enqueued: list) -> None:
    # cheap prefilter — queue-ops are a tiny fraction of lines
    if b'"queue-operation"' not in raw:
        return
    try:
        e = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return
    if e.get('type') != 'queue-operation':
        return
    _apply_op(queue, enqueued, e)


def _replay_queue_ops(path: str) -> tuple[list, list]:
    """Replay a transcript's queue-operation entries in arrival order.

    Returns `(still_queued, enqueued)` — the surviving items oldest first, and
    every enqueue event including the popped ones. The full stream is
    replayed: system auto-queue items occupy queue slots too, so they must
    take part in FIFO accounting; callers drop them from what they surface
    afterwards.

    Only the bytes appended since the last call are folded; see the note on
    `_replay_states`. A file that has SHRUNK was rewritten rather than
    appended to, and the fold restarts from zero.
    """
    try:
        st = os.stat(path)
    except OSError:
        return [], []
    size = st.st_size
    # Identity, not just length: a path can be replaced by a different file of
    # equal or greater size (a rotated or rewritten transcript, or a path a
    # later session reuses), and folding fresh bytes onto the previous file's
    # state would carry a queue that never existed.
    identity = (st.st_dev, st.st_ino)

    state = _replay_states.get(path)
    if state is None or state['identity'] != identity or size < state['offset']:
        state = {'identity': identity, 'offset': 0, 'queue': [], 'enqueued': []}
        _replay_states[path] = state
    _replay_states.move_to_end(path)
    while len(_replay_states) > _MAX_REPLAY_STATES:
        # Least recently READ, so the session being polled is never the one
        # evicted — insertion order would have thrown out the hottest entry
        # and sent it back to re-reading the whole transcript.
        _replay_states.popitem(last=False)

    if size > state['offset']:
        try:
            with open(path, 'rb') as f:
                f.seek(state['offset'])
                chunk = f.read()
        except OSError:
            return [], []
        # Stop at the last newline: a trailing partial line is a half-written
        # record, and folding it would both fail to parse and consume bytes
        # the next call must see whole.
        cut = chunk.rfind(b'\n')
        if cut != -1:
            for raw in chunk[:cut + 1].split(b'\n'):
                _fold_line(raw, state['queue'], state['enqueued'])
            state['offset'] += cut + 1

    return list(state['queue']), list(state['enqueued'])


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


# A prompt that was QUEUED is not logged as a `type:user` turn when it finally
# runs — Claude Code records it as a `queued_command` attachment carrying the
# delivered text. Reading user turns alone therefore never sees a mid-turn
# steer get processed, which is precisely the case a steer chip exists for: it
# would stay up for the session's lifetime with the prompt long since answered.
_QUEUED_COMMAND = 'queued_command'


def _processed_text(entry: dict) -> str:
    """Normalized body this record shows as PROCESSED, or '' for anything
    that is not a completed prompt."""
    kind = entry.get('type')
    if kind == 'user':
        if entry.get('isMeta'):
            return ''
        return _norm(_turn_text((entry.get('message') or {}).get('content')))
    if kind == 'attachment':
        attachment = entry.get('attachment') or {}
        if attachment.get('type') == _QUEUED_COMMAND:
            return _norm(attachment.get('prompt'))
    return ''


def _consumed_turns(path: str) -> dict:
    """Normalized body → timestamps of the prompts the transcript has already
    PROCESSED (non-meta `type:user` turns and run queue entries). Empty when
    unreadable."""
    turns: dict = defaultdict(list)
    try:
        with open(path) as f:
            for line in f:
                # cheap prefilter on the value tokens — matches both compact
                # transcripts and any pretty-printed spacing around the colon
                if '"user"' not in line and _QUEUED_COMMAND not in line:
                    continue
                try:
                    e = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                text = _processed_text(e)
                if text:
                    turns[text].append(e.get('timestamp'))
                    command = _command_line(text)
                    if command:
                        turns[command].append(e.get('timestamp'))
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
