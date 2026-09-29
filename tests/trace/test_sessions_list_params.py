"""Characterization of `/api/sessions` query params and derived row fields
not covered elsewhere: `q`/`scope`, `active`, `kind`, `trace_id`, the
`workflow=only` date hint, and the per-row context/activity derivations."""

from __future__ import annotations

import json
import sqlite3

import pytest

from web import app as app_module


@pytest.fixture
def trace_db(tmp_path, monkeypatch):
    db_path = tmp_path / 'trace.db'
    import lib.orm.engine as db_module
    monkeypatch.setattr(db_module, 'DB_PATH', str(db_path))
    db_module.init_db()
    return db_path


@pytest.fixture
def client(trace_db):
    from lib.auth import create_token
    app = app_module.create_app()
    app.config['TESTING'] = True
    c = app.test_client()
    c.environ_base['HTTP_AUTHORIZATION'] = f"Bearer {create_token(1, 'test-editor', 'admin')}"
    return c


_ROWS = [
    # trace_id, title, status, started_at, last_seen, is_test, origin,
    # model, peak, peak_main, live, active_ms
    ('alpha-1', 'Fix the parser', 'active', '2026-01-01T00:00:00',
     '2026-01-01T00:10:00', 0, 'session', 'claude-opus-4', 50000, 40000,
     20000, 300000),
    ('alpha-2', 'Write docs', 'ended', '2026-01-01T00:00:00',
     '2026-01-02T00:00:00', 0, 'session', None, None, None, None, None),
    ('beta-1', 'Parser tests', 'ended', '2026-01-03T00:00:00',
     '2026-01-03T00:00:00', 1, 'session', None, None, None, None, 0),
    ('run-1', 'Nightly run', 'ended', '2026-01-01T00:00:00',
     '2026-01-01T00:05:00', 0, 'workflow', None, 1000, None, None, None),
    ('run-2', 'Older run', 'ended', '2025-12-01T00:00:00',
     '2025-12-01T00:05:00', 0, 'workflow', None, None, None, None, None),
]


def _seed(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        for r in _ROWS:
            conn.execute(
                "INSERT INTO sessions (trace_id, title, status, started_at, "
                "last_seen, is_test, origin, model, peak_context_tokens, "
                "peak_main_context_tokens, live_context_tokens, active_work_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", r)
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, parent_id, name, "
            "kind, start_time, attributes, status_code) "
            "VALUES (?, ?, NULL, 'prompt', 'internal', ?, ?, 'UNSET')",
            ('alpha-2', 'p1', '2026-01-01T00:00:01',
             json.dumps({'text': 'please refactor the PARSER'})))
        conn.commit()
    finally:
        conn.close()


def _ids(body):
    return sorted(it['trace_id'] for it in body['items'])


_QUERIES = [
    ('', ['alpha-1', 'alpha-2', 'run-1', 'run-2']),
    ('kind=test', ['beta-1']),
    ('kind=all', ['alpha-1', 'alpha-2', 'beta-1', 'run-1', 'run-2']),
    ('kind=bogus&include_tests=1', ['alpha-1', 'alpha-2', 'beta-1', 'run-1', 'run-2']),
    ('active=active', []),
    ('active=inactive', ['alpha-1', 'alpha-2', 'run-1', 'run-2']),
    ('active=nonsense', ['alpha-1', 'alpha-2', 'run-1', 'run-2']),
    ('trace_id=ALPHA', ['alpha-1', 'alpha-2']),
    ('q=parser', ['alpha-1']),
    ('q=parser&scope=title&kind=all', ['alpha-1', 'beta-1']),
    ('q=parser&scope=prompt', ['alpha-2']),
    ('q=parser&scope=both', ['alpha-1', 'alpha-2']),
    ('q=parser&scope=weird', ['alpha-1']),
    ('since=2026-01-01T00:07:00', ['alpha-1', 'alpha-2']),
    ('until=2026-01-01T00:07:00', ['run-1', 'run-2']),
    ('workflow=only&since=2026-01-01T00:00:00', ['run-1']),
]


@pytest.mark.parametrize('qs,expected', _QUERIES)
def test_sessions_list_filters(client, trace_db, qs, expected):
    _seed(trace_db)
    body = client.get(f'/api/sessions?{qs}').get_json()
    assert _ids(body) == expected


@pytest.mark.parametrize('qs,key,expected', [
    ('workflow=only&since=2026-01-01T00:00:00', 'workflow_date_hidden_count', 1),
    ('workflow=only', 'workflow_date_hidden_count', None),
    ('workflow=hide', 'workflow_hidden_count', 2),
    ('workflow=show', 'workflow_hidden_count', None),
    ('q=parser', 'search', 'parser'),
    ('empty=hide', 'empty_hidden_count', 3),
    ('', 'empty_hidden_count', None),
])
def test_sessions_list_envelope(client, trace_db, qs, key, expected):
    _seed(trace_db)
    body = client.get(f'/api/sessions?{qs}').get_json()
    assert body[key] == expected


_ROW_FIELDS = ('context_pct', 'context_pct_all', 'context_window_tokens',
               'active_pct', 'idle_ms', 'agent_kind', 'origin', 'category',
               'is_workflow', 'is_run', 'runner_phase')


def test_sessions_list_row_derivations(client, trace_db):
    _seed(trace_db)
    body = client.get('/api/sessions?kind=all').get_json()
    got = {it['trace_id']: tuple(it.get(k) for k in _ROW_FIELDS)
           for it in body['items']}
    assert got == {
        'alpha-1': (10.0, 25.0, 200000, 50, 300000, None, 'session', 'user',
                    False, False, None),
        'alpha-2': (None, None, None, None, None, None, 'session', 'user',
                    False, False, None),
        'beta-1': (None, None, None, None, 0, None, 'session', 'user',
                   False, False, None),
        'run-1': (0.5, 0.5, 200000, None, None, None, 'workflow', 'system',
                  True, True, None),
        'run-2': (None, None, None, None, None, None, 'workflow', 'system',
                  True, True, None),
    }
