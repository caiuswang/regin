"""Task-list projection folded from TaskCreate/TaskUpdate/TodoList spans."""

from __future__ import annotations

import json

from lib.orm import SessionLocal
from lib.orm.models import SessionSpan


# TaskCreate/TaskUpdate carry ONE task per span; the snapshot tool — Kimi's
# `TodoList` — resends the WHOLE list on every write. Both feed the same event
# log. Claude Code's `TodoWrite` is deliberately excluded: it is not recorded as
# a snapshot span at all (hook_manager/handlers/post_tool_trace.py).
_TASK_SPAN_NAMES = ('tool.TaskCreate', 'tool.TaskUpdate', 'tool.TodoList')

# Statuses a dropped task is never rewritten to `deleted` from — only an
# already-retired one, so a task retires on the snapshot that dropped it and
# not on every snapshot after.
_UNRETIRABLE_STATUSES = frozenset({'deleted'})


def _fetch_session_task_list(trace_id: str) -> dict | None:
    """Return the session's task-list events plus its final snapshot.

    TodoWrite-style task lists evolve over the life of the session —
    subject lives on the early TaskCreate, status flips via TaskUpdate
    spans scattered across later prompts. Two consumers need different
    views of that:

    * The conversation spine wants ONE card per task-write span,
      snapshotting the list as the model saw it AT THAT POINT (no
      future state). Replaying events client-side keeps the payload
      lean instead of shipping 53 near-identical snapshot blobs.
    * The session-header badge wants the FINAL state across the
      whole session.

    Computing both here (not client-side) lets the frontend render the
    full list even when most prompts are unexpanded — the conversation
    view lazy-loads each prompt's children, so a client-side scan over
    `props.spans` would drop tasks whose owning prompt isn't open.

    Returns `{events: [...], final: [...]}` or `None` if the session
    never used Task tools. Each event is `{span_id, timestamp, task_id,
    subject?, status?, order?, agent?}`; absent fields mean "this span didn't
    touch that field" (`order` is a snapshot span's payload position, `agent`
    the writing agent — absent for the main one). `final` is the LIVE snapshot
    after the last event (retired tasks dropped), grouped by owning agent and
    ordered the way that agent last wrote its list (snapshot position, falling
    back to numeric task_id).
    """
    from sqlmodel import select as _select
    with SessionLocal() as session:
        rows = session.exec(
            _select(
                SessionSpan.span_id, SessionSpan.name,
                SessionSpan.start_time, SessionSpan.attributes,
                SessionSpan.agent_id,
            )
            .where(SessionSpan.trace_id == trace_id)
            .where(SessionSpan.name.in_(_TASK_SPAN_NAMES))
            .order_by(SessionSpan.start_time.asc(), SessionSpan.id.asc())
        ).all()
    if not rows:
        return None
    events: list[dict] = []
    state: dict[str, dict] = {}
    # Per-task, track the most recent span_id that set each status.
    # After the loop the final entry's `current_span_id` resolves to
    # the latest span that produced its final status — the header
    # click-to-jump lands on "where the work for this task ended up"
    # rather than always on its TaskCreate. Pending tasks (never
    # updated) fall back to the TaskCreate span.
    last_span_by_status: dict[str, dict[str, str]] = {}
    # (agent_id, subject) → task_id, for the snapshot tool whose todos carry no
    # id. Keyed by agent because a subagent keeps its OWN list in the same
    # trace: sharing identity across agents would let one agent's snapshot
    # retire another's live tasks.
    todo_ids: dict[tuple[str, str], str] = {}
    for r in rows:
        _apply_task_row(r, events, state, last_span_by_status, todo_ids)
    if not events:
        return None
    final = _finalize_task_state(state, last_span_by_status)
    return {'events': events, 'final': final}


def _str_attr(value) -> str:
    """Coerce an attribute to a non-empty string, or '' otherwise."""
    return value if isinstance(value, str) and value else ''


def _apply_task_row(r, events, state, last_span_by_status, todo_ids=None) -> None:
    """Fold one task-write span into the event log + state.

    A snapshot span (`todos` attr) expands into one event per task; a
    TaskCreate/TaskUpdate span contributes its single task.
    """
    try:
        attrs = json.loads(r.attributes) if r.attributes else {}
    except (ValueError, TypeError):
        attrs = {}
    # The column is promoted from attributes at insert; rows written before
    # that promotion only carry the attribute.
    agent = _str_attr(getattr(r, 'agent_id', None)) or _str_attr(
        attrs.get('agent_id'))
    todos = attrs.get('todos')
    if isinstance(todos, list) and todos:
        _apply_todo_snapshot(r, todos, agent, events, state,
                             last_span_by_status,
                             todo_ids if todo_ids is not None else {})
        return
    tid = attrs.get('task_id')
    if tid is None:
        return
    _record_task_event(r, {
        'task_id': str(tid),
        'subject': _str_attr(attrs.get('subject')),
        'status': _str_attr(attrs.get('status')),
        'active_form': _str_attr(attrs.get('active_form')),
    }, events, state, last_span_by_status, agent)


