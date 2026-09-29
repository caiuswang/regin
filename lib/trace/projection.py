"""Pure data transforms used to reshape raw session_spans into the tree
the Vue Trace view consumes.

These live in `lib/` because they are pure transforms over span dicts
with zero Flask/request dependency — both the web blueprints and the
lib-side ingest/queries layer need them, so they belong below the web
layer rather than above it. A handful of tests reach them as
`app_module._graft_orphans` etc.; `web/app.py` re-exports the names so
that surface stays intact.

Pairing is deliberate:
    _fetch_spans(conn, trace_id)       # read
    _graft_orphans(spans)              # pure
    _widen_envelopes(spans)            # pure
    _build_span_tree(spans)            # pure — builds the view model
    _persist_projection(conn, …)       # write — turns a projection into DB UPDATEs

The GET /api/sessions/<id> handler composes these as:
    raw = _fetch_spans(conn, id)
    projected = _widen_envelopes(_graft_orphans(raw))
    tree = _build_span_tree(projected)

POST /api/sessions/<id>/materialize adds a trailing _persist_projection
call to commit the shape back to disk.
"""

from __future__ import annotations

from datetime import datetime

from lib.trace.projection_agents import (
    _AGENT_RESULT_MAX_BYTES,
    _attach_agent_rollups,
    _normalize_agent_ids,
)
from lib.trace.projection_fetch import (
    _fetch_spans,
    _fetch_spans_for_group,
    _fetch_subagent_spans,
    _launch_tool_use_id,
    drop_delegated_spans,
)
from lib.trace.projection_graft import _graft_orphans


def _widen_envelopes(spans: list[dict]) -> list[dict]:
    """Return a copy where every parent span's start/end covers all of its
    children. Pure — does not touch the DB.
    """
    if not spans:
        return spans
    out = [dict(s) for s in spans]
    by_id = {s['span_id']: s for s in out}

    for parent_id, child_spans in _children_by_parent(out).items():
        parent = by_id.get(parent_id)
        if parent:
            _widen_parent(parent, [by_id[c['span_id']] for c in child_spans])
    return out


def _children_by_parent(spans: list[dict]) -> dict[str, list[dict]]:
    children: dict[str, list[dict]] = {}
    for s in spans:
        pid = s.get('parent_id')
        if pid:
            children.setdefault(pid, []).append(s)
    return children


def _widen_parent(parent: dict, child_spans: list[dict]) -> None:
    child_starts = [c['start_time'] for c in child_spans]
    child_ends = [c.get('end_time') or c['start_time'] for c in child_spans]
    new_start = min(parent['start_time'], *child_starts)
    new_end = max(parent.get('end_time') or parent['start_time'], *child_ends)
    parent['start_time'] = new_start
    parent['end_time'] = new_end
    # Widen start/end for layout, but never shrink a semantic duration.
    # Point-in-time spans (assistant_response / assistant.thinking carry
    # inference latency with start==end) would otherwise have their real
    # duration_ms overwritten by the trivial child-envelope width when a
    # server-side tool nests under them. Children fire during inference,
    # so the envelope never legitimately exceeds the semantic value.
    envelope_ms = int(
        (datetime.fromisoformat(new_end) - datetime.fromisoformat(new_start)).total_seconds() * 1000
    )
    parent['duration_ms'] = max(int(parent.get('duration_ms') or 0), envelope_ms)


def _persist_projection(conn, trace_id: str, raw: list[dict], projected: list[dict]) -> dict:
    """Apply the differences between `raw` and `projected` back to the DB.

    Returns counts of the writes made. Caller is responsible for commit().
    """
    raw_by_id = {s['span_id']: s for s in raw}
    parent_updates = 0
    envelope_updates = 0

    for s in projected:
        prev = raw_by_id.get(s['span_id'])
        if prev is None:
            continue
        if s.get('parent_id') != prev.get('parent_id'):
            conn.execute(
                "UPDATE session_spans SET parent_id = ? WHERE span_id = ? AND trace_id = ?",
                (s.get('parent_id'), s['span_id'], trace_id),
            )
            parent_updates += 1
        changed_start = s['start_time'] != prev['start_time']
        changed_end = s.get('end_time') != prev.get('end_time')
        if changed_start or changed_end:
            conn.execute(
                """UPDATE session_spans
                   SET start_time = ?, end_time = ?, duration_ms = ?
                   WHERE span_id = ? AND trace_id = ?""",
                (s['start_time'], s.get('end_time'), s.get('duration_ms'),
                 s['span_id'], trace_id),
            )
            envelope_updates += 1
    if parent_updates or envelope_updates:
        # Re-parenting and envelope widening change the served tree without
        # inserting a row, so the read-side memo cannot notice on its own.
        from lib.trace.trace_service import projection_cache
        projection_cache.invalidate(trace_id)
    return {'parent_updates': parent_updates, 'envelope_updates': envelope_updates}


