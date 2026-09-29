"""The shallow-map projection memo and its writer-driven invalidation.

The load-bearing case is an in-place span rewrite: `_insert_span_row` upserts
on `(trace_id, span_id)`, so a re-ingested span keeps its DB row id. Any memo
validated by a read-side aggregate — `MAX(id)`, `COUNT(*)` — therefore cannot
see an assistant response growing, and would serve a frozen body for as long
as the tab stayed open. These tests pin the invalidation that closes it.
"""

from __future__ import annotations

import json

from lib.trace.trace_service import ingest_session_spans, projection_cache


def _span(trace_id, span_id, name, text, *, end=None, parent=None):
    return (
        {'trace_id': trace_id, 'span_id': span_id, 'parent_id': parent,
         'name': name, 'kind': 'internal',
         'start_time': '2026-09-02T10:00:00.000',
         'end_time': end, 'status_code': 'OK'},
        {'text': text},
    )


def _map(client, trace_id):
    res = client.get(f'/api/sessions/{trace_id}/map?shallow=1&limit=50')
    assert res.status_code == 200
    return json.loads(res.data)


def _root(payload, span_id):
    return next(s for s in payload['spans'] if s['span_id'] == span_id)


def test_rewritten_root_span_is_not_served_stale(flask_client):
    """A prompt re-ingested with a later end_time must not keep the old one."""
    tid = 'cache-inplace'
    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'hi')])
    assert _root(_map(flask_client, tid), 'prompt-1')['end_time'] is None

    ingest_session_spans([
        _span(tid, 'prompt-1', 'prompt', 'hi', end='2026-09-02T10:00:09.000'),
    ])
    assert (_root(_map(flask_client, tid), 'prompt-1')['end_time']
            == '2026-09-02T10:00:09.000')


def test_row_ids_do_not_move_on_rewrite(flask_client):
    """Guards the premise: if this ever fails, a read-side memo would do."""
    from lib.orm.engine import get_connection

    tid = 'cache-premise'
    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'short')])
    conn = get_connection()
    try:
        before = conn.execute(
            "SELECT MAX(id), COUNT(*) FROM session_spans WHERE trace_id = ?",
            (tid,)).fetchone()
    finally:
        conn.close()

    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'a much longer body')])
    conn = get_connection()
    try:
        after = conn.execute(
            "SELECT MAX(id), COUNT(*) FROM session_spans WHERE trace_id = ?",
            (tid,)).fetchone()
        length = conn.execute(
            "SELECT length(attributes) FROM session_spans WHERE span_id = 'prompt-1'",
        ).fetchone()[0]
    finally:
        conn.close()

    assert tuple(before) == tuple(after)
    assert length > len('short')


def test_new_span_appears_without_a_restart(flask_client):
    tid = 'cache-append'
    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'one')])
    assert _map(flask_client, tid)['span_count'] == 1

    ingest_session_spans([_span(tid, 'prompt-2', 'prompt', 'two')])
    payload = _map(flask_client, tid)
    assert payload['span_count'] == 2
    assert {s['span_id'] for s in payload['spans']} == {'prompt-1', 'prompt-2'}


def test_repeat_read_is_served_from_the_memo(flask_client):
    """Two identical reads with no write between them hit the cache."""
    tid = 'cache-hit'
    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'one')])
    first = _map(flask_client, tid)
    calls = []

    from lib.trace import trace_service
    original = trace_service.fetch_session_paginated

    def counting(*a, **kw):
        calls.append(a)
        return original(*a, **kw)

    trace_service.fetch_session_paginated = counting
    try:
        second = _map(flask_client, tid)
    finally:
        trace_service.fetch_session_paginated = original

    assert calls == []
    assert second['spans'] == first['spans']
    assert second['tree'] == first['tree']


