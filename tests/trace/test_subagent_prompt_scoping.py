"""Subagent launch-prompt spans (`prompt-sa-<agent_id>`, name='prompt',
attributes.agent_id set) must NOT act as main-conversation prompt anchors.

Two proven regressions this guards:
  A. merge: a PENDING main `promptlive-` placeholder was dropped when a
     subagent's prompt-sa had a higher id (window_max / ceiling counted it).
  B. projection: a main turn-less orphan fired during a subagent run was
     grafted under the subagent's prompt-sa subtree.
"""

from __future__ import annotations

import sqlite3

from lib.trace.merge import merge_spans
from lib.trace.projection import _graft_orphans
from lib.trace.trace_service.queries import _prompt_ceiling


def _row(span_id, name, attrs, *, status='UNSET', start, tid='t1', id, parent=None):
    return {
        'id': id, 'trace_id': tid, 'span_id': span_id, 'parent_id': parent,
        'name': name, 'kind': 'internal',
        'start_time': start, 'end_time': None, 'duration_ms': 0,
        'status_code': status, 'status_message': None,
        'attributes': attrs, 'turn_uuid': None,
    }


# ── A. merge: main placeholder survives a higher-id prompt-sa ─────────

def test_main_placeholder_survives_higher_id_subagent_prompt():
    """The user's in-flight `promptlive-` (PENDING, lower id) must NOT drop
    just because a subagent's prompt-sa landed with a higher id."""
    rows = [
        _row('promptlive-main', 'prompt', {'text': 'the users live prompt'},
             status='PENDING', start='2026-04-18T12:00:00', id=10),
        _row('prompt-sa-agentX', 'prompt',
             {'text': 'go do a subtask', 'agent_id': 'agentX'},
             start='2026-04-18T12:00:05', id=20),
    ]
    served = {s['span_id']: s for s in merge_spans(rows)}
    assert 'promptlive-main' in served
    assert served['promptlive-main']['status_code'] == 'PENDING'


def test_verbatim_text_prompt_sa_does_not_supersede_main_placeholder():
    """Text-hash supersede path: a subagent launched with the user's prompt
    VERBATIM must not retire the live main placeholder mid-turn."""
    from lib.trace.pending_spans import prompt_placeholder_id
    text = 'relay this exact request to the worker'
    live_id = prompt_placeholder_id('t1', text)
    rows = [
        _row(live_id, 'prompt', {'text': text},
             status='PENDING', start='2026-04-18T12:00:00', id=10),
        _row('prompt-sa-agentX', 'prompt', {'text': text, 'agent_id': 'agentX'},
             start='2026-04-18T12:00:05', id=20),
    ]
    served = {s['span_id']: s for s in merge_spans(rows)}
    assert live_id in served
    assert served[live_id]['status_code'] == 'PENDING'

    real_anchor = _row('prompt-abc123def4567', 'prompt', {'text': text},
                       start='2026-04-18T12:00:09', id=30)
    served = {s['span_id']: s for s in merge_spans(rows + [real_anchor])}
    assert live_id not in served


def test_main_placeholder_still_drops_for_a_real_higher_main_prompt():
    """Control: a genuine newer MAIN prompt DOES supersede the stale
    placeholder — the scoping only excludes agent-tagged prompts."""
    rows = [
        _row('promptlive-old', 'prompt', {'text': 'abandoned client cmd'},
             status='PENDING', start='2026-04-18T12:00:00', id=10),
        _row('prompt-new', 'prompt', {'text': 'a real new turn'},
             start='2026-04-18T12:00:05', id=20),
    ]
    served = {s['span_id']: s for s in merge_spans(rows)}
    assert 'promptlive-old' not in served


# ── B. projection: main orphan stays under the MAIN prompt ────────────

def test_main_orphan_during_subagent_run_not_nested_under_prompt_sa():
    rows = [
        _row('prompt-main', 'prompt', {'text': 'user goal'},
             start='2026-04-18T12:00:00', id=1),
        _row('subagent.start-1', 'subagent.start', {'agent_id': 'agentX'},
             start='2026-04-18T12:00:01', id=2),
        _row('prompt-sa-agentX', 'prompt',
             {'text': 'subtask', 'agent_id': 'agentX'},
             start='2026-04-18T12:00:02', id=3),
        _row('permreq-tu9', 'permission.request', {'tool_name': 'Bash'},
             status='PENDING', start='2026-04-18T12:00:03', id=4),
    ]
    by_id = {s['span_id']: s for s in _graft_orphans(rows)}
    # The main permission.request nests under the MAIN prompt, never the
    # subagent's launch prompt.
    assert by_id['permreq-tu9']['parent_id'] == 'prompt-main'
    # The prompt-sa itself is reparented under its subagent.start (not a root).
    assert by_id['prompt-sa-agentX']['parent_id'] == 'subagent.start-1'


# ── ceiling SQL: MAX main prompt id excludes prompt-sa ────────────────

def _ceiling_conn():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE session_spans (id INTEGER PRIMARY KEY, trace_id TEXT, "
        "name TEXT, agent_id TEXT, attributes TEXT)"
    )
    return conn


def test_prompt_ceiling_ignores_subagent_prompts():
    conn = _ceiling_conn()
    conn.executemany(
        "INSERT INTO session_spans (id, trace_id, name, agent_id, attributes) "
        "VALUES (?, ?, 'prompt', ?, ?)",
        [
            (1, 't1', None, '{}'),                        # main
            (2, 't1', 'agentX', '{"agent_id":"agentX"}'),  # prompt-sa, column set
            (3, 't1', None, '{"agent_id":"agentY"}'),      # prompt-sa, json only
        ],
    )
    assert _prompt_ceiling(conn, 't1') == 1
