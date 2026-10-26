"""Serve-time merge of the append-only span store into one canonical view.

`session_spans` is written append-only by two independent sources — live
hook events (`source='hook'`: tool timing, permissions, skill reads, the
in-flight `promptlive-` prompt placeholder) and the transcript scan
(`source='transcript'`: the real `prompt-<uuid>` anchor,
assistant_response/thinking, local commands). The two disagree about the
same turn: a placeholder and its real anchor coexist; a pending tool/
permission span coexists with its resolved counterpart.

`merge_spans` is the SINGLE place that reconciles them. It runs at read
time as a pure function over the rows of one window — no DB writes, no
mutation — so the store stays append-only and projection rules can change
without a data migration (they self-heal on the next read).

It owns four things, in order:

  1. **dedup / supersession** — drop the transient placeholder/pending rows
     whose resolved counterpart is present in the window (this replaces the
     old ingest-time DELETE/promote machinery: `_retire_superseded_pending`,
     `_retire_pending_permissions`, `_sweep_superseded_pending_blockers`).
  2. **reparent** — `_graft_orphans` (the 3-tier ladder in projection_graft.py).
  3. **reorder** — `_graft_orphans` already sorts; `_build_span_tree`
     (downstream) does the final stable sort by (start_time, id).
  4. **link** — `_link_background_tasks` joins a backgrounded `Bash` call to
     the `task.notification` its shell emitted on completion, by task id.

When the resolved counterpart is NOT yet present (a prompt still in flight,
a permission still blocking), the placeholder is kept — that is exactly how
the live view shows in-progress work. Over an already-reconciled historical
window (no placeholders/pending left) every drop rule is a no-op, so
`merge_spans(raw) == _graft_orphans(raw)` — the idempotency/no-regression
property the read path relies on.
"""

from __future__ import annotations

from lib.trace.agent_spawn import AGENT_SPAWN_SPAN
from lib.trace.pending_spans import parse_naive_ts as _merge_ts
from lib.trace.projection import _graft_orphans
from lib.trace.tool_decision import stamp_tool_decisions
from lib.trace.merge_anchors import _absorb_slash_command_expansions, _attrs
from lib.trace.merge_gates import (
    _collapse_open_gates,
    _drop_denied_tool_failures,
    _drop_resolved_permission_requests,
    _drop_superseded_placeholders,
    open_gate_placeholder_ids,
)
from lib.trace.merge_cross_source import (
    QUEUED_STATUS_MESSAGE,
    _collapse_shared_span_ids,
    _dedup_cross_source,
    _drop_resolved_transcript_pendings,
)
from lib.trace.merge_pending import (
    INTERRUPT_SOURCE_STALE,
    _demote_stale_pending,
    _drop_stale_blockers,
    prompt_births,
)


# Attribute keys the background-shell join stamps on a launching `tool.Bash`
# span. Named apart from the Bash call's own `stdout`/`duration_ms` because
# they describe the SHELL, which outlives the call that started it.
_BG_STATUS = 'background_status'
_BG_RUNNING = 'running'

#: Row id of the OTHER span a serve-time-derived body was built from.
#:
#: `_body_needed` (web/blueprints/trace/sessions/subtrees.py) withholds a span the
#: client already holds once it is below the watermark and settled — resting
#: on "a body only changes when its own row changes". That holds for every
#: attribute a hook wrote and fails for one derived here: the source row is an
#: INSERT with a fresh id, while the span it enriches keeps the id it had. The
#: reader ships the body when THIS id is above the watermark, which is the same
#: identifier test applied to the row the body actually came from — so it
#: settles as soon as the client has read past it, rather than re-sending
#: forever.
DERIVED_FROM_ID_KEY = 'derived_from_id'


def _background_completions(spans: list[dict]) -> dict:
    """`{task_id: notification span}` for every `task.notification` in the
    window. Agent reports land here too; they simply match no shell."""
    done: dict = {}
    for s in spans:
        if s.get('name') != 'task.notification':
            continue
        task_id = _attrs(s).get('task_id')
        if isinstance(task_id, str) and task_id:
            done[task_id] = s
    return done


def _elapsed_ms(started: str | None, ended: str | None) -> int | None:
    """Milliseconds between two RECORDED events. Not a window, threshold or
    skew allowance — both endpoints are stamps the harness already wrote."""
    a, b = _merge_ts(started), _merge_ts(ended)
    if a is None or b is None or b < a:
        return None
    return int((b - a).total_seconds() * 1000)


def _link_background_tasks(spans: list[dict]) -> list[dict]:
    """Join each backgrounded `tool.Bash` call to its completion notification.

    A `run_in_background` call returns in milliseconds and its shell keeps
    running; the completion arrives later as an independent
    `task.notification` row. Without this the trace shows an instant
    no-output success and an unrelated notification elsewhere, and a shell
    still running shows nothing at all.

    Keyed on `background_task_id == task_id` — an identifier equality, never a
    timestamp or ordering test, so parallel shells cannot be crossed. The
    launching span's own `duration_ms` is left alone: 3 ms is the true cost of
    the tool call and the cost aggregates read it.
    """
    done = _background_completions(spans)
    out = []
    for s in spans:
        attrs = _attrs(s)
        task_id = attrs.get('background_task_id')
        if s.get('name') != 'tool.Bash' or not isinstance(task_id, str) or not task_id:
            out.append(s)
            continue
        out.append({**s, 'attributes': _background_attrs(attrs, done.get(task_id), s)})
    return out


