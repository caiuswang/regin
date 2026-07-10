"""Serve-time labels for workflow-subagent markers (lib/trace/workflow_labels).

The launching session's SubagentStart hooks carry no label for workflow
subagents; the captured run trace stores the same agent_ids WITH labels.
These tests lock in the join: projection/paginated responses and the /live
roster get the run's labels, and every path degrades to a no-op when the run
trace isn't ingested (or the marker already carries a label).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

LAUNCH_TRACE = 'launch-trace-1'
RUN_TRACE = 'wf_run-1'
T0 = '2026-01-01T00:00:00'


@pytest.fixture
def trace_db(tmp_path, monkeypatch):
    db_path = tmp_path / 'trace.db'
    import lib.orm.engine as db_module
    monkeypatch.setattr(db_module, 'DB_PATH', str(db_path))
    db_module.init_db()
    return db_path


def _seed(db_path, rows):
    conn = sqlite3.connect(str(db_path))
    try:
        for r in rows:
            conn.execute(
                """INSERT INTO session_spans
                   (trace_id, span_id, parent_id, name, kind,
                    start_time, status_code, attributes)
                   VALUES (?, ?, ?, ?, 'internal', ?, ?, ?)""",
                (r['trace_id'], r['span_id'], r.get('parent_id'),
                 r['name'], r.get('start_time', T0),
                 r.get('status_code', 'OK'),
                 json.dumps(r.get('attributes', {}))),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_launch_session(db_path, *, marker_label=None, run_id=RUN_TRACE):
    attrs = {'agent_type': 'workflow-subagent', 'agent_id': 'agent-aaa'}
    if marker_label:
        attrs['label'] = marker_label
    _seed(db_path, [
        {'trace_id': LAUNCH_TRACE, 'span_id': 'prompt-1', 'name': 'prompt',
         'attributes': {'text': 'run a workflow'}},
        {'trace_id': LAUNCH_TRACE, 'span_id': 'wf-tool-1',
         'parent_id': 'prompt-1', 'name': 'tool.Workflow',
         'attributes': {'workflow_run_id': run_id, 'workflow_name': 'demo'}},
        {'trace_id': LAUNCH_TRACE, 'span_id': 'sa-start-1',
         'parent_id': 'wf-tool-1', 'name': 'subagent.start',
         'start_time': '2026-01-01T00:00:01', 'attributes': dict(attrs)},
        {'trace_id': LAUNCH_TRACE, 'span_id': 'sa-stop-1',
         'parent_id': 'sa-start-1', 'name': 'subagent.stop',
         'start_time': '2026-01-01T00:00:02', 'attributes': dict(attrs)},
    ])


def _seed_run_trace(db_path):
    _seed(db_path, [
        {'trace_id': RUN_TRACE, 'span_id': 'run-sa-1', 'name': 'subagent.start',
         'attributes': {'agent_id': 'agent-aaa', 'label': 'survey:tags',
                        'agent_type': 'workflow-subagent'}},
    ])


def _marker_attrs(widened, span_id):
    return next(s for s in widened if s['span_id'] == span_id)['attributes']


def test_projection_attaches_run_labels(trace_db):
    _seed_launch_session(trace_db)
    _seed_run_trace(trace_db)
    from lib.trace.trace_service import fetch_session_projection
    widened, _tree = fetch_session_projection(LAUNCH_TRACE)
    assert _marker_attrs(widened, 'sa-start-1')['label'] == 'survey:tags'
    assert _marker_attrs(widened, 'sa-stop-1')['label'] == 'survey:tags'


def test_paginated_attaches_run_labels(trace_db):
    _seed_launch_session(trace_db)
    _seed_run_trace(trace_db)
    from lib.trace.trace_service import fetch_session_paginated
    widened, _tree, _more, _retired = fetch_session_paginated(LAUNCH_TRACE)
    assert _marker_attrs(widened, 'sa-start-1')['label'] == 'survey:tags'


def test_missing_run_trace_is_a_noop(trace_db):
    _seed_launch_session(trace_db)  # run trace never ingested
    from lib.trace.trace_service import fetch_session_projection
    widened, _tree = fetch_session_projection(LAUNCH_TRACE)
    assert 'label' not in _marker_attrs(widened, 'sa-start-1')


def test_existing_marker_label_is_preserved(trace_db):
    _seed_launch_session(trace_db, marker_label='hook-label')
    _seed_run_trace(trace_db)
    from lib.trace.trace_service import fetch_session_projection
    widened, _tree = fetch_session_projection(LAUNCH_TRACE)
    assert _marker_attrs(widened, 'sa-start-1')['label'] == 'hook-label'


def test_roster_description_filled_from_run_labels(trace_db):
    _seed_launch_session(trace_db)
    _seed_run_trace(trace_db)
    from web.blueprints.trace.sessions import _roster_with_activity
    roster, _activity, _ended = _roster_with_activity(LAUNCH_TRACE)
    entry = next(e for e in roster if e['agent_id'] == 'agent-aaa')
    assert entry['description'] == 'survey:tags'
