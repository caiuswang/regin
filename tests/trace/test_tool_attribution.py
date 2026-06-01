"""Tests for trace_service.ingest_tool_attribution."""

from __future__ import annotations

import json
import sqlite3

import pytest

from lib.trace import trace_service
from lib.tokens import pricing
from lib.tokens.pricing import reset_cache


_FAKE_CATALOGUE = {
    'anthropic': {
        'id': 'anthropic',
        'models': {
            'claude-opus-4-7': {
                'cost': {'input': 5, 'output': 25, 'cache_read': 0.5, 'cache_write': 6.25},
            },
        },
    },
}


@pytest.fixture(autouse=True)
def _stub_pricing(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _FAKE_CATALOGUE)
    reset_cache()
    yield
    reset_cache()


def _insert_session(db_path, trace_id, model):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO sessions (trace_id, started_at, last_seen, model) VALUES (?, ?, ?, ?)",
            (trace_id, '2026-05-14T00:00:00', '2026-05-14T00:00:00', model),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_tool_span(db_path, trace_id, span_id, tool_use_id, *,
                      in_column=False, name='tool.Bash', parent_id=None,
                      extra_attrs=None):
    conn = sqlite3.connect(str(db_path))
    try:
        attrs = {'tool_name': name.removeprefix('tool.')}
        if not in_column:
            attrs['tool_use_id'] = tool_use_id
        if extra_attrs:
            attrs.update(extra_attrs)
        col_val = tool_use_id if in_column else None
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
            "attributes, tool_use_id, parent_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (trace_id, span_id, name, '2026-05-14T00:00:00',
             json.dumps(attrs), col_val, parent_id),
        )
        conn.commit()
    finally:
        conn.close()


def _read_span(db_path, trace_id, tool_use_id):
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT output_tokens, input_tokens, image_tokens, cost_usd, "
            "tool_use_id, turn_uuid FROM session_spans WHERE trace_id = ? "
            "AND (tool_use_id = ? OR json_extract(attributes, '$.tool_use_id') = ?)",
            (trace_id, tool_use_id, tool_use_id),
        ).fetchone()
        return row
    finally:
        conn.close()


def _read_parent_and_attrs(db_path, trace_id, span_id):
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT parent_id, attributes FROM session_spans "
            "WHERE trace_id = ? AND span_id = ?",
            (trace_id, span_id),
        ).fetchone()
        return (row[0], json.loads(row[1]) if row and row[1] else {})
    finally:
        conn.close()


def test_ingest_updates_span_columns(tmp_db):
    _insert_session(tmp_db, 'trace-1', 'claude-opus-4-7')
    _insert_tool_span(tmp_db, 'trace-1', 'span-1', 'toolu_abc')

    updated, skipped = trace_service.ingest_tool_attribution({
        'trace_id': 'trace-1',
        'turn_uuid': 'uuid-1',
        'tool_calls': [
            {'tool_use_id': 'toolu_abc', 'name': 'Bash',
             'output_tokens': 25, 'input_tokens': 1230, 'image_tokens': 1200},
        ],
    })
    assert updated == 1
    assert skipped == 0
    row = _read_span(tmp_db, 'trace-1', 'toolu_abc')
    out, inp, img, usd, tu_col, turn_col = row
    assert out == 25
    assert inp == 1230
    assert img == 1200
    # cost: (5*1230 + 25*25)/1M = (6150 + 625)/1e6 = 6.775e-3
    assert usd == pytest.approx((5 * 1230 + 25 * 25) / 1_000_000)
    assert tu_col == 'toolu_abc'
    assert turn_col == 'uuid-1'


def test_ingest_matches_attribute_when_column_empty(tmp_db):
    _insert_session(tmp_db, 'trace-2', 'claude-opus-4-7')
    # Span only has tool_use_id in attributes (legacy)
    _insert_tool_span(tmp_db, 'trace-2', 'span-2', 'toolu_legacy', in_column=False)

    updated, _ = trace_service.ingest_tool_attribution({
        'trace_id': 'trace-2', 'turn_uuid': 'uuid-2',
        'tool_calls': [
            {'tool_use_id': 'toolu_legacy',
             'output_tokens': 10, 'input_tokens': 50, 'image_tokens': 0},
        ],
    })
    assert updated == 1
    row = _read_span(tmp_db, 'trace-2', 'toolu_legacy')
    assert row is not None and row[4] == 'toolu_legacy'


def test_ingest_skips_unknown_tool_use_id(tmp_db):
    _insert_session(tmp_db, 'trace-3', 'claude-opus-4-7')
    updated, skipped = trace_service.ingest_tool_attribution({
        'trace_id': 'trace-3', 'turn_uuid': 'uuid-3',
        'tool_calls': [{'tool_use_id': 'toolu_missing', 'output_tokens': 1,
                        'input_tokens': 1, 'image_tokens': 0}],
    })
    assert updated == 0
    assert skipped == 1


def test_ingest_handles_missing_model_without_cost(tmp_db):
    _insert_session(tmp_db, 'trace-4', None)
    _insert_tool_span(tmp_db, 'trace-4', 'span-4', 'toolu_nomodel')
    updated, _ = trace_service.ingest_tool_attribution({
        'trace_id': 'trace-4', 'turn_uuid': 'uuid-4',
        'tool_calls': [{'tool_use_id': 'toolu_nomodel', 'output_tokens': 25,
                        'input_tokens': 1230, 'image_tokens': 0}],
    })
    assert updated == 1
    row = _read_span(tmp_db, 'trace-4', 'toolu_nomodel')
    assert row[0] == 25 and row[1] == 1230
    assert row[3] is None  # cost_usd left NULL when model unknown


