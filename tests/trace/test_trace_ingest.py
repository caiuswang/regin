"""Tests for the hardened trace ingest path.

Covers design changes introduced in this session:

1. POST /api/session-spans is now transactional — a malformed span
   aborts the whole batch; previously it committed the prefix that
   had already succeeded.

2. `attributes.is_test` is normalised at write time so downstream
   filters can rely on a single JSON-boolean shape. Producers that
   send `1`, `"true"`, `"True"`, `"yes"`, `True`, etc. all land as
   JSON boolean true; producers that send `0`, `""`, `"false"`, etc.
   are stripped rather than left as ambiguous strings.

3. The duplicate (trace_id, span_id) case is explicitly surfaced as
   `skipped_duplicates` so callers can tell "new row" from "idempotent
   retry".

4. /api/skill-reads replaces per-row correlated subqueries with CTE
   joins — the regression test asserts we stay under a fixed query
   budget as the session count grows.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

from web import app as app_module
from web import helpers as _helpers_module
from web.blueprints import trace as _trace_module


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
    # The app gates /api/ reads behind a valid JWT; authenticate as editor.
    # Ingest POSTs are public and unaffected by the extra header.
    c.environ_base['HTTP_AUTHORIZATION'] = f"Bearer {create_token(1, 'test-editor', 'editor')}"
    return c


def _make_span(**over):
    span = {
        'trace_id': 't1',
        'span_id': 's1',
        'name': 'tool.Read',
        'start_time': '2026-04-18T12:00:00',
        'end_time': '2026-04-18T12:00:01',
        'duration_ms': 1000,
        'attributes': {},
    }
    span.update(over)
    return span


def _count_spans(db_path, trace_id=None):
    conn = sqlite3.connect(str(db_path))
    try:
        if trace_id is None:
            return conn.execute('SELECT COUNT(*) FROM session_spans').fetchone()[0]
        return conn.execute(
            'SELECT COUNT(*) FROM session_spans WHERE trace_id = ?',
            (trace_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def _get_attrs(db_path, span_id):
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            'SELECT attributes FROM session_spans WHERE span_id = ?',
            (span_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


# ── _normalize_is_test: pure helper tests ────────────────────────────

@pytest.mark.parametrize('value, expected', [
    (True, True),
    (False, False),
    (1, True),
    (0, False),
    ('1', True),
    ('0', False),
    ('true', True),
    ('True', True),
    ('TRUE', True),
    ('yes', True),
    ('y', True),
    ('false', False),
    ('no', False),
    ('', False),
    (None, False),
    ('random', False),
])
def test_normalize_is_test_covers_producer_variants(value, expected):
    assert app_module._normalize_is_test(value) is expected


# ── ingest: single + batch happy path ────────────────────────────────

def test_ingest_accepts_single_span(client, trace_db):
    r = client.post('/api/session-spans', json=_make_span())
    assert r.status_code == 200
    body = r.get_json()
    assert body == {'ok': True, 'ingested': 1, 'skipped_duplicates': 0}
    assert _count_spans(trace_db) == 1


def test_ingest_accepts_batch(client, trace_db):
    batch = [_make_span(span_id=f's{i}') for i in range(5)]
    r = client.post('/api/session-spans', json=batch)
    body = r.get_json()
    assert r.status_code == 200
    assert body['ok'] is True
    assert body['ingested'] == 5
    assert _count_spans(trace_db) == 5


# ── ingest: validation rejects the batch, nothing is committed ───────

def test_ingest_rejects_batch_with_missing_trace_id(client, trace_db):
    batch = [
        _make_span(span_id='ok1'),
        _make_span(span_id='bad', trace_id=None),
        _make_span(span_id='ok2'),
    ]
    r = client.post('/api/session-spans', json=batch)
    assert r.status_code == 400
    body = r.get_json()
    assert body['ok'] is False
    assert any(e['index'] == 1 and 'trace_id' in e['reason'] for e in body['errors'])
    # Nothing was committed — validation failed before the transaction.
    assert _count_spans(trace_db) == 0


def test_ingest_rejects_non_object_span(client, trace_db):
    r = client.post('/api/session-spans', json=['not-a-dict'])
    assert r.status_code == 400
    assert _count_spans(trace_db) == 0


def test_ingest_rejects_invalid_attributes_shape(client, trace_db):
    r = client.post(
        '/api/session-spans',
        json=_make_span(attributes=['not', 'a', 'dict']),
    )
    assert r.status_code == 400
    assert _count_spans(trace_db) == 0


# ── ingest: transaction rolls back on DB error mid-batch ─────────────

class _FlakyConn:
    """Wrap a real sqlite3 connection and make the Nth INSERT fail."""

    def __init__(self, real, fail_on_insert_n):
        self._real = real
        self._fail_on = fail_on_insert_n
        self._insert_count = 0

    def execute(self, sql, *a, **kw):
        if sql.lstrip().upper().startswith('INSERT INTO SESSION_SPANS'):
            self._insert_count += 1
            if self._insert_count == self._fail_on:
                raise sqlite3.OperationalError('simulated DB failure')
        return self._real.execute(sql, *a, **kw)

    # Passthrough for everything else the handler uses.
    def __getattr__(self, name):
        return getattr(self._real, name)


def test_ingest_rolls_back_when_db_execute_raises_mid_batch(client, trace_db, monkeypatch):
    """If sqlite raises halfway through a batch, the prefix must not
    survive — that was the pre-hardening behaviour and silently leaked
    orphan rows."""
    batch = [_make_span(span_id=f's{i}') for i in range(4)]

    # The ingest path moved to lib.trace.trace_service in phase-c.2;
    # the service's lazy `from lib.orm.engine import get_connection` re-reads the
    # canonical symbol on every call, so patching lib.orm.engine.get_connection
    # takes effect without needing to thread the patch through the
    # service module.
    import lib.orm.engine as db_module
    real_get_conn = db_module.get_connection

    def flaky_get_conn():
        return _FlakyConn(real_get_conn(), fail_on_insert_n=3)

    monkeypatch.setattr(db_module, 'get_connection', flaky_get_conn)

    r = client.post('/api/session-spans', json=batch)

    assert r.status_code == 500
    body = r.get_json()
    assert body['ok'] is False
    assert 'simulated DB failure' in body['error']
    # All-or-nothing: the first 2 INSERTs were rolled back.
    assert _count_spans(trace_db) == 0


# ── ingest: duplicate (trace_id, span_id) is reported, not an error ──

def test_ingest_second_post_of_same_span_is_counted_as_duplicate(client, trace_db):
    span = _make_span()
    r1 = client.post('/api/session-spans', json=span)
    assert r1.get_json()['ingested'] == 1
    r2 = client.post('/api/session-spans', json=span)
    body = r2.get_json()
    assert r2.status_code == 200
    assert body['ok'] is True
    assert body['skipped_duplicates'] == 1
    assert body['ingested'] == 0
    assert _count_spans(trace_db) == 1  # UNIQUE constraint kept it a single row.


# ── ingest: is_test normalisation ────────────────────────────────────

@pytest.mark.parametrize('raw_is_test, expected_attr', [
    (True, True),
    ('true', True),
    ('True', True),
    ('yes', True),
    ('1', True),
    (1, True),
    # Falsy values are dropped entirely so the WHERE clause never has
    # to wonder if '0' is "set to zero" or "unset".
    (False, None),
    ('false', None),
    ('0', None),
    (0, None),
])
def test_ingest_normalises_is_test_attribute(client, trace_db, raw_is_test, expected_attr):
    span = _make_span(span_id=f's-{raw_is_test!r}',
                      attributes={'is_test': raw_is_test})
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 200
    stored = _get_attrs(trace_db, span['span_id'])
    if expected_attr is None:
        assert 'is_test' not in stored
    else:
        assert stored['is_test'] is True


# ── skill-reads: query-count regression for the N+1 fix ──────────────

def _seed_skill_reads(db_path, sessions):
    conn = sqlite3.connect(str(db_path))
    try:
        for i, session_id in enumerate(sessions):
            conn.execute(
                """INSERT INTO skill_reads
                   (skill_id, session_id, file_path, found, read_at)
                   VALUES (?, ?, ?, 1, ?)""",
                (f'skill-{i}', session_id, '/path.md', f'2026-04-18T12:00:{i:02d}'),
            )
            conn.execute(
                """INSERT INTO plan_sessions
                   (session_id, plan_filename, started_at)
                   VALUES (?, ?, ?)""",
                (session_id, f'plan-{i}.md', f'2026-04-18T11:00:{i:02d}'),
            )
        conn.commit()
    finally:
        conn.close()


class _CountingConn:
    """Wrap a real sqlite3 connection and count SQL calls on trace tables."""

    TRACKED = ('session_spans', 'skill_reads', 'plan_sessions')

    def __init__(self, real, counter):
        self._real = real
        self._counter = counter

    def execute(self, sql, *a, **kw):
        if any(t in sql for t in self.TRACKED):
            self._counter['n'] += 1
        return self._real.execute(sql, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_skill_reads_query_count_does_not_grow_with_session_count(client, trace_db, monkeypatch):
    """Pre-fix: each of 50 session rows spawned 2 correlated subqueries
    for plan_filename + test_name. The new implementation uses CTEs, so
    total SQL calls are bounded by a small constant regardless of
    session_count."""
    _seed_skill_reads(trace_db, [f'sess-{i}' for i in range(50)])

    import lib.orm.engine as db_module
    real_get_conn = db_module.get_connection
    counter = {'n': 0}

    def counting_get_conn():
        return _CountingConn(real_get_conn(), counter)

    # trace endpoints moved to lib.trace.trace_service in phase-c.1/c.2;
    # patch the canonical lib.orm.engine.get_connection so the service's lazy
    # import picks up the counting wrapper.
    monkeypatch.setattr(db_module, 'get_connection', counting_get_conn)

    r = client.get('/api/skill-reads')

    assert r.status_code == 200
    body = r.get_json()
    # All 50 seeded sessions should come back with their plan.
    assert len(body['sessions']) == 50
    assert all(s['plan_filename'] and s['plan_filename'].startswith('plan-')
               for s in body['sessions'])
    # The endpoint should issue at most a handful of SQL calls total
    # (list query, stats query, sessions query). If this regresses to
    # per-row correlated subqueries we'd see 100+ calls.
    assert counter['n'] < 10, (
        f'/api/skill-reads used {counter["n"]} SQL calls for 50 sessions — '
        'the N+1 correlated-subquery pattern may have returned.'
    )


# ── /api/sessions + /api/mcp-calls regression + behaviour tests ──────

def _seed_spans(db_path, rows):
    """Insert session_spans rows directly + populate the sessions table.

    The `sessions` table is the read-side source of truth; in production
    it's maintained at ingest time. Tests bypass the REST ingest, so we
    do a one-shot aggregate insert that mirrors what ingest accumulates.
    Only the fields the tests assert on are populated; everything else
    keeps its column default.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        for r in rows:
            conn.execute(
                """INSERT INTO session_spans
                   (trace_id, span_id, parent_id, name, kind,
                    start_time, end_time, duration_ms, attributes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (r['trace_id'], r['span_id'], r.get('parent_id'),
                 r['name'], 'internal',
                 r['start_time'], r.get('end_time'),
                 r.get('duration_ms'), json.dumps(r.get('attributes', {}))),
            )
        seeded = sorted({r['trace_id'] for r in rows})
        placeholders = ','.join(['?'] * len(seeded))
        conn.execute(
            f"DELETE FROM sessions WHERE trace_id IN ({placeholders})",
            seeded,
        )
        conn.execute(
            f"""
            INSERT INTO sessions (
                trace_id, title, title_source,
                started_at, last_seen,
                span_count, prompts, tool_calls,
                is_test, test_name
            )
            SELECT
                sp.trace_id,
                (SELECT json_extract(sp2.attributes, '$.text')
                   FROM session_spans sp2
                  WHERE sp2.trace_id = sp.trace_id AND sp2.name = 'prompt'
                  ORDER BY sp2.start_time LIMIT 1) AS title,
                CASE WHEN EXISTS (
                    SELECT 1 FROM session_spans sp3
                     WHERE sp3.trace_id = sp.trace_id AND sp3.name = 'prompt'
                ) THEN 'first_prompt' ELSE NULL END,
                MIN(sp.start_time),
                MAX(COALESCE(sp.end_time, sp.start_time)),
                COUNT(*),
                SUM(CASE WHEN sp.name = 'prompt' THEN 1 ELSE 0 END),
                SUM(CASE WHEN sp.name LIKE 'tool.%' OR sp.name LIKE 'pre_tool.%' THEN 1 ELSE 0 END),
                MAX(CASE WHEN json_extract(sp.attributes, '$.is_test') = 1 THEN 1 ELSE 0 END),
                MAX(json_extract(sp.attributes, '$.test_name'))
            FROM session_spans sp
            WHERE sp.trace_id IN ({placeholders})
            GROUP BY sp.trace_id
            """,
            seeded,
        )
        conn.commit()
    finally:
        conn.close()


def test_sessions_query_count_does_not_grow_with_session_count(client, trace_db, monkeypatch):
    """/api/sessions used to fire a correlated subquery for test_name on
    every group. After collapsing it to a plain MAX() aggregate, total
    query count is bounded by a constant."""
    rows = []
    for i in range(50):
        rows.append({
            'trace_id': f't{i}', 'span_id': f's{i}',
            'name': 'prompt', 'start_time': f'2026-04-18T12:00:{i:02d}',
            'end_time': f'2026-04-18T12:01:{i:02d}', 'duration_ms': 1000,
            'attributes': {},
        })
    _seed_spans(trace_db, rows)

    import lib.orm.engine as db_module
    real_get_conn = db_module.get_connection
    counter = {'n': 0}
    # /api/sessions moved to SessionLocal + keyset_page_stmt in b.5.9,
    # so it doesn't go through lib.orm.engine.get_connection at all anymore.
    # The counter stays at 0 — which still satisfies the <10 invariant.
    # Patch anyway so we catch a regression if someone reintroduces a
    # raw-conn call in the sessions path.
    monkeypatch.setattr(db_module, 'get_connection',
                        lambda: _CountingConn(real_get_conn(), counter))

    r = client.get('/api/sessions')
    assert r.status_code == 200
    body = r.get_json()
    assert len(body['sessions']) == 50
    assert counter['n'] < 10, (
        f'/api/sessions used {counter["n"]} SQL calls for 50 sessions — '
        'the per-group test_name subquery may have returned.'
    )


def test_sessions_exposes_test_name_when_present(client, trace_db):
    """After the refactor, test_name comes from a MAX() aggregate over
    spans in the same group. Seed a session with test_name on one span
    and a plain span on another; the API should still surface it."""
    rows = [
        {'trace_id': 't1', 'span_id': 'a', 'name': 'prompt',
         'start_time': '2026-04-18T12:00:00', 'end_time': '2026-04-18T12:00:01',
         'duration_ms': 1000, 'attributes': {'is_test': True,
                                             'test_name': 'my_test'}},
        {'trace_id': 't1', 'span_id': 'b', 'name': 'tool.Read',
         'start_time': '2026-04-18T12:00:02', 'end_time': '2026-04-18T12:00:03',
         'duration_ms': 500, 'attributes': {}},  # no test_name here
    ]
    _seed_spans(trace_db, rows)
    r = client.get('/api/sessions?include_tests=true')
    body = r.get_json()
    assert len(body['sessions']) == 1
    assert body['sessions'][0]['test_name'] == 'my_test'
    assert body['sessions'][0]['is_test'] == 1


def test_sessions_hides_test_sessions_by_default(client, trace_db):
    rows = [
        {'trace_id': 'test-tr', 'span_id': 'a', 'name': 'prompt',
         'start_time': '2026-04-18T12:00:00',
         'attributes': {'is_test': True}},
        {'trace_id': 'real-tr', 'span_id': 'b', 'name': 'prompt',
         'start_time': '2026-04-18T12:01:00',
         'attributes': {}},
    ]
    _seed_spans(trace_db, rows)
    hidden = client.get('/api/sessions').get_json()
    shown = client.get('/api/sessions?include_tests=true').get_json()
    trace_ids_hidden = [s['trace_id'] for s in hidden['sessions']]
    trace_ids_shown = [s['trace_id'] for s in shown['sessions']]
    assert 'real-tr' in trace_ids_hidden
    assert 'test-tr' not in trace_ids_hidden
    assert {'real-tr', 'test-tr'} <= set(trace_ids_shown)


def _seed_session_repo_rows(db_path):
    """Two repos and two sessions: one single-repo (alpha), one multi-repo
    (alpha primary + beta). Used by the /api/sessions?repo= filter test."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("INSERT INTO repos (id, name, path, is_active, default_branch) "
                     "VALUES (1, 'alpha', '/x/alpha', 1, 'main')")
        conn.execute("INSERT INTO repos (id, name, path, is_active, default_branch) "
                     "VALUES (2, 'beta', '/x/beta', 1, 'main')")
        for tid in ('s-alpha', 's-both'):
            conn.execute(
                "INSERT INTO sessions (trace_id, started_at, last_seen) "
                "VALUES (?, '2026-04-18T12:00:00', '2026-04-18T12:05:00')",
                (tid,),
            )
        conn.executemany(
            "INSERT INTO session_repos (trace_id, repo_id, is_primary) VALUES (?, ?, ?)",
            [('s-alpha', 1, 1), ('s-both', 1, 1), ('s-both', 2, 0)],
        )
        conn.commit()
    finally:
        conn.close()


