"""Unit tests for lib.trace.trace_service.

Validates Phase C.1's extraction: the service produces the same
envelopes the blueprint used to build inline, given a controlled DB
state. Uses the tmp_db fixture from tests/conftest.py.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from lib.trace import trace_service


# ── Seeding helpers ──────────────────────────────────────────

def _seed_skill_reads(db_path, rows):
    """Insert skill_reads rows directly — bypass the ingest handler so
    we test the read path in isolation."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany(
            "INSERT INTO skill_reads (skill_id, session_id, file_path, found, read_at) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _seed_spans(db_path, rows):
    """Insert session_spans rows directly. Each row: (trace_id, span_id,
    name, start_time, attributes_dict)."""
    conn = sqlite3.connect(str(db_path))
    try:
        for trace_id, span_id, name, start_time, attrs in rows:
            conn.execute(
                "INSERT INTO session_spans (trace_id, span_id, name, "
                "start_time, attributes) VALUES (?, ?, ?, ?, ?)",
                (trace_id, span_id, name, start_time, json.dumps(attrs)),
            )
        conn.commit()
    finally:
        conn.close()


# ── list_skill_reads_page ────────────────────────────────────

def test_list_skill_reads_empty_db_returns_empty_page(tmp_db):
    page, stats, sessions = trace_service.list_skill_reads_page(
        skill_filter=None, session_filter=None,
        include_tests=True, cursor_token=None, size=10,
    )
    assert page.items == []
    assert page.next_cursor is None
    assert stats == []
    assert sessions == []


def test_list_skill_reads_returns_all_reads(tmp_db):
    _seed_skill_reads(tmp_db, [
        ("grit-rules", "sess-a", "/p/content.md", 1, "2026-04-21T10:00:00"),
        ("grit-rules", "sess-a", "/p/content.md", 1, "2026-04-21T10:00:01"),
        ("git", "sess-b", "/p/git/content.md", 1, "2026-04-21T09:00:00"),
    ])
    page, stats, _sessions = trace_service.list_skill_reads_page(
        skill_filter=None, session_filter=None,
        include_tests=True, cursor_token=None, size=10,
    )
    assert len(page.items) == 3
    # Ordered newest-first
    assert page.items[0]["read_at"] > page.items[-1]["read_at"]
    # Stats rolled up per-skill
    stats_by_id = {s["skill_id"]: s for s in stats}
    assert stats_by_id["grit-rules"]["total"] == 2
    assert stats_by_id["git"]["total"] == 1


def test_list_skill_reads_skill_filter_narrows(tmp_db):
    _seed_skill_reads(tmp_db, [
        ("grit-rules", "sess-a", "/p/content.md", 1, "2026-04-21T10:00:00"),
        ("git", "sess-b", "/p/git/content.md", 1, "2026-04-21T09:00:00"),
    ])
    page, _stats, _sessions = trace_service.list_skill_reads_page(
        skill_filter="grit-rules",
        session_filter=None, include_tests=True,
        cursor_token=None, size=10,
    )
    assert len(page.items) == 1
    assert page.items[0]["skill_id"] == "grit-rules"


def test_list_skill_reads_paginates_with_cursor(tmp_db):
    _seed_skill_reads(tmp_db, [
        ("s", f"sess-{i}", "/p", 1, f"2026-04-21T10:00:{i:02d}")
        for i in range(5)
    ])
    page1, stats1, _ = trace_service.list_skill_reads_page(
        skill_filter=None, session_filter=None, include_tests=True,
        cursor_token=None, size=2,
    )
    assert len(page1.items) == 2
    assert page1.next_cursor is not None
    # Summaries only on first page.
    assert stats1 != []

    page2, stats2, _ = trace_service.list_skill_reads_page(
        skill_filter=None, session_filter=None, include_tests=True,
        cursor_token=page1.next_cursor, size=2,
    )
    assert len(page2.items) == 2
    # No overlap between pages.
    p1_ids = {r["id"] for r in page1.items}
    p2_ids = {r["id"] for r in page2.items}
    assert p1_ids.isdisjoint(p2_ids)
    # Later pages drop the summaries.
    assert stats2 == []