def test_ingest_rejects_malformed_payload(tmp_db):
    assert trace_service.ingest_tool_attribution({}) == (0, 1)
    assert trace_service.ingest_tool_attribution({'trace_id': 'x'}) == (0, 1)
    assert trace_service.ingest_tool_attribution(
        {'trace_id': 'x', 'tool_calls': 'not a list'}) == (0, 1)
    assert trace_service.ingest_tool_attribution(None) == (0, 1)


def test_ingest_skips_calls_without_tool_use_id(tmp_db):
    _insert_session(tmp_db, 'trace-5', 'claude-opus-4-7')
    updated, skipped = trace_service.ingest_tool_attribution({
        'trace_id': 'trace-5',
        'tool_calls': [
            {'output_tokens': 1, 'input_tokens': 1},  # no tool_use_id
            'not a dict',
        ],
    })
    assert updated == 0
    assert skipped == 2


def test_ingest_backfills_tool_parent_id_and_preserves_attrs(tmp_db):
    """P2b: the live tool.* span is posted parent-less at PostToolUse
    time; the attribution event backfills parent_id = resp-<turn> WHERE
    tool_use_id matches — without touching the rich PostToolUse attrs."""
    _insert_session(tmp_db, 'trace-p', 'claude-opus-4-7')
    _insert_tool_span(tmp_db, 'trace-p', 'span-p', 'toolu_p', name='tool.Edit',
                      extra_attrs={'diff': '@@ -1 +1 @@\n-a\n+b'})
    updated, _ = trace_service.ingest_tool_attribution({
        'trace_id': 'trace-p', 'turn_uuid': 'uuid-p',
        'parent_span_id': 'resp-uuid-p012345',
        'tool_calls': [{'tool_use_id': 'toolu_p', 'output_tokens': 5,
                        'input_tokens': 5, 'image_tokens': 0}],
    })
    assert updated == 1
    parent, attrs = _read_parent_and_attrs(tmp_db, 'trace-p', 'span-p')
    assert parent == 'resp-uuid-p012345'
    # rich PostToolUse attributes survive the parent UPDATE
    assert attrs['diff'] == '@@ -1 +1 @@\n-a\n+b'


def test_ingest_parent_backfill_skips_non_tool_rows(tmp_db):
    """A permission.request row sharing the tool's tool_use_id must keep
    its own parentage — the parent_id CASE is scoped to `tool.%`."""
    _insert_session(tmp_db, 'trace-q', 'claude-opus-4-7')
    _insert_tool_span(tmp_db, 'trace-q', 'perm-q', 'toolu_q',
                      name='permission.request', parent_id='prompt-orig123')
    trace_service.ingest_tool_attribution({
        'trace_id': 'trace-q', 'turn_uuid': 'uuid-q',
        'parent_span_id': 'resp-should-not-apply',
        'tool_calls': [{'tool_use_id': 'toolu_q', 'output_tokens': 1,
                        'input_tokens': 1, 'image_tokens': 0}],
    })
    parent, _ = _read_parent_and_attrs(tmp_db, 'trace-q', 'perm-q')
    assert parent == 'prompt-orig123'  # untouched


def test_ingest_parent_backfill_coalesces_existing_parent(tmp_db):
    """An already-parented tool span (server/deny synth) keeps its parent
    — COALESCE only fills a NULL parent_id."""
    _insert_session(tmp_db, 'trace-r', 'claude-opus-4-7')
    _insert_tool_span(tmp_db, 'trace-r', 'srvtool-r', 'toolu_r',
                      name='tool.advisor', parent_id='think-existing01')
    trace_service.ingest_tool_attribution({
        'trace_id': 'trace-r', 'turn_uuid': 'uuid-r',
        'parent_span_id': 'resp-new-parent',
        'tool_calls': [{'tool_use_id': 'toolu_r', 'output_tokens': 1,
                        'input_tokens': 1, 'image_tokens': 0}],
    })
    parent, _ = _read_parent_and_attrs(tmp_db, 'trace-r', 'srvtool-r')
    assert parent == 'think-existing01'  # not overwritten


def test_ingest_without_parent_span_id_leaves_parent_null(tmp_db):
    """No parent_span_id in the payload → the tool span's NULL parent is
    left for the P3 read-time ladder; tokens still land."""
    _insert_session(tmp_db, 'trace-s', 'claude-opus-4-7')
    _insert_tool_span(tmp_db, 'trace-s', 'span-s', 'toolu_s')
    trace_service.ingest_tool_attribution({
        'trace_id': 'trace-s', 'turn_uuid': 'uuid-s',
        'tool_calls': [{'tool_use_id': 'toolu_s', 'output_tokens': 7,
                        'input_tokens': 7, 'image_tokens': 0}],
    })
    parent, _ = _read_parent_and_attrs(tmp_db, 'trace-s', 'span-s')
    assert parent is None
    row = _read_span(tmp_db, 'trace-s', 'toolu_s')
    assert row[0] == 7  # output_tokens still attributed


def test_ingest_uses_strip_variant_via_session_model(tmp_db):
    # Session was recorded with the [1m] variant; pricing strips it.
    _insert_session(tmp_db, 'trace-6', 'claude-opus-4-7[1m]')
    _insert_tool_span(tmp_db, 'trace-6', 'span-6', 'toolu_v')
    updated, _ = trace_service.ingest_tool_attribution({
        'trace_id': 'trace-6', 'turn_uuid': 'uuid-6',
        'tool_calls': [{'tool_use_id': 'toolu_v', 'output_tokens': 25,
                        'input_tokens': 1000, 'image_tokens': 0}],
    })
    assert updated == 1
    row = _read_span(tmp_db, 'trace-6', 'toolu_v')
    assert row[3] is not None
    assert row[3] > 0