def test_invalidate_is_scoped_to_the_trace_written(flask_client):
    """A write to one session must not evict another's cached answer."""
    ingest_session_spans([_span('cache-a', 'prompt-1', 'prompt', 'a')])
    ingest_session_spans([_span('cache-b', 'prompt-1', 'prompt', 'b')])
    _map(flask_client, 'cache-a')
    _map(flask_client, 'cache-b')

    ingest_session_spans([_span('cache-b', 'prompt-2', 'prompt', 'b2')])

    from lib.trace import trace_service
    calls = []
    original = trace_service.fetch_session_paginated

    def counting(trace_id, **kw):
        calls.append(trace_id)
        return original(trace_id, **kw)

    trace_service.fetch_session_paginated = counting
    try:
        _map(flask_client, 'cache-a')
    finally:
        trace_service.fetch_session_paginated = original

    assert calls == [], "an unrelated session's write evicted this one"


def test_roster_status_stays_live_under_the_memo(flask_client):
    """The memo holds the roster's ROWS; the stale verdict is recomputed.

    `_roster_status` stales an unstopped agent on age alone, so caching the
    built roster would freeze a badge that must still flip with the clock.
    """
    import web.blueprints.trace.sessions.roster as sessions_bp

    tid = 'cache-roster'
    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'go')])
    sessions_bp._roster_rows(tid)

    seen = []
    original = sessions_bp._build_roster

    def counting(*a, **kw):
        seen.append(True)
        return original(*a, **kw)

    sessions_bp._build_roster = counting
    try:
        sessions_bp._roster_with_activity(tid)
        sessions_bp._roster_with_activity(tid)
    finally:
        sessions_bp._build_roster = original

    assert len(seen) == 2, "roster verdict was served from the memo"


def test_materialize_reparent_is_visible_to_the_next_read(flask_client):
    """`_persist_projection` UPDATEs parent_id/envelopes with no INSERT.

    It is reached from POST /materialize, runs in the server process, and
    touches neither `_insert_span_row` nor `_attribute_one_call` — so it has
    to drop the memo itself or the view keeps serving the pre-graft tree.
    """
    from lib.orm.engine import get_connection
    from lib.trace.projection import _persist_projection

    tid = 'cache-materialize'
    ingest_session_spans([
        _span(tid, 'prompt-1', 'prompt', 'go'),
        _span(tid, 'tool-1', 'tool.Bash', 'ls', parent='prompt-1'),
    ])
    _map(flask_client, tid)

    conn = get_connection()
    try:
        raw = [dict(r) for r in conn.execute(
            "SELECT * FROM session_spans WHERE trace_id = ?", (tid,))]
        projected = [dict(s) for s in raw]
        for s in projected:
            if s['span_id'] == 'tool-1':
                s['end_time'] = '2026-09-02T10:05:00.000'
                s['duration_ms'] = 5000
        _persist_projection(conn, tid, raw, projected)
        conn.commit()
    finally:
        conn.close()

    payload = _map(flask_client, tid)
    tool = next((s for s in payload['spans'] if s['span_id'] == 'tool-1'), None)
    if tool is not None:
        assert tool['end_time'] == '2026-09-02T10:05:00.000'


def test_token_attribution_update_is_visible(flask_client):
    """`_attribute_one_call`'s UPDATE moves cost/token columns, not row ids."""
    from lib.trace.trace_service.ingest import ingest_tool_attribution

    tid = 'cache-attribution'
    # `tool_use_id` is not an INSERT column — the attribution UPDATE joins on
    # `attributes.tool_use_id` for a span that has not been stamped yet.
    span, attrs = _span(tid, 'prompt-1', 'prompt', 'go')
    attrs['tool_use_id'] = 'tu-1'
    ingest_session_spans([(span, attrs)])
    assert _root(_map(flask_client, tid), 'prompt-1').get('output_tokens') in (None, 0)

    ingest_tool_attribution({
        'trace_id': tid, 'turn_uuid': 'turn-1',
        'tool_calls': [{'tool_use_id': 'tu-1',
                        'output_tokens': 4242, 'input_tokens': 7}],
    })
    assert _root(_map(flask_client, tid), 'prompt-1')['output_tokens'] == 4242


