"""Transcript backfill of per-tool duration/source-prompt, and the
provider scope of the bare-restart status hold.

Kimi's CLI reports no tool duration and no issuing prompt on its hook
payload, so both are recovered from the transcript and land through the
tool_attribution UPDATE. Claude's hook payload DOES carry the tool's own
execution time, so the backfill must be fill-only. Claude's SessionStart
is likewise trustworthy, so the resume-preview hold must not touch it.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def ingest_db(tmp_path, monkeypatch):
    db_path = tmp_path / 'ingest.db'
    import lib.orm.engine as db_module
    monkeypatch.setattr(db_module, 'DB_PATH', str(db_path))
    db_module.init_db()
    return db_path


def _span(trace_id, span_id, name, start, attrs=None, duration_ms=0):
    return (
        {'trace_id': trace_id, 'span_id': span_id, 'parent_id': None,
         'name': name, 'kind': 'internal', 'start_time': start,
         'end_time': start, 'duration_ms': duration_ms,
         'status_code': 'UNSET', 'status_message': None},
        attrs or {},
    )


def _ingest(spans):
    from lib.trace.trace_service import ingest_session_spans
    ingest_session_spans(spans)


def _attribute(payload):
    from lib.trace.trace_service import ingest_tool_attribution
    return ingest_tool_attribution(payload)


def _span_row(db_path, trace_id, span_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT duration_ms, source_prompt_id, output_tokens, parent_id "
            "FROM session_spans WHERE trace_id = ? AND span_id = ?",
            (trace_id, span_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _session_status(db_path, trace_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status, ended_at, last_start_at FROM sessions "
            "WHERE trace_id = ?", (trace_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── (a) duration / source_prompt_id plumbing ────────────────────

def test_transcript_backfill_fills_duration_and_source_prompt(ingest_db):
    """The Kimi shape: the live span was posted with no duration and no
    issuing prompt; the transcript scan supplies both."""
    _ingest([_span('k1', 'tool-1', 'tool.Bash', '2026-07-26T08:00:00',
                   {'tool_use_id': 'call_abc'}, duration_ms=0)])

    updated, skipped = _attribute({
        'trace_id': 'k1', 'turn_uuid': 'turn-1',
        'tool_calls': [{'tool_use_id': 'call_abc', 'name': 'Bash',
                        'output_tokens': 12, 'input_tokens': 3,
                        'duration_ms': 32513,
                        'source_prompt_id': 'prompt-9fe5'}],
    })

    assert (updated, skipped) == (1, 0)
    row = _span_row(ingest_db, 'k1', 'tool-1')
    assert row['duration_ms'] == 32513
    assert row['source_prompt_id'] == 'prompt-9fe5'
    assert row['output_tokens'] == 12


def test_backfill_never_overwrites_a_live_claude_duration(ingest_db):
    """Claude's PostToolUse payload carries the authoritative execution
    time; a transcript bracket must never clobber it (nor the
    source_prompt_id the live span already stamped)."""
    _ingest([_span('c1', 'tool-1', 'tool.Bash', '2026-07-26T08:00:00',
                   {'tool_use_id': 'call_live',
                    'source_prompt_id': 'prompt-live'},
                   duration_ms=8400)])

    _attribute({
        'trace_id': 'c1', 'turn_uuid': 'turn-1',
        'tool_calls': [{'tool_use_id': 'call_live', 'name': 'Bash',
                        'output_tokens': 12, 'input_tokens': 3,
                        'duration_ms': 12,
                        'source_prompt_id': 'prompt-other'}],
    })

    row = _span_row(ingest_db, 'c1', 'tool-1')
    assert row['duration_ms'] == 8400
    assert row['source_prompt_id'] == 'prompt-live'
    assert row['output_tokens'] == 12


def test_claude_payload_without_the_new_keys_changes_nothing(ingest_db):
    """Claude's transcript tool_call dict carries neither key, so the
    payload omits them — the columns must survive untouched."""
    _ingest([_span('c2', 'tool-1', 'tool.Read', '2026-07-26T08:00:00',
                   {'tool_use_id': 'call_read',
                    'source_prompt_id': 'prompt-live'},
                   duration_ms=8400)])

    _attribute({
        'trace_id': 'c2', 'turn_uuid': 'turn-1',
        'tool_calls': [{'tool_use_id': 'call_read', 'name': 'Read',
                        'output_tokens': 40, 'input_tokens': 900,
                        'image_tokens': None}],
    })

    row = _span_row(ingest_db, 'c2', 'tool-1')
    assert row['duration_ms'] == 8400
    assert row['source_prompt_id'] == 'prompt-live'
    assert row['output_tokens'] == 40


def test_non_tool_row_sharing_a_tool_use_id_keeps_its_own_timing(ingest_db):
    """`permission.request` rows share the tool_use_id but measure a
    different thing — same name scoping the parent_id backfill uses."""
    _ingest([
        _span('k2', 'tool-1', 'tool.Bash', '2026-07-26T08:00:00',
              {'tool_use_id': 'call_x'}, duration_ms=0),
        _span('k2', 'perm-1', 'permission.request', '2026-07-26T08:00:00',
              {'tool_use_id': 'call_x'}, duration_ms=0),
    ])

    _attribute({
        'trace_id': 'k2', 'turn_uuid': 'turn-1',
        'tool_calls': [{'tool_use_id': 'call_x', 'name': 'Bash',
                        'output_tokens': 1, 'input_tokens': 1,
                        'duration_ms': 777,
                        'source_prompt_id': 'prompt-1'}],
    })

    assert _span_row(ingest_db, 'k2', 'tool-1')['duration_ms'] == 777
    perm = _span_row(ingest_db, 'k2', 'perm-1')
    assert perm['duration_ms'] == 0
    assert perm['source_prompt_id'] is None


def test_poster_forwards_duration_and_source_prompt(monkeypatch):
    """The hook-side payload actually carries the two fields the parser
    computed — and stays None-safe for Claude's tool_call shape."""
    from hook_manager.handlers.turn_trace import span_posters

    sent = {}
    monkeypatch.setattr(
        'lib.hook_plugin.post_event',
        lambda name, payload: sent.update({name: payload}),
    )

    class _Turn:
        uuid = 'turn-1'
        tool_calls = [
            {'id': 'call_kimi', 'name': 'Bash', 'output_token_estimate': 5,
             'input_token_estimate': None, 'image_token_estimate': None,
             'duration_ms': 3, 'source_prompt_id': 'prompt-1'},
            {'id': 'call_claude', 'name': 'Read', 'output_token_estimate': 5,
             'input_token_estimate': None, 'image_token_estimate': None},
        ]

    span_posters._post_tool_attribution_event('t1', _Turn(), 'resp-1')

    calls = sent['tool_attribution']['tool_calls']
    assert calls[0]['duration_ms'] == 3
    assert calls[0]['source_prompt_id'] == 'prompt-1'
    assert calls[1]['duration_ms'] is None
    assert calls[1]['source_prompt_id'] is None


