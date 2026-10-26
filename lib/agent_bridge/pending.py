"""Operator messages a session has accepted but not yet handed to its model.

One reader for two callers that must agree: the queue chip on the session map
and the `task finish` guard (`lib/tasks/message_gate.py`). The guard refuses a
report while this list is non-empty, so a count the chip shows and the guard
does not see — or the reverse — is a message that can be finished over.
"""

from __future__ import annotations

import re
from datetime import datetime

from lib.orm import SessionLocal
from lib.orm.models import Session as SessionModel
from lib.trace.pasted_content import unwrap_pasted_content

PREVIEW_CHARS = 80

_SOURCES = {"bridge": "bridge_row", "transcript": "transcript_queue"}


def operator_queue(trace_id: str, *, conn=None) -> list[dict]:
    """The queue chips for `trace_id`, oldest first — dropped rows excluded.

    `conn` is a connection holding the write lock (the tmux seal): the bridge
    rows are read on it, a read failure raises rather than reading as empty,
    and the chip poll's one-way state writes are skipped — they would wait on
    the very lock the caller holds.
    """
    runner_queue = _runner_queue(trace_id)
    if runner_queue is not None:
        return runner_queue
    from lib.trace.queued_prompts import current_queued_prompts
    try:
        queued = current_queued_prompts(trace_id)
    except Exception:
        queued = []
    return queued + _pending_bridge_steers(trace_id, queued, conn=conn)


def pending_operator_messages(trace_id: str, *, conn=None) -> list[dict]:
    """`{source, id, preview}` for each message `operator_queue` holds."""
    if not trace_id:
        return []
    acp = _is_acp(trace_id)
    out = []
    for i, chip in enumerate(operator_queue(trace_id, conn=conn)):
        source = chip.get("source") or "transcript"
        if source == "sdk":
            source = "acp_queue" if acp else "sdk_queue"
        out.append({"source": _SOURCES.get(source, source),
                    "id": str(chip.get("id") or f"t{i}"),
                    "preview": str(chip.get("content") or "")[:PREVIEW_CHARS]})
    return out


def _is_acp(trace_id: str) -> bool:
    from lib.agent_sdk import registry
    try:
        return registry._acp_handle(trace_id) is not None
    except Exception:
        return False


def _runner_queue(trace_id: str) -> list | None:
    """What an SDK-owned run still holds behind its current turn, or None when
    this is not such a run.

    A run regin launched queues prompts in memory, so this list IS the queue:
    an entry appears when the prompt is accepted and disappears when its turn
    starts. Neither other source can stand in for it, and both actively
    mislead here, which is why an owned run takes this path *instead of* them
    rather than merging with them:

    - the transcript path sees nothing — a launched run writes no
      `queue-operation` entries — so a queued steer would have no server
      representation at all while the prompt is still waiting.
    - the bridge path records every SDK steer as a `bridge_messages` row for
      the audit trail, but those rows are born state `closed` (this queue is
      what displays them), so the bridge chip feed rightly serves none of
      them — and its consumed-transition is transcript-derived, which a run
      with no transcript could never trigger.

    Identical prompts are deliberately not collapsed: two "continue"s are two
    queue entries and must drop one at a time. `id` is what makes that possible
    from the browser — it names one entry for the edit/remove routes, and its
    presence is also how the card knows these rows are editable at all (the
    transcript and bridge paths have no id to give, because neither queue is
    regin's to write).
    """
    from lib import agent_sdk
    from lib.agent_sdk import store as sdk_store
    try:
        if not agent_sdk.is_sdk_owned(trace_id):
            # Not live — but if regin launched it, its queue was this process's
            # and is simply gone, which is not the same as "ask the other
            # sources". The child's transcript would answer, and an edited
            # prompt would lie: it shows the body it was sent with rather than
            # the body it ran as (seen on a real run, pre-edit).
            return [] if sdk_store.find_run(trace_id) else None
        pending = agent_sdk.queued_prompts(trace_id)
    except Exception:
        return None
    return [{'id': q['id'], 'content': q['text'], 'source': 'sdk'}
            for q in pending]


def _steer_key(content) -> str:
    """Normalized body for deduping a bridge steer against the transcript
    queue — whitespace-collapsed so a composer echo and the enqueued copy
    match, and unwrapped so the `<pasted_content>` block Claude Code builds
    around every bracketed paste >= 20 chars compares equal to the body the
    bridge pasted."""
    return ' '.join(unwrap_pasted_content(content).split())


def _pending_bridge_steers(trace_id: str, queued: list, *,
                           conn=None) -> list:
    """Bridge steers still owed a chip, oldest first — and the transition
    writer that retires the rest.

    No time window: a delivered steer's row is born `pending` and stays
    chip-eligible until an observed event settles it, persisted one-way here
    so the state machine converges instead of being re-guessed every poll:

    - the transcript now represents its body (still-queued entry, or a
      processed user turn / reconstructed command line) → `consumed`; the
      transcript is authoritative from then on and the chip must never
      resurface, however the turn was logged.
    - the session ended → `closed`: a chip is a promise the pane may still
      act on the text, which an exited claude can never honour.
    - the operator removed it → `dismissed` (the DELETE route, not here).

    A steer none of those events ever touch stays visible for the session's
    lifetime — honest for a message that was delivered but never ran (a TUI
    overlay swallowed it, say), and exactly what the dismiss affordance is
    for. Bodies are matched as normalized text (`_steer_key`), so identical
    twin steers retire together once one of them runs — set-based, same
    fidelity the window-era dedup had."""
    from lib.agent_bridge import store as bridge_store
    if conn is not None:
        rows = bridge_store.list_pending_steers(trace_id, conn=conn)
    else:
        try:
            rows = bridge_store.list_pending_steers(trace_id)
        except Exception:
            return []
    if not rows:
        return []
    settle = conn is None
    if _session_has_ended(trace_id):
        if settle:
            bridge_store.settle_steers([r['id'] for r in rows], 'closed')
        return []
    out, settled = _partition_steers(rows, _represented_texts(trace_id, queued))
    if settled and settle:
        bridge_store.settle_steers(settled, 'consumed')
    return out


