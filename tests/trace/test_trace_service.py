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


def _naive_local_to_z(ts):
    """Convert a naive wall-clock ISO string into the `Z`-suffixed UTC form
    Claude Code writes into `turn_usage.timestamp`. Spans store the naive
    local string; the matching turn row stores the SAME instant as UTC-with-
    `Z`. Deriving one from the other here keeps the fixture's two timestamp
    conventions describing the same instant on any host timezone — exactly
    what `_to_utc` reconciles in production."""
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(ts).astimezone(timezone.utc)
    return dt.isoformat().replace('+00:00', 'Z')


def _seed_turn_usage(db_path, rows):
    """Insert turn_usage rows directly. Each row: (trace_id, turn_uuid,
    turn_index, naive_local_timestamp, context_used_tokens). The naive
    wall-clock timestamp (same convention as seeded spans) is converted to
    Claude Code's `Z`-suffixed UTC form on the way in, so the test exercises
    the real cross-convention `_to_utc` reconciliation regardless of host
    timezone."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany(
            "INSERT INTO turn_usage (trace_id, turn_uuid, turn_index, "
            "timestamp, context_used_tokens) VALUES (?, ?, ?, ?, ?)",
            [(t, u, i, _naive_local_to_z(ts), ctx) for t, u, i, ts, ctx in rows],
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
    widened, tree, has_more_older, _ = trace_service.fetch_session_paginated(
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
    _w, tree, has_more_older, _ = trace_service.fetch_session_paginated(
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
    _w, tree, has_more_older, _ = trace_service.fetch_session_paginated(
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
    widened, tree, _, _ = trace_service.fetch_session_paginated(
        't-after', after_id=latest_id,
    )
    assert widened == []
    assert tree == []


def test_fetch_session_paginated_after_id_picks_up_new_prompts(tmp_db):
    prompts = _seed_chatty_session(tmp_db, 't-new', n_prompts=5)
    latest_id = prompts[-1][0]  # prompt-4's id
    # Now seed 3 more prompts arriving "later".
    _seed_chatty_session(tmp_db, 't-new', n_prompts=3, base_min=10)
    widened, tree, _, _ = trace_service.fetch_session_paginated(
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
    _widened, tree, _, _ = trace_service.fetch_session_paginated(
        't-compact', after_id=latest_id,
    )
    boundary_names = sorted(
        n['data']['name'] for n in tree
        if n['data']['name'] in ('compact.pre', 'compact.post')
    )
    assert boundary_names == ['compact.post', 'compact.pre']


# ── compaction reclaim delta ─────────────────────────────────

def _compact_post_span(spans):
    return next(s for s in spans if s['name'] == 'compact.post')


def test_compaction_reclaim_delta_attached_to_post(tmp_db):
    """compact.post carries `reclaimed_tokens` = context of the last turn
    before compact.pre minus context of the first turn after compact.post."""
    _seed_chatty_session(tmp_db, 't-rec', n_prompts=2)
    _seed_spans(tmp_db, [
        ('t-rec', 'cpre', 'compact.pre', '2026-05-01T10:05:00', {}),
        ('t-rec', 'cpost', 'compact.post', '2026-05-01T10:06:00', {}),
    ])
    _seed_turn_usage(tmp_db, [
        ('t-rec', 'tu-0', 0, '2026-05-01T10:00:30', 40_000),
        ('t-rec', 'tu-1', 1, '2026-05-01T10:04:30', 150_000),   # before pre
        ('t-rec', 'tu-2', 2, '2026-05-01T10:07:00', 25_000),    # after post
    ])
    widened, _tree = trace_service.fetch_session_projection('t-rec')
    post = _compact_post_span(widened)
    assert post['attributes']['reclaimed_tokens'] == 125_000


def test_compaction_reclaim_omitted_without_turn_after(tmp_db):
    """No turn after compact.post → nothing to subtract → no attribute
    (it self-heals on the next poll once a post-turn lands)."""
    _seed_chatty_session(tmp_db, 't-norec', n_prompts=2)
    _seed_spans(tmp_db, [
        ('t-norec', 'cpre', 'compact.pre', '2026-05-01T10:05:00', {}),
        ('t-norec', 'cpost', 'compact.post', '2026-05-01T10:06:00', {}),
    ])
    _seed_turn_usage(tmp_db, [
        ('t-norec', 'tu-1', 1, '2026-05-01T10:04:30', 150_000),  # before pre only
    ])
    widened, _tree = trace_service.fetch_session_projection('t-norec')
    post = _compact_post_span(widened)
    assert 'reclaimed_tokens' not in post.get('attributes', {})


def test_compaction_reclaim_omits_nonpositive_delta(tmp_db):
    """Context didn't shrink across the boundary → no garbage number."""
    _seed_chatty_session(tmp_db, 't-grow', n_prompts=2)
    _seed_spans(tmp_db, [
        ('t-grow', 'cpre', 'compact.pre', '2026-05-01T10:05:00', {}),
        ('t-grow', 'cpost', 'compact.post', '2026-05-01T10:06:00', {}),
    ])
    _seed_turn_usage(tmp_db, [
        ('t-grow', 'tu-1', 1, '2026-05-01T10:04:30', 20_000),   # before pre
        ('t-grow', 'tu-2', 2, '2026-05-01T10:07:00', 30_000),   # after post (grew)
    ])
    widened, _tree = trace_service.fetch_session_projection('t-grow')
    post = _compact_post_span(widened)
    assert 'reclaimed_tokens' not in post.get('attributes', {})


