"""`POST /api/sessions/batch-close` — the selection bar's Close."""

from __future__ import annotations

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
    # trace_id, status, ended_at, ended_reason
    ('A', 'active', None, None),
    ('B', 'ended', '2026-01-01T01:00:00', 'session_end'),
    ('D', None, None, None),
]


def _seed(db_path):
    conn = sqlite3.connect(str(db_path))
    for tid, status, ended_at, reason in _ROWS:
        conn.execute("INSERT INTO sessions (trace_id, status, ended_at, ended_reason, "
                     "started_at, last_seen) VALUES (?, ?, ?, ?, '2026-01-01T00:00:00', "
                     "'2026-01-01T00:30:00')", (tid, status, ended_at, reason))
    conn.commit()
    conn.close()


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0]: r[1:] for r in conn.execute(
            "SELECT trace_id, status, ended_reason, ended_at FROM sessions")}
    finally:
        conn.close()


def test_closes_open_rows_skips_ended_reports_missing(client, trace_db):
    _seed(trace_db)
    res = client.post('/api/sessions/batch-close', json={'trace_ids': ['A', 'B', 'C', 'D']})
    body = res.get_json()
    assert res.status_code == 200
    assert (body['closed'], body['skipped'], body['missing']) == (['A', 'D'], ['B'], ['C'])
    assert body['processed'] == 4
    rows = _rows(trace_db)
    assert rows['A'][:2] == ('ended', 'manual') and rows['A'][2]
    assert rows['B'] == ('ended', 'session_end', '2026-01-01T01:00:00')


def test_closed_rows_read_closed_in_the_list(client, trace_db):
    _seed(trace_db)
    client.post('/api/sessions/batch-close', json={'trace_ids': ['A']})
    body = client.get('/api/sessions?status=closed').get_json()
    assert [it['trace_id'] for it in body['items']] == ['A']


def test_is_idempotent(client, trace_db):
    _seed(trace_db)
    client.post('/api/sessions/batch-close', json={'trace_ids': ['A']})
    first = _rows(trace_db)['A']
    body = client.post('/api/sessions/batch-close', json={'trace_ids': ['A']}).get_json()
    assert (body['closed'], body['skipped']) == ([], ['A'])
    assert _rows(trace_db)['A'] == first


@pytest.mark.parametrize('payload', [{}, {'trace_ids': []}, {'trace_ids': 'A'},
                                     {'trace_ids': ['A', 3]}, {'trace_ids': ['']}])
def test_rejects_a_bad_body(client, trace_db, payload):
    _seed(trace_db)
    res = client.post('/api/sessions/batch-close', json=payload)
    assert res.status_code == 400
    assert res.get_json()['ok'] is False
    assert _rows(trace_db)['A'][0] == 'active'


def test_a_mid_batch_failure_rolls_back_every_row(client, trace_db, monkeypatch):
    _seed(trace_db)
    from sqlalchemy.orm import Session as OrmSession
    real_flush = OrmSession.flush
    calls = {'n': 0}

    def flaky_flush(self, *a, **kw):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('disk full')
        return real_flush(self, *a, **kw)

    monkeypatch.setattr(OrmSession, 'flush', flaky_flush)
    res = client.post('/api/sessions/batch-close', json={'trace_ids': ['A', 'D', 'B']})
    monkeypatch.setattr(OrmSession, 'flush', real_flush)
    assert res.status_code == 500
    rows = _rows(trace_db)
    assert rows['A'] == ('active', None, None)
    assert rows['D'] == (None, None, None)