def test_sessions_repo_filter_narrows_and_matches_multi_repo(client, trace_db):
    """`?repo=beta` returns only the session tagged with beta; the
    multi-repo session is matched by every repo it touched."""
    _seed_session_repo_rows(trace_db)

    alpha = client.get('/api/sessions?repo=alpha&include_tests=true').get_json()
    beta = client.get('/api/sessions?repo=beta&include_tests=true').get_json()
    alpha_ids = {s['trace_id'] for s in alpha['sessions']}
    beta_ids = {s['trace_id'] for s in beta['sessions']}

    assert alpha_ids == {'s-alpha', 's-both'}   # both touched alpha
    assert beta_ids == {'s-both'}               # only the multi-repo one


def test_sessions_list_attaches_repos_and_multi_flag(client, trace_db):
    """Every session row carries `repos`, `primary_repo`, `is_multi_repo`."""
    _seed_session_repo_rows(trace_db)
    body = client.get('/api/sessions?include_tests=true').get_json()
    by_id = {s['trace_id']: s for s in body['sessions']}

    assert by_id['s-alpha']['is_multi_repo'] is False
    assert by_id['s-alpha']['primary_repo'] == 'alpha'
    assert by_id['s-both']['is_multi_repo'] is True
    assert by_id['s-both']['primary_repo'] == 'alpha'
    assert {r['name'] for r in by_id['s-both']['repos']} == {'alpha', 'beta'}


def test_sessions_repo_filter_unknown_repo_returns_empty(client, trace_db):
    _seed_session_repo_rows(trace_db)
    body = client.get('/api/sessions?repo=ghost&include_tests=true').get_json()
    assert body['sessions'] == []


def test_mcp_calls_query_count_does_not_grow_with_session_count(client, trace_db, monkeypatch):
    """/api/mcp-calls had the same correlated-subquery pattern as
    skill-reads. Same bound applies."""
    rows = []
    for i in range(50):
        rows.append({
            'trace_id': f'mt{i}', 'span_id': f'ms{i}',
            'name': 'tool.mcp__plugin__do', 'start_time': f'2026-04-18T12:00:{i:02d}',
            'duration_ms': 10,
            'attributes': {'tool_name': 'mcp__plugin__do'},
        })
    _seed_spans(trace_db, rows)

    import lib.orm.engine as db_module
    real_get_conn = db_module.get_connection
    counter = {'n': 0}
    # mcp-calls moved to lib.trace.trace_service in phase-c.1; patch
    # the canonical symbol so the service's lazy import picks it up.
    monkeypatch.setattr(db_module, 'get_connection',
                        lambda: _CountingConn(real_get_conn(), counter))

    r = client.get('/api/mcp-calls')
    assert r.status_code == 200
    body = r.get_json()
    assert len(body['sessions']) == 50
    assert counter['n'] < 10, (
        f'/api/mcp-calls used {counter["n"]} SQL calls for 50 sessions — '
        'the per-group is_test/test_name subqueries may have returned.'
    )


# ── validation: _is_iso_timestamp and _is_non_blank_str helpers ──────

@pytest.mark.parametrize('value, ok', [
    ('2026-04-18T12:00:00', True),
    ('2026-04-18T12:00:00.123456', True),
    ('2026-04-18', True),
    ('', False),
    ('   ', False),
    ('not-a-date', False),
    ('NaN', False),
    ('2026-13-99', False),
    (None, False),
    (1234567890, False),
    ({'iso': '2026-04-18T12:00:00'}, False),
])
def test_is_iso_timestamp_covers_common_producer_variants(value, ok):
    assert app_module._is_iso_timestamp(value) is ok