# ── list_mcp_calls_page ──────────────────────────────────────

def test_list_mcp_calls_filters_to_tool_prefix(tmp_db):
    _seed_spans(tmp_db, [
        ("t1", "s1", "tool.mcp__foo__bar", "2026-04-21T10:00:00",
         {"tool_name": "foo__bar"}),
        ("t1", "s2", "tool.Bash", "2026-04-21T10:00:01", {"command": "ls"}),
        ("t1", "s3", "tool.mcp__baz", "2026-04-21T10:00:02",
         {"tool_name": "baz"}),
    ])
    page, _stats, _sessions = trace_service.list_mcp_calls_page(
        tool_filter=None, session_filter=None, include_tests=True,
        cursor_token=None, size=10,
    )
    names = [r["tool_name"] for r in page.items]
    assert "foo__bar" in names
    assert "baz" in names
    assert "Bash" not in names  # non-mcp tool excluded


def test_list_mcp_calls_tool_filter(tmp_db):
    _seed_spans(tmp_db, [
        ("t1", "s1", "tool.mcp__foo", "2026-04-21T10:00:00", {"tool_name": "foo"}),
        ("t1", "s2", "tool.mcp__bar", "2026-04-21T10:00:01", {"tool_name": "bar"}),
    ])
    page, _stats, _sessions = trace_service.list_mcp_calls_page(
        tool_filter="foo", session_filter=None, include_tests=True,
        cursor_token=None, size=10,
    )
    assert len(page.items) == 1
    assert page.items[0]["tool_name"] == "foo"


# ── fetch_session_projection ─────────────────────────────────

def test_fetch_session_projection_empty_trace_returns_empty(tmp_db):
    widened, tree = trace_service.fetch_session_projection("nonexistent")
    assert widened == []
    assert tree == []


# ── fetch_session_paginated ──────────────────────────────────

def _seed_chatty_session(db_path, trace_id, n_prompts, *, base_min=0):
    """Seed a session with `n_prompts` prompt anchors, each with a few
    child spans (assistant_response + tool). Returns the list of
    seeded (id, span_id) tuples for the prompts, in insertion order."""
    rows = []
    for i in range(n_prompts):
        idx = base_min + i
        t0 = f'2026-05-01T10:{idx:02d}:00'
        rows.append((trace_id, f'prompt-{idx}', 'prompt', t0, {}))
        rows.append((trace_id, f'resp-{idx}', 'assistant_response',
                     f'2026-05-01T10:{idx:02d}:01', {}))
        rows.append((trace_id, f'tool-{idx}', 'tool.Bash',
                     f'2026-05-01T10:{idx:02d}:02', {}))
    _seed_spans(db_path, rows)
    # Pull back ids so callers can use them as cursors.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        return [
            (r['id'], r['span_id']) for r in conn.execute(
                "SELECT id, span_id FROM session_spans "
                "WHERE trace_id = ? AND name = 'prompt' "
                "ORDER BY start_time ASC, id ASC",
                (trace_id,),
            )
        ]
    finally:
        conn.close()


def test_fetch_session_paginated_no_cursor_returns_latest_page(tmp_db):
    _seed_chatty_session(tmp_db, 't-page', n_prompts=12)
    widened, tree, has_more_older = trace_service.fetch_session_paginated(
        't-page', limit=5,
    )
    prompt_roots = [n for n in tree if n['data']['name'] == 'prompt']
    assert len(prompt_roots) == 5
    # Latest five — prompt-7 .. prompt-11
    assert [r['data']['span_id'] for r in prompt_roots] == [
        'prompt-7', 'prompt-8', 'prompt-9', 'prompt-10', 'prompt-11',
    ]
    assert has_more_older is True


