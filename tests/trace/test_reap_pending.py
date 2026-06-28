"""Reaper for superseded PENDING placeholder spans (lib/trace/reap.py).

`session_spans` is append-only, so the live `promptlive-`/`pending-`/`permreq-`
placeholders only ever get HIDDEN by `merge_spans` at read time — they pile up
forever. `reap_pending_spans` is the prune path: it deletes exactly the rows
merge already hides AND whose removal leaves the merged view unchanged.

These tests are the golden spec for *which* rows are safe to delete:
  * the pure `reapable_span_ids` decision (no DB), and
  * the end-to-end `reap_pending_spans` DB sweep (dry-run, real delete,
    view-preservation, idempotency).
"""

from __future__ import annotations

import json

from lib.orm.engine import get_connection
from lib.trace.merge import merge_spans
from lib.trace.pending_spans import (
    prompt_placeholder_id,
    tool_pending_id,
)
from lib.trace.reap import (
    _view_signature,
    reap_pending_spans,
    reapable_span_ids,
)


def _row(span_id, name, attrs, *, status='UNSET', tid='t1', id=1):
    return {
        'id': id, 'trace_id': tid, 'span_id': span_id, 'parent_id': None,
        'name': name, 'kind': 'internal',
        'start_time': '2026-04-18T12:00:00', 'end_time': '2026-04-18T12:00:00',
        'duration_ms': 0, 'status_code': status, 'status_message': None,
        'attributes': attrs, 'turn_uuid': None,
    }


def _survivors(rows):
    return {s['span_id'] for s in merge_spans(rows)}


# ── reapable_span_ids: the pure decision ─────────────────────────

def test_no_pending_rows_reaps_nothing():
    """Acceptance 1: a fully-resolved window has nothing to reap."""
    rows = [_row('prompt-realuuid00001', 'prompt', {'text': 'hi'}, status='OK')]
    assert reapable_span_ids(rows) == []


def test_empty_window_reaps_nothing():
    assert reapable_span_ids([]) == []


def test_resolved_tool_pending_is_reapable():
    """Acceptance 2: a pending tool placeholder whose resolved span is present
    is reapable, and deleting it leaves the merged view byte-identical."""
    tu = 'toolu_abc123456789'
    rows = [
        _row(tool_pending_id(tu), 'tool.Bash',
             {'tool_use_id': tu, 'command_preview': 'ls'}, status='PENDING', id=1),
        _row('rand16hexabcdef0', 'tool.Bash',
             {'tool_use_id': tu, 'command_preview': 'ls'}, status='OK', id=2),
    ]
    before = _view_signature(merge_spans(rows))
    reap = reapable_span_ids(rows)
    assert reap == [tool_pending_id(tu)]

    survivors = [s for s in rows if s['span_id'] not in reap]
    after = _view_signature(merge_spans(survivors))
    assert after == before  # the rendered view did not change


def test_in_flight_pending_is_kept():
    """Acceptance 3: a placeholder whose resolved counterpart hasn't arrived is
    still live — never reaped."""
    tu = 'toolu_inflight9999'
    rows = [_row(tool_pending_id(tu), 'tool.Bash',
                 {'tool_use_id': tu, 'command_preview': 'sleep 99'},
                 status='PENDING', id=1)]
    assert reapable_span_ids(rows) == []
    assert _survivors(rows) == {tool_pending_id(tu)}  # still rendered


def test_slash_command_expansion_source_is_kept():
    """Acceptance 4: a slash-command `promptlive-` placeholder whose full
    expansion merge transfers onto its resolved echo anchor must NOT be reaped
    — deleting it would lose the expansion permanently."""
    expansion = '/goal-verified # full expansion body ' + 'x' * 500
    ph = prompt_placeholder_id('t1', expansion)
    rows = [
        _row(ph, 'prompt', {'text': expansion, 'live_placeholder': True},
             status='PENDING', id=1),
        _row('prompt-echo00000001', 'prompt', {'text': '/goal-verified'},
             status='OK', id=2),
        _row('prompt-next00000002', 'prompt', {'text': 'next turn'},
             status='OK', id=3),
    ]
    # merge already hides the placeholder (it's absorbed), but it is NOT safe to
    # physically delete: the expansion survives only because the row is present.
    assert ph not in _survivors(rows)
    assert reapable_span_ids(rows) == []
    # ...and the expansion is still rendered on the anchor after a (no-op) reap.
    anchor = next(s for s in merge_spans(rows) if s['span_id'] == 'prompt-echo00000001')
    assert anchor['attributes']['text'] == expansion


def test_normal_prompt_placeholder_is_reapable():
    """Acceptance 5: a plain (non-slash) placeholder whose resolved anchor
    carries the identical text loses nothing when deleted, so it is reaped."""
    text = 'plain user prompt with no slash command'
    ph = prompt_placeholder_id('t1', text)
    rows = [
        _row(ph, 'prompt', {'text': text, 'live_placeholder': True},
             status='PENDING', id=1),
        _row('prompt-realuuid00009', 'prompt', {'text': text}, status='OK', id=2),
    ]
    before = _view_signature(merge_spans(rows))
    assert reapable_span_ids(rows) == [ph]
    survivors = [s for s in rows if s['span_id'] != ph]
    assert _view_signature(merge_spans(survivors)) == before


