"""Characterization of `/api/sessions` query params and derived row fields
not covered elsewhere: `q`/`scope`, `status`, `kind`, `trace_id`, the
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
    ('status=working,retrying,awaiting,idle', []),
    ('status=inactive', ['alpha-1']),
    ('status=ended', ['alpha-2', 'run-1', 'run-2']),
    ('status=inactive,ended', ['alpha-1', 'alpha-2', 'run-1', 'run-2']),
    ('status=inactive,bogus', ['alpha-1']),
    ('status=bogus', ['alpha-1', 'alpha-2', 'run-1', 'run-2']),
    ('status=INACTIVE', ['alpha-1']),
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


_NO_COUNTS = {'awaiting': 0, 'retrying': 0, 'working': 0, 'idle': 0,
              'starting': 0, 'inactive': 0, 'ended': 0, 'closed': 0}


@pytest.mark.parametrize('qs', ['', 'status=inactive', 'status=ended,closed'])
def test_status_counts_ignore_the_status_selection(client, trace_db, qs):
    """`status_counts` drops the status facet itself (what toggling a chip
    would give); `total_count` is the selection's own sum."""
    _seed(trace_db)
    conn = sqlite3.connect(str(trace_db))
    conn.execute("UPDATE sessions SET ended_reason='manual' WHERE trace_id='run-2'")
    conn.commit()
    conn.close()
    body = client.get(f'/api/sessions?{qs}').get_json()
    assert body['status_counts'] == {**_NO_COUNTS, 'inactive': 1, 'ended': 2, 'closed': 1}
    picked = [w for w in qs.removeprefix('status=').split(',') if w] or list(_NO_COUNTS)
    assert body['total_count'] == sum(body['status_counts'][w] for w in picked)
    assert body['total_count'] == len(body['items'])
    assert body['active_count'] == 0


def test_unknown_status_equals_the_known_part(client, trace_db):
    _seed(trace_db)
    a = client.get('/api/sessions?status=inactive,bogus')
    b = client.get('/api/sessions?status=inactive')
    assert a.status_code == 200
    strip = ('server_now', 'server_now_utc')
    assert ({k: v for k, v in a.get_json().items() if k not in strip}
            == {k: v for k, v in b.get_json().items() if k not in strip})


def test_active_count_sums_the_active_statuses(client, trace_db, monkeypatch):
    """A runner in `starting` with a recent last_seen counts under starting,
    not active — the chip says starting, so the pill must not count it."""
    from datetime import datetime, timedelta
    from web.blueprints.trace.sessions import listing as S
    _seed(trace_db)
    fresh = (datetime.now() - timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%S')
    conn = sqlite3.connect(str(trace_db))
    for tid in ('live-1', 'live-2', 'boot-1'):
        conn.execute("INSERT INTO sessions (trace_id, status, started_at, last_seen, "
                     "is_test, origin) VALUES (?, 'active', ?, ?, 0, 'session')",
                     (tid, fresh, fresh))
    conn.commit()
    conn.close()
    monkeypatch.setattr(S, '_owned_run_phases',
                        lambda: {'boot-1': 'starting', 'live-2': 'waiting-input'})
    body = client.get('/api/sessions').get_json()
    c = body['status_counts']
    assert (c['starting'], c['working'], c['awaiting']) == (1, 1, 1)
    assert body['active_count'] == c['working'] + c['retrying'] + c['awaiting'] + c['idle'] == 2