def test_fetch_session_paginated_before_id_walks_older(tmp_db):
    prompts = _seed_chatty_session(tmp_db, 't-walk', n_prompts=12)
    # Use prompt-7's id as the before cursor — should return prompt-2..6.
    cursor = next(pid for pid, sid in prompts if sid == 'prompt-7')
    _w, tree, has_more_older = trace_service.fetch_session_paginated(
        't-walk', limit=5, before_id=cursor,
    )
    prompt_roots = [n for n in tree if n['data']['name'] == 'prompt']
    assert [r['data']['span_id'] for r in prompt_roots] == [
        'prompt-2', 'prompt-3', 'prompt-4', 'prompt-5', 'prompt-6',
    ]
    # Two prompts (0, 1) still older.
    assert has_more_older is True


def test_fetch_session_paginated_before_id_exhausts_history(tmp_db):
    prompts = _seed_chatty_session(tmp_db, 't-end', n_prompts=12)
    # Cursor past the very first prompt — there's nothing older.
    cursor = prompts[2][0]  # prompt-2's id; request 5 older but only 2 exist
    _w, tree, has_more_older = trace_service.fetch_session_paginated(
        't-end', limit=5, before_id=cursor,
    )
    prompt_roots = [n for n in tree if n['data']['name'] == 'prompt']
    assert [r['data']['span_id'] for r in prompt_roots] == [
        'prompt-0', 'prompt-1',
    ]
    assert has_more_older is False


def test_fetch_session_paginated_after_id_no_new_returns_empty(tmp_db):
    prompts = _seed_chatty_session(tmp_db, 't-after', n_prompts=12)
    latest_id = prompts[-1][0]
    widened, tree, _ = trace_service.fetch_session_paginated(
        't-after', after_id=latest_id,
    )
    assert widened == []
    assert tree == []


def test_fetch_session_paginated_after_id_picks_up_new_prompts(tmp_db):
    prompts = _seed_chatty_session(tmp_db, 't-new', n_prompts=5)
    latest_id = prompts[-1][0]  # prompt-4's id
    # Now seed 3 more prompts arriving "later".
    _seed_chatty_session(tmp_db, 't-new', n_prompts=3, base_min=10)
    widened, tree, _ = trace_service.fetch_session_paginated(
        't-new', after_id=latest_id,
    )
    prompt_roots = [n for n in tree if n['data']['name'] == 'prompt']
    assert len(prompt_roots) == 3
    # All three new prompts present.
    assert all('id' in r['data'] and r['data']['id'] > latest_id
               for r in prompt_roots)


def test_fetch_session_paginated_after_id_picks_up_compact_boundaries(tmp_db):
    # `/compact` emits compact.pre then compact.post WITHOUT a new prompt,
    # so an additive reload that only anchors on prompts would miss them.
    prompts = _seed_chatty_session(tmp_db, 't-compact', n_prompts=3)
    latest_id = prompts[-1][0]
    _seed_spans(tmp_db, [
        ('t-compact', 'cpre', 'compact.pre', '2026-05-01T10:05:00', {}),
        ('t-compact', 'cpost', 'compact.post', '2026-05-01T10:06:00',
         {'summary': 'recap'}),
    ])
    _widened, tree, _ = trace_service.fetch_session_paginated(
        't-compact', after_id=latest_id,
    )
    boundary_names = sorted(
        n['data']['name'] for n in tree
        if n['data']['name'] in ('compact.pre', 'compact.post')
    )
    assert boundary_names == ['compact.post', 'compact.pre']