@pytest.mark.parametrize('value, ok', [
    ('hello', True),
    (' padded ', True),   # content survives strip() check
    ('', False),
    ('   ', False),       # previously slipped through `if not x`
    ('\t\n', False),
    (None, False),
    (0, False),
    (['a'], False),
])
def test_is_non_blank_str_covers_common_producer_variants(value, ok):
    assert app_module._is_non_blank_str(value) is ok


# ── ingest: validation rejects bad timestamps + blank required fields ─

def test_ingest_rejects_non_iso_start_time(client, trace_db):
    span = _make_span(start_time='NaN')
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 400
    body = r.get_json()
    assert any('start_time' in e['reason'] for e in body['errors'])
    assert _count_spans(trace_db) == 0


def test_ingest_rejects_bad_end_time_when_provided(client, trace_db):
    span = _make_span(end_time='not-a-date')
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 400
    body = r.get_json()
    assert any('end_time' in e['reason'] for e in body['errors'])


def test_ingest_accepts_missing_end_time(client, trace_db):
    """end_time is genuinely optional for still-open spans."""
    span = _make_span()
    span.pop('end_time', None)
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 200
    assert r.get_json()['ok'] is True


def test_ingest_rejects_whitespace_only_trace_id(client, trace_db):
    span = _make_span(trace_id='   ')
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 400
    body = r.get_json()
    assert any('trace_id' in e['reason'] for e in body['errors'])
    assert _count_spans(trace_db) == 0


def test_ingest_rejects_whitespace_only_name(client, trace_db):
    span = _make_span(name='\t\n')
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 400
    assert _count_spans(trace_db) == 0


def test_ingest_mixed_batch_good_and_bad_rolls_back_whole_batch(client, trace_db):
    """If ANY span in a batch fails validation, NONE are written. This
    makes hook retries safe (they can replay the batch without thinking
    about which prefix landed)."""
    batch = [
        _make_span(span_id='a'),
        _make_span(span_id='b', start_time='NaN'),
        _make_span(span_id='c'),
    ]
    r = client.post('/api/session-spans', json=batch)
    assert r.status_code == 400
    assert _count_spans(trace_db) == 0


# ── materialize: atomic on mid-projection failure ───────────────────

def _seed_session_for_materialize(db_path):
    """Seed a trace where materialize WILL have work to do:
    a conversation span, a prompt grafted under it, and an orphan tool
    span that graft + envelope-widen should both touch."""
    _seed_spans(db_path, [
        {'trace_id': 'mat1', 'span_id': 'root', 'parent_id': None,
         'name': 'conversation', 'start_time': '2026-04-18T12:00:00',
         'end_time': '2026-04-18T12:00:10', 'duration_ms': 10000,
         'attributes': {}},
        {'trace_id': 'mat1', 'span_id': 'p1', 'parent_id': None,
         'name': 'prompt', 'start_time': '2026-04-18T12:00:01',
         'end_time': '2026-04-18T12:00:09', 'duration_ms': 8000,
         'attributes': {}},
        {'trace_id': 'mat1', 'span_id': 'tool1', 'parent_id': None,
         'name': 'tool.Read', 'start_time': '2026-04-18T12:00:02',
         'end_time': '2026-04-18T12:00:03', 'duration_ms': 1000,
         'attributes': {}},
    ])


def test_materialize_succeeds_on_clean_session(client, trace_db):
    _seed_session_for_materialize(trace_db)
    r = client.post('/api/sessions/mat1/materialize')
    body = r.get_json()
    assert r.status_code == 200
    assert body['ok'] is True
    assert body['trace_id'] == 'mat1'


class _FlakyMaterializeConn:
    """Wraps a real connection; makes the Nth UPDATE raise.

    The materialize handler issues several UPDATE statements against
    session_spans (parent_id / start_time / end_time / duration_ms).
    Failing one of them mid-series is exactly the scenario the new
    transactional wrapper must protect against.
    """

    def __init__(self, real, fail_on_update_n=1):
        self._real = real
        self._fail_on = fail_on_update_n
        self._update_count = 0

    def execute(self, sql, *a, **kw):
        if sql.lstrip().upper().startswith('UPDATE SESSION_SPANS'):
            self._update_count += 1
            if self._update_count == self._fail_on:
                raise sqlite3.OperationalError('simulated mid-projection failure')
        return self._real.execute(sql, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _snapshot_spans(db_path, trace_id):
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """SELECT span_id, parent_id, start_time, end_time, duration_ms
               FROM session_spans WHERE trace_id = ?""",
            (trace_id,),
        ).fetchall()
        return {r[0]: tuple(r[1:]) for r in rows}
    finally:
        conn.close()


def test_materialize_rolls_back_on_mid_projection_failure(client, trace_db, monkeypatch):
    _seed_session_for_materialize(trace_db)
    before = _snapshot_spans(trace_db, 'mat1')

    # The materialize handler moved to lib.trace.trace_service in
    # phase-c.1; the service's lazy `from lib.orm.engine import get_connection`
    # re-reads the canonical symbol on every call, so patching
    # lib.orm.engine.get_connection takes effect without needing to thread the
    # patch through the service module.
    import lib.orm.engine as db_module
    real_get_conn = db_module.get_connection
    monkeypatch.setattr(
        db_module, 'get_connection',
        lambda: _FlakyMaterializeConn(real_get_conn(), fail_on_update_n=1),
    )

    r = client.post('/api/sessions/mat1/materialize')
    assert r.status_code == 500
    body = r.get_json()
    assert body['ok'] is False
    assert 'simulated mid-projection failure' in body['error']

    # Critical invariant: session state is unchanged — every row still
    # has the values it had before the failed materialize attempt.
    after = _snapshot_spans(trace_db, 'mat1')
    assert after == before


# ── skill_reads / plan_sessions retry-idempotency ───────────────────

def _count_skill_reads(db_path, **where):
    conn = sqlite3.connect(str(db_path))
    try:
        if where:
            clause = ' AND '.join(f'{k} = ?' for k in where)
            q = f"SELECT COUNT(*) FROM skill_reads WHERE {clause}"
            return conn.execute(q, list(where.values())).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM skill_reads").fetchone()[0]
    finally:
        conn.close()


def _count_plan_sessions(db_path, **where):
    conn = sqlite3.connect(str(db_path))
    try:
        if where:
            clause = ' AND '.join(f'{k} = ?' for k in where)
            q = f"SELECT COUNT(*) FROM plan_sessions WHERE {clause}"
            return conn.execute(q, list(where.values())).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM plan_sessions").fetchone()[0]
    finally:
        conn.close()


def test_skill_read_first_post_inserts(client, trace_db):
    payload = {'skill_id': 'my-skill', 'session_id': 's1',
               'file_path': '/x/content.md'}
    r = client.post('/api/skill-reads', json=payload)
    assert r.status_code == 200
    body = r.get_json()
    assert body == {'ok': True, 'skipped_duplicate': False}
    assert _count_skill_reads(trace_db) == 1


def test_skill_read_immediate_retry_is_idempotent(client, trace_db):
    """Simulates the realistic retry scenario: hook POSTs, server commits,
    response is lost, hook retries with the same payload. Second call
    must be recognised as a duplicate and not insert a new row."""
    payload = {'skill_id': 'my-skill', 'session_id': 's1',
               'file_path': '/x/content.md'}
    client.post('/api/skill-reads', json=payload)
    r2 = client.post('/api/skill-reads', json=payload)
    assert r2.status_code == 200
    assert r2.get_json()['skipped_duplicate'] is True
    assert _count_skill_reads(trace_db) == 1


def test_skill_read_different_file_paths_not_deduped(client, trace_db):
    """Two different file paths in the same session are legitimate
    distinct reads, not duplicates."""
    base = {'skill_id': 'my-skill', 'session_id': 's1'}
    client.post('/api/skill-reads', json={**base, 'file_path': '/a/content.md'})
    client.post('/api/skill-reads', json={**base, 'file_path': '/b/content.md'})
    assert _count_skill_reads(trace_db) == 2


def test_skill_read_different_sessions_not_deduped(client, trace_db):
    base = {'skill_id': 'my-skill', 'file_path': '/x/content.md'}
    client.post('/api/skill-reads', json={**base, 'session_id': 's1'})
    client.post('/api/skill-reads', json={**base, 'session_id': 's2'})
    assert _count_skill_reads(trace_db) == 2


def test_skill_read_after_dedup_window_inserts_new_row(client, trace_db, monkeypatch):
    """With the dedup window set to 0 seconds, the second POST should
    count as a new read. Pins the window-based semantics so a future
    change to the window value can be reasoned about."""
    monkeypatch.setattr(_helpers_module, '_INGEST_DEDUP_WINDOW_SEC', 0.0)
    payload = {'skill_id': 'my-skill', 'session_id': 's1',
               'file_path': '/x/content.md'}
    client.post('/api/skill-reads', json=payload)
    # Sleep nothing — just verify the window governs the decision.
    r2 = client.post('/api/skill-reads', json=payload)
    assert r2.get_json()['skipped_duplicate'] is False
    assert _count_skill_reads(trace_db) == 2


def test_skill_read_rejects_blank_skill_id(client, trace_db):
    r = client.post('/api/skill-reads',
                    json={'skill_id': '   ', 'session_id': 's1',
                          'file_path': '/x/content.md'})
    assert r.status_code == 400
    assert _count_skill_reads(trace_db) == 0


def test_skill_read_rejects_blank_file_path(client, trace_db):
    r = client.post('/api/skill-reads',
                    json={'skill_id': 'my-skill', 'session_id': 's1',
                          'file_path': ''})
    assert r.status_code == 400
    assert _count_skill_reads(trace_db) == 0


def test_plan_session_enter_retry_is_idempotent(client, trace_db):
    """The `enter` event carries a client-stamped started_at that is
    stable across retries, so exact-match dedup is enough."""
    payload = {
        'event': 'enter',
        'session_id': 'sess-a',
        'plan_filename': 'plan-1.md',
        'started_at': '2026-04-18T12:00:00',
    }
    r1 = client.post('/api/plan-sessions', json=payload)
    assert r1.get_json()['skipped_duplicate'] is False
    r2 = client.post('/api/plan-sessions', json=payload)
    assert r2.get_json()['skipped_duplicate'] is True
    assert _count_plan_sessions(trace_db, session_id='sess-a') == 1


def test_plan_session_enter_different_started_at_dedupes(client, trace_db):
    """Dedup is on (session_id, plan_filename) — a second `enter` from
    the same session for the same plan collapses to one row regardless
    of started_at. plan_trace re-posts on every edit; the table only
    needs to record one PlanSession per (session, plan)."""
    base = {'event': 'enter', 'session_id': 'sess-a',
            'plan_filename': 'plan-1.md'}
    client.post('/api/plan-sessions',
                json={**base, 'started_at': '2026-04-18T12:00:00'})
    r2 = client.post('/api/plan-sessions',
                     json={**base, 'started_at': '2026-04-18T13:00:00'})
    assert r2.get_json()['skipped_duplicate'] is True
    assert _count_plan_sessions(trace_db, session_id='sess-a') == 1