def test_compaction_reclaim_per_boundary_with_two_compactions(tmp_db):
    """Two `/compact` runs each get their own delta from their own bracket
    turns — the second pairs with the second compact.pre, not the first."""
    _seed_chatty_session(tmp_db, 't-multi', n_prompts=2)
    _seed_spans(tmp_db, [
        ('t-multi', 'cpre1', 'compact.pre', '2026-05-01T10:05:00', {}),
        ('t-multi', 'cpost1', 'compact.post', '2026-05-01T10:06:00', {}),
        ('t-multi', 'cpre2', 'compact.pre', '2026-05-01T10:20:00', {}),
        ('t-multi', 'cpost2', 'compact.post', '2026-05-01T10:21:00', {}),
    ])
    _seed_turn_usage(tmp_db, [
        ('t-multi', 'tu-a', 0, '2026-05-01T10:04:30', 150_000),  # before pre1
        ('t-multi', 'tu-b', 1, '2026-05-01T10:07:00', 30_000),   # after post1
        ('t-multi', 'tu-c', 2, '2026-05-01T10:19:30', 180_000),  # before pre2
        ('t-multi', 'tu-d', 3, '2026-05-01T10:22:00', 45_000),   # after post2
    ])
    widened, _tree = trace_service.fetch_session_projection('t-multi')
    by_span = {s['span_id']: s for s in widened}
    assert by_span['cpost1']['attributes']['reclaimed_tokens'] == 120_000
    assert by_span['cpost2']['attributes']['reclaimed_tokens'] == 135_000


def test_compaction_reclaim_in_paginated_window(tmp_db):
    """The live-tail path (fetch_session_paginated) also stamps the delta,
    even though the bracketing turns aren't loaded as spans in the window."""
    prompts = _seed_chatty_session(tmp_db, 't-pgrec', n_prompts=3)
    latest_id = prompts[-1][0]
    _seed_spans(tmp_db, [
        ('t-pgrec', 'cpre', 'compact.pre', '2026-05-01T10:05:00', {}),
        ('t-pgrec', 'cpost', 'compact.post', '2026-05-01T10:06:00', {}),
    ])
    _seed_turn_usage(tmp_db, [
        ('t-pgrec', 'tu-1', 1, '2026-05-01T10:04:30', 160_000),  # before pre
        ('t-pgrec', 'tu-2', 2, '2026-05-01T10:07:00', 35_000),   # after post
    ])
    widened, _tree, _more, _retired = trace_service.fetch_session_paginated(
        't-pgrec', after_id=latest_id,
    )
    post = _compact_post_span(widened)
    assert post['attributes']['reclaimed_tokens'] == 125_000