_ACTIVE_GAP_THRESHOLD_MS = 60_000   # gaps ≤ this count as active processing
_ACTIVE_CONTAINER_NAMES = frozenset({
    'prompt', 'task.notification', 'conversation',
    'session.start', 'session.end',
    'compact.pre', 'compact.post', 'rewind',
    'subagent.start', 'subagent.stop',
})


def _compute_active_work_ms(spans: list[dict]) -> int:
    """Sum of short inter-event gaps among operation spans.

    Tool/turn/skill/rule spans in this codebase are point-in-time
    events — PostToolUse hooks fire after the tool returns, so
    `start_time == end_time`. Summing widths therefore yields zero,
    while summing root-prompt widths overcounts (prompts that the
    user walked away from mid-turn end up spanning days).

    Instead we sort the operation events by timestamp and add up
    consecutive gaps that are ≤ `_ACTIVE_GAP_THRESHOLD_MS`. Short
    gaps represent the agent doing something (model thinking, tool
    overhead, hook plumbing); longer gaps mean the user stepped
    away. Container spans (prompts, conversations, subagent markers,
    session lifecycle) are excluded — they're envelopes around the
    real events, not operations themselves.

    Pure — no DB. Caller passes the already-grafted+widened spans for
    consistency with what the UI renders, but the answer only depends
    on operation timestamps.
    """
    timestamps = sorted(_operation_timestamps(spans))
    total_ms = 0
    for prev, cur in zip(timestamps, timestamps[1:]):
        gap_ms = int((cur - prev).total_seconds() * 1000)
        if 0 < gap_ms <= _ACTIVE_GAP_THRESHOLD_MS:
            total_ms += gap_ms
    return total_ms


def _operation_timestamps(spans: list[dict]) -> list[datetime]:
    """Start times of the leaf, non-container spans (unparseable ones dropped)."""
    span_ids = {s['span_id'] for s in spans}
    has_children = {s.get('parent_id') for s in spans if s.get('parent_id') in span_ids}
    timestamps: list[datetime] = []
    for s in spans:
        if s['span_id'] in has_children or s.get('name') in _ACTIVE_CONTAINER_NAMES:
            continue
        try:
            timestamps.append(datetime.fromisoformat(s['start_time']))
        except (TypeError, ValueError):
            continue
    return timestamps


def _build_span_tree(spans: list[dict]) -> list[dict]:
    """Produce the TreeTable-shaped node list the Vue frontend consumes."""
    if not spans:
        return []
    span_lookup = {s['span_id']: s for s in spans}
    children_map: dict[str, list[dict]] = {}
    roots: list[dict] = []
    for span in spans:
        pid = span.get('parent_id')
        if pid and pid in span_lookup:
            children_map.setdefault(pid, []).append(span)
        else:
            roots.append(span)

    for pid in children_map:
        children_map[pid].sort(key=lambda s: (s['start_time'], s['id']))
    roots.sort(key=lambda s: (s['start_time'], s['id']))

    def _to_nodes(spans_list):
        nodes = []
        for span in spans_list:
            child_spans = children_map.get(span['span_id'], [])
            child_nodes = _to_nodes(child_spans) if child_spans else []

            start_time = span['start_time']
            end_time = span['end_time'] or span['start_time']
            for child in child_spans:
                c_start = child['start_time']
                c_end = child.get('end_time') or c_start
                if c_start < start_time:
                    start_time = c_start
                if c_end > end_time:
                    end_time = c_end

            data = {
                **span,
                'start_time': start_time,
                'end_time': end_time,
            }
            if child_nodes:
                data['isGroup'] = True
                data['spans'] = child_spans
            else:
                data['isSpan'] = True

            node = {'key': f"span-{span['id']}", 'data': data}
            if child_nodes:
                node['children'] = child_nodes
            nodes.append(node)
        return nodes

    return _to_nodes(roots)
