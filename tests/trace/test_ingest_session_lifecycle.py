"""Session-lifecycle ingest: resume-preview status flips + model aggregation.

Kimi fires SessionStart for every session it renders in the resume
picker, so an already-ended session gets a `session.start` newer than its
`ended_at` and nothing else. The upsert derives status from
`last_start_at` vs `ended_at` alone, which resurrected those sessions to
'active' forever. The hold is scoped to providers that preview resumed
sessions (`_RESUME_PREVIEW_PROVIDERS`); Claude is excluded, because a
behavioural-only guard stranded a genuinely-live Claude session at
'ended' whenever its SessionStart and its first prompt landed in
different ingest batches.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def lifecycle_db(tmp_path, monkeypatch):
    db_path = tmp_path / 'lifecycle.db'
    import lib.orm.engine as db_module
    monkeypatch.setattr(db_module, 'DB_PATH', str(db_path))
    db_module.init_db()
    return db_path


def _span(trace_id, span_id, name, start, attrs=None):
    return (
        {'trace_id': trace_id, 'span_id': span_id, 'parent_id': None,
         'name': name, 'kind': 'internal', 'start_time': start,
         'end_time': start, 'duration_ms': 0,
         'status_code': 'UNSET', 'status_message': None},
        attrs or {},
    )


def _session_row(db_path, trace_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status, ended_at, last_start_at, model "
            "FROM sessions WHERE trace_id = ?", (trace_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _ingest(spans):
    from lib.trace.trace_service import ingest_session_spans
    ingest_session_spans(spans)


def _seed_ended_session(trace_id):
    """A session that started, did work, and ended."""
    _ingest([
        _span(trace_id, 'start-1', 'session.start', '2026-06-18T16:00:00',
              {'cwd': '/repo', 'source': 'startup', 'agent_type': 'kimi'}),
        _span(trace_id, 'prompt-1', 'prompt', '2026-06-18T16:01:00',
              {'text': 'do the thing'}),
        _span(trace_id, 'end-1', 'session.end', '2026-06-18T16:32:51',
              {'reason': 'exit'}),
    ])


# ── (1) Resume-preview status flip ──────────────────────────────

def test_bare_session_start_after_end_does_not_reactivate(lifecycle_db):
    """The resume-picker preview footprint: a `session.start` (plus the
    environment root it drags along) newer than `ended_at`, no work."""
    _seed_ended_session('t-preview')
    assert _session_row(lifecycle_db, 't-preview')['status'] == 'ended'

    _ingest([
        _span('t-preview', 'start-2', 'session.start', '2026-07-26T07:58:25',
              {'cwd': '/repo', 'source': 'resume', 'agent_type': 'kimi'}),
        _span('t-preview', 'git-2', 'environment.git_status',
              '2026-07-26T07:58:25.2', {'cwd': '/repo'}),
    ])

    row = _session_row(lifecycle_db, 't-preview')
    assert row['status'] == 'ended'
    assert row['ended_at'] == '2026-06-18T16:32:51'
    # The restart is still recorded so a later real prompt can promote it.
    assert row['last_start_at'] == '2026-07-26T07:58:25'


def test_prompt_after_bare_restart_reactivates(lifecycle_db):
    """A real resume: the held-back restart promotes the moment work lands,
    even though the operation span arrives in a LATER batch than the
    `session.start` (Claude's SessionStart / UserPromptSubmit ordering)."""
    _seed_ended_session('t-resume')
    _ingest([
        _span('t-resume', 'start-2', 'session.start', '2026-07-26T07:58:25',
              {'cwd': '/repo', 'source': 'resume'}),
    ])
    assert _session_row(lifecycle_db, 't-resume')['status'] == 'ended'

    _ingest([
        _span('t-resume', 'prompt-2', 'prompt', '2026-07-26T07:59:00',
              {'text': 'keep going'}),
    ])
    assert _session_row(lifecycle_db, 't-resume')['status'] == 'active'


def test_restart_with_work_in_same_batch_reactivates(lifecycle_db):
    """Provider-safe: a restart batch that already carries an operation
    span is live immediately — the guard never fires."""
    _seed_ended_session('t-batched')
    _ingest([
        _span('t-batched', 'start-2', 'session.start', '2026-07-26T07:58:25',
              {'cwd': '/repo', 'source': 'resume'}),
        _span('t-batched', 'tool-2', 'tool.Read', '2026-07-26T07:58:30',
              {'file_path': '/repo/a.py'}),
    ])
    assert _session_row(lifecycle_db, 't-batched')['status'] == 'active'


def test_first_session_start_still_activates(lifecycle_db):
    """No prior `ended_at` → nothing to protect; a fresh start is active."""
    _ingest([
        _span('t-fresh', 'start-1', 'session.start', '2026-07-26T07:58:25',
              {'cwd': '/repo', 'source': 'startup'}),
    ])
    row = _session_row(lifecycle_db, 't-fresh')
    assert row['status'] == 'active'
    assert row['ended_at'] is None


def test_stale_operation_span_does_not_reactivate(lifecycle_db):
    """Re-ingesting the session's OWN pre-end work (a transcript rescan)
    after a preview must not count as new activity."""
    _seed_ended_session('t-rescan')
    _ingest([
        _span('t-rescan', 'start-2', 'session.start', '2026-07-26T07:58:25',
              {'cwd': '/repo', 'source': 'resume'}),
    ])
    _ingest([
        _span('t-rescan', 'prompt-old', 'prompt', '2026-06-18T16:02:00',
              {'text': 'earlier turn recovered from the transcript'}),
    ])
    assert _session_row(lifecycle_db, 't-rescan')['status'] == 'ended'


def test_turn_trailing_session_end_does_not_count_as_restart_activity(
        lifecycle_db):
    """Observed on every real session: the transcript scan lands the final
    `turn` a few ms AFTER the `session.end` span. That turn belongs to the
    run that just finished, so a later preview must still be held."""
    _ingest([
        _span('t-trail', 'start-1', 'session.start', '2026-06-18T16:03:18',
              {'cwd': '/repo', 'source': 'startup'}),
        _span('t-trail', 'prompt-1', 'prompt', '2026-06-18T16:04:00',
              {'text': 'work'}),
        _span('t-trail', 'end-1', 'session.end', '2026-06-18T16:32:51.520091',
              {'reason': 'exit'}),
        _span('t-trail', 'turn-late', 'turn', '2026-06-18T16:32:51.579065',
              {'model': 'kimi-code/k3'}),
    ])
    _ingest([
        _span('t-trail', 'start-2', 'session.start', '2026-07-26T07:58:30',
              {'cwd': '/repo', 'source': 'resume', 'agent_type': 'kimi'}),
        _span('t-trail', 'git-2', 'environment.git_status',
              '2026-07-26T07:58:30.067856', {'cwd': '/repo'}),
    ])
    assert _session_row(lifecycle_db, 't-trail')['status'] == 'ended'


# ── (2) Model aggregation ───────────────────────────────────────

def test_model_resolves_from_assistant_response(lifecycle_db):
    """Kimi carries the model only on `assistant_response` — its
    `session.start` has no `model` attribute at all."""
    _ingest([
        _span('t-model', 'start-1', 'session.start', '2026-07-26T06:57:34',
              {'cwd': '/repo', 'source': 'startup', 'agent_type': 'kimi'}),
        _span('t-model', 'resp-1', 'assistant_response',
              '2026-07-26T07:04:59', {'model': 'kimi-code/k3'}),
    ])
    assert _session_row(lifecycle_db, 't-model')['model'] == 'kimi-code/k3'


def test_subagent_assistant_response_does_not_overwrite_parent_model(
        lifecycle_db):
    """Kimi subagent turns are `assistant_response` spans under the PARENT
    trace_id; they carry an `agent_id` the main-agent turns never have."""
    _ingest([
        _span('t-sub', 'resp-1', 'assistant_response', '2026-07-26T07:04:59',
              {'model': 'kimi-code/k3'}),
        _span('t-sub', 'resp-sub', 'assistant_response',
              '2026-07-26T07:20:00',
              {'model': 'kimi-code/k2-mini', 'agent_id': 'agent-explore-1'}),
    ])
    assert _session_row(lifecycle_db, 't-sub')['model'] == 'kimi-code/k3'