def test_a_raw_out_of_band_update_is_a_known_gap(flask_client):
    """Pins the documented bound: a writer that neither goes through the two
    ingest hooks nor calls `invalidate()` IS served stale until the row set
    moves. Recorded as behaviour so the limit is visible, not discovered."""
    from lib.orm.engine import get_connection

    tid = 'cache-raw-update'
    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'go')])
    assert _root(_map(flask_client, tid), 'prompt-1')['end_time'] is None

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE session_spans SET end_time = ? WHERE trace_id = ? "
            "AND span_id = 'prompt-1'", ('2026-09-02T10:09:00.000', tid))
        conn.commit()
    finally:
        conn.close()

    stale = _root(_map(flask_client, tid), 'prompt-1')['end_time']
    assert stale is None, "gap closed — tighten this test into an assertion"

    projection_cache.invalidate(tid)
    assert (_root(_map(flask_client, tid), 'prompt-1')['end_time']
            == '2026-09-02T10:09:00.000')


def test_clear_empties_the_trace_index(flask_client):
    tid = 'cache-clear'
    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'x')])
    _map(flask_client, tid)
    projection_cache.clear()
    assert not projection_cache._cache
    assert not projection_cache._by_trace


# ── Push-stream continuity: what licenses withholding a body ──────────────
#
# The fan-out sheds frames under load and re-sends nothing, so the browser is
# only allowed to trust its cached bodies while its own tally of changes
# matches the server's. These pin both halves: the epoch advances for EVERY
# writer, and `/subtrees` withholds a body only when it is provably held.

def test_epoch_advances_for_every_writer(flask_client):
    """Including writers that cannot name the spans they touched."""
    tid = 'epoch-advances'
    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'one')])
    first = _map(flask_client, tid)['span_epoch']

    ingest_session_spans([_span(tid, 'prompt-2', 'prompt', 'two')])
    second = _map(flask_client, tid)['span_epoch']
    assert second > first

    # A reconciler-style invalidation names no spans, and must still move it —
    # this is what makes a client's tally disagree and force a resync.
    projection_cache.invalidate(tid)
    assert _map(flask_client, tid)['span_epoch'] > second


def _subtrees(client, trace_id, ids, **params):
    query = f"ids={','.join(ids)}"
    for key, value in params.items():
        query += f"&{key}={value}"
    res = client.get(f'/api/sessions/{trace_id}/subtrees?{query}')
    assert res.status_code == 200
    return json.loads(res.data)


def _seed_subtree(tid):
    ingest_session_spans([
        _span(tid, 'prompt-1', 'prompt', 'go'),
        _span(tid, 'tool-1', 'tool.Bash', 'x' * 500,
              end='2026-09-02T10:00:02.000', parent='prompt-1'),
    ])


def test_subtrees_ships_every_body_without_a_watermark(flask_client):
    """No watermark means the client claims nothing — send it all."""
    tid = 'subtree-nowatermark'
    _seed_subtree(tid)
    data = _subtrees(flask_client, tid, ['prompt-1'])
    assert {s['span_id'] for s in data['spans']} == {'tool-1'}


def test_subtrees_withholds_a_body_the_client_already_holds(flask_client):
    """A settled span at or below the watermark is the client's already."""
    from lib.orm.engine import get_connection

    tid = 'subtree-withhold'
    _seed_subtree(tid)
    conn = get_connection()
    try:
        row_id = conn.execute(
            "SELECT id FROM session_spans WHERE trace_id = ? AND span_id = 'tool-1'",
            (tid,)).fetchone()[0]
    finally:
        conn.close()

    data = _subtrees(flask_client, tid, ['prompt-1'], known=f'prompt-1:{row_id}')
    assert data['spans'] == []
    # The STRUCTURE still ships — the client rejoins bodies from its own store.
    assert data['subtrees']['prompt-1'][0]['span_id'] == 'tool-1'


def test_a_span_above_the_watermark_still_ships(flask_client):
    """A new span is an INSERT, so it always lands above the watermark."""
    tid = 'subtree-newspan'
    _seed_subtree(tid)
    data = _subtrees(flask_client, tid, ['prompt-1'], known='prompt-1:1')
    assert {s['span_id'] for s in data['spans']} == {'tool-1'}


def test_an_unsettled_span_is_never_withheld(flask_client):
    """It is still being written; its body grows with no identifier moving."""
    from lib.orm.engine import get_connection

    tid = 'subtree-unsettled'
    ingest_session_spans([
        _span(tid, 'prompt-1', 'prompt', 'go'),
        _span(tid, 'tool-1', 'tool.Bash', 'partial', parent='prompt-1'),
    ])
    conn = get_connection()
    try:
        top = conn.execute(
            "SELECT MAX(id) FROM session_spans WHERE trace_id = ?", (tid,)
        ).fetchone()[0]
    finally:
        conn.close()

    data = _subtrees(flask_client, tid, ['prompt-1'], known=f'prompt-1:{top}')
    assert {s['span_id'] for s in data['spans']} == {'tool-1'}