def test_fetch_session_paginated_grafting_works_on_window(tmp_db):
    """Tool spans inside the window must still graft under the right
    prompt — the page must not break the orphan-grafting invariant."""
    _seed_chatty_session(tmp_db, 't-graft', n_prompts=8)
    _w, tree, _ = trace_service.fetch_session_paginated(
        't-graft', limit=3,
    )
    # Pick the latest prompt root from the page.
    prompt_roots = [n for n in tree if n['data']['name'] == 'prompt']
    assert prompt_roots, 'expected prompt roots in page'
    last_prompt = prompt_roots[-1]
    child_names = {c['data']['name'] for c in last_prompt.get('children', [])}
    # Every prompt seeded a Bash tool + assistant_response; both must
    # graft under the prompt within the windowed projection.
    assert 'tool.Bash' in child_names
    assert 'assistant_response' in child_names


def test_fetch_session_paginated_rejects_both_cursors(tmp_db):
    _seed_chatty_session(tmp_db, 't-bad', n_prompts=3)
    with pytest.raises(ValueError):
        trace_service.fetch_session_paginated(
            't-bad', before_id=1, after_id=2,
        )


def test_fetch_session_paginated_empty_trace_returns_empty(tmp_db):
    widened, tree, has_more = trace_service.fetch_session_paginated(
        'nonexistent',
    )
    assert widened == []
    assert tree == []
    assert has_more is False


# ── ingest_session_status ────────────────────────────────────


def _read_session(db_path, trace_id):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT model, peak_context_tokens, context_window_tokens "
            "FROM sessions WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_ingest_session_status_seeds_row_when_none_exists(tmp_db):
    """The statusline can fire before any span ingest has created the
    row — the helper must seed it rather than silently drop the data."""
    trace_service.ingest_session_status(
        trace_id='t-fresh',
        model='claude-opus-4-7[1m]',
        context_used_tokens=120_000,
        context_window_tokens=1_000_000,
    )
    row = _read_session(tmp_db, 't-fresh')
    assert row == {
        'model': 'claude-opus-4-7[1m]',
        'peak_context_tokens': 120_000,
        'context_window_tokens': 1_000_000,
    }


def test_ingest_session_status_updates_existing_row(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, "
            "model, peak_context_tokens, context_window_tokens) "
            "VALUES ('t-exists', '2026-04-24T00:00:00', '2026-04-24T00:01:00', "
            "'claude-opus-4-7', 50_000, 200_000)"
        )
        conn.commit()
    finally:
        conn.close()

    trace_service.ingest_session_status(
        trace_id='t-exists',
        model='claude-opus-4-7[1m]',
        context_used_tokens=180_000,
        context_window_tokens=1_000_000,
    )
    row = _read_session(tmp_db, 't-exists')
    assert row['model'] == 'claude-opus-4-7[1m]'
    assert row['peak_context_tokens'] == 180_000
    assert row['context_window_tokens'] == 1_000_000


def test_ingest_session_status_preserves_variant_against_bare_base(tmp_db):
    """A previously-stored `[1m]` id must survive a later post that
    only carries the bare base — otherwise a transcript-backed ingest
    firing after the statusline would silently downgrade the row."""
    trace_service.ingest_session_status(
        trace_id='t-variant',
        model='claude-opus-4-7[1m]',
        context_used_tokens=100_000,
        context_window_tokens=1_000_000,
    )
    trace_service.ingest_session_status(
        trace_id='t-variant',
        model='claude-opus-4-7',  # bare — must not overwrite
        context_used_tokens=110_000,
    )
    row = _read_session(tmp_db, 't-variant')
    assert row['model'] == 'claude-opus-4-7[1m]'
    assert row['peak_context_tokens'] == 110_000


def test_ingest_session_status_peak_is_monotonic(tmp_db):
    """A lower incoming `context_used_tokens` never moves the stored
    peak backwards (e.g. right after /compact the observed context
    drops sharply)."""
    trace_service.ingest_session_status(
        trace_id='t-peak',
        model='claude-opus-4-7[1m]',
        context_used_tokens=180_000,
    )
    trace_service.ingest_session_status(
        trace_id='t-peak',
        context_used_tokens=20_000,  # post-compact dip
    )
    row = _read_session(tmp_db, 't-peak')
    assert row['peak_context_tokens'] == 180_000