# ── subagent main-session impact ─────────────────────────────

def _seed_rich_spans(db_path, rows):
    """Insert spans carrying parent_id + input_tokens (which `_seed_spans`
    doesn't). Each row: (trace_id, span_id, parent_id, name, start_time,
    input_tokens, attrs)."""
    conn = sqlite3.connect(str(db_path))
    try:
        for trace_id, span_id, parent_id, name, start, in_tok, attrs in rows:
            conn.execute(
                "INSERT INTO session_spans (trace_id, span_id, parent_id, "
                "name, start_time, input_tokens, attributes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (trace_id, span_id, parent_id, name, start, in_tok,
                 json.dumps(attrs)),
            )
        conn.commit()
    finally:
        conn.close()


def _subagent_span(spans, span_id):
    return next(s for s in spans if s['span_id'] == span_id)


def test_subagent_impact_stamped_for_unambiguous_pair(tmp_db):
    """A prompt with exactly one tool.Agent + one subagent.start stamps the
    launch's input_tokens (result text returned to main) onto the start."""
    _seed_rich_spans(tmp_db, [
        ('t-imp', 'prompt-0', None, 'prompt', '2026-05-01T10:00:00', None, {}),
        ('t-imp', 'ag-0', 'prompt-0', 'tool.Agent', '2026-05-01T10:00:05',
         4391, {'tool_name': 'Agent'}),
        ('t-imp', 'sa-0', None, 'subagent.start', '2026-05-01T10:00:02', None,
         {'agent_id': 'a0', 'agent_type': 'Explore'}),
    ])
    widened, _tree = trace_service.fetch_session_projection('t-imp')
    sa = _subagent_span(widened, 'sa-0')
    assert sa['attributes']['main_session_impact_tokens'] == 4391


def test_subagent_impact_omitted_on_parallel_fanout(tmp_db):
    """Two subagents in one prompt can't be ordered against two launches
    safely (completion vs start order), so neither gets a per-subagent
    number — avoids misattribution."""
    _seed_rich_spans(tmp_db, [
        ('t-fan', 'prompt-0', None, 'prompt', '2026-05-01T10:00:00', None, {}),
        ('t-fan', 'ag-0', 'prompt-0', 'tool.Agent', '2026-05-01T10:00:05',
         5000, {'tool_name': 'Agent'}),
        ('t-fan', 'ag-1', 'prompt-0', 'tool.Agent', '2026-05-01T10:00:06',
         3000, {'tool_name': 'Agent'}),
        ('t-fan', 'sa-0', None, 'subagent.start', '2026-05-01T10:00:02', None,
         {'agent_id': 'a0', 'agent_type': 'Explore'}),
        ('t-fan', 'sa-1', None, 'subagent.start', '2026-05-01T10:00:03', None,
         {'agent_id': 'a1', 'agent_type': 'Plan'}),
    ])
    widened, _tree = trace_service.fetch_session_projection('t-fan')
    for sid in ('sa-0', 'sa-1'):
        sa = _subagent_span(widened, sid)
        assert 'main_session_impact_tokens' not in sa.get('attributes', {})