def _record_task_event(r, task: dict, events, state, last_span_by_status,
                       agent: str = '') -> None:
    """Append one task's event (omitting subject/status when absent) and update
    the running per-task entry: first non-empty subject wins, every set status
    both overwrites the entry and records the span_id under that status in
    `last_span_by_status`. A snapshot-supplied `order` overwrites — it is the
    task's position in the LATEST whole-list payload, so the rendered list
    tracks the order the agent last wrote, not first-seen order. `agent` rides
    along so both surfaces can keep one agent's list whole instead of
    interleaving two agents' payload positions.
    """
    tid, status = task['task_id'], task['status']
    optional = {
        'subject': task['subject'],
        'status': status,
        'order': task.get('order'),
        'agent': agent,
    }
    events.append({
        'span_id': r.span_id,
        'timestamp': r.start_time,
        'task_id': tid,
        **{k: v for k, v in optional.items() if v is not None and v != ''},
    })
    entry = state.setdefault(tid, {
        'task_id': tid,
        'subject': '',
        'status': 'pending',
        'active_form': '',
        'agent_id': agent,
        # First TaskCreate's span_id — fallback for pending tasks.
        'created_span_id': r.span_id,
    })
    for field in ('subject', 'active_form'):
        if task[field] and not entry[field]:
            entry[field] = task[field]
    if task.get('order') is not None:
        entry['order'] = task['order']
    if status:
        entry['status'] = status
        last_span_by_status.setdefault(tid, {})[status] = r.span_id


def _apply_todo_snapshot(r, todos, agent, events, state, last_span_by_status,
                         todo_ids) -> None:
    """Fold one whole-list task write (`tool.TodoList`) by its writing agent.

    Its todos carry no id, and a session can replace the list wholesale (a new
    plan reuses position 1 for a different task), so identity is the subject:
    `todo_ids` hands each newly-seen (agent, subject) the next ordinal.
    Position-keyed ids would be one span shorter but graft a replacement's
    subject onto the old task's history. Display order comes from the event's
    `order` field (position in this payload), not the minted id, so a
    reordered list renders the way the agent last wrote it.
    """
    seen: set[str] = set()
    for position, todo in enumerate(todos):
        if not isinstance(todo, dict):
            continue
        subject = _str_attr(todo.get('subject'))
        if not subject:
            continue
        # Namespaced so a minted snapshot id can never collide with a
        # provider-supplied TaskCreate/TaskUpdate `task_id`, which is a bare
        # ordinal drawn from the same `state` keyspace.
        tid = todo_ids.setdefault((agent, subject), f'todo-{len(todo_ids) + 1}')
        seen.add(tid)
        _record_task_event(r, {
            'task_id': tid,
            'subject': subject,
            'status': _str_attr(todo.get('status')) or 'pending',
            'active_form': _str_attr(todo.get('active_form')),
            'order': position,
        }, events, state, last_span_by_status, agent)
    _retire_missing_todos(r, agent, seen, events, state, last_span_by_status,
                          todo_ids)


def _retire_missing_todos(r, agent, seen, events, state, last_span_by_status,
                          todo_ids) -> None:
    """A task the newest snapshot dropped is recorded `deleted` — that's how the
    task-list surfaces retire it, so the card keeps matching the list the model
    now sees instead of stranding the old plan's items as open work.

    Finished tasks retire too: a snapshot payload IS the whole list, so a plan
    the agent has moved on from is gone from its terminal regardless of how its
    items ended. Sparing `completed` instead accumulated every plan a long
    session ever wrote into one list, and their payload positions collided
    (CAI-44). Nothing is lost — `events` still carries each write, so the card
    for an earlier span replays that plan intact.

    Only the writing agent's OWN tasks are eligible: a subagent's list is a
    different list that happens to share the trace, and retiring across agents
    would mark the main agent's live work deleted (and vice versa).
    """
    for (owner, _subject), tid in todo_ids.items():
        if owner != agent or tid in seen:
            continue
        if state.get(tid, {}).get('status') in _UNRETIRABLE_STATUSES:
            continue
        _record_task_event(r, {
            'task_id': tid, 'subject': '', 'status': 'deleted',
            'active_form': '',
        }, events, state, last_span_by_status, agent)


def _finalize_task_state(state, last_span_by_status) -> list[dict]:
    """Resolve `current_span_id` per task and return the sorted live snapshot.

    Retired tasks are dropped: `final` is what the session's agents have on
    their lists NOW, and every consumer either filtered `deleted` itself or
    (the /live card) miscounted it as open work.

    `current_span_id` is the latest span that set the FINAL status,
    falling back to the TaskCreate for pending tasks. Tasks group by owning
    agent — main agent first, then each subagent in the order its first STILL-
    LIVE task was created — because two agents' payload positions both start
    at 0 and would otherwise interleave
    into one unreadable list. Within an agent, tasks a snapshot span
    positioned (an `order` from the latest whole-list payload) sort by that
    position — the order the agent last wrote; unpositioned tasks
    (TaskCreate/TaskUpdate numeric ids) keep their legacy order: numeric ids
    numerically, non-digit ids sunk to the end lexically.
    """
    live = [e for e in state.values() if e['status'] != 'deleted']
    for entry in live:
        per_status = last_span_by_status.get(entry['task_id'], {})
        entry['current_span_id'] = (
            per_status.get(entry['status']) or entry['created_span_id']
        )
    ranks: dict[str, int] = {'': 0}
    for entry in live:
        ranks.setdefault(entry.get('agent_id') or '', len(ranks))
    return sorted(live, key=lambda t: _task_sort_key(t, ranks))


def _task_sort_key(t: dict, ranks: dict[str, int]) -> tuple:
    agent = ranks.get(t.get('agent_id') or '', 0)
    order = t.get('order')
    if order is not None:
        return (agent, 0, order, t['task_id'])
    return (agent, 1,
            int(t['task_id']) if t['task_id'].isdigit() else 1_000_000,
            t['task_id'])