def _read_session_title(db_path, trace_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT title, title_source FROM sessions WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _spans(trace_id, items):
    """Build the (span, attrs) tuples `_span_counter_buckets` expects."""
    out = []
    for span_id, name, start_time, attrs in items:
        out.append((
            {'trace_id': trace_id, 'span_id': span_id, 'parent_id': None,
             'name': name, 'kind': 'internal', 'start_time': start_time,
             'end_time': start_time, 'duration_ms': 0,
             'status_code': 'UNSET', 'status_message': None},
            attrs,
        ))
    return out


def _apply_title_upsert(db_path, trace_id, bucket, new_status='active'):
    """Drive the same upsert that ingest_session_spans drives, but
    without going through session_trace_map (which isn't in the test
    schema). Lets us assert the SQL precedence in isolation."""
    from lib.trace.trace_service import _SESSIONS_UPSERT_SQL
    if bucket['live_title']:
        title_val, title_src = bucket['live_title'], bucket['live_title_source']
    elif bucket['title']:
        title_val, title_src = bucket['title'], 'first_prompt'
    else:
        title_val, title_src = None, None
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_SESSIONS_UPSERT_SQL, (
            trace_id, title_val, title_src,
            new_status, bucket['last_start_at'], bucket['ended_at'],
            bucket['ended_reason'],
            bucket['started_at'], bucket['last_seen'],
            bucket['span_count'], bucket['skill_reads'], bucket['file_edits'],
            bucket['rule_checks'], bucket['plan_enters'], bucket['prompts'],
            bucket['tool_calls'], bucket['is_test'], bucket['test_name'],
            bucket['agent_type'], bucket['model'], bucket['cwd'],
        ))
        conn.commit()
    finally:
        conn.close()


def test_session_title_first_prompt_when_no_ai_title(tmp_db):
    """A batch with only `prompt` spans seeds title from the earliest one
    with `title_source = 'first_prompt'`."""
    from lib.trace.trace_service import _span_counter_buckets
    spans = _spans('t-fp', [
        ('p1', 'prompt', '2026-05-15T10:00:00', {'text': 'do thing X'}),
        ('p2', 'prompt', '2026-05-15T10:05:00', {'text': 'follow-up'}),
    ])
    buckets = _span_counter_buckets(spans, set())
    _apply_title_upsert(tmp_db, 't-fp', buckets['t-fp'])
    assert _read_session_title(tmp_db, 't-fp') == {
        'title': 'do thing X', 'title_source': 'first_prompt',
    }


def test_session_title_first_prompt_takes_first_nonempty_line(tmp_db):
    """A multi-paragraph first prompt collapses to its first non-empty
    line — full instructions don't belong in a one-row title."""
    from lib.trace.trace_service import _span_counter_buckets
    spans = _spans('t-fl', [
        ('p1', 'prompt', '2026-05-15T10:00:00',
         {'text': '\n  \n# Regin Topic Proposal Agent Task\n\n'
                  'Inspect this repository as needed and draft '
                  'reviewable topic graphs…'}),
    ])
    buckets = _span_counter_buckets(spans, set())
    _apply_title_upsert(tmp_db, 't-fl', buckets['t-fl'])
    assert _read_session_title(tmp_db, 't-fl') == {
        'title': '# Regin Topic Proposal Agent Task',
        'title_source': 'first_prompt',
    }