def test_subagent_impact_omitted_when_launch_unenriched(tmp_db):
    """A 1:1 pair whose tool.Agent never got token attribution (input_tokens
    NULL) stamps nothing — the chip degrades to hidden rather than showing 0."""
    _seed_rich_spans(tmp_db, [
        ('t-bare', 'prompt-0', None, 'prompt', '2026-05-01T10:00:00', None, {}),
        ('t-bare', 'ag-0', 'prompt-0', 'tool.Agent', '2026-05-01T10:00:05',
         None, {'tool_name': 'Agent'}),
        ('t-bare', 'sa-0', None, 'subagent.start', '2026-05-01T10:00:02', None,
         {'agent_id': 'a0', 'agent_type': 'Explore'}),
    ])
    widened, _tree = trace_service.fetch_session_projection('t-bare')
    sa = _subagent_span(widened, 'sa-0')
    assert 'main_session_impact_tokens' not in sa.get('attributes', {})


def test_subagent_impact_stamped_in_paginated_window(tmp_db):
    """The live-tail path (fetch_session_paginated) stamps the impact too —
    its raw fetch must carry input_tokens, or the chip would show on a full
    reload but vanish on scroll/live-tail (the both-read-paths invariant)."""
    _seed_rich_spans(tmp_db, [
        ('t-pgimp', 'prompt-0', None, 'prompt', '2026-05-01T10:00:00', None, {}),
        ('t-pgimp', 'ag-0', 'prompt-0', 'tool.Agent', '2026-05-01T10:00:05',
         4391, {'tool_name': 'Agent'}),
        ('t-pgimp', 'sa-0', None, 'subagent.start', '2026-05-01T10:00:02', None,
         {'agent_id': 'a0', 'agent_type': 'Explore'}),
    ])
    widened, _tree, _more, _retired = trace_service.fetch_session_paginated(
        't-pgimp', limit=50,
    )
    sa = _subagent_span(widened, 'sa-0')
    assert sa['attributes']['main_session_impact_tokens'] == 4391


