"""Smoke tests for web.startup boot-time schema bootstrap.

The helpers are pure CREATE TABLE IF NOT EXISTS — these tests only
verify that the table/index surface they produce matches what the
ingest path and read-side queries expect.
"""

from __future__ import annotations

import sqlite3

from web import startup


def _conn(path):
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    return c


def _has_table(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _has_index(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def test_init_session_spans_creates_table_and_indexes(tmp_path):
    conn = _conn(tmp_path / "fresh.db")
    try:
        startup.init_session_spans_schema(conn)
        assert _has_table(conn, "session_spans")
        assert _has_index(conn, "idx_session_spans_trace")
        assert _has_index(conn, "idx_session_spans_start")
        assert _has_index(conn, "idx_session_spans_name")
        assert _has_index(conn, "idx_session_spans_parent")
        assert _has_index(conn, "idx_session_spans_tool_use_id")
        assert _has_index(conn, "ux_session_spans_trace_span")
    finally:
        conn.close()


def test_init_session_spans_is_idempotent(tmp_path):
    conn = _conn(tmp_path / "idem.db")
    try:
        startup.init_session_spans_schema(conn)
        startup.init_session_spans_schema(conn)  # second pass must not raise
        assert _has_index(conn, "ux_session_spans_trace_span")
    finally:
        conn.close()


def test_init_sessions_creates_table(tmp_path):
    conn = _conn(tmp_path / "s.db")
    try:
        startup.init_session_spans_schema(conn)
        startup.init_sessions_schema(conn)
        assert _has_table(conn, "sessions")
        assert _has_index(conn, "idx_sessions_last_seen")
        assert _has_index(conn, "idx_sessions_title_nocase")
    finally:
        conn.close()


def test_init_sessions_is_idempotent(tmp_path):
    conn = _conn(tmp_path / "idem.db")
    try:
        startup.init_session_spans_schema(conn)
        startup.init_sessions_schema(conn)
        startup.init_sessions_schema(conn)
    finally:
        conn.close()


def test_init_turn_usage_creates_table(tmp_path):
    conn = _conn(tmp_path / "tu.db")
    try:
        startup.init_turn_usage_schema(conn)
        assert _has_table(conn, "turn_usage")
        assert _has_index(conn, "idx_turn_usage_trace_ts")
    finally:
        conn.close()


def test_init_prompt_images_creates_table(tmp_path):
    conn = _conn(tmp_path / "pi.db")
    try:
        startup.init_prompt_images_schema(conn)
        assert _has_table(conn, "prompt_images")
        assert _has_index(conn, "idx_prompt_images_trace")
    finally:
        conn.close()
