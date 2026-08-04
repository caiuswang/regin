"""The HTTP surface of an aliased SDK session, through the real Flask app.

A regin-launched run is traced twice and the readers merge the pair. These
endpoints each resolved the group separately and drifted apart: the session
list showed the run twice, the full `/map` served a different span tree
depending on which of the two ids you opened, and the "N of M" denominator
counted one writer against a merged numerator.

Exercised through the app rather than the merge functions because that is where
the drift lived — a `NameError` in this path survived the whole unit suite.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import web.app as app_module

RUN = 'sdk-abc123'
CHILD = '77777777-8888-9999-aaaa-bbbbbbbbbbbb'


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
        f"Bearer {create_token(1, 'test-editor', 'admin')}")
    return c


def _conn(db_path):
    return sqlite3.connect(str(db_path))


def _span(conn, trace_id, span_id, name, start, *, attrs=None,
          tool_use_id=None):
    conn.execute(
        """INSERT INTO session_spans
           (trace_id, span_id, name, kind, start_time, attributes, tool_use_id,
            status_code)
           VALUES (?, ?, ?, 'internal', ?, ?, ?, 'UNSET')""",
        (trace_id, span_id, name, start, json.dumps(attrs or {}), tool_use_id))


def _session_row(conn, trace_id, *, span_count, model='claude-opus-5',
                 status='ended'):
    conn.execute(
        """INSERT INTO sessions (trace_id, started_at, last_seen, origin,
                                 is_test, title, span_count, model, status)
           VALUES (?, '2026-08-01T10:00:00', '2026-08-01T10:05:00',
                   'session', 0, 'the shared prompt', ?, ?, ?)""",
        (trace_id, span_count, model, status))


@pytest.fixture
def aliased(trace_db):
    """One session written by both writers, linked as `agent_runs` would."""
    conn = _conn(trace_db)
    try:
        conn.execute(
            "INSERT INTO agent_runs (trace_id, status, cli_session_id) "
            "VALUES (?, 'exited', ?)", (RUN, CHILD))
        # The child's own hooks: the enrichment only they see.
        _session_row(conn, CHILD, span_count=3)
        _span(conn, CHILD, 'p-1', 'prompt', '2026-08-01T10:00:00',
              attrs={'text': 'the shared prompt'})
        _span(conn, CHILD, 'h-tool', 'tool.Bash', '2026-08-01T10:00:05',
              attrs={'command': 'ls'}, tool_use_id='toolu_1')
        _span(conn, CHILD, 'h-rule', 'rule.check', '2026-08-01T10:00:06')
        # The runner's SDK stream: the same two events, plus nothing new.
        # Its prompt is the delivery echo — same span id as the child's
        # anchor (both derive from the transcript entry uuid).
        _session_row(conn, RUN, span_count=2)
        _span(conn, RUN, 'p-1', 'prompt', '2026-08-01T10:00:00',
              attrs={'text': 'the shared prompt', 'entry_uuid': 'p-1-uuid'})
        _span(conn, RUN, 's-tool', 'tool.Bash', '2026-08-01T10:00:05',
              attrs={'tool_use_id': 'toolu_1'})
        conn.commit()
    finally:
        conn.close()
    return trace_db


def _map(client, trace_id):
    r = client.get(f'/api/sessions/{trace_id}/map')
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_full_map_is_identical_whichever_id_is_opened(client, aliased):
    """The run and its child are ONE session; opening either must not change
    the tree. They served 3 spans vs 2 before the group resolution landed."""
    by_child = _map(client, CHILD)
    by_run = _map(client, RUN)
    assert ([s['span_id'] for s in by_child['spans']]
            == [s['span_id'] for s in by_run['spans']])
    assert by_child['span_count'] == by_run['span_count']


def test_full_map_merges_without_duplicating(client, aliased):
    names = [s['name'] for s in _map(client, CHILD)['spans']]
    assert names.count('tool.Bash') == 1     # both writers saw it
    assert names.count('prompt') == 1
    assert names.count('rule.check') == 1    # only the hooks saw it


def test_map_span_dict_keeps_its_shape(client, aliased):
    """`tool_use_id` and the origin marker are fetched for the reconcile only;
    neither is part of this endpoint's contract."""
    span = _map(client, CHILD)['spans'][0]
    assert 'tool_use_id' not in span
    assert '_alias_origin' not in span


def test_loaded_total_is_the_merged_count_not_the_raw_union(client, aliased):
    """`N of M`: summing both writers double-counts every shared event, so the
    denominator has to be the merged total — 3 here, not the 5 raw rows."""
    body = client.get(f'/api/sessions/{CHILD}/map?shallow=1&limit=50').get_json()
    assert body['span_count_total'] == 3
    assert body['span_count_total'] >= body['span_count']


def test_session_list_shows_the_pair_once_but_keeps_the_deep_link(client,
                                                                  aliased):
    listed = client.get('/api/sessions?size=50').get_json()
    ids = [s['trace_id'] for s in listed['items']]
    assert CHILD in ids
    assert RUN not in ids
    # `/live` links carry the run's own id, so an explicit lookup must resolve.
    deep = client.get(f'/api/sessions?size=50&trace_id={RUN}').get_json()
    assert [s['trace_id'] for s in deep['items']] == [RUN]