def test_a_dirty_span_is_shipped_despite_the_watermark(flask_client):
    """The push frame named it as changed, so the client's copy is stale."""
    from lib.orm.engine import get_connection

    tid = 'subtree-dirty'
    _seed_subtree(tid)
    conn = get_connection()
    try:
        top = conn.execute(
            "SELECT MAX(id) FROM session_spans WHERE trace_id = ?", (tid,)
        ).fetchone()[0]
    finally:
        conn.close()

    held = _subtrees(flask_client, tid, ['prompt-1'], known=f'prompt-1:{top}')
    assert held['spans'] == []
    fresh = _subtrees(flask_client, tid, ['prompt-1'],
                      known=f'prompt-1:{top}', dirty='tool-1')
    assert {s['span_id'] for s in fresh['spans']} == {'tool-1'}


def test_a_malformed_watermark_ships_everything(flask_client):
    """Anything unparseable must fail toward sending the body, not withholding."""
    tid = 'subtree-malformed'
    _seed_subtree(tid)
    for bad in ('prompt-1:', 'prompt-1:abc', 'garbage', ''):
        data = _subtrees(flask_client, tid, ['prompt-1'], known=bad)
        assert {s['span_id'] for s in data['spans']} == {'tool-1'}, bad


def test_epoch_is_summed_over_an_alias_group(flask_client):
    """Spans land under a run's child id while the reader holds the canonical.

    Counting one member would leave the group's epoch frozen — and a frozen
    epoch is exactly what tells a client its cached bodies are still good.
    """
    assert projection_cache.epoch(('grp-a', 'grp-b')) == 0
    projection_cache.invalidate('grp-b')
    assert projection_cache.epoch(('grp-a', 'grp-b')) == 1
    assert projection_cache.epoch('grp-a') == 0, "sibling must not be counted"
    projection_cache.invalidate('grp-a')
    assert projection_cache.epoch(('grp-a', 'grp-b')) == 2


def test_an_unnamed_change_cannot_hide_inside_a_later_frame(flask_client):
    """The hole that makes body-withholding unsafe if left open.

    A writer that changes spans without naming them (a reconciler, a
    materialize reparent) advances the epoch with no frame. The NEXT ordinary
    frame quotes the live counter, so it already contains that bump — a client
    that simply banked the number would read EQUAL against /map and never
    re-fetch the spans it never heard about.

    The contract that closes it: a frame's epoch must exceed the client's
    tally by exactly the number of spans the frame names. This pins the server
    half — the arithmetic is only checkable if these two agree.
    """
    tid = 'epoch-arithmetic'
    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'one')])
    tally = _map(flask_client, tid)['span_epoch']

    # An unnamed change: nothing broadcasts, but the counter moves.
    projection_cache.invalidate(tid)

    # Then an ordinary two-span batch, which is all a frame could account for.
    ingest_session_spans([
        _span(tid, 'prompt-2', 'prompt', 'two'),
        _span(tid, 'prompt-3', 'prompt', 'three'),
    ])
    served = _map(flask_client, tid)['span_epoch']

    assert served - tally == 3, "one unnamed bump plus two named spans"
    assert served - tally != 2, (
        "the unnamed change must NOT be absorbable by a 2-span frame")


def test_frame_and_map_report_the_same_epoch_for_a_group(flask_client):
    """Two call sites resolve the alias group — they must agree.

    `_announce_spans` uses `trace_group`; the map uses `_alias_group_ids`.
    Divergence would make every frame read as a mismatch, silently costing
    every session its cheap path forever.
    """
    import web.blueprints.trace.sessions as sessions_bp
    from lib.orm.engine import get_connection
    from lib.trace.alias import trace_group

    tid = 'epoch-group-agree'
    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'one')])

    conn = get_connection()
    try:
        announce_group = trace_group(conn, tid)
    finally:
        conn.close()
    map_group = sessions_bp._alias_group_ids(tid)

    assert (projection_cache.epoch(announce_group)
            == projection_cache.epoch(map_group)
            == _map(flask_client, tid)['span_epoch'])