# What the tmux tier types is not byte-identical to the body it recorded, and
# a steer chip has no time window to expire — so a retire predicate that only
# knows exact equality leaves the chip up for the session's lifetime. Two
# rewrites are known and both are reversed here rather than tolerated fuzzily:
#
#   * `attachments.for_tmux` materializes each `[Image #N]` marker into an
#     `@<path>` reference, so the transcript turn carries paths the recorded
#     body never had. The body keeps the bare markers on purpose (they bind the
#     stored bytes to the span for the thumbnail), so the PATHS come off the
#     transcript side. Matched on the `pasted/` delivery directory rather than
#     on where the path sits, because the tier has placed it both at the marker
#     and hoisted to the front — and because an operator's own `@src/foo.py`
#     must survive, or two different prompts would start retiring each other.
#   * a composer in vim NORMAL mode eats the head of the typed text as motions,
#     so the logged turn is a proper suffix of what was sent. `delivery` now
#     prevents that, but rows stranded by it still have to be able to retire.
_IMAGE_PATH_REF_RE = re.compile(
    r'@\S*/pasted/\S+?\.(?:png|jpe?g|gif|webp)\s*', re.IGNORECASE)

# Below this length a suffix match is coincidence, not a truncated echo.
_STEER_SUFFIX_FLOOR = 40


def _delivered_key(text) -> str:
    """`_steer_key` with the `@path` references the tmux tier injects removed,
    so a transcript turn and the body it was delivered from compare equal.
    Re-normalized after the strip: removing a reference closes a gap, and a
    stray double space would defeat the very comparison this exists for."""
    return ' '.join(_IMAGE_PATH_REF_RE.sub('', _steer_key(text)).split())


def _represented_texts(trace_id: str, queued: list) -> set:
    """Normalized bodies the transcript already speaks for — processed turns
    (including reconstructed command lines) plus the still-queued entries."""
    from lib.trace.queued_prompts import consumed_prompt_texts
    try:
        seen = consumed_prompt_texts(trace_id)
    except Exception:
        seen = set()
    return {_delivered_key(t) for t in seen} | {
        _delivered_key(q.get('content')) for q in queued}


def _is_represented(key: str, seen: set) -> bool:
    """True when `seen` speaks for this steer body — exactly, or across the
    two ways the head of a typed line stops matching: the composer ate it
    (the turn is a suffix of the body) or the composer already held a draft
    the text was typed onto (the body is a suffix of the turn). Either way
    the transcript demonstrably carries the steer, which is the question."""
    if key in seen:
        return True
    if len(key) < _STEER_SUFFIX_FLOOR:
        return False
    return any(len(t) >= _STEER_SUFFIX_FLOOR
               and (key.endswith(t) or t.endswith(key)) for t in seen)


def _partition_steers(rows: list, seen: set) -> tuple[list, list]:
    """Split pending steer rows into (chips to serve, row ids to settle).
    An empty body settles too: it can never dedup, so it could never leave."""
    out, settled = [], []
    for r in rows:
        key = _delivered_key(r.get('body'))
        if not key or _is_represented(key, seen):
            settled.append(r['id'])
            continue
        out.append({'id': f"b{r['id']}", 'content': r['body'],
                    'enqueued_at': r.get('delivered_at') or r.get('created_at'),
                    'source': 'bridge'})
    return out, settled


def _session_has_ended(trace_id: str) -> bool:
    """True when the session row reads closed. A steer chip is a promise the
    pane may still act on the text, which an exited claude can never honour —
    without this gate the /exit that killed a session haunts its own trace as
    a queued prompt for the rest of the delivery window. Mirrors the client's
    ended rule (useLiveTail): trust `status`; honour a stored `ended_at` only
    when no activity followed it (the ingest clears it on a genuine resume,
    but this poll can land in between). Fails open — an unreadable row must
    not hide a live session's steers."""
    from sqlmodel import select as _select
    try:
        with SessionLocal() as session:
            row = session.exec(
                _select(SessionModel.status, SessionModel.ended_at,
                        SessionModel.last_seen)
                .where(SessionModel.trace_id == trace_id)).first()
    except Exception:
        return False
    if row is None:
        return False
    status, ended_at, last_seen = row
    if status == 'ended':
        return True
    if not ended_at:
        return False
    try:
        ended = datetime.fromisoformat(str(ended_at))
        seen = datetime.fromisoformat(str(last_seen)) if last_seen else None
    except ValueError:
        return False
    return seen is None or (seen - ended).total_seconds() <= 1.0