def test_fetch_session_paginated_grafting_works_on_window(tmp_db):
    """Tool spans inside the window must still graft under the right
    prompt — the page must not break the orphan-grafting invariant."""
    _seed_chatty_session(tmp_db, 't-graft', n_prompts=8)
    _w, tree, _, _ = trace_service.fetch_session_paginated(
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
    widened, tree, has_more, _ = trace_service.fetch_session_paginated(
        'nonexistent',
    )
    assert widened == []
    assert tree == []
    assert has_more is False


def _insert_pending_prompt(db_path, trace_id, span_id, start_time):
    """Insert a live PENDING prompt placeholder (name='prompt'); return id."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
            "attributes, status_code) VALUES (?, ?, 'prompt', ?, ?, 'PENDING')",
            (trace_id, span_id, start_time, json.dumps({'live_placeholder': True})),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_after_id_reload_keeps_returning_pending_placeholder(tmp_db):
    """The live promptlive- placeholder must keep being returned by the
    additive (after_id) reload even once the cursor has advanced to its own
    id — otherwise the in-flight prompt vanishes from the live view until
    its real anchor lands (the regression the seamless-handoff fix undoes)."""
    _seed_chatty_session(tmp_db, 't-live', n_prompts=2)  # prompt-0,1
    ph_id = _insert_pending_prompt(tmp_db, 't-live', 'promptlive-abc',
                                   '2026-05-01T10:05:00')
    # Cursor already at the placeholder's own id (a prior reload saw it):
    # `id > ph_id` matches nothing, so only `OR status_code='PENDING'` keeps
    # it visible.
    _w, tree, _, _ = trace_service.fetch_session_paginated('t-live', after_id=ph_id)
    assert 'promptlive-abc' in [n['data']['span_id'] for n in tree]


def test_after_id_reload_drops_retired_placeholder(tmp_db):
    """Once retired (deleted on handoff), the placeholder is no longer
    returned — the frontend prunes it and the real anchor takes its place,
    so there's no duplicate."""
    _seed_chatty_session(tmp_db, 't-ret', n_prompts=2)
    ph_id = _insert_pending_prompt(tmp_db, 't-ret', 'promptlive-xyz',
                                   '2026-05-01T10:05:00')
    conn = sqlite3.connect(str(tmp_db))
    conn.execute("DELETE FROM session_spans WHERE span_id='promptlive-xyz'")
    conn.commit()
    conn.close()
    _w, tree, _, _ = trace_service.fetch_session_paginated('t-ret', after_id=ph_id)
    assert 'promptlive-xyz' not in [n['data']['span_id'] for n in tree]


def test_pending_placeholder_projects_as_a_top_level_root(tmp_db):
    """The placeholder opens a new turn: it must project as a top-level
    root, not nested under the previous prompt."""
    _seed_chatty_session(tmp_db, 't-root', n_prompts=2)  # prompt-0,1
    _insert_pending_prompt(tmp_db, 't-root', 'promptlive-root',
                           '2026-05-01T10:05:00')
    _w, tree, _, _ = trace_service.fetch_session_paginated('t-root', limit=50)
    roots = {(n['data']['span_id'], n['data']['name']) for n in tree}
    assert ('promptlive-root', 'prompt') in roots


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


# ── retired_span_ids (dedup signal for the client cache) ─────

def test_fetch_session_paginated_reports_retired_placeholder(tmp_db):
    """fetch_session_paginated returns the placeholder ids the merge dropped
    (raw window − merged), so the client can prune its append-only cache and
    not show a duplicate card. Robust to id order: the placeholder sorts BELOW
    its surviving anchor here."""
    from lib.trace.pending_spans import prompt_placeholder_id
    tid = 't-retired'
    ph = prompt_placeholder_id(tid, 'hi there')
    _seed_spans(tmp_db, [
        (tid, ph, 'prompt', '2026-01-01T00:00:01', {'text': 'hi there'}),
        (tid, 'prompt-realone0001', 'prompt', '2026-01-01T00:00:02', {'text': 'hi there'}),
    ])
    widened, _tree, _more, retired = trace_service.fetch_session_paginated(tid, limit=50)
    survivors = {s['span_id'] for s in widened}
    assert ph not in survivors                      # merge dropped the placeholder
    assert 'prompt-realone0001' in survivors        # ...kept the real anchor
    assert ph in retired                            # ...and reported it for the prune


def test_fetch_session_paginated_no_retired_when_only_live_placeholder(tmp_db):
    """An in-flight placeholder with no resolved counterpart survives and is
    NOT reported retired (instant feedback preserved)."""
    from lib.trace.pending_spans import prompt_placeholder_id
    tid = 't-inflight'
    ph = prompt_placeholder_id(tid, 'typing')
    _seed_spans(tmp_db, [
        (tid, ph, 'prompt', '2026-01-01T00:00:01', {'text': 'typing'}),
    ])
    widened, _tree, _more, retired = trace_service.fetch_session_paginated(tid, limit=50)
    assert ph in {s['span_id'] for s in widened}
    assert retired == []


def _insert_tool_spans(db_path, trace_id, rows):
    """Insert tool.* spans carrying token counts. Each row:
    (span_id, name, input_tokens, output_tokens, attributes_dict[, status_code]).
    status_code defaults to 'OK'; pass 'PENDING' to model a live placeholder."""
    conn = sqlite3.connect(str(db_path))
    try:
        for row in rows:
            sid, name, intok, outtok, attrs = row[:5]
            status = row[5] if len(row) > 5 else "OK"
            conn.execute(
                "INSERT INTO session_spans (trace_id, span_id, name, "
                "start_time, input_tokens, output_tokens, attributes, status_code) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (trace_id, sid, name, "2026-06-02T10:00:00",
                 intok, outtok, json.dumps(attrs), status),
            )
        conn.commit()
    finally:
        conn.close()