# ── (b) bare-restart hold, provider-scoped ──────────────────────

def _seed_ended_session(trace_id, agent_type):
    _ingest([
        _span(trace_id, 'start-1', 'session.start', '2026-06-18T16:00:00',
              {'cwd': '/repo', 'source': 'startup', 'agent_type': agent_type}),
        _span(trace_id, 'prompt-1', 'prompt', '2026-06-18T16:01:00',
              {'text': 'do the thing'}),
        _span(trace_id, 'end-1', 'session.end', '2026-06-18T16:32:51',
              {'reason': 'exit'}),
    ])


@pytest.mark.parametrize('agent_type', ['claude', None, 'explorer'])
def test_claude_bare_resume_is_live_immediately(ingest_db, agent_type):
    """The regression: a resumed Claude session must read as active from
    its SessionStart, not only once the user's first prompt lands in a
    later batch."""
    _seed_ended_session('c-resume', agent_type)
    assert _session_status(ingest_db, 'c-resume')['status'] == 'ended'

    _ingest([_span('c-resume', 'start-2', 'session.start',
                   '2026-07-26T07:58:25',
                   {'cwd': '/repo', 'source': 'resume',
                    'agent_type': agent_type})])

    assert _session_status(ingest_db, 'c-resume')['status'] == 'active'


def test_kimi_bare_preview_is_held_then_promoted_by_a_later_batch(ingest_db):
    """Kimi's resume picker starts every session it renders; the preview
    must not resurrect the ended one, but a real resume must go active as
    soon as any operation span arrives — in a later batch."""
    _seed_ended_session('k-resume', 'kimi')

    _ingest([_span('k-resume', 'start-2', 'session.start',
                   '2026-07-26T07:58:25',
                   {'cwd': '/repo', 'source': 'resume'})])
    held = _session_status(ingest_db, 'k-resume')
    assert held['status'] == 'ended'
    assert held['last_start_at'] == '2026-07-26T07:58:25'

    _ingest([_span('k-resume', 'prompt-2', 'prompt', '2026-07-26T07:59:00',
                   {'text': 'keep going'})])
    assert _session_status(ingest_db, 'k-resume')['status'] == 'active'