def test_session_title_first_prompt_long_line_gets_ellipsis(tmp_db):
    """A first prompt that's one very long line is capped at
    `_TITLE_MAX_CHARS` with a trailing ellipsis — preserves the lead but
    bounds the row."""
    from lib.trace.trace_service import _span_counter_buckets
    from lib.trace.trace_service.ingest import _TITLE_MAX_CHARS
    long_text = 'x' * (_TITLE_MAX_CHARS + 200)
    spans = _spans('t-ln', [
        ('p1', 'prompt', '2026-05-15T10:00:00', {'text': long_text}),
    ])
    buckets = _span_counter_buckets(spans, set())
    _apply_title_upsert(tmp_db, 't-ln', buckets['t-ln'])
    row = _read_session_title(tmp_db, 't-ln')
    assert row['title_source'] == 'first_prompt'
    assert row['title'] == 'x' * _TITLE_MAX_CHARS + '…'


def test_session_title_claude_ai_title_overrides_first_prompt(tmp_db):
    """When a `session.title` span lands in the same batch, it wins."""
    from lib.trace.trace_service import _span_counter_buckets
    spans = _spans('t-ai', [
        ('p1', 'prompt', '2026-05-15T10:00:00',
         {'text': 'verbose original prompt text…'}),
        ('st1', 'session.title', '2026-05-15T10:01:00',
         {'text': 'Refactor parser', 'source': 'claude_ai_title'}),
    ])
    buckets = _span_counter_buckets(spans, set())
    _apply_title_upsert(tmp_db, 't-ai', buckets['t-ai'])
    assert _read_session_title(tmp_db, 't-ai') == {
        'title': 'Refactor parser', 'title_source': 'claude_ai_title',
    }


def test_session_title_ai_title_upgrades_existing_first_prompt(tmp_db):
    """An ai-title arriving in a LATER batch replaces a stored first_prompt
    title — that's the realistic flow."""
    from lib.trace.trace_service import _span_counter_buckets
    b1 = _span_counter_buckets(_spans('t-up', [
        ('p1', 'prompt', '2026-05-15T10:00:00', {'text': 'help me'}),
    ]), set())
    _apply_title_upsert(tmp_db, 't-up', b1['t-up'])
    assert _read_session_title(tmp_db, 't-up')['title_source'] == 'first_prompt'

    b2 = _span_counter_buckets(_spans('t-up', [
        ('st1', 'session.title', '2026-05-15T10:05:00',
         {'text': 'Refactor parser', 'source': 'claude_ai_title'}),
    ]), set())
    _apply_title_upsert(tmp_db, 't-up', b2['t-up'])
    assert _read_session_title(tmp_db, 't-up') == {
        'title': 'Refactor parser', 'title_source': 'claude_ai_title',
    }


def test_session_title_ai_title_can_change_mid_session(tmp_db):
    """Claude regenerates the title when the topic pivots — a second
    ai-title overwrites the first."""
    from lib.trace.trace_service import _span_counter_buckets
    b1 = _span_counter_buckets(_spans('t-piv', [
        ('st1', 'session.title', '2026-05-15T10:00:00',
         {'text': 'First topic', 'source': 'claude_ai_title'}),
    ]), set())
    _apply_title_upsert(tmp_db, 't-piv', b1['t-piv'])

    b2 = _span_counter_buckets(_spans('t-piv', [
        ('st2', 'session.title', '2026-05-15T10:30:00',
         {'text': 'Pivoted topic', 'source': 'claude_ai_title'}),
    ]), set())
    _apply_title_upsert(tmp_db, 't-piv', b2['t-piv'])
    assert _read_session_title(tmp_db, 't-piv') == {
        'title': 'Pivoted topic', 'title_source': 'claude_ai_title',
    }