def test_tool_rollup_drill_down_by_target(tmp_db):
    """fetch_tool_token_rollup hangs a per-tool target breakdown (top
    files/commands by token cost, each with the peak call's span_id) off
    each rollup row — the actionable 'which file/command cost most' jump."""
    _insert_tool_spans(tmp_db, "trd", [
        # (span_id, name, input_tokens, output_tokens, attributes)
        ("r1", "tool.Read", 100, 0, {"tool_name": "Read", "file_path": "/a/big.py"}),
        ("r2", "tool.Read", 300, 0, {"tool_name": "Read", "file_path": "/a/big.py"}),
        ("r3", "tool.Read", 50, 0, {"tool_name": "Read", "file_path": "/a/small.py"}),
        # Edit: tiny result (input) but large emitted content (output) — the
        # drill-down must count input+output so it reconciles with the row.
        ("e1", "tool.Edit", 20, 1800, {"tool_name": "Edit", "file_path": "/a/big.py"}),
        # Bash tool_input arrives as a python-repr string AND as JSON; both parse.
        ("b1", "tool.Bash", 200, 0, {"tool_name": "Bash",
                                     "tool_input": "{'command': 'ls -la /tmp'}"}),
        ("b2", "tool.Bash", 80, 0, {"tool_name": "Bash",
                                    "tool_input": '{"command": "echo hi"}'}),
        # Agent has no file/command target → no drill-down.
        ("ag", "tool.Agent", 999, 0, {"tool_name": "Agent"}),
    ])

    rollup, _totals = trace_service.fetch_tool_token_rollup("trd")
    by_name = {r["name"]: r for r in rollup}

    # Read: big.py (100+300, 2 calls) ranks above small.py (50, 1 call).
    read_targets = [(t["label"], t["tokens"], t["calls"])
                    for t in by_name["Read"]["targets"]]
    assert read_targets == [("big.py", 400, 2), ("small.py", 50, 1)]
    # The jump target is the single most expensive call (r2: 300 > r1: 100).
    assert by_name["Read"]["targets"][0]["span_id"] == "r2"

    # Edit: input+output (20+1800), so the drill-down reconciles with the
    # rollup row instead of collapsing to the tiny result size.
    assert by_name["Edit"]["targets"] == [
        {"target": "/a/big.py", "label": "big.py", "tokens": 1820,
         "calls": 1, "span_id": "e1"}]

    # Bash command extracted from BOTH the python-repr and JSON tool_input.
    bash_labels = {t["label"] for t in by_name["Bash"]["targets"]}
    assert "ls -la /tmp" in bash_labels
    assert "echo hi" in bash_labels

    # Agent carries no drill-down target.
    assert by_name["Agent"]["targets"] == []


def test_tool_rollup_drill_down_hook_path_command(tmp_db):
    """The live hook path (post_tool_trace) stores a command-tool's command in
    `command`/`command_preview` (Bash) or `pattern` (Grep/Glob), NOT in a
    `tool_input` dict (which only the workflow-ingest path writes). The target
    breakdown must read those attrs too, or every hook-captured Bash/Grep/Glob
    span gets no drill-down target and its jump-to-span button stays disabled.
    PENDING placeholders are excluded so the jump never lands on a span the
    serve-time merge retires."""
    _insert_tool_spans(tmp_db, "hkp", [
        # short Bash: only command_preview is stored (command omitted < cap)
        ("b1", "tool.Bash", 150, 0, {"tool_name": "Bash",
                                     "command_preview": "git status"}),
        # long Bash: full command stored; preview is the truncated head — the
        # full `command` is preferred so the two calls group as one target.
        ("b2", "tool.Bash", 90, 0, {"tool_name": "Bash",
                                    "command": "make build && make test",
                                    "command_preview": "make build && make…"}),
        ("b3", "tool.Bash", 400, 0, {"tool_name": "Bash",
                                     "command": "make build && make test",
                                     "command_preview": "make build && make…"}),
        # Grep stores `pattern` (no tool_input).
        ("g1", "tool.Grep", 60, 0, {"tool_name": "Grep", "pattern": "TODO"}),
        # PENDING placeholder for the highest-token call must NOT win the jump.
        ("bp", "tool.Bash", 5000, 0, {"tool_name": "Bash",
                                      "command": "make build && make test",
                                      "command_preview": "make build && make…"},
         "PENDING"),
    ])

    rollup, _ = trace_service.fetch_tool_token_rollup("hkp")
    by_name = {r["name"]: r for r in rollup}

    bash = {t["label"]: t for t in by_name["Bash"]["targets"]}
    assert "git status" in bash                       # resolved from command_preview
    # both long-command calls grouped under the full `command` (490 = 90+400),
    # and the PENDING 5000-token row is excluded entirely.
    assert bash["make build && make test"]["tokens"] == 490
    assert bash["make build && make test"]["calls"] == 2
    assert bash["make build && make test"]["span_id"] == "b3"   # peak resolved call
    # Grep resolves its pattern target with a real span_id (jump enabled).
    assert by_name["Grep"]["targets"] == [
        {"target": "TODO", "label": "TODO", "tokens": 60, "calls": 1, "span_id": "g1"}]