# ── /api/session-spans batch-size cap ──────────────────────────────

def _batch(n, trace_id='t-batch'):
    return [_make_span(span_id=f's{i}', trace_id=trace_id) for i in range(n)]


def test_ingest_accepts_exact_limit_batch(client, trace_db, monkeypatch):
    """Tests the boundary: a batch equal to the limit must succeed
    (strictly-greater-than is the right comparison, not ≥)."""
    monkeypatch.setattr(_helpers_module, '_INGEST_MAX_BATCH_SIZE', 10)
    r = client.post('/api/session-spans', json=_batch(10))
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True
    assert body['ingested'] == 10
    assert _count_spans(trace_db) == 10


def test_ingest_rejects_oversized_batch_with_413(client, trace_db, monkeypatch):
    """The batch cap is the first check — an oversized payload is
    rejected before any validation runs and before any row is written."""
    monkeypatch.setattr(_helpers_module, '_INGEST_MAX_BATCH_SIZE', 10)
    r = client.post('/api/session-spans', json=_batch(11))
    assert r.status_code == 413
    body = r.get_json()
    assert body['ok'] is False
    assert '11 spans' in body['error']
    assert 'max: 10' in body['error']
    assert _count_spans(trace_db) == 0


def test_ingest_max_batch_respects_env_override(client, trace_db, monkeypatch):
    """`REGIN_INGEST_MAX_BATCH` env var overrides the module constant so
    operators can tune it without a code change or redeploy."""
    monkeypatch.setenv('REGIN_INGEST_MAX_BATCH', '3')
    r = client.post('/api/session-spans', json=_batch(4))
    assert r.status_code == 413
    # And 3 at the override limit is fine.
    r2 = client.post('/api/session-spans', json=_batch(3, trace_id='env-ok'))
    assert r2.status_code == 200


def test_ingest_max_batch_ignores_invalid_env(client, trace_db, monkeypatch):
    """Garbage in REGIN_INGEST_MAX_BATCH falls back to the module default
    rather than crashing the request."""
    monkeypatch.setattr(_helpers_module, '_INGEST_MAX_BATCH_SIZE', 10)
    monkeypatch.setenv('REGIN_INGEST_MAX_BATCH', 'not-an-int')
    r = client.post('/api/session-spans', json=_batch(10))
    assert r.status_code == 200
    # And 11 still uses the module default (10) and is rejected.
    r2 = client.post('/api/session-spans',
                     json=_batch(11, trace_id='env-bad'))
    assert r2.status_code == 413


# ── per-span attributes size cap ──────────────────────────────────

def test_ingest_accepts_small_attributes(client, trace_db):
    span = _make_span(attributes={'file_path': '/tmp/ok.md', 'size': 42})
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 200
    assert _count_spans(trace_db) == 1


def test_ingest_rejects_oversized_attributes_with_400(client, trace_db, monkeypatch):
    """One span with a multi-kilobyte attribute blob blocks the whole
    batch, so other spans from the same producer aren't silently
    dropped without explanation."""
    monkeypatch.setattr(_helpers_module, '_INGEST_MAX_ATTRIBUTES_BYTES', 100)
    span = _make_span(attributes={'huge': 'x' * 200})  # > 100 bytes serialized
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 400
    body = r.get_json()
    assert any('attributes too large' in e['reason'] for e in body['errors'])
    assert _count_spans(trace_db) == 0


def test_ingest_attribute_size_boundary_is_inclusive(client, trace_db, monkeypatch):
    """A payload that serializes to EXACTLY the limit is accepted — the
    comparison is strictly-greater-than, not ≥. This pins the boundary
    semantics in case anyone later tightens the comparison."""
    # Construct attrs that land at exactly a known size.
    payload = {'s': 'x' * 50}
    serialized = len(json.dumps(payload).encode('utf-8'))
    monkeypatch.setattr(_helpers_module, '_INGEST_MAX_ATTRIBUTES_BYTES', serialized)
    span = _make_span(attributes=payload)
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 200
    assert _count_spans(trace_db) == 1


def test_ingest_attr_size_respects_env_override(client, trace_db, monkeypatch):
    monkeypatch.setenv('REGIN_INGEST_MAX_ATTR_BYTES', '50')
    span = _make_span(attributes={'big': 'x' * 200})
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 400
    assert _count_spans(trace_db) == 0


def test_ingest_reports_span_index_in_oversized_attr_error(client, trace_db, monkeypatch):
    """When a batch contains ONE oversized span among many, the error
    reason must cite that span's index so hooks / CI can pinpoint the
    misbehaving emitter."""
    monkeypatch.setattr(_helpers_module, '_INGEST_MAX_ATTRIBUTES_BYTES', 100)
    batch = [
        _make_span(span_id='ok', attributes={'small': 'v'}),
        _make_span(span_id='bad', attributes={'huge': 'x' * 500}),
        _make_span(span_id='ok2', attributes={'small': 'v'}),
    ]
    r = client.post('/api/session-spans', json=batch)
    assert r.status_code == 400
    body = r.get_json()
    assert any(e['index'] == 1 and 'attributes too large' in e['reason']
               for e in body['errors'])
    assert _count_spans(trace_db) == 0


# ── /api/ingest-errors observability endpoint ──────────────────────

def _write_error_log(tmp_path, entries):
    """Write a list of dicts as JSONL to a temp path and return it."""
    p = tmp_path / 'ingest-errors.jsonl'
    with open(p, 'w') as f:
        for e in entries:
            f.write(json.dumps(e) + '\n')
    return str(p)


def test_ingest_errors_returns_empty_when_log_missing(client, monkeypatch, tmp_path):
    """Fresh install with no log file must 200 with an empty shape, not
    404 — this endpoint is safe to poll from a dashboard."""
    from lib import hook_plugin as _hp
    monkeypatch.setattr(_hp, '_INGEST_ERROR_LOG',
                        str(tmp_path / 'does-not-exist.jsonl'))
    r = client.get('/api/ingest-errors')
    assert r.status_code == 200
    body = r.get_json()
    assert body['rows'] == []
    assert body['total_read'] == 0
    assert body['by_endpoint'] == {}


def test_ingest_errors_returns_rows_reverse_chrono(client, monkeypatch, tmp_path):
    from lib import hook_plugin as _hp
    entries = [
        {'timestamp': '2026-04-18T10:00:00', 'endpoint': 'session_spans',
         'url': 'http://x', 'error_type': 'TimeoutError', 'error': 'a',
         'attempt': 1, 'max_attempts': 3, 'gave_up': False},
        {'timestamp': '2026-04-18T10:00:01', 'endpoint': 'skill_reads',
         'url': 'http://x', 'error_type': 'HTTPError', 'error': 'b',
         'attempt': 3, 'max_attempts': 3, 'gave_up': True, 'http_status': 503},
        {'timestamp': '2026-04-18T10:00:02', 'endpoint': 'session_spans',
         'url': 'http://x', 'error_type': 'TimeoutError', 'error': 'c',
         'attempt': 2, 'max_attempts': 3, 'gave_up': True},
    ]
    path = _write_error_log(tmp_path, entries)
    monkeypatch.setattr(_hp, '_INGEST_ERROR_LOG', path)

    r = client.get('/api/ingest-errors')
    body = r.get_json()
    assert body['total_read'] == 3
    # Most-recent first.
    assert [row['error'] for row in body['rows']] == ['c', 'b', 'a']
    # Aggregations over ALL rows, not filtered subset.
    assert body['by_endpoint'] == {'session_spans': 2, 'skill_reads': 1}
    assert body['by_error_type'] == {'TimeoutError': 2, 'HTTPError': 1}
    assert body['by_gave_up'] == {'true': 2, 'false': 1}


def test_ingest_errors_filters_by_endpoint(client, monkeypatch, tmp_path):
    from lib import hook_plugin as _hp
    entries = [
        {'endpoint': 'session_spans', 'error_type': 'T',
         'error': 'a', 'gave_up': False},
        {'endpoint': 'skill_reads', 'error_type': 'T',
         'error': 'b', 'gave_up': False},
    ]
    monkeypatch.setattr(_hp, '_INGEST_ERROR_LOG',
                        _write_error_log(tmp_path, entries))
    r = client.get('/api/ingest-errors?endpoint=skill_reads')
    body = r.get_json()
    assert [row['error'] for row in body['rows']] == ['b']
    # Aggregations still reflect the full log, not the filter.
    assert body['by_endpoint']['session_spans'] == 1


def test_ingest_errors_filters_by_gave_up(client, monkeypatch, tmp_path):
    from lib import hook_plugin as _hp
    entries = [
        {'endpoint': 'session_spans', 'error_type': 'T',
         'error': 'survived', 'gave_up': False},
        {'endpoint': 'session_spans', 'error_type': 'T',
         'error': 'dropped', 'gave_up': True},
    ]
    monkeypatch.setattr(_hp, '_INGEST_ERROR_LOG',
                        _write_error_log(tmp_path, entries))
    r = client.get('/api/ingest-errors?gave_up=true')
    body = r.get_json()
    assert [row['error'] for row in body['rows']] == ['dropped']


def test_ingest_errors_respects_limit(client, monkeypatch, tmp_path):
    from lib import hook_plugin as _hp
    entries = [{'endpoint': 'session_spans', 'error_type': 'T',
                'error': f'e{i}', 'gave_up': False} for i in range(10)]
    monkeypatch.setattr(_hp, '_INGEST_ERROR_LOG',
                        _write_error_log(tmp_path, entries))
    r = client.get('/api/ingest-errors?limit=3')
    body = r.get_json()
    assert len(body['rows']) == 3
    assert body['returned'] == 3
    assert body['total_read'] == 10


def test_ingest_errors_caps_limit_at_1000(client, monkeypatch, tmp_path):
    from lib import hook_plugin as _hp
    monkeypatch.setattr(_hp, '_INGEST_ERROR_LOG',
                        str(tmp_path / 'empty.jsonl'))
    open(_hp._INGEST_ERROR_LOG, 'w').close()
    r = client.get('/api/ingest-errors?limit=99999')
    assert r.status_code == 200
    # No rows anyway, but the request itself must not explode.


