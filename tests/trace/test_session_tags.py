"""Session tags: the derived-builtin + stored-custom grouping model.

Covers the registry (origin → builtin category, slug validation), the
list-endpoint surface (per-row `category`/`tags`, `?tag=` filter for both
builtin and custom slugs, `tag_counts`), and the custom-tag CRUD + delete
cleanup. Fixtures mirror tests/trace/test_trace_api.py.
"""

from __future__ import annotations

import sqlite3

import pytest

from web import app as app_module
from lib.trace import session_tags as reg


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
    c.environ_base['HTTP_AUTHORIZATION'] = (
        f"Bearer {create_token(1, 'test-editor', 'editor')}")
    c._db_path = trace_db
    return c


def _seed(db_path, trace_id, *, origin='session', is_test=0, agent_type=None,
          last_seen='2026-01-01T00:00:00'):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions "
            "(trace_id, started_at, last_seen, origin, is_test, agent_type) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (trace_id, '2026-01-01T00:00:00', last_seen, origin, is_test,
             agent_type))
        conn.commit()
    finally:
        conn.close()


# ── registry ────────────────────────────────────────────────────

def test_builtin_tag_for_origin_partitions_origins():
    assert reg.builtin_tag_for_origin('session') == 'user'
    assert reg.builtin_tag_for_origin(None) == 'user'
    assert reg.builtin_tag_for_origin('topic-proposal') == 'topic-proposal'
    assert reg.builtin_tag_for_origin('workflow') == 'system'
    assert reg.builtin_tag_for_origin('llm-stage') == 'system'
    # An unknown origin falls back to user, never crashes.
    assert reg.builtin_tag_for_origin('something-new') == 'user'


def test_system_origins_drives_run_definition():
    assert set(reg.system_origins()) == {'workflow', 'llm-stage'}


def test_normalize_custom_slug_rejects_builtins_and_bad_charset():
    assert reg.normalize_custom_slug('Important') == 'important'
    assert reg.normalize_custom_slug('  My-Tag  ') == 'my-tag'
    assert reg.normalize_custom_slug('system') is None      # builtin reserved
    assert reg.normalize_custom_slug('user') is None        # builtin reserved
    assert reg.normalize_custom_slug('has spaces') is None
    assert reg.normalize_custom_slug('under_score') is None
    assert reg.normalize_custom_slug('') is None
    assert reg.normalize_custom_slug('x' * 41) is None
    assert reg.normalize_custom_slug(None) is None


# ── list endpoint: category + tags + filter + counts ────────────

def test_list_row_carries_category_and_builtin_tag(client):
    _seed(client._db_path, 't-user', origin='session')
    r = client.get('/api/sessions?workflow=show')
    row = next(x for x in r.get_json()['items'] if x['trace_id'] == 't-user')
    assert row['category'] == 'user'
    assert row['tags'] == [{'slug': 'user', 'source': 'auto', 'builtin': True}]


def test_builtin_tag_filter_matches_origin(client):
    p = client._db_path
    _seed(p, 't-sess', origin='session')
    _seed(p, 't-tp', origin='topic-proposal')
    _seed(p, 't-wf', origin='workflow')
    _seed(p, 't-llm', origin='llm-stage')

    def ids(tag):
        j = client.get(f'/api/sessions?workflow=show&tag={tag}').get_json()
        return {x['trace_id'] for x in j['items']}

    assert ids('user') == {'t-sess'}
    assert ids('topic-proposal') == {'t-tp'}
    assert ids('system') == {'t-wf', 't-llm'}


def test_tag_counts_match_group_by(client):
    p = client._db_path
    _seed(p, 'a', origin='session')
    _seed(p, 'b', origin='session')
    _seed(p, 'c', origin='topic-proposal')
    _seed(p, 'd', origin='workflow')
    j = client.get('/api/sessions?workflow=show').get_json()
    assert j['tag_counts'] == {'user': 2, 'topic-proposal': 1, 'system': 1}
    assert {t['slug'] for t in j['builtin_tags']} == {
        'user', 'topic-proposal', 'system'}


def test_invalid_tag_narrows_to_empty(client):
    _seed(client._db_path, 'x', origin='session')
    # malformed slug (bad charset) → requested but unusable → empty
    j = client.get('/api/sessions?workflow=show&tag=bad_slug!!').get_json()
    assert j['items'] == []
    # well-formed but nonexistent custom slug → empty
    j = client.get('/api/sessions?workflow=show&tag=ghost-tag').get_json()
    assert j['items'] == []
    # empty tag param → no selection → row visible
    j = client.get('/api/sessions?workflow=show&tag=').get_json()
    assert {x['trace_id'] for x in j['items']} == {'x'}


# ── custom tag CRUD ─────────────────────────────────────────────

def test_custom_tag_add_filter_list_remove_roundtrip(client):
    _seed(client._db_path, 's1', origin='session')

    # add (uppercase normalizes to lowercase slug)
    r = client.post('/api/sessions/s1/tags', json={'tag': 'Important'})
    assert r.status_code == 200 and r.get_json()['tags'] == ['important']

    # a session now carries builtin + custom together (M2M)
    j = client.get('/api/sessions?workflow=show').get_json()
    row = next(x for x in j['items'] if x['trace_id'] == 's1')
    assert [t['slug'] for t in row['tags']] == ['user', 'important']

    # filter by the custom tag finds it; counts + list include it
    j = client.get('/api/sessions?workflow=show&tag=important').get_json()
    assert {x['trace_id'] for x in j['items']} == {'s1'}
    assert j['tag_counts'].get('important') == 1
    assert client.get('/api/session-tags').get_json()['tags'] == [
        {'slug': 'important', 'count': 1}]

    # idempotent re-add
    r = client.post('/api/sessions/s1/tags', json={'tag': 'important'})
    assert r.get_json()['tags'] == ['important']

    # remove
    r = client.delete('/api/sessions/s1/tags/important')
    assert r.status_code == 200 and r.get_json()['tags'] == []
    assert client.get('/api/session-tags').get_json()['tags'] == []


def test_builtin_slug_cannot_be_assigned_or_removed(client):
    _seed(client._db_path, 's2', origin='session')
    assert client.post('/api/sessions/s2/tags',
                       json={'tag': 'system'}).status_code == 400
    assert client.post('/api/sessions/s2/tags',
                       json={'tag': 'bad slug'}).status_code == 400
    assert client.delete('/api/sessions/s2/tags/system').status_code == 400


def test_deleting_session_clears_its_custom_tags(client):
    _seed(client._db_path, 's3', origin='session')
    client.post('/api/sessions/s3/tags', json={'tag': 'keep'})
    assert client.get('/api/session-tags').get_json()['tags']
    client.delete('/api/sessions/s3')
    assert client.get('/api/session-tags').get_json()['tags'] == []