def test_session_title_user_rename_overrides_claude_ai_title(tmp_db):
    """A `/rename`-emitted span (source='user_rename') replaces a stored
    claude_ai_title, and a subsequent claude_ai_title batch must NOT
    revert — Claude keeps regenerating its auto title in the background
    but the user-chosen name should stick."""
    from lib.trace.trace_service import _span_counter_buckets
    b1 = _span_counter_buckets(_spans('t-ren', [
        ('st1', 'session.title', '2026-05-15T10:00:00',
         {'text': 'Auto: refactor parser', 'source': 'claude_ai_title'}),
    ]), set())
    _apply_title_upsert(tmp_db, 't-ren', b1['t-ren'])
    assert _read_session_title(tmp_db, 't-ren')['title_source'] == 'claude_ai_title'

    b2 = _span_counter_buckets(_spans('t-ren', [
        ('st2', 'session.title', '2026-05-15T10:05:00',
         {'text': 'My handpicked name', 'source': 'user_rename'}),
    ]), set())
    _apply_title_upsert(tmp_db, 't-ren', b2['t-ren'])
    assert _read_session_title(tmp_db, 't-ren') == {
        'title': 'My handpicked name', 'title_source': 'user_rename',
    }

    # Background ai-title regeneration must NOT clobber the rename.
    b3 = _span_counter_buckets(_spans('t-ren', [
        ('st3', 'session.title', '2026-05-15T10:10:00',
         {'text': 'Auto: different now', 'source': 'claude_ai_title'}),
    ]), set())
    _apply_title_upsert(tmp_db, 't-ren', b3['t-ren'])
    assert _read_session_title(tmp_db, 't-ren') == {
        'title': 'My handpicked name', 'title_source': 'user_rename',
    }


def test_session_title_user_rename_can_change(tmp_db):
    """A newer /rename batch overwrites an older one (latest user
    intent wins) — but ai-title in between still doesn't touch it."""
    from lib.trace.trace_service import _span_counter_buckets
    b1 = _span_counter_buckets(_spans('t-ren2', [
        ('st1', 'session.title', '2026-05-15T10:00:00',
         {'text': 'First rename', 'source': 'user_rename'}),
    ]), set())
    _apply_title_upsert(tmp_db, 't-ren2', b1['t-ren2'])

    b2 = _span_counter_buckets(_spans('t-ren2', [
        ('st2', 'session.title', '2026-05-15T10:10:00',
         {'text': 'Second rename', 'source': 'user_rename'}),
    ]), set())
    _apply_title_upsert(tmp_db, 't-ren2', b2['t-ren2'])
    assert _read_session_title(tmp_db, 't-ren2') == {
        'title': 'Second rename', 'title_source': 'user_rename',
    }


def test_session_title_user_override_is_sticky_against_ai_title(tmp_db):
    """A user-set title must NEVER be clobbered by a later ai-title."""
    from lib.trace.trace_service import _span_counter_buckets
    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, "
            "title, title_source) "
            "VALUES ('t-u', '2026-05-15T10:00:00', '2026-05-15T10:00:00', "
            "'My handpicked title', 'user')"
        )
        conn.commit()
    finally:
        conn.close()

    b = _span_counter_buckets(_spans('t-u', [
        ('st1', 'session.title', '2026-05-15T10:05:00',
         {'text': 'Auto-generated', 'source': 'claude_ai_title'}),
    ]), set())
    _apply_title_upsert(tmp_db, 't-u', b['t-u'])
    assert _read_session_title(tmp_db, 't-u') == {
        'title': 'My handpicked title', 'title_source': 'user',
    }


