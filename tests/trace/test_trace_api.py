"""Tests for the /api/sessions/<trace_id> read-only contract and the
/api/sessions/<trace_id>/materialize side-effecting endpoint.

The previous handler issued UPDATE statements from inside GET. That
violated HTTP safety and raced with concurrent hook ingestion. This
test locks in the new behaviour: GET projects the tree in memory only;
materialize() is the explicit mutating endpoint.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

from web import app as app_module


@pytest.fixture
def trace_db(tmp_path, monkeypatch):
    """Patch lib.orm.engine.DB_PATH to a fresh SQLite file and apply the full
    canonical schema."""
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
    c.environ_base['HTTP_AUTHORIZATION'] = f"Bearer {create_token(1, 'test-editor', 'editor')}"
    return c


def _seed(db_path, rows):
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
        conn.commit()
    finally:
        conn.close()


def _seed_session_row(db_path, trace_id, *, origin='session', is_test=0,
                      agent_type=None, title=None, last_seen='2026-01-01T00:00:00'):
    """Minimal `sessions` row so `_session_summary` / the list endpoint see it.

    `is_test` defaults to 0 so the row matches the list endpoint's default
    `kind='real'` filter; `origin`/`agent_type`/`title` are settable so the
    workflow-axis filter tests can seed both interactive sessions and
    captured runs.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions "
            "(trace_id, started_at, last_seen, origin, is_test, agent_type, title) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trace_id, '2026-01-01T00:00:00', last_seen,
             origin, is_test, agent_type, title))
        conn.commit()
    finally:
        conn.close()