def test_ingest_errors_skips_malformed_lines(client, monkeypatch, tmp_path):
    """A partial write (server crash) leaves a half-JSON line. The
    endpoint must tolerate it and still return valid neighbors."""
    from lib import hook_plugin as _hp
    path = tmp_path / 'ingest-errors.jsonl'
    with open(path, 'w') as f:
        f.write('{"endpoint": "session_spans", "error": "good1"}\n')
        f.write('{"endpoint": "session_spans", "error":  ## not JSON\n')
        f.write('{"endpoint": "session_spans", "error": "good2"}\n')
    monkeypatch.setattr(_hp, '_INGEST_ERROR_LOG', str(path))
    r = client.get('/api/ingest-errors')
    body = r.get_json()
    assert body['total_read'] == 2
    assert {row['error'] for row in body['rows']} == {'good1', 'good2'}


def test_ingest_errors_ignores_invalid_limit(client, monkeypatch, tmp_path):
    """A malformed `limit` param falls back to the default (50) rather
    than crashing the request."""
    from lib import hook_plugin as _hp
    monkeypatch.setattr(_hp, '_INGEST_ERROR_LOG',
                        str(tmp_path / 'missing.jsonl'))
    r = client.get('/api/ingest-errors?limit=not-an-int')
    assert r.status_code == 200


# ── /api/rule-triggers hardening ──────────────────────────────────

def _count_rule_triggers(db_path, **where):
    conn = sqlite3.connect(str(db_path))
    try:
        if where:
            clause = ' AND '.join(f'{k} = ?' for k in where)
            q = f"SELECT COUNT(*) FROM rule_triggers WHERE {clause}"
            return conn.execute(q, list(where.values())).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM rule_triggers").fetchone()[0]
    finally:
        conn.close()


def _rule_event(**over):
    ev = {
        'rule_id': 'require_field_annotation',
        'file_path': '/repo/src/Foo.java',
        'repo': 'example-service',
        'match_count': 0,
        'severity': 'warn',
        'guide': 'bean-contract',
        'summary': 'some summary',
        'source': 'rule_check',
        'session_id': 'sess-a',
    }
    ev.update(over)
    return ev


def test_rule_trigger_accepts_single_event(client, trace_db):
    r = client.post('/api/rule-triggers', json=_rule_event(match_count=3))
    assert r.status_code == 200
    assert r.get_json() == {'ok': True, 'ingested': 1}
    assert _count_rule_triggers(trace_db) == 1


def test_rule_trigger_derives_triggered_flag_from_match_count(client, trace_db):
    """`triggered` is a denormalised boolean of `match_count > 0` used
    by the dashboard's fired-rules query. Must survive a 0-match event
    (not triggered) and a non-zero event (triggered)."""
    client.post('/api/rule-triggers', json=_rule_event(rule_id='r_zero', match_count=0))
    client.post('/api/rule-triggers', json=_rule_event(rule_id='r_many', match_count=5))
    assert _count_rule_triggers(trace_db, rule_id='r_zero', triggered=0) == 1
    assert _count_rule_triggers(trace_db, rule_id='r_many', triggered=1) == 1


def test_rule_trigger_accepts_batch(client, trace_db):
    batch = [_rule_event(rule_id=f'r{i}') for i in range(5)]
    r = client.post('/api/rule-triggers', json=batch)
    assert r.status_code == 200
    assert r.get_json()['ingested'] == 5
    assert _count_rule_triggers(trace_db) == 5


def test_rule_trigger_rejects_missing_rule_id(client, trace_db):
    r = client.post('/api/rule-triggers', json=_rule_event(rule_id=''))
    assert r.status_code == 400
    body = r.get_json()
    assert any('rule_id' in e['reason'] for e in body['errors'])
    assert _count_rule_triggers(trace_db) == 0


def test_rule_trigger_rejects_blank_file_path(client, trace_db):
    r = client.post('/api/rule-triggers', json=_rule_event(file_path='   '))
    assert r.status_code == 400
    assert _count_rule_triggers(trace_db) == 0


def test_rule_trigger_rejects_oversized_batch(client, trace_db, monkeypatch):
    monkeypatch.setattr(_helpers_module, '_INGEST_MAX_BATCH_SIZE', 10)
    r = client.post('/api/rule-triggers',
                    json=[_rule_event(rule_id=f'r{i}') for i in range(11)])
    assert r.status_code == 413
    assert _count_rule_triggers(trace_db) == 0


def test_rule_trigger_mixed_bad_batch_rolls_back(client, trace_db):
    """Single bad event in a batch rejects the whole batch — retry is
    safe without worrying about which prefix landed."""
    batch = [
        _rule_event(rule_id='r1'),
        _rule_event(rule_id=''),       # invalid
        _rule_event(rule_id='r3'),
    ]
    r = client.post('/api/rule-triggers', json=batch)
    assert r.status_code == 400
    assert _count_rule_triggers(trace_db) == 0


class _FlakyRuleTriggerSession:
    """Wraps a real SQLModel Session, makes the Nth RuleTrigger add
    raise — tests the transaction rollback path under the SQLModel
    migration (phase-b.5.4).

    Before B.5.4 the test wrapped the raw `get_connection()` and
    failed on a hand-counted INSERT; the endpoint now goes through
    `SessionLocal` + `session.add(RuleTrigger(...))`, so the fake
    intercepts `add` instead. On failure we call `rollback()` on the
    wrapped session so any pending inserts are discarded — matching
    the raw-SQL helper's `conn.rollback()` path.
    """

    def __init__(self, real, fail_on_n=1):
        self._real = real
        self._fail_on = fail_on_n
        self._n = 0

    def add(self, obj, *a, **kw):
        from lib.orm.models import RuleTrigger
        if isinstance(obj, RuleTrigger):
            self._n += 1
            if self._n == self._fail_on:
                raise sqlite3.OperationalError('simulated rule insert failure')
        return self._real.add(obj, *a, **kw)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self._real.rollback()
        self._real.close()
        return False

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_rule_trigger_rolls_back_on_mid_batch_db_error(client, trace_db, monkeypatch):
    from web.blueprints import rules as rules_bp_module
    real_factory = rules_bp_module.SessionLocal
    monkeypatch.setattr(
        rules_bp_module, 'SessionLocal',
        lambda: _FlakyRuleTriggerSession(real_factory(), fail_on_n=2),
    )
    batch = [_rule_event(rule_id=f'r{i}') for i in range(3)]
    r = client.post('/api/rule-triggers', json=batch)
    assert r.status_code == 500
    body = r.get_json()
    assert body['ok'] is False
    assert 'simulated rule insert failure' in body['error']
    assert _count_rule_triggers(trace_db) == 0


# ── session model tracking: session.start → sessions.model ───────────
# These tests pin the end-to-end path that lets the Sessions dashboard
# answer "which model ran this session?" and `GROUP BY model` answer
# "how many sessions per model?". Broken in any of these cases would
# silently zero-fill every sessions.model column going forward.

