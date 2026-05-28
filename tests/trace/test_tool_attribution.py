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
                      in_column=False, name='tool.Bash'):
    conn = sqlite3.connect(str(db_path))
    try:
        attrs = {'tool_name': name.removeprefix('tool.')}
        if not in_column:
            attrs['tool_use_id'] = tool_use_id
        col_val = tool_use_id if in_column else None
        conn.execute(
            "INSERT INTO session_spans (trace_id, span_id, name, start_time, "
            "attributes, tool_use_id) VALUES (?, ?, ?, ?, ?, ?)",
            (trace_id, span_id, name, '2026-05-14T00:00:00',
             json.dumps(attrs), col_val),
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
