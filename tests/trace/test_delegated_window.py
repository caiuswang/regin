"""A windowed read must still know which agent a runner-tagged row belongs to.

The runner tags a subagent's spans with the launching call's id; only the
`subagent.start` marker maps that to the roster id. A paginated page or a
`/subtrees` refresh reads a time window, and an agent launched in an earlier
turn keeps emitting into the current one — so the marker is routinely outside
the rows being normalized. Left untranslated, its running Bash rendered as
the parent's own running command.
"""

from __future__ import annotations

import json
import sqlite3

from lib.trace.projection import _normalize_agent_ids, drop_delegated_spans

TRACE = 'parent-trace'
LAUNCH = 'toolu_01LAUNCH'
AGENT = 'a1b2c3d4e5f6a7b8c'


def _conn():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE session_spans (
        id INTEGER PRIMARY KEY, trace_id TEXT, span_id TEXT, name TEXT,
        agent_id TEXT, attributes TEXT)""")
    conn.execute(
        "INSERT INTO session_spans (trace_id, span_id, name, agent_id, attributes) "
        "VALUES (?,?,?,?,?)",
        (TRACE, 'start-1', 'subagent.start', AGENT,
         json.dumps({'agent_id': AGENT, 'launch_tool_use_id': LAUNCH})))
    return conn


def _row(span_id, name, agent_id, **attrs):
    return {'id': 1, 'trace_id': TRACE, 'span_id': span_id, 'name': name,
            'agent_id': agent_id, 'attributes': {'agent_id': agent_id, **attrs}}


def test_marker_outside_the_window_is_read_from_the_store():
    rows = [_row('pending-toolu_9', 'tool.Bash', LAUNCH)]
    out = _normalize_agent_ids(rows, _conn(), [TRACE])
    assert out[0]['agent_id'] == AGENT
    assert out[0]['attributes']['agent_id'] == AGENT


def test_marker_inside_the_window_needs_no_store():
    rows = [
        _row('start-1', 'subagent.start', AGENT, launch_tool_use_id=LAUNCH),
        _row('pending-toolu_9', 'tool.Bash', LAUNCH),
    ]
    out = _normalize_agent_ids(rows, None, None)
    assert out[1]['agent_id'] == AGENT


def test_an_id_no_marker_names_stays_as_stored():
    rows = [_row('x', 'tool.Bash', 'toolu_unknown')]
    out = _normalize_agent_ids(rows, _conn(), [TRACE])
    assert out[0]['agent_id'] == 'toolu_unknown'


def test_normalized_row_is_dropped_from_the_parent_once_its_marker_is_present():
    """The point of the translation: with the marker in the served set the
    agent collapses to its card, and a translated row goes with it."""
    rows = _normalize_agent_ids([_row('pending-toolu_9', 'tool.Bash', LAUNCH)],
                                _conn(), [TRACE])
    marker = _row('start-1', 'subagent.start', AGENT, launch_tool_use_id=LAUNCH)
    served = drop_delegated_spans([marker, *rows])
    assert [s['span_id'] for s in served] == ['start-1']


def _launch(span_id, tool_use_id, agent_id, **attrs):
    """A `tool.Agent` call. `agent_id` names the agent that MADE the call —
    set for a nested launch, None for one the main session made."""
    return {'id': 2, 'trace_id': TRACE, 'span_id': span_id, 'name': 'tool.Agent',
            'agent_id': agent_id, 'tool_use_id': tool_use_id,
            'attributes': {'tool_name': 'Agent', 'tool_use_id': tool_use_id,
                           'agent_id': agent_id, **attrs}}


NESTED = 'b1b2c3d4e5f6a7b8c'
NESTED_LAUNCH = 'toolu_01NESTED'


def _nested_pair():
    """An agent that launched another: its own launch is delegated work, the
    marker it produced is not."""
    return [
        _row('start-1', 'subagent.start', AGENT, launch_tool_use_id=LAUNCH),
        _launch('nested-launch', NESTED_LAUNCH, AGENT,
                description='Read the docs', prompt='Read docs/a.md and report'),
        _row('start-2', 'subagent.start', NESTED, launch_tool_use_id=NESTED_LAUNCH),
    ]


def test_a_withheld_launch_leaves_its_brief_on_the_marker():
    served = drop_delegated_spans(_nested_pair())
    assert [s['span_id'] for s in served] == ['start-1', 'start-2']
    nested = served[1]['attributes']
    assert nested['agent_launch_description'] == 'Read the docs'
    assert nested['agent_launch_prompt'] == 'Read docs/a.md and report'


def test_the_brief_lands_on_the_marker_that_names_the_launch():
    """Not on whichever marker sits nearest it: the join is the launching
    call's id, so a sibling agent's card cannot inherit another's brief."""
    served = drop_delegated_spans(_nested_pair())
    assert 'agent_launch_prompt' not in served[0]['attributes']


def test_a_served_launch_is_not_copied_onto_its_marker():
    """The main session's own `tool.Agent` stays in the feed and the card reads
    it there — copying it would ship the brief twice."""
    spans = [_launch('own-launch', LAUNCH, None, description='d', prompt='p'),
             _row('start-1', 'subagent.start', AGENT, launch_tool_use_id=LAUNCH)]
    served = drop_delegated_spans(spans)
    assert [s['span_id'] for s in served] == ['own-launch', 'start-1']
    assert 'agent_launch_prompt' not in served[1]['attributes']