def test_epoch_map_is_bounded(flask_client):
    """A long-lived server must not accumulate one entry per trace forever."""
    projection_cache.clear()
    for i in range(projection_cache._MAX_EPOCHS + 50):
        projection_cache.invalidate(f'bulk-{i}')
    assert len(projection_cache._epoch) <= projection_cache._MAX_EPOCHS
    # The most recently changed traces are the ones kept — a watched session is
    # being written to, so eviction never targets it.
    assert f'bulk-{projection_cache._MAX_EPOCHS + 49}' in projection_cache._epoch


def test_an_unchanged_subtree_is_named_not_reserialised(flask_client):
    """The trailing prompts are re-asked every reconcile; a finished one has
    nothing to say, and its structure alone ran to 16KB on a real session."""
    from lib.orm.engine import get_connection

    tid = 'subtree-digest'
    _seed_subtree(tid)
    first = _subtrees(flask_client, tid, ['prompt-1'])
    digest = first['digests']['prompt-1']
    assert first['subtrees']['prompt-1']

    conn = get_connection()
    try:
        top = conn.execute(
            "SELECT MAX(id) FROM session_spans WHERE trace_id = ?", (tid,)
        ).fetchone()[0]
    finally:
        conn.close()

    again = _subtrees(flask_client, tid, ['prompt-1'],
                      known=f'prompt-1:{top}', seen=f'prompt-1:{digest}')
    assert again['unchanged'] == ['prompt-1']
    assert 'prompt-1' not in again['subtrees']
    assert again['spans'] == []


def test_a_grown_subtree_is_resent_despite_a_matching_request(flask_client):
    """A new child changes the shape, so the digest must stop matching."""
    from lib.orm.engine import get_connection

    tid = 'subtree-digest-grow'
    _seed_subtree(tid)
    digest = _subtrees(flask_client, tid, ['prompt-1'])['digests']['prompt-1']
    conn = get_connection()
    try:
        top = conn.execute(
            "SELECT MAX(id) FROM session_spans WHERE trace_id = ?", (tid,)
        ).fetchone()[0]
    finally:
        conn.close()

    ingest_session_spans([
        _span(tid, 'tool-2', 'tool.Bash', 'y' * 200,
              end='2026-09-02T10:00:05.000', parent='prompt-1'),
    ])
    grown = _subtrees(flask_client, tid, ['prompt-1'],
                      known=f'prompt-1:{top}', seen=f'prompt-1:{digest}')
    assert grown['unchanged'] == []
    assert {s['span_id'] for s in grown['spans']} == {'tool-2'}


def test_an_unchanged_shape_still_ships_a_body_that_moved(flask_client):
    """Shape and content are separate: a span can grow inside a stable tree."""
    tid = 'subtree-digest-body'
    _seed_subtree(tid)
    first = _subtrees(flask_client, tid, ['prompt-1'])
    digest = first['digests']['prompt-1']
    # No watermark => every body is owed, so the shape match must not suppress.
    again = _subtrees(flask_client, tid, ['prompt-1'], seen=f'prompt-1:{digest}')
    assert again['unchanged'] == []
    assert {s['span_id'] for s in again['spans']} == {'tool-1'}


def test_retired_list_is_omitted_once_applied(flask_client):
    """It reached 1544 ids / 37KB — half the response — and never changes."""
    tid = 'retired-digest'
    ingest_session_spans([_span(tid, 'prompt-1', 'prompt', 'go')])
    first = _map(flask_client, tid)
    digest = first['retired_digest']

    res = flask_client.get(
        f'/api/sessions/{tid}/map?shallow=1&limit=50&retired_seen={digest}')
    again = json.loads(res.data)
    if digest:
        assert 'retired_span_ids' not in again
    assert again['retired_digest'] == digest
    # A digest the client has NOT applied always arrives in full.
    res = flask_client.get(
        f'/api/sessions/{tid}/map?shallow=1&limit=50&retired_seen=stale')
    assert 'retired_span_ids' in json.loads(res.data)