def _insert_session_and_turns(db_path, trace_id, *, session_row, turns):
    """Seed a `sessions` aggregate row plus its `turn_usage` rows for the bill
    reconciliation. `session_row` is the recorded token/cost aggregate the
    footer shows; `turns` are (model, input, output, cache_read, cache_write,
    context_used) tuples the per-turn dollar split is summed from."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, "
            "input_tokens, output_tokens, cache_read_tokens, "
            "cache_creation_tokens, cost_usd) VALUES (?,?,?,?,?,?,?,?)",
            (trace_id, "2026-06-02T10:00:00", "2026-06-02T10:05:00",
             session_row["input"], session_row["output"],
             session_row["cache_read"], session_row["cache_write"],
             session_row["cost_usd"]),
        )
        for i, (model, intok, outtok, cr, cw, ctx) in enumerate(turns):
            conn.execute(
                "INSERT INTO turn_usage (trace_id, turn_uuid, turn_index, "
                "timestamp, model, input_tokens, output_tokens, "
                "cache_read_tokens, cache_creation_tokens, context_used_tokens) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (trace_id, f"u{i}", i, f"2026-06-02T10:00:0{i}", model,
                 intok, outtok, cr, cw, ctx),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_server_side_span(db_path, trace_id, span_id, name, *,
                             input_tokens, output_tokens, cost_usd):
    """Insert one server-side sub-model span (the advisor), carrying its own
    `cost_usd` and the `server_side` marker `_session_bill_cost`'s sibling
    sub-agent query keys off. Separate from `_insert_tool_spans` because that
    helper never writes cost_usd."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
            "input_tokens, output_tokens, cost_usd, attributes) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trace_id, span_id, name, "2026-06-02T10:01:00",
             input_tokens, output_tokens, cost_usd,
             json.dumps({"tool_name": name[5:], "server_side": True})),
        )
        conn.commit()
    finally:
        conn.close()