def test_fetch_turn_usage_groups_spans_into_turns(tmp_db):
    """Each turn row gets the spans whose start_time falls in its
    interval `(prev_ts, this_ts]`, with structural names filtered and
    tool_summary derived from `attributes.tool_name`."""
    import sqlite3, json
    # Two turns, ~40 s apart, in UTC (the transcript's format).
    #
    # Spans use naive local ISO — the hook emitters call
    # `datetime.now().isoformat()`. To keep the test stable regardless
    # of where it runs, we compute the local-clock values as the UTC
    # turn timestamps converted to this host's local zone, so
    # `_to_utc` round-trips them back to the right turn.
    from datetime import datetime, timezone
    def _local(utc_iso):
        return (datetime.fromisoformat(utc_iso)
                .astimezone()
                .replace(tzinfo=None)
                .isoformat())
    turn_a_utc = '2026-04-24T13:43:23.251+00:00'
    turn_b_utc = '2026-04-24T13:44:05.000+00:00'
    span_mid_a_local = _local('2026-04-24T13:43:10.000+00:00')  # before A
    span_edge_a_local = _local(turn_a_utc)                       # equals A
    span_mid_b_local = _local('2026-04-24T13:43:50.000+00:00')  # between
    span_structural_local = _local('2026-04-24T13:43:55.000+00:00')
    span_after_local = _local('2026-04-24T13:44:30.000+00:00')   # after B

    conn = sqlite3.connect(str(tmp_db))
    try:
        conn.executemany(
            "INSERT INTO turn_usage (trace_id, turn_uuid, turn_index, "
            "timestamp, model, input_tokens, output_tokens, "
            "cache_read_tokens, cache_creation_tokens, "
            "context_used_tokens, request_id) "
            "VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, NULL)",
            [
                ('t-group', 'uuid-A', 0, turn_a_utc, 'claude-opus-4-7[1m]'),
                ('t-group', 'uuid-B', 1, turn_b_utc, 'claude-opus-4-7[1m]'),
            ],
        )
        conn.executemany(
            "INSERT INTO session_spans (trace_id, span_id, name, "
            "start_time, attributes) VALUES (?, ?, ?, ?, ?)",
            [
                # Turn A: two spans, with tool_name variation.
                ('t-group', 's-pre-a', 'tool.Read', span_mid_a_local,
                 json.dumps({'tool_name': 'Read'})),
                ('t-group', 's-edge-a', 'tool.Bash', span_edge_a_local,
                 json.dumps({'tool_name': 'Bash'})),
                # Turn B: one tool + one structural (dropped).
                ('t-group', 's-mid-b', 'tool.Read', span_mid_b_local,
                 json.dumps({'tool_name': 'Read'})),
                ('t-group', 'p-b', 'prompt', span_structural_local,
                 json.dumps({'text': 'hi'})),
                # After the last turn — dropped, not in any bucket.
                ('t-group', 's-after', 'tool.Bash', span_after_local,
                 json.dumps({'tool_name': 'Bash'})),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    rows = trace_service.fetch_turn_usage('t-group')
    assert [r['turn_uuid'] for r in rows] == ['uuid-A', 'uuid-B']

    a, b = rows
    # Turn A owns the earlier span plus the boundary-equal span. Tool
    # summary ranks by count desc, then name — both are single, so
    # alphabetical: Bash, Read.
    assert a['span_count'] == 2
    assert {s['span_id'] for s in a['span_refs']} == {'s-pre-a', 's-edge-a'}
    assert a['tool_summary'] == [
        {'name': 'Bash', 'count': 1},
        {'name': 'Read', 'count': 1},
    ]

    # Turn B: only the mid span. The `prompt` span is structural and
    # filtered; the post-B span is unassigned (dropped).
    assert b['span_count'] == 1
    assert b['span_refs'][0]['span_id'] == 's-mid-b'
    assert b['tool_summary'] == [{'name': 'Read', 'count': 1}]


def test_fetch_turn_usage_empty_when_no_turns(tmp_db):
    """No turn_usage rows → empty list, no span query issued."""
    assert trace_service.fetch_turn_usage('t-none') == []


def test_ingest_session_status_ignores_empty_trace_id(tmp_db):
    """Malformed statusline payloads must be a silent no-op, not a
    500 — the script treats any failure as "don't break the UI"."""
    trace_service.ingest_session_status(trace_id='', model='claude-opus-4-7[1m]')
    # Nothing created.
    conn = sqlite3.connect(str(tmp_db))
    try:
        n = conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
        assert n == 0
    finally:
        conn.close()