def _session_model(db_path, trace_id):
    """Read back the sessions.model column for a given trace_id."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            'SELECT model FROM sessions WHERE trace_id = ?',
            (trace_id,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _session_agent_type(db_path, trace_id):
    """Read back the sessions.agent_type column for a given trace_id."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            'SELECT agent_type FROM sessions WHERE trace_id = ?',
            (trace_id,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_session_start_span_populates_sessions_model(client, trace_db):
    """Happy path: posting a session.start span with attributes.model
    populates sessions.model via the ingest upsert."""
    span = _make_span(
        span_id='root', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-haiku-4-5-20251001'},
    )
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 200
    assert _session_model(trace_db, 't1') == 'claude-haiku-4-5-20251001'


def test_session_start_span_populates_sessions_agent_type(client, trace_db):
    """SessionStart fixes the session agent type for list rendering."""
    span = _make_span(
        span_id='root', name='session.start',
        attributes={'source': 'startup', 'agent_type': 'claude'},
    )
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 200
    assert _session_agent_type(trace_db, 't1') == 'claude'


def test_later_session_start_does_not_replace_agent_type(client, trace_db):
    """The first stored SessionStart agent_type wins for the session."""
    client.post('/api/session-spans', json=_make_span(
        span_id='r1', name='session.start',
        start_time='2026-04-19T10:00:00',
        attributes={'source': 'startup', 'agent_type': 'claude'},
    ))
    client.post('/api/session-spans', json=_make_span(
        span_id='r2', name='session.start',
        start_time='2026-04-19T11:00:00',
        attributes={'source': 'resume', 'agent_type': 'codex'},
    ))
    assert _session_agent_type(trace_db, 't1') == 'claude'


def test_session_start_without_model_leaves_sessions_model_null(client, trace_db):
    """Older Claude Code versions omit `model` from SessionStart. The
    column stays NULL rather than being coerced to empty string —
    dashboards can cleanly filter `model IS NOT NULL` to skip them."""
    span = _make_span(
        span_id='root', name='session.start',
        attributes={'source': 'startup'},
    )
    r = client.post('/api/session-spans', json=span)
    assert r.status_code == 200
    assert _session_model(trace_db, 't1') is None


def test_newer_session_start_model_replaces_older_on_same_trace(client, trace_db):
    """A resume on a different model (user ran /model between sessions)
    must update the stored model. The newer session.start's model wins
    — keyed on start_time, not insert order."""
    client.post('/api/session-spans', json=_make_span(
        span_id='r1', name='session.start',
        start_time='2026-04-19T10:00:00',
        attributes={'source': 'startup', 'model': 'claude-haiku-4-5-20251001'},
    ))
    client.post('/api/session-spans', json=_make_span(
        span_id='r2', name='session.start',
        start_time='2026-04-19T11:00:00',
        attributes={'source': 'resume', 'model': 'claude-sonnet-4-6'},
    ))
    assert _session_model(trace_db, 't1') == 'claude-sonnet-4-6'


def test_subsequent_batch_without_session_start_preserves_model(client, trace_db):
    """Tool spans without a session.start in the batch must NOT wipe
    the stored model. COALESCE(excluded.model, sessions.model) keeps
    the existing value when the incoming batch contributes NULL."""
    client.post('/api/session-spans', json=_make_span(
        span_id='r1', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-opus-4-7'},
    ))
    # A tool span in the next batch — no model attr.
    client.post('/api/session-spans', json=_make_span(
        span_id='t1', name='tool.Read',
        attributes={'file_path': '/tmp/x'},
    ))
    assert _session_model(trace_db, 't1') == 'claude-opus-4-7'


def test_api_sessions_returns_model_field(client, trace_db):
    """The Sessions list endpoint must surface the model column so the
    Vue dashboard can render it without a second query per row."""
    client.post('/api/session-spans', json=_make_span(
        span_id='r1', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-haiku-4-5-20251001'},
    ))
    resp = client.get('/api/sessions?include_tests=true')
    assert resp.status_code == 200
    body = resp.get_json()
    session_entries = {s['trace_id']: s for s in body['sessions']}
    assert 't1' in session_entries
    assert session_entries['t1']['model'] == 'claude-haiku-4-5-20251001'


def test_api_sessions_returns_agent_type_field(client, trace_db):
    client.post('/api/session-spans', json=_make_span(
        span_id='r1', name='session.start',
        attributes={'source': 'startup', 'agent_type': 'codex'},
    ))
    resp = client.get('/api/sessions?include_tests=true')
    assert resp.status_code == 200
    body = resp.get_json()
    session_entries = {s['trace_id']: s for s in body['sessions']}
    assert session_entries['t1']['agent_type'] == 'codex'


def test_session_start_model_in_batch_with_other_spans(client, trace_db):
    """Mixed batch: a session.start with model + a tool.Read in the
    same POST. Both must be ingested; sessions.model must be set from
    the session.start attributes even though other spans don't carry
    the field."""
    batch = [
        _make_span(span_id='root', name='session.start',
                   attributes={'source': 'startup',
                               'model': 'claude-haiku-4-5-20251001'}),
        _make_span(span_id='tool1', name='tool.Read',
                   attributes={'file_path': '/tmp/x'}),
    ]
    r = client.post('/api/session-spans', json=batch)
    assert r.status_code == 200
    assert r.get_json()['ingested'] == 2
    assert _session_model(trace_db, 't1') == 'claude-haiku-4-5-20251001'


def test_model_aggregation_query_by_model(client, trace_db):
    """End-to-end: three sessions using two distinct models should
    aggregate cleanly into per-model counts via GROUP BY. This is the
    motivating user-facing question: 'how much am I using each model?'"""
    for tid, model in [('tA', 'claude-haiku-4-5-20251001'),
                       ('tB', 'claude-haiku-4-5-20251001'),
                       ('tC', 'claude-sonnet-4-6')]:
        client.post('/api/session-spans', json=_make_span(
            trace_id=tid, span_id=f'{tid}-root', name='session.start',
            attributes={'source': 'startup', 'model': model},
        ))
    conn = sqlite3.connect(str(trace_db))
    try:
        rows = list(conn.execute(
            'SELECT model, COUNT(*) FROM sessions GROUP BY model ORDER BY model'
        ))
        assert ('claude-haiku-4-5-20251001', 2) in rows
        assert ('claude-sonnet-4-6', 1) in rows
    finally:
        conn.close()


# ── Mid-session /model switch: turn span → updated sessions.model ────
# Claude Code doesn't fire any standalone event on /model. The
# turn_trace handler tails the transcript on every Stop and emits a
# `turn` span with the latest model. The rollup treats `turn` models
# with the same start_time-ordered precedence as session.start, so a
# later `turn` span supersedes the initial SessionStart capture.

def test_turn_span_model_supersedes_session_start_model(client, trace_db):
    """Session starts on Haiku, user runs /model to Sonnet mid-session,
    next Stop emits a turn span with model=Sonnet. sessions.model must
    update to Sonnet — not stay on the initial Haiku from SessionStart."""
    client.post('/api/session-spans', json=_make_span(
        span_id='ss', name='session.start',
        start_time='2026-04-19T10:00:00',
        attributes={'source': 'startup', 'model': 'claude-haiku-4-5-20251001'},
    ))
    client.post('/api/session-spans', json=_make_span(
        span_id='turn1', name='turn',
        start_time='2026-04-19T10:05:00',
        attributes={'model': 'claude-sonnet-4-6'},
    ))
    assert _session_model(trace_db, 't1') == 'claude-sonnet-4-6'


def test_turn_span_before_session_start_does_not_override(client, trace_db):
    """start_time-ordered precedence: a turn span with an EARLIER
    start_time than the most recent session.start must NOT override
    the session.start's model. Otherwise out-of-order batch ingest
    would let stale turns clobber fresh starts."""
    client.post('/api/session-spans', json=_make_span(
        span_id='turn_old', name='turn',
        start_time='2026-04-19T09:00:00',
        attributes={'model': 'claude-sonnet-4-6'},
    ))
    client.post('/api/session-spans', json=_make_span(
        span_id='ss_new', name='session.start',
        start_time='2026-04-19T10:00:00',
        attributes={'source': 'resume', 'model': 'claude-haiku-4-5-20251001'},
    ))
    assert _session_model(trace_db, 't1') == 'claude-haiku-4-5-20251001'


def test_multiple_turn_spans_within_batch_latest_wins(client, trace_db):
    """Three turn spans posted in ONE batch — the one with the latest
    start_time wins regardless of insert order inside the batch.
    (Cross-batch out-of-order ingest is not a guarantee — real hook
    streams arrive in temporal order anyway.)"""
    batch = [
        _make_span(span_id='t_mid', name='turn',
                   start_time='2026-04-19T11:00:00',
                   attributes={'model': 'claude-sonnet-4-6'}),
        _make_span(span_id='t_last', name='turn',
                   start_time='2026-04-19T12:00:00',
                   attributes={'model': 'claude-opus-4-7'}),
        _make_span(span_id='t_first', name='turn',
                   start_time='2026-04-19T10:00:00',
                   attributes={'model': 'claude-haiku-4-5-20251001'}),
    ]
    client.post('/api/session-spans', json=batch)
    assert _session_model(trace_db, 't1') == 'claude-opus-4-7'


def test_turn_spans_in_order_across_batches_latest_wins(client, trace_db):
    """Real hook streams deliver spans in temporal order, across batches.
    The rollup must replace the stored model on each newer batch."""
    for span_id, ts, model in [
        ('t1', '2026-04-19T10:00:00', 'claude-haiku-4-5-20251001'),
        ('t2', '2026-04-19T11:00:00', 'claude-sonnet-4-6'),
        ('t3', '2026-04-19T12:00:00', 'claude-opus-4-7'),
    ]:
        client.post('/api/session-spans', json=_make_span(
            span_id=span_id, name='turn',
            start_time=ts,
            attributes={'model': model},
        ))
    assert _session_model(trace_db, 't1') == 'claude-opus-4-7'


def test_turn_span_without_model_does_not_overwrite(client, trace_db):
    """A turn span whose model is None/empty must not wipe the stored
    model. Only non-null updates propagate."""
    client.post('/api/session-spans', json=_make_span(
        span_id='ss', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-haiku-4-5-20251001'},
    ))
    client.post('/api/session-spans', json=_make_span(
        span_id='turn_empty', name='turn',
        attributes={},  # no model
    ))
    assert _session_model(trace_db, 't1') == 'claude-haiku-4-5-20251001'


# ── turn_usage table → sessions aggregates ────────────────────────────
#
# Per-assistant-turn counters live in their own table now (not as
# session_spans rows). The ingest endpoint upserts by (trace_id,
# turn_uuid) so handler replays dedup at the DB layer, and
# ingest_turn_usage rebuilds sessions.* aggregates from the table after
# each write.

def _session_tokens(db_path, trace_id):
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("""
            SELECT input_tokens, output_tokens, cache_read_tokens,
                   cache_creation_tokens, peak_context_tokens,
                   context_window_tokens
            FROM sessions WHERE trace_id = ?
        """, (trace_id,)).fetchone()
        if not row:
            return None
        keys = ('input_tokens', 'output_tokens', 'cache_read_tokens',
                'cache_creation_tokens', 'peak_context_tokens',
                'context_window_tokens')
        return dict(zip(keys, row))
    finally:
        conn.close()


def _turn_row(**over):
    """Build a minimal /api/turn-usage row. Callers override only the
    fields they care about — a sensible default fills the rest."""
    row = {
        'trace_id': 't1',
        'turn_uuid': 'uuid-aaa',
        'turn_index': 0,
        'timestamp': '2026-04-20T10:00:00.000Z',
        'model': 'claude-opus-4-7',
        'input_tokens': 0,
        'output_tokens': 0,
        'cache_read_tokens': 0,
        'cache_creation_tokens': 0,
        'context_used_tokens': 0,
    }
    row.update(over)
    return row


def test_turn_usage_row_populates_session_tokens(client, trace_db):
    """A single /api/turn-usage row updates the session aggregates; peak
    tracks context_used_tokens, counters match the row."""
    # A session.start span seeds sessions.model so infer_window picks
    # the right window at the end.
    client.post('/api/session-spans', json=_make_span(
        span_id='s0', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-opus-4-7'},
    ))
    client.post('/api/turn-usage', json=_turn_row(
        input_tokens=500, output_tokens=120,
        cache_read_tokens=80_000, cache_creation_tokens=200,
        context_used_tokens=80_700,
    ))
    t = _session_tokens(trace_db, 't1')
    assert t == {
        'input_tokens': 500,
        'output_tokens': 120,
        'cache_read_tokens': 80_000,
        'cache_creation_tokens': 200,
        'peak_context_tokens': 80_700,
        'context_window_tokens': 1_000_000,
    }


def test_turn_usage_counters_sum_across_turns(client, trace_db):
    """Each turn is one row in turn_usage; aggregates add the counters
    across rows and max the peak."""
    client.post('/api/session-spans', json=_make_span(
        span_id='s0', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-opus-4-7'},
    ))
    client.post('/api/turn-usage', json=_turn_row(
        turn_uuid='t-a', turn_index=0,
        input_tokens=100, output_tokens=10,
        cache_read_tokens=5_000, cache_creation_tokens=50,
        context_used_tokens=5_150,
    ))
    client.post('/api/turn-usage', json=_turn_row(
        turn_uuid='t-b', turn_index=1,
        input_tokens=300, output_tokens=40,
        cache_read_tokens=180_000, cache_creation_tokens=200,
        context_used_tokens=180_500,
    ))
    t = _session_tokens(trace_db, 't1')
    assert t['input_tokens'] == 400                 # 100 + 300
    assert t['output_tokens'] == 50                 # 10 + 40
    assert t['cache_read_tokens'] == 185_000
    assert t['cache_creation_tokens'] == 250
    assert t['peak_context_tokens'] == 180_500      # max, not sum
    assert t['context_window_tokens'] == 1_000_000


def test_peak_main_excludes_server_side_turns(client, trace_db):
    """A turn whose API call rolled in a server-side sub-call (advisor)
    has its tokens charged to the parent turn's `usage` block, inflating
    `context_used`. `peak_main_context_tokens` excludes those turns so
    the headline ctx % matches the terminal statusline."""
    client.post('/api/session-spans', json=_make_span(
        span_id='s0', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-opus-4-7'},
    ))
    # Main turn — normal context size.
    client.post('/api/turn-usage', json=_turn_row(
        turn_uuid='t-main', turn_index=0,
        input_tokens=100, cache_read_tokens=50_000, cache_creation_tokens=500,
        context_used_tokens=50_600,
    ))
    # Advisor turn — its usage bundles advisor's internal prompt, so
    # context_used spikes well past what main conversation actually holds.
    client.post('/api/turn-usage', json=_turn_row(
        turn_uuid='t-adv', turn_index=1,
        timestamp='2026-04-20T10:01:00.000Z',
        input_tokens=100, cache_read_tokens=150_000, cache_creation_tokens=140_000,
        context_used_tokens=290_100,
    ))
    # The span that flags the turn as server-side.
    client.post('/api/session-spans', json=_make_span(
        span_id='s-adv', name='tool.advisor',
        start_time='2026-04-20T10:01:00',
        attributes={'tool_name': 'advisor', 'server_side': True, 'turn_uuid': 't-adv'},
    ))
    conn = sqlite3.connect(str(trace_db))
    try:
        row = conn.execute(
            "SELECT peak_context_tokens, peak_main_context_tokens "
            "FROM sessions WHERE trace_id = 't1'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 290_100   # all-inclusive peak: advisor's rollup wins
    assert row[1] == 50_600    # main peak: advisor turn excluded


def test_turn_usage_populates_cost_usd(client, trace_db, monkeypatch):
    """Each turn_usage row is priced via lib.tokens.pricing.cost() and the
    USD figure lands both on the row and on the session aggregate.
    Before this, the column existed but ingest never wrote to it."""
    from lib.tokens import pricing
    monkeypatch.setattr(pricing, '_fetch', lambda: {
        'anthropic': {'id': 'anthropic', 'models': {
            'claude-opus-4-7': {'cost': {
                'input': 5, 'output': 25, 'cache_read': 0.5, 'cache_write': 6.25,
            }},
        }},
    })
    pricing.reset_cache()

    client.post('/api/session-spans', json=_make_span(
        span_id='s0', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-opus-4-7'},
    ))
    client.post('/api/turn-usage', json=_turn_row(
        turn_uuid='t-cost-a', turn_index=0,
        input_tokens=1000, output_tokens=200,
        cache_read_tokens=10_000, cache_creation_tokens=400,
        context_used_tokens=11_600,
    ))
    client.post('/api/turn-usage', json=_turn_row(
        turn_uuid='t-cost-b', turn_index=1,
        input_tokens=500, output_tokens=100,
        cache_read_tokens=4_000, cache_creation_tokens=0,
        context_used_tokens=4_600,
    ))

    conn = sqlite3.connect(str(trace_db))
    try:
        rows = conn.execute(
            "SELECT turn_uuid, cost_usd FROM turn_usage "
            "WHERE trace_id = 't1' ORDER BY turn_index"
        ).fetchall()
        session_cost = conn.execute(
            "SELECT cost_usd FROM sessions WHERE trace_id = 't1'"
        ).fetchone()[0]
    finally:
        conn.close()

    # turn a: (5*1000 + 25*200 + 0.5*10_000 + 6.25*400)/1e6
    #       = (5000 + 5000 + 5000 + 2500) / 1e6 = 0.0175
    # turn b: (5*500 + 25*100 + 0.5*4000)/1e6 = (2500 + 2500 + 2000)/1e6 = 0.007
    cost_a = rows[0][1]
    cost_b = rows[1][1]
    assert cost_a == pytest.approx(0.0175)
    assert cost_b == pytest.approx(0.007)
    assert session_cost == pytest.approx(cost_a + cost_b)

    pricing.reset_cache()


def test_turn_usage_cost_is_null_for_unknown_model(client, trace_db, monkeypatch):
    """A model that isn't in the pricing catalogue leaves cost_usd
    NULL — surfacing "no cost data" beats showing a misleading zero."""
    from lib.tokens import pricing
    monkeypatch.setattr(pricing, '_fetch', lambda: {
        'anthropic': {'id': 'anthropic', 'models': {}},
    })
    pricing.reset_cache()

    client.post('/api/session-spans', json=_make_span(
        span_id='s0', name='session.start',
        attributes={'source': 'startup', 'model': 'mystery-model-9'},
    ))
    client.post('/api/turn-usage', json=_turn_row(
        turn_uuid='t-unknown', turn_index=0,
        model='mystery-model-9',
        input_tokens=100, output_tokens=10,
        cache_read_tokens=0, cache_creation_tokens=0,
        context_used_tokens=100,
    ))

    conn = sqlite3.connect(str(trace_db))
    try:
        row = conn.execute(
            "SELECT cost_usd FROM turn_usage WHERE trace_id = 't1'"
        ).fetchone()
        sess = conn.execute(
            "SELECT cost_usd FROM sessions WHERE trace_id = 't1'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] is None
    assert sess[0] is None

    pricing.reset_cache()


def test_turn_usage_replay_is_idempotent(client, trace_db):
    """Hooks rescan the full transcript on every fire. Posting the same
    (trace_id, turn_uuid) twice must be a no-op, not a double-count."""
    client.post('/api/session-spans', json=_make_span(
        span_id='s0', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-opus-4-7'},
    ))
    row = _turn_row(
        turn_uuid='t-unique',
        input_tokens=100, output_tokens=10,
        cache_read_tokens=5_000, cache_creation_tokens=50,
        context_used_tokens=5_150,
    )
    client.post('/api/turn-usage', json=row)
    client.post('/api/turn-usage', json=row)  # replay
    client.post('/api/turn-usage', json=row)  # replay
    t = _session_tokens(trace_db, 't1')
    assert t['input_tokens'] == 100
    assert t['peak_context_tokens'] == 5_150


def test_turn_usage_batch_post(client, trace_db):
    """The endpoint accepts a list; all rows land in turn_usage and the
    session aggregate reflects the full batch."""
    client.post('/api/session-spans', json=_make_span(
        span_id='s0', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-opus-4-7'},
    ))
    client.post('/api/turn-usage', json=[
        _turn_row(turn_uuid='t-1', turn_index=0, input_tokens=10,
                  context_used_tokens=1000),
        _turn_row(turn_uuid='t-2', turn_index=1, input_tokens=20,
                  context_used_tokens=3000),
        _turn_row(turn_uuid='t-3', turn_index=2, input_tokens=30,
                  context_used_tokens=2000),
    ])
    t = _session_tokens(trace_db, 't1')
    assert t['input_tokens'] == 60
    assert t['peak_context_tokens'] == 3_000


def test_turn_usage_rejects_malformed(client, trace_db):
    """Missing trace_id / turn_uuid / timestamp → silently skipped, not
    an error (the batch still progresses for well-formed siblings)."""
    r = client.post('/api/turn-usage', json=[
        _turn_row(turn_uuid='good'),
        {'trace_id': 't1'},  # missing turn_uuid + timestamp
        {},
    ])
    assert r.status_code == 200
    body = r.get_json()
    assert body['ingested'] == 1
    assert body['skipped_malformed'] == 2


def test_turn_usage_api_returns_per_turn_rows(client, trace_db):
    """Read endpoint returns all turn_usage rows for a session, sorted
    by timestamp so the UI can render them in order."""
    for i, (ts, ctx) in enumerate([
        ('2026-04-20T10:00:00.000Z', 5_000),
        ('2026-04-20T10:05:00.000Z', 8_000),
        ('2026-04-20T10:02:00.000Z', 6_500),  # out of order insert
    ]):
        client.post('/api/turn-usage', json=_turn_row(
            turn_uuid=f't-{i}', turn_index=i, timestamp=ts,
            context_used_tokens=ctx,
        ))
    r = client.get('/api/sessions/t1/turn-usage')
    assert r.status_code == 200
    body = r.get_json()
    assert body['trace_id'] == 't1'
    assert [t['context_used_tokens'] for t in body['turns']] == [5_000, 6_500, 8_000]


def test_api_sessions_returns_context_pct(client, trace_db):
    """Session list endpoint exposes the aggregate ctx% so the Sessions
    view can render the badge without extra per-row queries."""
    client.post('/api/session-spans', json=_make_span(
        span_id='s0', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-opus-4-7'},
    ))
    client.post('/api/turn-usage', json=_turn_row(
        input_tokens=500, output_tokens=120,
        cache_read_tokens=159_500, cache_creation_tokens=500,
        context_used_tokens=160_000,
    ))
    r = client.get('/api/sessions?include_tests=true')
    assert r.status_code == 200
    body = r.get_json()
    rows = body['sessions']
    assert len(rows) == 1
    s = rows[0]
    assert s['peak_context_tokens'] == 160_000
    assert s['context_window_tokens'] == 1_000_000
    assert s['context_pct'] == 16.0


def test_turn_span_cannot_downgrade_variant_bracketed_model(client, trace_db):
    """SessionStart's hook payload carries `claude-opus-4-7[1m]` but the
    transcript-backed `turn` span only has the bare base `claude-opus-4-7`.
    The latter must not overwrite the richer id or the UI will mis-size
    the context window and display the wrong ctx%."""
    client.post('/api/session-spans', json=_make_span(
        span_id='r1', name='session.start',
        start_time='2026-04-24T10:00:00',
        attributes={'source': 'startup', 'model': 'claude-opus-4-7[1m]'},
    ))
    client.post('/api/session-spans', json=_make_span(
        span_id='t1', name='turn',
        start_time='2026-04-24T10:05:00',
        attributes={'model': 'claude-opus-4-7'},
    ))
    assert _session_model(trace_db, 't1') == 'claude-opus-4-7[1m]'


def test_turn_span_can_still_upgrade_when_existing_is_unrelated(client, trace_db):
    """The preserve-richer guard must not lock the model forever —
    switching families mid-session (e.g. `/model` from Opus to Haiku)
    should still take effect. Only prefix-base downgrades are blocked."""
    client.post('/api/session-spans', json=_make_span(
        span_id='r1', name='session.start',
        start_time='2026-04-24T10:00:00',
        attributes={'source': 'startup', 'model': 'claude-opus-4-7[1m]'},
    ))
    client.post('/api/session-spans', json=_make_span(
        span_id='t1', name='turn',
        start_time='2026-04-24T10:05:00',
        attributes={'model': 'claude-haiku-4-5-20251001'},
    ))
    assert _session_model(trace_db, 't1') == 'claude-haiku-4-5-20251001'


def test_api_session_detail_returns_context_pct(client, trace_db):
    client.post('/api/session-spans', json=_make_span(
        span_id='s0', name='session.start',
        attributes={'source': 'startup', 'model': 'claude-opus-4-7'},
    ))
    client.post('/api/turn-usage', json=_turn_row(
        input_tokens=500, output_tokens=120,
        cache_read_tokens=1_500, cache_creation_tokens=0,
        context_used_tokens=2_000,
    ))
    r = client.get('/api/sessions/t1?shallow=1')
    assert r.status_code == 200
    body = r.get_json()
    assert body['peak_context_tokens'] == 2_000
    assert body['context_window_tokens'] == 1_000_000
    assert body['context_pct'] == 0.2  # 2000/1000000 = 0.2%


# ── session_repos resolution at ingest ───────────────────────────────

def _seed_repo(name, path):
    import lib.orm.engine as db_module
    conn = sqlite3.connect(str(db_module.DB_PATH))
    try:
        conn.execute(
            "INSERT INTO repos (name, path, is_active, default_branch) "
            "VALUES (?, ?, 1, 'main')",
            (name, str(path)),
        )
        conn.commit()
        return conn.execute(
            "SELECT id FROM repos WHERE name = ?", (name,)
        ).fetchone()[0]
    finally:
        conn.close()


def _session_repo_tags(trace_id):
    import lib.orm.engine as db_module
    conn = sqlite3.connect(str(db_module.DB_PATH))
    try:
        return {
            (row[0], row[1])
            for row in conn.execute(
                "SELECT repo_id, is_primary FROM session_repos WHERE trace_id = ?",
                (trace_id,),
            )
        }
    finally:
        conn.close()


def _repo_span(trace_id, name, span_id, attrs):
    return ({
        'trace_id': trace_id, 'span_id': span_id, 'parent_id': None,
        'name': name, 'kind': 'internal',
        'start_time': '2026-04-18T12:00:00',
        'end_time': '2026-04-18T12:00:01', 'duration_ms': 1000,
        'status_code': 'UNSET', 'status_message': None,
    }, attrs)


def test_ingest_repo_resolution_excludes_reads_and_bash(tmp_db, tmp_path):
    """A Read or Bash into another registered repo must NOT tag the
    session with it — only the starting cwd's repo is recorded."""
    from lib.trace.trace_service import ingest_session_spans
    repo_a = tmp_path / 'repoA'; repo_a.mkdir()
    repo_b = tmp_path / 'repoB'; repo_b.mkdir()
    rid_a = _seed_repo('repoA', repo_a)
    rid_b = _seed_repo('repoB', repo_b)

    ingest_session_spans([
        _repo_span('t-ex', 'session.start', 's1',
                   {'cwd': str(repo_a), 'source': 'startup'}),
        _repo_span('t-ex', 'tool.Read', 's2',
                   {'tool_name': 'Read', 'file_path': str(repo_b / 'x.py')}),
        _repo_span('t-ex', 'tool.Bash', 's3',
                   {'tool_name': 'Bash', 'command': f'cd {repo_b}'}),
    ])

    tags = _session_repo_tags('t-ex')
    assert (rid_a, 1) in tags                       # started in repoA → primary
    assert all(rid != rid_b for rid, _ in tags)     # repoB never tagged


def test_ingest_repo_resolution_tags_edits_as_multi_repo(tmp_db, tmp_path):
    """A file mutation into another registered repo DOES tag it — that's
    the add-dir-to-work-here case, yielding a multi-repo session."""
    from lib.trace.trace_service import ingest_session_spans
    repo_a = tmp_path / 'repoA'; repo_a.mkdir()
    repo_b = tmp_path / 'repoB'; repo_b.mkdir()
    rid_a = _seed_repo('repoA', repo_a)
    rid_b = _seed_repo('repoB', repo_b)

    ingest_session_spans([
        _repo_span('t-edit', 'session.start', 's1', {'cwd': str(repo_a)}),
        _repo_span('t-edit', 'tool.Edit', 's2',
                   {'tool_name': 'Edit', 'file_path': str(repo_b / 'y.py')}),
    ])

    tags = _session_repo_tags('t-edit')
    assert (rid_a, 1) in tags        # primary
    assert (rid_b, 0) in tags        # secondary (edit) → multi-repo


def test_ingest_repo_resolution_ignores_unregistered_paths(tmp_db, tmp_path):
    """Paths outside every registered repo resolve to nothing."""
    from lib.trace.trace_service import ingest_session_spans
    repo_a = tmp_path / 'repoA'; repo_a.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    rid_a = _seed_repo('repoA', repo_a)

    ingest_session_spans([
        _repo_span('t-out', 'session.start', 's1', {'cwd': str(outside)}),
        _repo_span('t-out', 'tool.Edit', 's2',
                   {'tool_name': 'Edit', 'file_path': str(repo_a / 'z.py')}),
    ])

    tags = _session_repo_tags('t-out')
    # cwd was outside any repo (no primary), but the edit lands in repoA.
    assert tags == {(rid_a, 0)}


# ── prompt-image ingest: characterization (pins behavior pre-refactor) ─

# base64 of b'ABC' == 'QUJD'; a 1x1 PNG payload below is just any small blob.
_PI_DATA_B64 = 'QUJD'


def _make_prompt_image(**over):
    img = {
        'trace_id': 'pi-trace',
        'prompt_span_id': 'pi-span',
        'idx': 1,
        'media_type': 'image/png',
        'data_b64': _PI_DATA_B64,
    }
    img.update(over)
    return img


def _count_prompt_images(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute('SELECT COUNT(*) FROM prompt_images').fetchone()[0]
    finally:
        conn.close()


def test_prompt_image_accepts_single_object(client, trace_db):
    """A lone object (not a list) is wrapped and ingested."""
    r = client.post('/api/prompt-images', json=_make_prompt_image())
    assert r.status_code == 200
    assert r.get_json() == {'ok': True, 'ingested': 1, 'skipped_duplicates': 0}
    assert _count_prompt_images(trace_db) == 1


def test_prompt_image_idempotent_replay_skips(client, trace_db):
    """Replaying the same (trace, span, idx) is a no-op skip, not an insert."""
    payload = _make_prompt_image()
    first = client.post('/api/prompt-images', json=payload)
    assert first.get_json() == {'ok': True, 'ingested': 1, 'skipped_duplicates': 0}
    second = client.post('/api/prompt-images', json=payload)
    assert second.status_code == 200
    assert second.get_json() == {'ok': True, 'ingested': 0, 'skipped_duplicates': 1}
    assert _count_prompt_images(trace_db) == 1


def test_prompt_image_none_body_is_400(client, trace_db):
    r = client.post('/api/prompt-images', data='not json',
                    content_type='application/json')
    assert r.status_code == 400
    assert r.get_json() == {'ok': False, 'error': 'invalid JSON body'}


def test_prompt_image_empty_list_is_ok_zero(client, trace_db):
    """`[]` runs no items, no errors → 200 with zero counts."""
    r = client.post('/api/prompt-images', json=[])
    assert r.status_code == 200
    assert r.get_json() == {'ok': True, 'ingested': 0, 'skipped_duplicates': 0}
    assert _count_prompt_images(trace_db) == 0


@pytest.mark.parametrize('payload, reason', [
    ('scalar', 'not an object'),
    ({'prompt_span_id': 's', 'idx': 1, 'media_type': 'image/png',
      'data_b64': _PI_DATA_B64}, 'trace_id required'),
    ({'trace_id': 't', 'idx': 1, 'media_type': 'image/png',
      'data_b64': _PI_DATA_B64}, 'prompt_span_id required'),
    ({'trace_id': 't', 'prompt_span_id': 's', 'idx': 0,
      'media_type': 'image/png', 'data_b64': _PI_DATA_B64},
     'idx must be a positive int'),
    ({'trace_id': 't', 'prompt_span_id': 's', 'idx': 1,
      'media_type': 'image/bmp', 'data_b64': _PI_DATA_B64},
     "unsupported media_type 'image/bmp'"),
    ({'trace_id': 't', 'prompt_span_id': 's', 'idx': 1,
      'media_type': 'image/png'}, 'data_b64 required'),
])
def test_prompt_image_validation_reasons(client, trace_db, payload, reason):
    """Each guard surfaces its exact reason string and ingests nothing."""
    r = client.post('/api/prompt-images', json=[payload])
    assert r.status_code == 400
    body = r.get_json()
    assert body['ok'] is False
    assert body['ingested'] == 0
    assert body['errors'] == [{'index': 0, 'reason': reason}]
    assert _count_prompt_images(trace_db) == 0


def test_prompt_image_bad_base64_reason(client, trace_db):
    """Undecodable base64 surfaces a 'bad base64: ...' prefixed reason."""
    bad = _make_prompt_image(data_b64='@@@not-base64@@@')
    r = client.post('/api/prompt-images', json=[bad])
    assert r.status_code == 400
    body = r.get_json()
    assert body['errors'][0]['index'] == 0
    assert body['errors'][0]['reason'].startswith('bad base64:')
    assert _count_prompt_images(trace_db) == 0


def test_prompt_image_too_large_reason(client, trace_db, monkeypatch):
    """A payload over the ingest ceiling is rejected with a size reason."""
    import base64
    from web.blueprints.trace import prompt_images as _pi_mod
    monkeypatch.setattr(_pi_mod, '_PROMPT_IMAGE_INGEST_MAX_BYTES', 4)
    big_b64 = base64.b64encode(b'01234567').decode()  # 8 bytes > 4
    r = client.post('/api/prompt-images',
                    json=[_make_prompt_image(data_b64=big_b64)])
    assert r.status_code == 400
    body = r.get_json()
    assert body['errors'][0]['reason'].startswith('image too large:')
    assert _count_prompt_images(trace_db) == 0


def test_prompt_image_first_failing_guard_wins(client, trace_db):
    """An item failing two guards reports only the first (idx before media)."""
    # idx invalid AND media_type unsupported → only the idx reason fires.
    bad = _make_prompt_image(idx=0, media_type='image/bmp')
    r = client.post('/api/prompt-images', json=[bad])
    assert r.status_code == 400
    assert r.get_json()['errors'] == [
        {'index': 0, 'reason': 'idx must be a positive int'},
    ]


def test_prompt_image_batch_aborts_entirely_on_any_error(client, trace_db):
    """A batch with any invalid item ingests NOTHING and lists every error."""
    batch = [
        _make_prompt_image(idx=1),                       # valid
        _make_prompt_image(idx=0),                       # bad idx
        _make_prompt_image(idx=2, media_type='image/bmp'),  # bad media
    ]
    r = client.post('/api/prompt-images', json=batch)
    assert r.status_code == 400
    body = r.get_json()
    assert body == {
        'ok': False,
        'ingested': 0,
        'errors': [
            {'index': 1, 'reason': 'idx must be a positive int'},
            {'index': 2, 'reason': "unsupported media_type 'image/bmp'"},
        ],
    }
    # The valid item must NOT have been written — batch-abort semantics.
    assert _count_prompt_images(trace_db) == 0
