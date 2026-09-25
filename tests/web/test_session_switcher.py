"""`GET /api/session-switcher` — the ⌘J roster (web/blueprints/trace/switcher.py).

One payload feeds the palette's Sessions facet and the trace-view status rail:
recent interactive sessions, the server phase verdict, what a waiting session
wants, and a short span tail.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta

import pytest

from web.blueprints.trace import switcher as SW


def _seed_session(conn, trace_id, *, last_seen, status='active',
                  is_test=0, origin='session', title=None, prompts=0,
                  cwd=None):
    conn.execute(
        "INSERT INTO sessions (trace_id, started_at, last_seen, status, "
        " is_test, origin, title, prompts, cwd) "
        "VALUES (?, '2026-01-01T00:00:00', ?, ?, ?, ?, ?, ?, ?)",
        (trace_id, last_seen, status, is_test, origin, title, prompts, cwd))


def _seed_span(conn, trace_id, span_id, name, *, start, status='OK', attrs=None):
    conn.execute(
        "INSERT INTO session_spans "
        "(trace_id, span_id, name, kind, start_time, status_code, attributes) "
        "VALUES (?, ?, ?, 'internal', ?, ?, ?)",
        (trace_id, span_id, name, start, status, json.dumps(attrs or {})))


# The phase verdict is evaluated against the wall clock (a blocker only counts
# while the session is still active), so the fixture stamps relative to *now*
# rather than at a fixed date — a frozen calendar date would read as stale and
# demote every pending blocker. `now` is captured per TEST, not at import: the
# working window is 12s wide, and under `-n auto` a module-level constant is
# minutes stale by the time the last test in the file runs.
@pytest.fixture
def ago():
    now = datetime.now()
    return lambda seconds: (now - timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def seeded(tmp_db, ago):
    _ago = ago
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    try:
        _seed_session(conn, 'live-1', last_seen=_ago(2),
                      title='Add rate limiting', prompts=7, cwd='/tmp/api-gateway')
        _seed_session(conn, 'live-2', last_seen=_ago(30),
                      title='PDF renderer swap', prompts=3, cwd='/tmp/billing')
        _seed_session(conn, 'gone-1', last_seen=_ago(3600),
                      status='ended', title='Finished work', cwd='/tmp/done')
        # Excluded populations: a Playwright fixture and a captured run.
        _seed_session(conn, 'fixture-1', last_seen=_ago(1),
                      is_test=1, title='playwright', cwd='/tmp/x')
        _seed_session(conn, 'run-1', last_seen=_ago(1),
                      origin='workflow', title='a workflow run', cwd='/tmp/x')
        # The column was added late; every pre-existing session reads NULL and
        # must still be offered.
        _seed_session(conn, 'legacy-1', last_seen=_ago(120),
                      origin=None, title='Traced before origin existed',
                      cwd='/tmp/legacy')
        # Spans POSTed by hand against a made-up trace id: no cwd, no prompt.
        # Not somewhere a human can be, so not something to attach to.
        _seed_session(conn, 'manual-gate', last_seen=_ago(5), cwd=None)

        for i in range(10):
            _seed_span(conn, 'live-1', f'a{i}', 'tool.Read',
                       start=_ago(100 - i * 10),
                       attrs={'file_path': f'src/f{i}.ts'})
        _seed_span(conn, 'live-2', 'b1', 'tool.AskUserQuestion',
                   start=_ago(30), status='PENDING',
                   attrs={'question': 'Delete the vendored binary?'})
        conn.commit()
    finally:
        conn.close()
    return tmp_db


# The route is deliberately NOT /api/sessions/switcher — that path is already
# claimed by /api/sessions/<trace_id>, which would resolve "switcher" as a
# trace id and answer 200 with an empty tree.
def _roster(flask_client, **params):
    qs = '&'.join(f'{k}={v}' for k, v in params.items())
    resp = flask_client.get(f'/api/session-switcher{"?" + qs if qs else ""}')
    assert resp.status_code == 200
    return resp.get_json()


def test_roster_ranks_by_state_then_recency(seeded, flask_client):
    """Recency alone is the wrong cut for a "who needs me" surface: the session
    parked on a question outranks the one merely touched more recently."""
    data = _roster(flask_client)
    ids = [s['trace_id'] for s in data['sessions']]
    #     waiting-input  working   idle        ended
    assert ids == ['live-2', 'live-1', 'legacy-1', 'gone-1']
    assert data['server_now']
    assert data['server_now_utc']


def test_roster_excludes_test_fixtures_and_captured_runs(seeded, flask_client):
    ids = {s['trace_id'] for s in _roster(flask_client)['sessions']}
    assert 'fixture-1' not in ids
    assert 'run-1' not in ids


def test_roster_excludes_sessions_with_no_working_directory(seeded, flask_client):
    """Attaching means opening a session you can be *inside*. A row with no
    cwd — hand-POSTed spans against a made-up trace id — is not one, and left
    in it crowds real sessions out at the cap. It stays in the session LIST,
    which answers a different question."""
    ids = {s['trace_id'] for s in _roster(flask_client)['sessions']}
    assert 'manual-gate' not in ids
    assert 'live-1' in ids


def _row(flask_client, trace_id, **params):
    return next(s for s in _roster(flask_client, **params)['sessions']
                if s['trace_id'] == trace_id)


def test_row_carries_identity_and_phase(seeded, flask_client):
    row = _row(flask_client, 'live-1')
    assert row['title'] == 'Add rate limiting'
    assert row['turn'] == 7
    assert row['cwd'] == '/tmp/api-gateway'
    # Its newest span landed 10s ago — inside the working window.
    assert row['phase'] == 'working'
    assert row['needs'] is None


def test_ended_session_phases_as_ended(seeded, flask_client):
    row = next(s for s in _roster(flask_client)['sessions']
               if s['trace_id'] == 'gone-1')
    assert row['phase'] == 'ended'
    assert row['needs'] is None


def test_tail_is_capped_and_oldest_first(seeded, ago, flask_client):
    row = _row(flask_client, 'live-1', tail=1)
    assert len(row['tail']) == SW.TAIL_ROWS
    stamps = [t['ts'] for t in row['tail']]
    assert stamps == sorted(stamps)
    # The newest span is the last row — that is what the list quotes as
    # "current activity".
    assert row['tail'][-1]['ts'] == ago(10)
    assert row['tail'][-1]['kind']
    assert 'src/f9.ts' in row['tail'][-1]['text']


def test_tail_is_omitted_unless_asked_for(seeded, flask_client):
    """The tail is two thirds of the payload and only the palette's preview
    renders it. The rail holds the ambient 10s poll and never reads it, so the
    default roster must not carry it."""
    assert 'tail' not in _row(flask_client, 'live-1')
    assert 'tail' in _row(flask_client, 'live-1', tail=1)


def test_waiting_session_reports_what_it_wants(seeded, flask_client):
    row = next(s for s in _roster(flask_client)['sessions']
               if s['trace_id'] == 'live-2')
    assert row['phase'] == 'waiting-input'
    assert row['needs'] == 'Delete the vendored binary?'


def test_priority_covers_every_phase():
    """An unranked phase silently falls to the bottom of the roster, so the
    ranking table must name every verdict `_session_phase` can return."""
    from web.blueprints.trace.sessions import _PHASE_PRECEDENCE
    assert set(_PHASE_PRECEDENCE) == set(SW._PRIORITY)


def test_a_waiting_session_survives_the_limit(seeded, ago, flask_client):
    """The trim happens AFTER ranking, so `limit=1` keeps the session that
    wants something — not merely the one touched last."""
    only = _roster(flask_client, limit=1)['sessions']
    assert [s['trace_id'] for s in only] == ['live-2']


def test_limit_is_clamped(seeded, flask_client):
    assert len(_roster(flask_client, limit=1)['sessions']) == 1
    # Junk and over-cap values fall back rather than 500.
    assert len(_roster(flask_client, limit='abc')['sessions']) == 4
    assert len(_roster(flask_client, limit=9999)['sessions']) == 4


def test_the_route_is_not_shadowed_by_the_session_detail_route(flask_client):
    """/api/sessions/<trace_id> matches any single segment, so a nested path
    would silently resolve as a session id rather than reach this handler."""
    shadowed = flask_client.get('/api/sessions/switcher').get_json()
    assert 'sessions' not in shadowed
    assert 'sessions' in _roster(flask_client)


def test_empty_store_returns_an_empty_roster(flask_client):
    data = _roster(flask_client)
    assert data['sessions'] == []
    assert data['server_now']


# --- `?id=<prefix>` — the palette's `#<session-id>` address sigil ------------
#
# An address is not a query: it must resolve whatever it names, including the
# populations the roster deliberately curates away (a finished run, a session
# with no cwd, a captured workflow). The roster's filter exists to make a
# *choose* surface worth choosing from; nothing about it should decide whether
# an id someone pasted is reachable.


def test_id_prefix_resolves_one_session(seeded, flask_client):
    data = _roster(flask_client, id='live-2')
    assert [s['trace_id'] for s in data['sessions']] == ['live-2']
    assert data['sessions'][0]['title'] == 'PDF renderer swap'


def test_id_prefix_matches_a_prefix_not_only_a_whole_id(seeded, flask_client):
    ids = {s['trace_id'] for s in _roster(flask_client, id='live')['sessions']}
    assert ids == {'live-1', 'live-2'}


def test_id_lookup_reaches_what_the_roster_curates_away(seeded, flask_client):
    """The roster drops a cwd-less session and a captured run because neither
    is worth OFFERING. Both are still addressable."""
    roster = {s['trace_id'] for s in _roster(flask_client)['sessions']}
    assert 'manual-gate' not in roster and 'run-1' not in roster
    assert [s['trace_id']
            for s in _roster(flask_client, id='manual-gate')['sessions']] \
        == ['manual-gate']
    assert [s['trace_id']
            for s in _roster(flask_client, id='run-1')['sessions']] == ['run-1']


def test_id_lookup_still_excludes_test_fixtures(seeded, flask_client):
    """A Playwright fixture is not a session anyone attaches to under any
    addressing."""
    assert _roster(flask_client, id='fixture-1')['sessions'] == []


def test_id_shorter_than_the_floor_answers_nothing(seeded, flask_client):
    """Below `MIN_ID_CHARS` the prefix is still mid-paste; answering it with
    every session whose id starts with `l` is noise, not an address."""
    assert _roster(flask_client, id='li')['sessions'] == []
    assert len(_roster(flask_client, id='live')['sessions']) == 2


def test_id_lookup_returns_no_match_rather_than_falling_back(seeded, flask_client):
    """A prefix that names nothing must NOT quietly answer with the roster —
    that would attach the reader to a session they did not ask for."""
    assert _roster(flask_client, id='no-such-session')['sessions'] == []


def test_id_lookup_carries_the_tail_when_asked(seeded, flask_client):
    plain = _roster(flask_client, id='live-1')['sessions'][0]
    assert 'tail' not in plain
    tailed = _roster(flask_client, id='live-1', tail=1)['sessions'][0]
    assert [row['name'] for row in tailed['tail']] == ['tool.Read'] * SW.TAIL_ROWS


def test_id_lookup_phases_like_the_roster(seeded, flask_client):
    """Same verdict either way — a session cannot be `ended` in one surface and
    running in the other."""
    assert _roster(flask_client, id='gone-1')['sessions'][0]['phase'] == 'ended'


def test_like_wildcards_in_a_pasted_id_are_literals(tmp_db, ago, flask_client):
    """`_` is a single-character LIKE wildcard, and trace ids are not always
    uuids — `manual_gate` must not match `manualXgate`."""
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    try:
        _seed_session(conn, 'manual_gate', last_seen=ago(5), cwd='/tmp/a')
        _seed_session(conn, 'manualXgate', last_seen=ago(4), cwd='/tmp/b')
        conn.commit()
    finally:
        conn.close()
    ids = [s['trace_id'] for s in _roster(flask_client, id='manual_g')['sessions']]
    assert ids == ['manual_gate']


def test_an_alias_pair_resolves_to_one_row(tmp_db, ago, flask_client):
    """A regin-launched run carries spans under two trace ids. Either is a
    legitimate thing to paste, and both name ONE session — so a prefix hitting
    the pair must not render it twice."""
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    try:
        _seed_session(conn, 'sdk-abc123', last_seen=ago(3), cwd='/tmp/run')
        _seed_session(conn, 'sdk-child9', last_seen=ago(2), cwd='/tmp/run')
        conn.execute(
            "INSERT INTO agent_runs (trace_id, cli_session_id, status) "
            "VALUES ('sdk-abc123', 'sdk-child9', 'running')")
        conn.commit()
    finally:
        conn.close()
    ids = [s['trace_id'] for s in _roster(flask_client, id='sdk-')['sessions']]
    assert ids == ['sdk-child9']


def test_a_runner_side_id_answers_with_the_canonical_row(tmp_db, ago, flask_client):
    """Pasting the id regin launched a run under must resolve to the session
    that id NAMES, not to the runner's own bookkeeping row — otherwise the
    right link renders under the wrong title and turn count."""
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    try:
        _seed_session(conn, 'sdk-runner7', last_seen=ago(9),
                      title='launcher bookkeeping', prompts=0, cwd='/tmp/run')
        _seed_session(conn, 'child-uuid7', last_seen=ago(8),
                      title='Rewrite the ingest', prompts=5, cwd='/tmp/run')
        conn.execute(
            "INSERT INTO agent_runs (trace_id, cli_session_id, status) "
            "VALUES ('sdk-runner7', 'child-uuid7', 'running')")
        conn.commit()
    finally:
        conn.close()
    rows = _roster(flask_client, id='sdk-runner7')['sessions']
    assert [r['trace_id'] for r in rows] == ['child-uuid7']
    assert rows[0]['title'] == 'Rewrite the ingest'
    assert rows[0]['turn'] == 5


def test_folding_an_alias_pair_does_not_cost_the_page_a_row(tmp_db, ago,
                                                            flask_client):
    """The SQL limit is loose and the cap is applied after the fold: trimming
    first would let one alias pair silently return 11 rows for a 12-row page."""
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    try:
        for n in range(SW.ID_LOOKUP_LIMIT + 2):
            _seed_session(conn, f'pfx-{n:03d}', last_seen=ago(100 + n),
                          title=f'session {n}', cwd='/tmp/x')
        # Two of them are one session under two ids.
        conn.execute(
            "INSERT INTO agent_runs (trace_id, cli_session_id, status) "
            "VALUES ('pfx-000', 'pfx-001', 'running')")
        conn.commit()
    finally:
        conn.close()
    ids = [r['trace_id'] for r in _roster(flask_client, id='pfx-')['sessions']]
    assert len(ids) == SW.ID_LOOKUP_LIMIT
    assert len(set(ids)) == SW.ID_LOOKUP_LIMIT
    assert 'pfx-000' not in ids


def _js_const_map(path: str, name: str) -> dict:
    """The literal object assigned to `const <name> = {...}` in a JS module.

    Parsed rather than duplicated: the point of the test below is that the two
    tables cannot drift, and a hand-copied expectation here would be a third
    table to keep in sync.
    """
    import ast
    import re
    src = pathlib.Path(path).read_text()
    body = re.search(rf'const {name} = \{{(.*?)\n\}}', src, re.S).group(1)
    out = {}
    for key, val in re.findall(r"['\"]?([\w-]+)['\"]?:\s*'?([\w-]+)'?", body):
        out[key] = int(val) if val.isdigit() else val
    return out


def test_priority_folds_onto_the_clients_state_rank():
    """`_PRIORITY` and the client's `STATE_RANK` must order the same way.

    Both files claim this in prose — the server ranks eight phases, the client
    folds them into states and re-sorts by that fold — but nothing enforced
    it, and the fold is only correct if it is MONOTONIC: two phases the server
    ranks in one order must never come back in the other after folding, or the
    roster keeps one session past the cap and the rail puts a different one
    under hotkey 1.
    """
    fold = _js_const_map('frontend/src/utils/sessionStatus.js', 'PHASE_STATUS')
    rank = _js_const_map('frontend/src/utils/sessionSwitcher.js', 'STATE_RANK')

    assert set(fold) == set(SW._PRIORITY), 'the fold must name every phase'
    ranks = [rank[fold[phase]] for phase in SW._PRIORITY]
    assert ranks == sorted(ranks), (
        f'fold is not monotonic: {list(zip(SW._PRIORITY, ranks))}')


def _js_set(path: str, name: str) -> set:
    """The literal `new Set([...])` assigned to `const <name>`."""
    import re
    src = pathlib.Path(path).read_text()
    body = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", src, re.S).group(1)
    return set(re.findall(r"'([\w-]+)'", body))


def test_every_live_run_phase_folds_to_an_active_status():
    """`live_session_ids()` feeds `_session_active_clause`, so every phase a
    session in that set can report must fold to a status the CLIENT also calls
    active — otherwise the `active_count` facet, the group header and the row's
    own chip disagree for exactly the sessions this feature exists to serve.

    This is what `_reserved` broke: it made a launching session server-active
    while `run_phase` reported it `starting`, which the client does not count.
    """
    from lib.agent_sdk import registry

    fold = _js_const_map('frontend/src/utils/sessionStatus.js', 'PHASE_STATUS')
    active = _js_set('frontend/src/utils/sessionStatus.js', 'ACTIVE_STATUSES')

    # Every branch of `run_phase` reachable once a runner is registered — the
    # only ids `live_session_ids` returns.
    for phase in ('waiting-permission', 'waiting-input', 'stopping',
                  'working', 'idle'):
        assert fold[phase] in active, (
            f'{phase} is server-active but folds to {fold[phase]}, '
            f'which the client does not count as active')

    # And the id set really is runner-backed only: a reserved launch reports
    # `starting`, which is NOT an active status.
    assert fold['starting'] not in active
    registry.reserve_run('res-only')
    try:
        assert 'res-only' not in registry.live_session_ids()
    finally:
        registry.release_run('res-only')


def test_a_topic_proposal_run_reaches_the_roster(tmp_db, ago, flask_client):
    """The proposal pipeline's own session is attachable history, not a
    hand-POSTed stub: its `session.start` must stamp a cwd, or the roster's
    no-cwd filter drops every proposal run. Its spans must also share the
    local basis `last_seen` is ordered on, or it sorts hours out of place."""
    from lib.topics.proposal_external import (
        _emit_session_start, external_trace_id,
    )
    import sqlite3
    conn = sqlite3.connect(str(tmp_db))
    try:
        _seed_session(conn, 'older-1', last_seen=ago(60), status='ended',
                      title='older work', cwd='/tmp/x')
        conn.commit()
    finally:
        conn.close()

    trace_id = external_trace_id('run42')
    _emit_session_start(trace_id, proposal_id='run42',
                        repo=pathlib.Path('/tmp/repo'), agent='claude')

    conn = sqlite3.connect(str(tmp_db))
    try:
        cwd, origin, last_seen = conn.execute(
            "SELECT cwd, origin, last_seen FROM sessions WHERE trace_id = ?",
            (trace_id,)).fetchone()
    finally:
        conn.close()
    assert cwd == '/tmp/repo'
    assert origin == 'topic-proposal'
    assert not last_seen.endswith('Z') and '+' not in last_seen

    ids = [s['trace_id'] for s in
           flask_client.get('/api/session-switcher').get_json()['sessions']]
    assert trace_id in ids
    assert ids.index(trace_id) < ids.index('older-1')