def test_loaded_total_never_falls_below_what_is_on_screen(client, aliased):
    """`sessions.span_count` only refreshes when the session ends, so mid-run
    it holds one writer's stale figure. Serving it raw read "35 of 28" — a
    denominator below its own numerator, which HEAD could never produce."""
    conn = _conn(aliased)
    try:
        conn.execute("UPDATE sessions SET span_count = 0 WHERE trace_id = ?",
                     (CHILD,))
        conn.commit()
    finally:
        conn.close()
    body = client.get(f'/api/sessions/{CHILD}/map?shallow=1&limit=50').get_json()
    assert body['span_count_total'] >= body['span_count']


def test_stop_reason_agrees_with_the_session_end_span(client, aliased):
    """Only the runner knows a run hit its idle timeout; the child's hook can
    report just the generic `other`. The header used to serve `other` while the
    `session.end` span in the same response said `idle timeout`."""
    conn = _conn(aliased)
    try:
        conn.execute("UPDATE sessions SET ended_reason='other' WHERE trace_id=?",
                     (CHILD,))
        conn.execute(
            "UPDATE sessions SET ended_reason='idle timeout' WHERE trace_id=?",
            (RUN,))
        conn.commit()
    finally:
        conn.close()
    body = client.get(f'/api/sessions/{CHILD}/map?shallow=1&limit=50').get_json()
    assert body['ended_reason'] == 'idle timeout'


def test_header_survives_a_child_that_never_wrote_a_hook_trace(client,
                                                               aliased):
    """On an install where regin's hooks aren't wired for that cwd, the child
    writes no `sessions` row at all — but the runner still records the alias
    off the SDK stream. Keying the header on the canonical row alone wiped it
    (status, title, model, phase all null) for every such run."""
    conn = _conn(aliased)
    try:
        conn.execute("DELETE FROM sessions WHERE trace_id = ?", (CHILD,))
        conn.commit()
    finally:
        conn.close()
    body = client.get(f'/api/sessions/{RUN}/map?shallow=1&limit=50').get_json()
    assert body['title'] == 'the shared prompt'
    assert body['model'] == 'claude-opus-5'
    assert body['status'] == 'ended'


def test_a_run_whose_child_wrote_nothing_still_appears_in_the_list(client,
                                                                   aliased):
    """Hiding the alias is only correct when the canonical row exists to take
    its place. Hiding it against a child that never wrote a trace removed the
    run's ONLY visible row, dropping it out of the session list for good —
    findable afterwards only by someone who already knew the sdk id."""
    conn = _conn(aliased)
    try:
        conn.execute("DELETE FROM sessions WHERE trace_id = ?", (CHILD,))
        conn.commit()
    finally:
        conn.close()
    ids = [s['trace_id']
           for s in client.get('/api/sessions?size=50').get_json()['items']]
    assert RUN in ids


@pytest.mark.parametrize('delete', [
    lambda c: c.delete(f'/api/sessions/{CHILD}'),
    lambda c: c.post('/api/sessions/batch-delete', json={'trace_ids': [CHILD]}),
])
def test_deleting_the_merged_session_removes_both_halves(client, aliased,
                                                         delete):
    """The pair is one row in the list, so deleting it must take both traces.
    Removing only the canonical one left the other holding the spans — and it
    surfaced as a NEW row where the deleted one had been, the same session
    apparently refusing to be deleted."""
    assert delete(client).status_code == 200
    ids = [s['trace_id']
           for s in client.get('/api/sessions?size=50').get_json()['items']]
    assert CHILD not in ids
    assert RUN not in ids
    conn = _conn(aliased)
    try:
        left = conn.execute(
            "SELECT COUNT(*) FROM session_spans WHERE trace_id IN (?, ?)",
            (CHILD, RUN)).fetchone()[0]
    finally:
        conn.close()
    assert left == 0


def test_an_ordinary_session_is_unaffected(client, trace_db):
    """The 99% case: no alias row, so nothing about it changes."""
    conn = _conn(trace_db)
    try:
        _session_row(conn, 'plain-session', span_count=2)
        _span(conn, 'plain-session', 'p1', 'prompt', '2026-08-01T10:00:00',
              attrs={'text': 'hello'})
        _span(conn, 'plain-session', 't1', 'tool.Bash', '2026-08-01T10:00:01',
              tool_use_id='toolu_9')
        conn.commit()
    finally:
        conn.close()
    body = _map(client, 'plain-session')
    assert [s['name'] for s in body['spans']] == ['prompt', 'tool.Bash']
    shallow = client.get(
        '/api/sessions/plain-session/map?shallow=1&limit=50').get_json()
    # Unaliased totals still come from the raw row count.
    assert shallow['span_count_total'] == 2


def test_the_map_names_the_id_the_session_is_really_keyed_on(client, aliased):
    """`/live` can only navigate to the run's own `sdk-…` id at launch — it is
    the only id that exists before the child names itself. The summary carries
    the canonical one so the card can rewrite its URL onto the id every other
    reader (and the transcript on disk) uses.
    """
    assert _map(client, RUN)['canonical_trace_id'] == CHILD
    assert _map(client, CHILD)['canonical_trace_id'] == CHILD


def test_an_unaliased_session_is_its_own_canonical_id(client, trace_db):
    """The 99% case must report itself, not null — the card compares this to
    the route id and a null would read as "rewrite to nothing"."""
    conn = _conn(trace_db)
    try:
        _session_row(conn, 'plain-session', span_count=1)
        _span(conn, 'plain-session', 'p1', 'prompt', '2026-08-01T10:00:00',
              attrs={'text': 'hello'})
        conn.commit()
    finally:
        conn.close()

    assert _map(client, 'plain-session')['canonical_trace_id'] == 'plain-session'
