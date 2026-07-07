"""Nested subagent parentage (design Move 3): a depth>=2 subagent's
`subagent.start` nests under its SPAWNING agent's `subagent.start` — carried by
`attributes.parent_agent_id` (stamped by lib/trace/claude_subagents) and
consumed by `_reparent_subagents` in the serve-time projection.

Legacy subagent rows (no `parent_agent_id`) keep the flat "under main" behavior.
"""

from __future__ import annotations

from lib.trace.projection import _build_span_tree, _graft_orphans


def _row(span_id, name, attrs, *, id, start, parent=None):
    return {
        'id': id, 'trace_id': 't1', 'span_id': span_id, 'parent_id': parent,
        'name': name, 'kind': 'internal',
        'start_time': start, 'end_time': None, 'duration_ms': 0,
        'status_code': 'UNSET', 'status_message': None,
        'attributes': attrs, 'turn_uuid': None,
    }


def test_nested_start_reparents_under_spawning_agent():
    """Child agent's `subagent.start` (parent_agent_id → agentP) nests under
    the parent agent's `subagent.start`, and the child's own tool nests under
    the child's start — prompt → parent.start → child.start → child tool."""
    rows = [
        _row('start-P', 'subagent.start', {'agent_id': 'agentP'},
             id=10, start='2026-04-18T12:00:00'),
        _row('start-C', 'subagent.start',
             {'agent_id': 'agentC', 'parent_agent_id': 'agentP'},
             id=20, start='2026-04-18T12:00:05'),
        _row('toolC', 'tool.Edit', {'agent_id': 'agentC', 'tool_name': 'Edit'},
             id=30, start='2026-04-18T12:00:06'),
    ]
    by_id = {s['span_id']: s for s in _graft_orphans(rows)}
    assert by_id['start-C']['parent_id'] == 'start-P'
    assert by_id['toolC']['parent_id'] == 'start-C'
    # The parent's own start stays a root here (no prompt/parent in-window).
    assert by_id['start-P']['parent_id'] is None


def test_legacy_start_without_parent_agent_id_stays_flat():
    """A subagent.start with no parent_agent_id is NOT reparented under another
    agent's start — preserves the flat behavior for legacy/ad-hoc rows."""
    rows = [
        _row('start-P', 'subagent.start', {'agent_id': 'agentP'},
             id=10, start='2026-04-18T12:00:00'),
        _row('start-C', 'subagent.start', {'agent_id': 'agentC'},
             id=20, start='2026-04-18T12:00:05'),
    ]
    by_id = {s['span_id']: s for s in _graft_orphans(rows)}
    assert by_id['start-C']['parent_id'] is None


def test_nested_start_unresolvable_parent_stays_flat():
    """parent_agent_id naming an agent with no start in-window → left as-is
    (no start to nest under), rather than erroring or mis-parenting."""
    rows = [
        _row('start-C', 'subagent.start',
             {'agent_id': 'agentC', 'parent_agent_id': 'agentGONE'},
             id=20, start='2026-04-18T12:00:05'),
    ]
    by_id = {s['span_id']: s for s in _graft_orphans(rows)}
    assert by_id['start-C']['parent_id'] is None


def test_mutually_cyclic_parent_agent_ids_do_not_vanish_from_tree():
    """Legacy bad data predating the resolver's cycle guard
    (`claude_subagents._resolve_parent_agents`): two `subagent.start` spans
    whose `attributes.parent_agent_id` point at EACH OTHER. Serve time must
    be robust to this regardless of resolver hygiene — reparenting either
    span into the other would form a 2-cycle `_build_span_tree` can't root
    (neither end has a reachable parent chain to a root), silently dropping
    BOTH from the rendered tree. Neither may take the other's start as its
    parent; both must still surface as roots."""
    rows = [
        _row('start-X', 'subagent.start',
             {'agent_id': 'agentX', 'parent_agent_id': 'agentY'},
             id=10, start='2026-04-18T12:00:00'),
        _row('start-Y', 'subagent.start',
             {'agent_id': 'agentY', 'parent_agent_id': 'agentX'},
             id=20, start='2026-04-18T12:00:05'),
    ]
    projected = _graft_orphans(rows)
    by_id = {s['span_id']: s for s in projected}
    assert by_id['start-X']['parent_id'] is None
    assert by_id['start-Y']['parent_id'] is None

    tree = _build_span_tree(projected)
    root_span_ids = {node['data']['span_id'] for node in tree}
    assert {'start-X', 'start-Y'} <= root_span_ids