def test_slow_tool_turn_linkage_source_is_kept():
    """Regression: a `pending-<tu>` tool placeholder that absorbed the turn
    linkage (`turn_uuid` + `resp-` parent) its resolved survivor never got is
    NOT reapable. `merge.py::_inherit_turn_linkage` transfers that linkage onto
    the survivor at READ time, so the placeholder row is the only copy —
    deleting it would flip the survivor off its assistant-response branch onto
    the prompt root. The signature must capture rendered parentage to catch
    this; otherwise the fast path would silently reap it."""
    tu = 'toolu_slowtool0001'
    ph = tool_pending_id(tu)
    placeholder = _row(ph, 'tool.Bash',
                       {'tool_use_id': tu, 'command_preview': 'slow'},
                       status='PENDING', id=1)
    placeholder['turn_uuid'] = 'turn-xyz'
    placeholder['parent_id'] = 'resp-turn-xyz'
    survivor = _row('resolvedtool00001', 'tool.Bash',
                    {'tool_use_id': tu, 'command_preview': 'slow'},
                    status='OK', id=2)  # turn_uuid stays None → triggers inherit
    rows = [placeholder, survivor]

    # merge hides the placeholder and hands its turn linkage to the survivor
    # (the dangling `resp-` parent then heals to None at graft, but the
    # inherited turn_uuid is durable and is what the signature catches)...
    merged = merge_spans(rows)
    assert ph not in {s['span_id'] for s in merged}
    resolved = next(s for s in merged if s['span_id'] == 'resolvedtool00001')
    assert resolved['turn_uuid'] == 'turn-xyz'  # inherited at read time
    # ...so the placeholder must NOT be physically reaped.
    assert reapable_span_ids(rows) == []


# ── reap_pending_spans: end-to-end DB sweep ──────────────────────

def _insert(conn, span_id, name, attrs, *, status='UNSET', tid='t-db',
            tool_use_id=None):
    conn.execute(
        "INSERT INTO session_spans (trace_id, span_id, parent_id, name, kind, "
        "start_time, attributes, status_code, tool_use_id, source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (tid, span_id, None, name, 'internal', '2026-04-18T12:00:00',
         json.dumps(attrs), status, tool_use_id, 'hook'))


def _seed_mixed_trace(conn, tid='t-db'):
    """A trace with: a reapable pending tool, a reapable normal prompt
    placeholder, and an in-flight pending tool that must survive."""
    tu = 'toolu_resolved0001'
    _insert(conn, tool_pending_id(tu), 'tool.Bash',
            {'tool_use_id': tu, 'command_preview': 'ls'}, status='PENDING', tid=tid)
    _insert(conn, 'rand16hexabcdef0', 'tool.Bash',
            {'tool_use_id': tu, 'command_preview': 'ls'}, status='OK',
            tool_use_id=tu, tid=tid)
    text = 'a plain prompt'
    _insert(conn, prompt_placeholder_id(tid, text), 'prompt',
            {'text': text}, status='PENDING', tid=tid)
    _insert(conn, 'prompt-realuuid00001', 'prompt', {'text': text}, status='OK',
            tid=tid)
    inflight = 'toolu_stillrunning'
    _insert(conn, tool_pending_id(inflight), 'tool.Bash',
            {'tool_use_id': inflight, 'command_preview': 'sleep'},
            status='PENDING', tid=tid)


def _count(conn, tid='t-db'):
    return conn.execute(
        "SELECT COUNT(*) c FROM session_spans WHERE trace_id = ?", (tid,)
    ).fetchone()['c']


def test_dry_run_writes_nothing_but_reports(tmp_db):
    """Acceptance 6a: --dry-run reports the would-delete tally and touches no
    rows."""
    conn = get_connection()
    try:
        _seed_mixed_trace(conn)
        conn.commit()
        before = _count(conn)
    finally:
        conn.close()

    result = reap_pending_spans(dry_run=True)
    assert result['rows_reaped'] == 2  # pending tool + normal prompt placeholder

    conn = get_connection()
    try:
        assert _count(conn) == before  # nothing deleted
    finally:
        conn.close()


def test_live_reap_deletes_exactly_the_reapable_set(tmp_db):
    """Acceptance 6b: a live reap deletes exactly the dry-run set; the in-flight
    placeholder survives and the rendered view is unchanged."""
    from lib.trace.projection import _fetch_spans

    conn = get_connection()
    try:
        _seed_mixed_trace(conn)
        conn.commit()
        before = _count(conn)
        before_view = _survivors(_fetch_spans(conn, 't-db'))
    finally:
        conn.close()

    result = reap_pending_spans()
    assert result['rows_reaped'] == 2
    assert result['traces_touched'] == 1

    conn = get_connection()
    try:
        assert _count(conn) == before - 2
        # the in-flight placeholder is still there
        inflight = tool_pending_id('toolu_stillrunning')
        assert conn.execute(
            "SELECT COUNT(*) c FROM session_spans WHERE span_id = ?",
            (inflight,)).fetchone()['c'] == 1
        # the rendered view is identical to before the reap
        assert _survivors(_fetch_spans(conn, 't-db')) == before_view
    finally:
        conn.close()


def test_reap_is_idempotent(tmp_db):
    """A second sweep finds nothing left to reap."""
    conn = get_connection()
    try:
        _seed_mixed_trace(conn)
        conn.commit()
    finally:
        conn.close()

    assert reap_pending_spans()['rows_reaped'] == 2
    assert reap_pending_spans()['rows_reaped'] == 0


def test_session_filter_scopes_to_one_trace(tmp_db):
    """--session limits the sweep to a single trace_id."""
    conn = get_connection()
    try:
        _seed_mixed_trace(conn, tid='keep-me')
        _seed_mixed_trace(conn, tid='sweep-me')
        conn.commit()
    finally:
        conn.close()

    result = reap_pending_spans(session='sweep-me')
    assert result['traces_scanned'] == 1
    assert result['rows_reaped'] == 2

    conn = get_connection()
    try:
        assert _count(conn, 'keep-me') == 5   # untouched
        assert _count(conn, 'sweep-me') == 3  # 5 - 2 reaped
    finally:
        conn.close()