def _background_attrs(attrs: dict, notification: dict | None, bash: dict) -> dict:
    if notification is None:
        return {**attrs, _BG_STATUS: _BG_RUNNING}
    note = _attrs(notification)
    ended = notification.get('start_time')
    merged = {**attrs,
              _BG_STATUS: note.get('status') or _BG_RUNNING,
              'background_ended_at': ended}
    merged = _derived_from(merged, notification.get('id'))
    for src, dst in (('summary', 'background_summary'),
                     ('output_file', 'background_output_file')):
        value = note.get(src)
        if value:
            merged[dst] = value
    elapsed = _elapsed_ms(bash.get('start_time'), ended)
    if elapsed is not None:
        merged['background_elapsed_ms'] = elapsed
    return merged


TOOL_BATCH_SPAN = 'batch.tools'


def _derived_from(attrs: dict, source_id) -> dict:
    """Keep the newest source row: one span can be enriched by two joins, and
    the watermark test is only right against the later of them."""
    if source_id is None:
        return attrs
    prev = attrs.get(DERIVED_FROM_ID_KEY)
    if isinstance(prev, int) and isinstance(source_id, int) and prev > source_id:
        return attrs
    return {**attrs, DERIVED_FROM_ID_KEY: source_id}


def _tool_batch_members(spans: list[dict]) -> dict:
    """`{tool_use_id: batch.tools row}` for every batch in the window."""
    members: dict = {}
    for s in spans:
        if s.get('name') != TOOL_BATCH_SPAN:
            continue
        ids = _attrs(s).get('tool_use_ids')
        for tu_id in ids if isinstance(ids, list) else ():
            if isinstance(tu_id, str) and tu_id:
                members[tu_id] = s
    return members


def _stamp_tool_batches(spans: list[dict]) -> list[dict]:
    """Stamp `tool_batch_id` on each tool span a PostToolBatch row lists, and
    drop the batch rows: they exist to carry the grouping, not to render.

    Joined on `tool_use_id` equality, so two batches can never be confused by
    their timing. A tool span no batch names is left untouched.
    """
    members = _tool_batch_members(spans)
    if not members:
        return spans
    out = []
    for s in spans:
        if s.get('name') == TOOL_BATCH_SPAN:
            continue
        attrs = _attrs(s)
        batch = members.get(attrs.get('tool_use_id'))
        if batch is None or not str(s.get('name') or '').startswith('tool.'):
            out.append(s)
            continue
        stamped = {**attrs, 'tool_batch_id': _attrs(batch).get('tool_batch_id')}
        out.append({**s, 'attributes': _derived_from(stamped, batch.get('id'))})
    return out


def merge_spans(
    raw: list[dict], prompt_id_ceiling=None, session_activity=None,
) -> list[dict]:
    """Reconcile one window of append-only rows into the canonical span list.

    Pure: returns a new list, mutates neither `raw` nor the DB. Dedup runs
    first (so superseded placeholders can't open phantom turns), then the
    double-rendered-rejection drop (`_drop_denied_tool_failures`), then a
    slash-command rescue (`_absorb_slash_command_expansions` moves an
    expansion onto its resolved echo before the stale sweep could drop it),
    the stale-blocker drop, then the stuck-pending demotion. Drop-in
    replacement for `_graft_orphans` at the read path — identical over
    already-reconciled windows.

    `prompt_id_ceiling` is the per-trace GLOBAL max prompt id; a windowed
    reader passes it so stray prompt placeholders drop even in an older
    scroll-up window (see `_drop_stale_blockers`). Whole-session readers omit
    it. `session_activity` ({'status', 'last_seen'}, optional) enables the
    session-inactive demotion path (`_demote_stale_pending`); without it only
    the same-agent-moved-on path fires."""
    if not raw:
        return raw
    births = prompt_births(raw)
    spans = _drop_superseded_placeholders(raw)
    spans = _drop_resolved_permission_requests(spans)
    # After the resolved-gate drop, so only a gate that is still open — the
    # one case that renders twice — can reach the collapse.
    spans = _collapse_open_gates(spans)
    spans = _drop_denied_tool_failures(spans)
    spans = _absorb_slash_command_expansions(spans)
    # After each writer's own placeholder rules have settled: they key on
    # span-id conventions that only hold within one writer, so collapsing a
    # cross-writer pair earlier strips the `pending-` row `_inherit_turn_linkage`
    # reads from and leaves a slash expansion stranded on the SDK row.
    spans = _dedup_cross_source(spans)
    spans = _drop_resolved_transcript_pendings(spans)
    spans = _drop_stale_blockers(spans, prompt_id_ceiling=prompt_id_ceiling,
                                 births=births)
    spans = _demote_stale_pending(spans, session_activity=session_activity)
    # Last: they read the survivors, and stamp attributes no earlier rule keys on.
    spans = _stamp_tool_batches(spans)
    spans = _link_background_tasks(spans)
    spans = stamp_tool_decisions(spans)
    # Lineage for claude_subagents' parent_agent_id stamp, not a tree node.
    spans = [s for s in spans if s.get('name') != AGENT_SPAWN_SPAN]
    return _graft_orphans(spans)
