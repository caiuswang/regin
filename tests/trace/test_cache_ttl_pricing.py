"""Cache writes are priced by TTL (F181 A1) and `context_composition` is gone (B4).

A Claude transcript splits `usage.cache_creation` into
`ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`; a 1h write bills at
2x input against the 5m write's `cache_write` rate (1.25x input).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command

import lib.orm.engine as engine
from cli.commands import db as db_cmd
from hook_manager.handlers.turn_trace.span_posters import _build_usage_row
from lib import db_migrate
from lib.orm.models.trace import Session as SessionModel
from lib.orm.models.trace import TurnUsage as TurnUsageModel
from lib.tokens import pricing
from lib.trace.trace_service import fetch_tool_token_rollup, ingest_turn_usage
from lib.trace.transcript_usage import read_usage

_REPO = Path(__file__).resolve().parents[2]
_MODEL = 'claude-opus-4-7'
_CATALOGUE = {'anthropic': {'id': 'anthropic', 'models': {_MODEL: {
    'id': _MODEL,
    'cost': {'input': 5, 'output': 25, 'cache_read': 0.5, 'cache_write': 6.25},
}}}}
_SID = 'ttl-sess'


@pytest.fixture(autouse=True)
def _catalogue(tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_PRICING_CACHE', str(tmp_path / 'models.json'))
    pricing.reset_cache()
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _CATALOGUE)
    yield
    pricing.reset_cache()


def _conn():
    conn = sqlite3.connect(engine.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_session():
    conn = _conn()
    conn.execute("INSERT INTO sessions (trace_id, started_at, last_seen, model) "
                 "VALUES (?, datetime('now'), datetime('now'), ?)", (_SID, _MODEL))
    conn.commit()
    conn.close()


def _ingest_transcript(tmp_path, usages: list[dict]) -> None:
    """Write `usages` as assistant entries, parse them the way turn_trace does,
    and post the resulting rows to the ingest."""
    path = tmp_path / 'transcript.jsonl'
    with path.open('w') as f:
        for i, usage in enumerate(usages):
            f.write(json.dumps({
                'type': 'assistant', 'uuid': f'u{i}',
                'timestamp': f'2026-10-20T10:00:0{i}.000Z',
                'message': {'id': f'msg_{i}', 'model': _MODEL,
                            'role': 'assistant',
                            'content': [{'type': 'text', 'text': 'ok'}],
                            'usage': usage},
            }) + '\n')
    turns = read_usage(str(path)).turns
    assert len(turns) == len(usages)
    ingest_turn_usage([_build_usage_row(_SID, t, i, _MODEL)
                       for i, t in enumerate(turns)])


def _turn(turn_uuid: str):
    conn = _conn()
    try:
        return conn.execute("SELECT * FROM turn_usage WHERE trace_id = ? "
                            "AND turn_uuid = ?", (_SID, turn_uuid)).fetchone()
    finally:
        conn.close()


def test_cache_write_priced_by_ttl(tmp_path):
    _seed_session()
    _ingest_transcript(tmp_path, [{
        'input_tokens': 0, 'output_tokens': 0,
        'cache_creation_input_tokens': 2000,
        'cache_creation': {'ephemeral_5m_input_tokens': 1000,
                           'ephemeral_1h_input_tokens': 1000},
    }])
    row = _turn('u0')
    assert row['cache_creation_tokens'] == 2000
    assert row['cache_creation_1h_tokens'] == 1000
    assert row['cost_usd'] == pytest.approx((1000 * 6.25 + 1000 * 10) / 1e6)


def test_split_without_flat_total_still_counts_every_write(tmp_path):
    _seed_session()
    _ingest_transcript(tmp_path, [{
        'cache_creation': {'ephemeral_5m_input_tokens': 400,
                           'ephemeral_1h_input_tokens': 600},
    }])
    row = _turn('u0')
    assert (row['cache_creation_tokens'], row['cache_creation_1h_tokens']) == (1000, 600)
    assert row['cost_usd'] == pytest.approx((400 * 6.25 + 600 * 10) / 1e6)


def test_flat_cache_creation_falls_back_to_5m_rate(tmp_path):
    _seed_session()
    _ingest_transcript(tmp_path, [{'cache_creation_input_tokens': 1000}])
    row = _turn('u0')
    assert row['cache_creation_tokens'] == 1000
    assert row['cache_creation_1h_tokens'] == 0
    assert row['cost_usd'] == pytest.approx(1000 * 6.25 / 1e6)


def test_published_1h_rate_outranks_the_2x_input_default(monkeypatch):
    rates = dict(_CATALOGUE['anthropic']['models'][_MODEL]['cost'],
                 cache_write_1h=9)
    catalogue = {'anthropic': {'id': 'anthropic', 'models': {
        _MODEL: {'id': _MODEL, 'cost': rates}}}}
    pricing.reset_cache()
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: catalogue)
    usd = pricing.cost(_MODEL, pricing.TokenBreakdown(
        cache_creation_tokens=1000, cache_creation_1h_tokens=1000))
    assert usd == pytest.approx(1000 * 9 / 1e6)


def test_session_rollup_carries_1h_cache_tokens(tmp_path):
    _seed_session()
    _ingest_transcript(tmp_path, [
        {'cache_creation_input_tokens': 1000,
         'cache_creation': {'ephemeral_5m_input_tokens': 700,
                            'ephemeral_1h_input_tokens': 300}},
        {'cache_creation_input_tokens': 1000,
         'cache_creation': {'ephemeral_5m_input_tokens': 300,
                            'ephemeral_1h_input_tokens': 700}},
    ])
    conn = _conn()
    try:
        sess = conn.execute("SELECT cache_creation_tokens, cache_creation_1h_tokens, "
                            "cost_usd FROM sessions WHERE trace_id = ?",
                            (_SID,)).fetchone()
    finally:
        conn.close()
    expected = (1000 * 6.25 + 1000 * 10) / 1e6
    assert sess['cache_creation_tokens'] == 2000
    assert sess['cache_creation_1h_tokens'] == 1000
    assert sess['cost_usd'] == pytest.approx(expected)

    _, totals = fetch_tool_token_rollup(_SID)
    assert totals['cache_write_cost_usd'] == pytest.approx(expected)


def _stamp_at_previous_head_with(sql: list[str]) -> None:
    conn = sqlite3.connect(engine.DB_PATH)
    for stmt in sql:
        conn.execute(stmt)
    conn.commit()
    conn.close()
    engine.dispose_engine()
    command.stamp(db_migrate._config(), "0046")


def _tables(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def test_context_composition_table_is_dropped(tmp_db):
    _stamp_at_previous_head_with([
        "CREATE TABLE context_composition (trace_id TEXT, part TEXT, tokens INTEGER)",
        "INSERT INTO context_composition VALUES ('t', 'system', 1)",
    ])

    db_cmd.cmd_migrate()

    assert 'context_composition' not in _tables(tmp_db)
    assert 'context_composition' not in (
        _REPO / 'lib' / 'trace' / 'prune.py').read_text()


def _column(path, table: str) -> tuple:
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    finally:
        conn.close()
    names = [r[1] for r in rows]
    r = rows[names.index('cache_creation_1h_tokens')]
    # (type, notnull, default, position) — position matters because the
    # whole-schema parity test compares column order.
    return (r[2].upper(), bool(r[3]), r[4], r[0])


def _orm_column(model, table_cols: int) -> tuple:
    col = model.__table__.c['cache_creation_1h_tokens']
    default = col.server_default.arg.text if col.server_default is not None else None
    return ('INTEGER', not col.nullable, default, table_cols - 1)


def test_cache_1h_schema_parity(tmp_db, tmp_path):
    fresh = tmp_path / 'fresh.db'
    conn = sqlite3.connect(str(fresh))
    conn.executescript(engine.load_schema_sql())
    conn.close()
    _stamp_at_previous_head_with([
        "ALTER TABLE turn_usage DROP COLUMN cache_creation_1h_tokens",
        "ALTER TABLE sessions DROP COLUMN cache_creation_1h_tokens",
    ])

    db_cmd.cmd_migrate()

    for table, model in (('turn_usage', TurnUsageModel),
                         ('sessions', SessionModel)):
        migrated = _column(tmp_db, table)
        assert migrated == _column(fresh, table), table
        n_cols = len(sqlite3.connect(str(fresh)).execute(
            f"PRAGMA table_info('{table}')").fetchall())
        assert migrated == _orm_column(model, n_cols), table


def test_sdk_turn_row_carries_the_1h_split():
    from lib.agent_events.from_sdk import _neutral_usage
    from lib.agent_events.events import TurnCompleted
    from lib.agent_events.usage import turn_usage_row

    usage = _neutral_usage({'cache_creation_input_tokens': 900,
                            'cache_creation': {'ephemeral_5m_input_tokens': 200,
                                               'ephemeral_1h_input_tokens': 700}})
    row = turn_usage_row(TurnCompleted(trace_id=_SID, usage=usage), 0)
    assert (row['cache_creation_tokens'], row['cache_creation_1h_tokens']) == (900, 700)
