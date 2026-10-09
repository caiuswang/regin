"""F152: a session row's resolved title, its launching task, its features, and
the `feature_id` filter — plus the retired auto `feature-*` tags."""

from __future__ import annotations

import sqlite3

import pytest
from alembic import command

from lib import db_migrate
from lib.auth import create_token
from lib.features import session_display


@pytest.fixture
def client(tmp_db):
    from web import app as app_module
    app = app_module.create_app()
    app.config['TESTING'] = True
    c = app.test_client()
    c.environ_base['HTTP_AUTHORIZATION'] = (
        f"Bearer {create_token(1, 'test-editor', 'admin')}")
    return c


@pytest.fixture
def db(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    yield conn
    conn.close()


def _session(db, trace_id, title=None, source=None, last_seen='2026-09-01T10:00:00'):
    db.execute(
        "INSERT INTO sessions (trace_id, title, title_source, started_at,"
        " last_seen) VALUES (?, ?, ?, ?, ?)",
        (trace_id, title, source, '2026-09-01T09:00:00', last_seen))
    db.commit()


def _feature(db, fid, title):
    db.execute("INSERT INTO features (id, title) VALUES (?, ?)", (fid, title))
    db.commit()


def _task(db, tid, title, feature_id=None, runs=()):
    db.execute("INSERT INTO agent_tasks (id, title, status, source, feature_id)"
               " VALUES (?, ?, 'doing', 'test', ?)", (tid, title, feature_id))
    for attempt, trace in enumerate(runs, start=1):
        db.execute("INSERT INTO agent_task_runs (task_id, trace_id, attempt,"
                   " status) VALUES (?, ?, ?, 'done')", (tid, trace, attempt))
    db.commit()


def _rows(client, query=''):
    resp = client.get(f'/api/sessions{query}')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return {r['trace_id']: r for r in resp.get_json()['items']}, resp.get_json()


@pytest.mark.parametrize('title,source,task,expected', [
    ('Refine', 'claude_ai_title', {'id': 1, 'title': 'T'}, ('Refine', 'claude_ai_title')),
    ('Mine', 'user_rename', {'id': 1, 'title': 'T'}, ('Mine', 'user_rename')),
    ('/skill\nfix', 'first_prompt', {'id': 1, 'title': 'T'}, ('T', 'task')),
    ('/skill\nfix', 'first_prompt', None, ('/skill\nfix', 'first_prompt')),
    (None, None, {'id': 1, 'title': 'T'}, ('T', 'task')),
    (None, None, None, (None, None)),
])
def test_display_title_precedence(title, source, task, expected):
    assert session_display.display_title(title, source, task) == expected


def test_task_title_replaces_first_prompt(client, db):
    _session(db, 's1', '/feature-pipeline\nfix the thing', 'first_prompt')
    _task(db, 451, 'Spec F152', runs=['s1'])

    rows, _ = _rows(client)

    assert rows['s1']['display_title'] == 'Spec F152'
    assert rows['s1']['display_title_source'] == 'task'
    assert rows['s1']['task'] == {'id': 451, 'title': 'Spec F152'}
    assert rows['s1']['title'] == '/feature-pipeline\nfix the thing'


def test_ai_title_beats_task_title(client, db):
    _session(db, 's1', 'Refine session list', 'claude_ai_title')
    _task(db, 451, 'Spec F152', runs=['s1'])

    rows, _ = _rows(client)

    assert rows['s1']['display_title'] == 'Refine session list'


def test_no_task_keeps_the_old_rule(client, db):
    _session(db, 's1', 'hello', 'first_prompt')

    rows, _ = _rows(client)

    assert rows['s1']['display_title'] == 'hello'
    assert rows['s1']['task'] is None
    assert rows['s1']['features'] == []


def test_newest_run_wins(client, db):
    _session(db, 's1', 'p', 'first_prompt')
    _task(db, 1, 'older', runs=['s1'])
    _task(db, 2, 'newer', runs=['s1'])

    rows, _ = _rows(client)

    assert rows['s1']['task']['title'] == 'newer'


def test_run_placeholder_collapses_to_child_session(client, db):
    _session(db, 'sdk-x', None, None)
    _session(db, 'cli-c', 'p', 'first_prompt')
    db.execute("INSERT INTO agent_run_sessions (trace_id, cli_session_id)"
               " VALUES ('sdk-x', 'cli-c')")
    db.execute("INSERT INTO agent_runs (trace_id, cli_session_id)"
               " VALUES ('sdk-x', 'cli-c')")
    _feature(db, 7, 'F seven')
    _task(db, 9, 'Build F7', feature_id=7, runs=['sdk-x'])

    rows, _ = _rows(client)

    assert 'sdk-x' not in rows
    assert rows['cli-c']['task'] == {'id': 9, 'title': 'Build F7'}
    assert rows['cli-c']['features'] == [
        {'id': 7, 'title': 'F seven', 'is_primary': True}]


def test_features_merge_declared_and_lane_primary_first(client, db):
    _session(db, 's1', 'p', 'first_prompt')
    _feature(db, 3, 'three')
    _feature(db, 5, 'five')
    db.execute("INSERT INTO session_features (trace_id, feature_id, provenance,"
               " is_primary) VALUES ('s1', 3, 'declared', 0)")
    db.commit()
    _task(db, 1, 't', feature_id=5, runs=['s1'])

    rows, _ = _rows(client)

    assert [f['id'] for f in rows['s1']['features']] == [5, 3]


def test_filter_by_feature(client, db):
    _session(db, 'a', 'a', 'first_prompt')
    _session(db, 'b', 'b', 'first_prompt')
    _session(db, 'c', 'c', 'first_prompt')
    _feature(db, 152, 'Session列表页优化')
    _task(db, 1, 't', feature_id=152, runs=['a'])
    db.execute("INSERT INTO session_features (trace_id, feature_id, provenance,"
               " is_primary) VALUES ('c', 152, 'declared', 1)")
    db.commit()

    rows, env = _rows(client, '?feature_id=152')

    assert set(rows) == {'a', 'c'}
    assert env['total_count'] == 2
    assert env['tag_counts'].get('user') == 2


def test_header_serves_the_same_display_title(client, db):
    _session(db, 's1', '/skill\nfix', 'first_prompt')
    _task(db, 451, 'Spec F152', runs=['s1'])

    listed, _ = _rows(client)
    from web.blueprints.trace.sessions.phase import _display_title_fields
    header = _display_title_fields('s1', '/skill\nfix', 'first_prompt')

    assert header['display_title'] == listed['s1']['display_title']
    assert header['task'] == listed['s1']['task']


def test_migration_removes_auto_feature_tags(tmp_db, db):
    command.stamp(db_migrate._config(), "0038")
    _session(db, 'T')
    db.executemany("INSERT INTO session_tags (trace_id, tag, source)"
                   " VALUES (?, ?, ?)",
                   [('T', 'feature-7', 'auto'), ('T', 'release', 'manual'),
                    ('T', 'feature-mine', 'manual')])
    db.commit()

    command.upgrade(db_migrate._config(), "head")

    left = sorted(db.execute("SELECT tag, source FROM session_tags").fetchall())
    assert left == [('feature-mine', 'manual'), ('release', 'manual')]