def test_tool_rollup_session_bill_reconciles(tmp_db, monkeypatch):
    """fetch_tool_token_rollup exposes the full recorded session bill — cache
    read/write, base input, output — with a per-bucket USD split summed
    per-turn, so the panel reconciles to `session_cost_usd` instead of passing
    off the (output-only) attributed cost as the session total. Cache reads
    bill ~10x cheaper than input, so the cost split must not echo the token
    split (here cache_read is the largest token bucket but a tiny cost one)."""
    # Flat rates (no tiers) so each bucket's cost is exactly rate * tokens.
    rates = {"input": 5, "output": 25, "cache_read": 0.5, "cache_write": 6.25}
    monkeypatch.setattr("lib.tokens.pricing.model_rates", lambda model: rates)

    _insert_tool_spans(tmp_db, "bill", [
        ("t1", "tool.Bash", 0, 100, {"tool_name": "Bash"}),
    ])
    _insert_session_and_turns(
        tmp_db, "bill",
        session_row={"input": 15, "output": 300, "cache_read": 1000,
                     "cache_write": 70, "cost_usd": 0.0085125},
        turns=[
            ("m", 10, 100, 0, 50, 60),
            ("m", 5, 200, 1000, 20, 1025),
        ],
    )
    # A server-side sub-model span (the advisor) bills on its OWN model — real
    # spend that sessions.cost_usd excludes — so it must surface as subagent_*
    # and lift total_spend above the main-model bill (its $0.05 dwarfs the
    # $0.0085 main bill, the very over/under-claim that motivated this).
    _insert_server_side_span(tmp_db, "bill", "adv", "tool.advisor",
                             input_tokens=2000, output_tokens=500, cost_usd=0.05)

    _rollup, t = trace_service.fetch_tool_token_rollup("bill")

    # Recorded token aggregate + sub-agent + true total (one assert keeps the
    # function under the cyclomatic-complexity grade).
    token_keys = ("session_cache_read_tokens", "session_cache_creation_tokens",
                  "session_input_tokens", "session_output_tokens",
                  "session_total_tokens", "subagent_tokens", "total_spend_tokens")
    assert {k: t[k] for k in token_keys} == {
        "session_cache_read_tokens": 1000,
        "session_cache_creation_tokens": 70,
        "session_input_tokens": 15,
        "session_output_tokens": 300,
        "session_total_tokens": 1385,             # 15 + 300 + 1000 + 70
        "subagent_tokens": 2500,                  # advisor 2000 + 500
        "total_spend_tokens": 3885,               # 1385 + 2500
    }

    # Per-bucket cost = flat rate * tokens / 1e6. cache_read is the biggest
    # token bucket (1000) yet a tiny cost ($0.0005) — the token-heavy,
    # cost-light bucket the footer exists to expose. total_spend adds the
    # advisor on top of the main-model bill.
    cost_keys = ("input_cost_usd", "output_cost_usd", "cache_read_cost_usd",
                 "cache_write_cost_usd", "session_cost_usd", "subagent_cost_usd",
                 "total_spend_usd")
    assert {k: t[k] for k in cost_keys} == pytest.approx({
        "input_cost_usd": 15 * 5 / 1e6,
        "output_cost_usd": 300 * 25 / 1e6,
        "cache_read_cost_usd": 1000 * 0.5 / 1e6,
        "cache_write_cost_usd": 70 * 6.25 / 1e6,
        "session_cost_usd": 0.0085125,
        "subagent_cost_usd": 0.05,
        "total_spend_usd": 0.0585125,             # 0.0085125 + 0.05
    })

    # The four main buckets reconcile to the recorded session cost, and
    # total_spend = that bill + the sub-agent — the invariant the "$X of $Y"
    # label and footer total depend on.
    bill = (t["input_cost_usd"] + t["output_cost_usd"]
            + t["cache_read_cost_usd"] + t["cache_write_cost_usd"])
    assert bill == pytest.approx(t["session_cost_usd"])
    assert t["total_spend_usd"] == pytest.approx(
        t["session_cost_usd"] + t["subagent_cost_usd"])


def test_tool_rollup_session_bill_absent_when_no_session(tmp_db):
    """With tool spans but no `sessions`/`turn_usage` rows (e.g. an in-flight
    session before the first usage flush), the bill fields stay zeroed rather
    than raising — `_session_bill_cost` and the `None` session row degrade to
    zeros so the frontend simply hides the footer."""
    _insert_tool_spans(tmp_db, "nosess", [
        ("t1", "tool.Bash", 0, 100, {"tool_name": "Bash"}),
    ])

    _rollup, t = trace_service.fetch_tool_token_rollup("nosess")

    assert t["session_total_tokens"] == 0
    assert t["session_cost_usd"] == 0.0
    assert t["cache_read_cost_usd"] == 0.0
    assert t["cache_write_cost_usd"] == 0.0
    assert t["subagent_cost_usd"] == 0.0
    assert t["total_spend_usd"] == 0.0