def _snapshot(db_path, trace_id):
    """Capture parent_id / start_time / end_time / duration_ms for every row."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """SELECT span_id, parent_id, start_time, end_time, duration_ms
               FROM session_spans WHERE trace_id = ?""",
            (trace_id,),
        ).fetchall()
        return {r[0]: r[1:] for r in rows}
    finally:
        conn.close()


# --- pure helper tests ---------------------------------------------------

def test_graft_orphans_assigns_prompts_to_conversation():
    spans = [
        {'span_id': 'c', 'parent_id': None, 'name': 'conversation',
         'start_time': '2026-01-01T00:00:00', 'end_time': None,
         'duration_ms': 0},
        {'span_id': 'p1', 'parent_id': None, 'name': 'prompt',
         'start_time': '2026-01-01T00:00:01', 'end_time': None,
         'duration_ms': 0},
    ]
    out = app_module._graft_orphans(spans)
    assert out[1]['parent_id'] == 'c'
    # Input untouched.
    assert spans[1]['parent_id'] is None


def test_graft_orphans_assigns_tool_spans_to_current_prompt():
    spans = [
        {'span_id': 'c', 'parent_id': None, 'name': 'conversation',
         'start_time': '2026-01-01T00:00:00', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'p', 'parent_id': 'c', 'name': 'prompt',
         'start_time': '2026-01-01T00:00:01', 'end_time': None, 'duration_ms': 0},
        {'span_id': 't', 'parent_id': None, 'name': 'tool.Read',
         'start_time': '2026-01-01T00:00:02', 'end_time': None, 'duration_ms': 5},
    ]
    out = app_module._graft_orphans(spans)
    tool = next(s for s in out if s['span_id'] == 't')
    assert tool['parent_id'] == 'p'


def test_graft_orphans_keeps_session_end_out_of_prompts():
    """session.end fires after the last prompt. It must stay a first-class
    span under `conversation` (or at the root if there's no conversation),
    never grafted under the trailing prompt.
    """
    spans = [
        {'span_id': 'c', 'parent_id': None, 'name': 'conversation',
         'start_time': '2026-01-01T00:00:00', 'end_time': None, 'duration_ms': 0},
        {'span_id': 's0', 'parent_id': None, 'name': 'session.start',
         'start_time': '2026-01-01T00:00:00', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'p', 'parent_id': 'c', 'name': 'prompt',
         'start_time': '2026-01-01T00:00:01', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'se', 'parent_id': None, 'name': 'session.end',
         'start_time': '2026-01-01T00:00:09', 'end_time': None, 'duration_ms': 0},
    ]
    out = app_module._graft_orphans(spans)
    session_end = next(s for s in out if s['span_id'] == 'se')
    session_start = next(s for s in out if s['span_id'] == 's0')
    assert session_end['parent_id'] == 'c', (
        "session.end must graft to conversation, not the trailing prompt"
    )
    assert session_start['parent_id'] == 'c'


def test_graft_orphans_leaves_session_end_at_root_without_conversation():
    """Without a conversation span to parent to, session.end stays at the
    root — it must not be pulled under the prompt.
    """
    spans = [
        {'span_id': 'p', 'parent_id': None, 'name': 'prompt',
         'start_time': '2026-01-01T00:00:01', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'se', 'parent_id': None, 'name': 'session.end',
         'start_time': '2026-01-01T00:00:09', 'end_time': None, 'duration_ms': 0},
    ]
    out = app_module._graft_orphans(spans)
    session_end = next(s for s in out if s['span_id'] == 'se')
    assert session_end.get('parent_id') is None


def test_graft_orphans_keeps_compact_boundaries_out_of_prompts():
    """compact.pre/compact.post fire BETWEEN prompts when the user runs
    `/compact`. They must graft under the conversation, not the previous
    prompt — otherwise the boundary divider hides inside an unrelated
    turn and the WebUI never renders the compaction marker.
    """
    spans = [
        {'span_id': 'c', 'parent_id': None, 'name': 'conversation',
         'start_time': '2026-01-01T00:00:00', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'p1', 'parent_id': 'c', 'name': 'prompt',
         'start_time': '2026-01-01T00:00:01', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'cpre', 'parent_id': None, 'name': 'compact.pre',
         'start_time': '2026-01-01T00:00:05', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'cpost', 'parent_id': None, 'name': 'compact.post',
         'start_time': '2026-01-01T00:00:07', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'p2', 'parent_id': 'c', 'name': 'prompt',
         'start_time': '2026-01-01T00:00:09', 'end_time': None, 'duration_ms': 0},
    ]
    out = app_module._graft_orphans(spans)
    cpre = next(s for s in out if s['span_id'] == 'cpre')
    cpost = next(s for s in out if s['span_id'] == 'cpost')
    assert cpre['parent_id'] == 'c'
    assert cpost['parent_id'] == 'c'


def test_graft_orphans_leaves_compact_boundaries_at_root_without_conversation():
    spans = [
        {'span_id': 'p', 'parent_id': None, 'name': 'prompt',
         'start_time': '2026-01-01T00:00:01', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'cpre', 'parent_id': None, 'name': 'compact.pre',
         'start_time': '2026-01-01T00:00:05', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'cpost', 'parent_id': None, 'name': 'compact.post',
         'start_time': '2026-01-01T00:00:07', 'end_time': None, 'duration_ms': 0},
    ]
    out = app_module._graft_orphans(spans)
    cpre = next(s for s in out if s['span_id'] == 'cpre')
    cpost = next(s for s in out if s['span_id'] == 'cpost')
    assert cpre.get('parent_id') is None
    assert cpost.get('parent_id') is None


def test_graft_orphans_reattaches_turn_span_near_next_prompt():
    """A `turn` span emitted on UserPromptSubmit fires a few
    milliseconds BEFORE the new `prompt` span. Without re-attribution,
    it sorts ahead of the new prompt and widens the PREVIOUS prompt's
    envelope to include the entire user-idle gap between the two
    prompts. The projection must re-attach such turn spans to the
    next prompt so the previous prompt's duration reflects only its
    own AI-response time, not the time until the user types again."""
    spans = [
        {'span_id': 'p1', 'parent_id': None, 'name': 'prompt',
         'start_time': '2026-01-01T00:00:00.000000', 'end_time': None,
         'duration_ms': 0},
        {'span_id': 't1', 'parent_id': None, 'name': 'tool.Read',
         'start_time': '2026-01-01T00:00:01.000000', 'end_time': None,
         'duration_ms': 0},
        # Turn span fires 8 ms before the new prompt — classic
        # UserPromptSubmit handler-chain pattern.
        {'span_id': 'turn', 'parent_id': None, 'name': 'turn',
         'start_time': '2026-01-01T00:01:29.992000', 'end_time': None,
         'duration_ms': 0},
        {'span_id': 'p2', 'parent_id': None, 'name': 'prompt',
         'start_time': '2026-01-01T00:01:30.000000', 'end_time': None,
         'duration_ms': 0},
    ]
    out = app_module._graft_orphans(spans)
    by_id = {s['span_id']: s for s in out}
    assert by_id['turn']['parent_id'] == 'p2', (
        "turn landing within 1 s before p2 must reattach to p2, not p1"
    )
    assert by_id['t1']['parent_id'] == 'p1'


def test_graft_orphans_nests_subagent_tool_spans_under_subagent_start():
    """Claude Code tags every hook firing inside a subagent with
    `agent_id`. Tool spans that carry that id must nest under their
    matching `subagent.start` span — producing a three-level tree
    (prompt → subagent → subagent's tool calls) instead of scattering
    the subagent's internal work as siblings of the parent's own tools.
    """
    spans = [
        {'span_id': 'p', 'parent_id': None, 'name': 'prompt', 'attributes': {},
         'start_time': '2026-01-01T00:00:00', 'end_time': None, 'duration_ms': 0},
        # Parent's own tool call — stays under the prompt.
        {'span_id': 'parent-tool', 'parent_id': None, 'name': 'tool.Read',
         'attributes': {'tool_name': 'Read'},
         'start_time': '2026-01-01T00:00:01', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'sa-start', 'parent_id': None, 'name': 'subagent.start',
         'attributes': {'agent_id': 'abc', 'agent_type': 'Explore'},
         'start_time': '2026-01-01T00:00:02', 'end_time': None, 'duration_ms': 0},
        # Subagent's internal tool calls — must reparent onto sa-start.
        {'span_id': 'sa-tool-1', 'parent_id': None, 'name': 'tool.Bash',
         'attributes': {'tool_name': 'Bash', 'agent_id': 'abc'},
         'start_time': '2026-01-01T00:00:03', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'sa-tool-2', 'parent_id': None, 'name': 'tool.Read',
         'attributes': {'tool_name': 'Read', 'agent_id': 'abc'},
         'start_time': '2026-01-01T00:00:04', 'end_time': None, 'duration_ms': 0},
        {'span_id': 'sa-stop', 'parent_id': None, 'name': 'subagent.stop',
         'attributes': {'agent_id': 'abc'},
         'start_time': '2026-01-01T00:00:05', 'end_time': None, 'duration_ms': 0},
    ]
    out = app_module._graft_orphans(spans)
    by_id = {s['span_id']: s for s in out}
    assert by_id['parent-tool']['parent_id'] == 'p'
    assert by_id['sa-start']['parent_id'] == 'p'
    assert by_id['sa-tool-1']['parent_id'] == 'sa-start'
    assert by_id['sa-tool-2']['parent_id'] == 'sa-start'
    assert by_id['sa-stop']['parent_id'] == 'sa-start'


def test_widen_envelopes_is_pure():
    spans = [
        {'span_id': 'parent', 'parent_id': None, 'name': 'prompt',
         'start_time': '2026-01-01T00:00:05', 'end_time': '2026-01-01T00:00:06',
         'duration_ms': 1000},
        {'span_id': 'child', 'parent_id': 'parent', 'name': 'tool.Read',
         'start_time': '2026-01-01T00:00:04', 'end_time': '2026-01-01T00:00:07',
         'duration_ms': 3000},
    ]
    out = app_module._widen_envelopes(spans)
    parent = next(s for s in out if s['span_id'] == 'parent')
    assert parent['start_time'] == '2026-01-01T00:00:04'
    assert parent['end_time'] == '2026-01-01T00:00:07'
    # Input unchanged.
    assert spans[0]['start_time'] == '2026-01-01T00:00:05'


# --- integration tests: GET must not mutate ------------------------------

def test_get_session_does_not_mutate_db(client, trace_db):
    trace_id = 'ro-1'
    _seed(trace_db, [
        {'trace_id': trace_id, 'span_id': 'c', 'parent_id': None,
         'name': 'conversation',
         'start_time': '2026-01-01T00:00:00', 'end_time': '2026-01-01T00:00:10',
         'duration_ms': 10_000},
        # Orphan prompt — GET will graft it to 'c' in the response, but
        # must NOT persist that graft.
        {'trace_id': trace_id, 'span_id': 'p', 'parent_id': None,
         'name': 'prompt',
         'start_time': '2026-01-01T00:00:01', 'end_time': '2026-01-01T00:00:02',
         'duration_ms': 1_000},
        {'trace_id': trace_id, 'span_id': 't', 'parent_id': 'p',
         'name': 'tool.Read',
         'start_time': '2026-01-01T00:00:00', 'end_time': '2026-01-01T00:00:05',
         'duration_ms': 5_000},
    ])
    before = _snapshot(trace_db, trace_id)

    resp = client.get(f'/api/sessions/{trace_id}')
    assert resp.status_code == 200
    data = resp.get_json()

    # The response MUST still graft the orphan and widen envelopes.
    grafted_prompt = next(s for s in data['spans'] if s['span_id'] == 'p')
    assert grafted_prompt['parent_id'] == 'c', (
        "GET should project orphan -> conversation in the returned payload"
    )
    widened_prompt = grafted_prompt
    assert widened_prompt['start_time'] == '2026-01-01T00:00:00', (
        "GET should widen envelopes in the response"
    )

    # A second GET must still not mutate.
    client.get(f'/api/sessions/{trace_id}')

    after = _snapshot(trace_db, trace_id)
    assert before == after, (
        "GET mutated the DB! before=%s after=%s" % (before, after)
    )


def test_materialize_persists_projection(client, trace_db):
    trace_id = 'rw-1'
    _seed(trace_db, [
        {'trace_id': trace_id, 'span_id': 'c', 'parent_id': None,
         'name': 'conversation',
         'start_time': '2026-01-01T00:00:00', 'end_time': '2026-01-01T00:00:10',
         'duration_ms': 10_000},
        {'trace_id': trace_id, 'span_id': 'p', 'parent_id': None,
         'name': 'prompt',
         'start_time': '2026-01-01T00:00:01', 'end_time': '2026-01-01T00:00:02',
         'duration_ms': 1_000},
        {'trace_id': trace_id, 'span_id': 't', 'parent_id': 'p',
         'name': 'tool.Read',
         'start_time': '2026-01-01T00:00:00', 'end_time': '2026-01-01T00:00:05',
         'duration_ms': 5_000},
    ])

    resp = client.post(f'/api/sessions/{trace_id}/materialize')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['parent_updates'] >= 1, "should have persisted the orphan graft"
    assert body['envelope_updates'] >= 1, "should have persisted the envelope widen"

    after = _snapshot(trace_db, trace_id)
    # Prompt's parent_id now c.
    assert after['p'][0] == 'c'
    # Prompt envelope now covers tool.Read.
    assert after['p'][1] == '2026-01-01T00:00:00'
    assert after['p'][2] == '2026-01-01T00:00:05'


def test_ingest_is_idempotent_on_repeated_span_id(client, trace_db):
    """Previously the POST /api/session-spans handler used INSERT OR REPLACE
    but the table had no UNIQUE (trace_id, span_id) constraint, so duplicate
    POSTs of the same span produced two rows. Now the unique index is
    installed by _init_session_spans_schema, and INSERT OR REPLACE actually
    dedupes."""
    trace_id = 'ingest-dedup'
    span = {
        'trace_id': trace_id,
        'span_id': 'abc123',
        'parent_id': None,
        'name': 'prompt',
        'start_time': '2026-01-01T00:00:00',
        'end_time': None,
        'duration_ms': 0,
        'attributes': {},
        'status_code': 'OK',
    }
    # First post — span starts, no end_time yet.
    r1 = client.post('/api/session-spans', json=span)
    assert r1.status_code == 200

    # Second post — span completes, with end_time + duration.
    span2 = dict(span, end_time='2026-01-01T00:00:05', duration_ms=5_000)
    r2 = client.post('/api/session-spans', json=span2)
    assert r2.status_code == 200

    # Exactly one row in DB; last-write-wins.
    conn = sqlite3.connect(str(trace_db))
    try:
        rows = conn.execute(
            "SELECT span_id, end_time, duration_ms FROM session_spans "
            "WHERE trace_id = ?", (trace_id,)
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}: {rows}"
    assert rows[0][1] == '2026-01-01T00:00:05'
    assert rows[0][2] == 5_000


def test_materialize_is_idempotent(client, trace_db):
    trace_id = 'idem-1'
    _seed(trace_db, [
        {'trace_id': trace_id, 'span_id': 'c', 'parent_id': None,
         'name': 'conversation',
         'start_time': '2026-01-01T00:00:00', 'end_time': '2026-01-01T00:00:10',
         'duration_ms': 10_000},
        {'trace_id': trace_id, 'span_id': 'p', 'parent_id': None,
         'name': 'prompt',
         'start_time': '2026-01-01T00:00:01', 'end_time': '2026-01-01T00:00:02',
         'duration_ms': 1_000},
    ])
    first = client.post(f'/api/sessions/{trace_id}/materialize').get_json()
    assert first['parent_updates'] >= 1
    second = client.post(f'/api/sessions/{trace_id}/materialize').get_json()
    assert second['parent_updates'] == 0
    assert second['envelope_updates'] == 0


# --- /spans/<id>/ancestors -------------------------------------------------

def test_ancestors_returns_root_first_chain(client, trace_db):
    """Backs the /trace/triggers deep-link: given a nested rule.check
    span, the frontend resolves the owning root prompt via this endpoint
    so it can load only that subtree (cheaper than the full map)."""
    trace_id = 'anc-1'
    _seed(trace_db, [
        {'trace_id': trace_id, 'span_id': 'root', 'parent_id': None,
         'name': 'prompt',
         'start_time': '2026-01-01T00:00:00'},
        {'trace_id': trace_id, 'span_id': 'mid', 'parent_id': 'root',
         'name': 'tool.Edit',
         'start_time': '2026-01-01T00:00:01'},
        {'trace_id': trace_id, 'span_id': 'leaf', 'parent_id': 'mid',
         'name': 'rule.check',
         'start_time': '2026-01-01T00:00:02'},
    ])
    body = client.get(f'/api/sessions/{trace_id}/spans/leaf/ancestors').get_json()
    assert body['span_id'] == 'leaf'
    assert body['root_span_id'] == 'root'
    assert body['chain'] == ['root', 'mid', 'leaf']


def test_ancestors_404_when_span_missing(client, trace_db):
    resp = client.get('/api/sessions/anc-x/spans/no-such/ancestors')
    assert resp.status_code == 404


def test_ancestors_breaks_self_referential_cycle(client, trace_db):
    """A malformed parent_id pointing back at the span itself must not
    spin forever — the loop guard ends the walk and returns what was
    collected so far."""
    trace_id = 'anc-cyc'
    _seed(trace_db, [
        {'trace_id': trace_id, 'span_id': 'loop', 'parent_id': 'loop',
         'name': 'prompt',
         'start_time': '2026-01-01T00:00:00'},
    ])
    body = client.get(f'/api/sessions/{trace_id}/spans/loop/ancestors').get_json()
    assert body['root_span_id'] == 'loop'
    assert body['chain'] == ['loop']


def test_ancestors_grafts_orphan_to_latest_prior_prompt(client, trace_db):
    """A rule.check with NULL parent_id is grafted by `_graft_orphans`
    under the chronologically-current prompt. The ancestors endpoint
    must mirror that — otherwise the deep-link asks for the wrong
    root subtree."""
    trace_id = 'anc-orphan'
    _seed(trace_db, [
        {'trace_id': trace_id, 'span_id': 'p1', 'parent_id': None,
         'name': 'prompt', 'start_time': '2026-01-01T00:00:00'},
        {'trace_id': trace_id, 'span_id': 'p2', 'parent_id': None,
         'name': 'prompt', 'start_time': '2026-01-01T00:01:00'},
        # rule.check fires DURING p2 — must graft to p2, not p1.
        {'trace_id': trace_id, 'span_id': 'chk', 'parent_id': None,
         'name': 'rule.check', 'start_time': '2026-01-01T00:01:30'},
    ])
    body = client.get(f'/api/sessions/{trace_id}/spans/chk/ancestors').get_json()
    assert body['root_span_id'] == 'p2'
    assert body['chain'] == ['p2', 'chk']


def test_ancestors_grafts_orphan_prompt_to_conversation(client, trace_db):
    """An orphan prompt grafts under the conversation span — same
    rule `_graft_orphans` applies at projection time."""
    trace_id = 'anc-graft-prompt'
    _seed(trace_db, [
        {'trace_id': trace_id, 'span_id': 'c', 'parent_id': None,
         'name': 'conversation', 'start_time': '2026-01-01T00:00:00'},
        {'trace_id': trace_id, 'span_id': 'p', 'parent_id': None,
         'name': 'prompt', 'start_time': '2026-01-01T00:00:01'},
    ])
    body = client.get(f'/api/sessions/{trace_id}/spans/p/ancestors').get_json()
    assert body['root_span_id'] == 'c'
    assert body['chain'] == ['c', 'p']


def test_ancestors_clears_dangling_parent_id(client, trace_db):
    """A parent_id pointing at a non-existent span gets ignored (mirrors
    `_graft_orphans` self-healing), and graft logic takes over."""
    trace_id = 'anc-dangle'
    _seed(trace_db, [
        {'trace_id': trace_id, 'span_id': 'p', 'parent_id': None,
         'name': 'prompt', 'start_time': '2026-01-01T00:00:00'},
        # parent_id points at a span that never existed.
        {'trace_id': trace_id, 'span_id': 'chk', 'parent_id': 'ghost',
         'name': 'rule.check', 'start_time': '2026-01-01T00:00:01'},
    ])
    body = client.get(f'/api/sessions/{trace_id}/spans/chk/ancestors').get_json()
    assert body['root_span_id'] == 'p'


def test_session_map_span_dict_has_exact_projection_keys(client, trace_db):
    """The `/api/sessions/<id>/map` endpoint projects from
    session_trace_map. `_graft_orphans` and the frontend both read
    these spans dict-keyed, so the key set must match the SQL
    projection. Pins the contract through the lib.db → lib.orm
    migration. session_trace_map is dual-written by ingest; the test
    seeds it directly so we exercise the projection in isolation."""
    trace_id = 'shape-map-1'
    conn = sqlite3.connect(str(trace_db))
    try:
        conn.execute(
            """INSERT INTO session_trace_map
               (trace_id, span_id, parent_id, name, kind,
                start_time, end_time, duration_ms,
                status_code, status_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (trace_id, 'p', None, 'prompt', 'internal',
             '2026-01-01T00:00:00', '2026-01-01T00:00:01', 1000,
             'UNSET', None),
        )
        conn.commit()
    finally:
        conn.close()
    body = client.get(f'/api/sessions/{trace_id}/map').get_json()
    span = body['spans'][0]
    expected_keys = {
        'id', 'trace_id', 'span_id', 'parent_id', 'name', 'kind',
        'start_time', 'end_time', 'duration_ms',
        'status_code', 'status_message',
    }
    assert set(span.keys()) == expected_keys, (
        f"span dict keys drifted: have {sorted(span.keys())}, "
        f"expected {sorted(expected_keys)}"
    )


def test_span_content_returns_decoded_attributes(client, trace_db):
    """`/spans/<id>/content` returns the JSON-decoded attributes blob.
    Pins the contract before the lib.db → lib.orm migration so the
    decoded dict survives the rewrite."""
    trace_id = 'span-content-1'
    _seed(trace_db, [
        {'trace_id': trace_id, 'span_id': 'a1', 'parent_id': None,
         'name': 'prompt', 'start_time': '2026-01-01T00:00:00',
         'attributes': {'text': 'hello world', 'image_count': 0}},
    ])
    body = client.get(f'/api/sessions/{trace_id}/spans/a1/content').get_json()
    assert body['trace_id'] == trace_id
    assert body['span_id'] == 'a1'
    assert body['attributes'] == {'text': 'hello world', 'image_count': 0}


def test_span_content_404_when_missing(client, trace_db):
    """Unknown span ids return a 404, not a crash. Pins behaviour
    across the ORM migration."""
    resp = client.get('/api/sessions/no-such-trace/spans/no-such-span/content')
    assert resp.status_code == 404


def test_workflow_runs_endpoint_lists_launched_runs(client, trace_db):
    """The session header's `workflows N` chip reads this endpoint: it
    lists captured runs in call order and excludes Workflow calls not yet
    linked to a run (no `workflow_run_id`)."""
    _seed(trace_db, [
        {'trace_id': 'sess', 'span_id': 's1', 'name': 'tool.Workflow',
         'start_time': '2026-01-01T00:00:02',
         'attributes': {'tool_name': 'Workflow',
                        'workflow_run_id': 'wf_b', 'workflow_name': 'beta'}},
        {'trace_id': 'sess', 'span_id': 's2', 'name': 'tool.Workflow',
         'start_time': '2026-01-01T00:00:01',
         'attributes': {'tool_name': 'Workflow',
                        'workflow_run_id': 'wf_a', 'workflow_name': 'alpha'}},
        {'trace_id': 'sess', 'span_id': 's3', 'name': 'tool.Workflow',
         'start_time': '2026-01-01T00:00:03',
         'attributes': {'tool_name': 'Workflow'}},  # unstamped -> excluded
    ])
    items = client.get('/api/sessions/sess/workflow-runs').get_json()['items']
    assert items == [{'run_id': 'wf_a', 'name': 'alpha'},
                     {'run_id': 'wf_b', 'name': 'beta'}]


def test_session_detail_surfaces_workflow_total_tokens(client, trace_db):
    """A workflow run's session-detail response carries `total_tokens`, read
    from the run-root span's attributes (the manifest grand total), so the
    header can show a total chip even though peak_context_tokens is NULL.
    A non-workflow session has no such span, so the field is None."""
    _seed(trace_db, [
        {'trace_id': 'wf_t', 'span_id': 'wfrun-t', 'name': 'session.start',
         'start_time': '2026-01-01T00:00:00',
         # New model: a run-root span's vendor is 'claude' and it carries a
         # `run_id` (the workflow-ness marker) — never agent_type='workflow'.
         'attributes': {'agent_type': 'claude', 'run_id': 'wf_t',
                        'total_tokens': 116626}},
    ])
    _seed_session_row(trace_db, 'wf_t')
    assert client.get('/api/sessions/wf_t').get_json()['total_tokens'] == 116626

    _seed(trace_db, [
        {'trace_id': 'norm', 'span_id': 'ss', 'name': 'session.start',
         'start_time': '2026-01-01T00:00:00',
         'attributes': {'agent_type': 'claude'}},
    ])
    _seed_session_row(trace_db, 'norm')
    assert client.get('/api/sessions/norm').get_json()['total_tokens'] is None


# --- /api/sessions list: workflow (origin) axis -------------------------

def _seed_origin_mix(trace_db):
    """Two interactive sessions + two captured runs, distinct last_seen so
    the keyset order is stable. Returns nothing; trace_ids are fixed."""
    _seed_session_row(trace_db, 'sess_a', origin='session',
                      agent_type='claude', last_seen='2026-01-01T00:00:04')
    _seed_session_row(trace_db, 'sess_b', origin='session',
                      agent_type='codex', last_seen='2026-01-01T00:00:03')
    _seed_session_row(trace_db, 'run_x', origin='workflow',
                      agent_type='claude', last_seen='2026-01-01T00:00:02')
    _seed_session_row(trace_db, 'run_y', origin='workflow',
                      agent_type='claude', last_seen='2026-01-01T00:00:01')


def _ids(body):
    return {it['trace_id'] for it in body['items']}


def test_sessions_workflow_default_show_returns_all(client, trace_db):
    """Server default is `show` = every row, so external callers and E2E
    fixtures are unaffected by the new origin axis. No `workflow_hidden_count`
    when not hiding."""
    _seed_origin_mix(trace_db)
    body = client.get('/api/sessions').get_json()
    assert _ids(body) == {'sess_a', 'sess_b', 'run_x', 'run_y'}
    assert body['workflow_hidden_count'] is None


def test_sessions_workflow_hide_excludes_runs_and_counts_them(client, trace_db):
    """`hide` drops origin='workflow' rows and reports how many the same
    other filters would have matched."""
    _seed_origin_mix(trace_db)
    body = client.get('/api/sessions?workflow=hide').get_json()
    assert _ids(body) == {'sess_a', 'sess_b'}
    assert body['workflow_hidden_count'] == 2


def test_sessions_workflow_only_returns_just_runs(client, trace_db):
    """`only` returns just the captured runs; no hidden count (only `hide`
    carries it)."""
    _seed_origin_mix(trace_db)
    body = client.get('/api/sessions?workflow=only').get_json()
    assert _ids(body) == {'run_x', 'run_y'}
    assert body['workflow_hidden_count'] is None


def test_sessions_workflow_hide_count_respects_other_filters(client, trace_db):
    """The hidden count runs the SAME shared WHERE set as the page query, so a
    workflow row excluded by another filter (here: `since`) is NOT counted."""
    # run_x is recent, run_y is older than the `since` bound.
    _seed_origin_mix(trace_db)
    body = client.get(
        '/api/sessions?workflow=hide&since=2026-01-01T00:00:02').get_json()
    # sess_a/sess_b are recent enough; run_y is below `since` so it isn't
    # among the hidden runs the filtered page would have shown.
    assert _ids(body) == {'sess_a', 'sess_b'}
    assert body['workflow_hidden_count'] == 1   # only run_x, not run_y


def test_sessions_rows_carry_origin_and_is_workflow(client, trace_db):
    """Each row exposes the orthogonal axes: `origin` (defaulting to 'session'
    for NULL legacy rows) and the derived `is_workflow` boolean. agent_type is
    vendor-only now, so a run reads as agent_kind='claude', not 'workflow'."""
    _seed_origin_mix(trace_db)
    rows = {it['trace_id']: it for it in client.get('/api/sessions').get_json()['items']}
    assert rows['sess_a']['origin'] == 'session'
    assert rows['sess_a']['is_workflow'] is False
    assert rows['run_x']['origin'] == 'workflow'
    assert rows['run_x']['is_workflow'] is True
    assert rows['run_x']['agent_kind'] == 'claude'   # never 'workflow'


def test_sessions_null_origin_reads_as_session(client, trace_db):
    """A legacy row with NULL origin is treated as an interactive session:
    `hide` keeps it, `only` drops it, and it reads back origin='session'."""
    _seed_session_row(trace_db, 'legacy', origin=None, agent_type='claude')
    hidden = client.get('/api/sessions?workflow=hide').get_json()
    assert _ids(hidden) == {'legacy'}
    assert hidden['workflow_hidden_count'] == 0
    only = client.get('/api/sessions?workflow=only').get_json()
    assert _ids(only) == set()
    row = client.get('/api/sessions').get_json()['items'][0]
    assert row['origin'] == 'session' and row['is_workflow'] is False
