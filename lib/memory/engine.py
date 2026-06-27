"""Engine + session factory for the separate memory database.

Third instance of regin's multi-engine pattern (after the primary and
auth engines in `lib/orm/engine.py`), reusing the same `_build_engine`
(WAL pragmas, `busy_timeout`) so concurrency behavior matches the rest
of the app. The DB file defaults to `<project_root>/db/regin_memory.db`
and is **self-initializing**: first checkout of the engine runs
`create_all` against the memory-only metadata plus the FTS5 DDL below.
Nothing here ever appears in `db/schema.sql` — that is the point (the
file survives `regin init` / `rebuild` and dodges the schema-drift trap).
"""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import Engine, text as sa_text
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session

from lib.orm.engine import _build_engine
from lib.settings import settings

from lib.memory.models import memory_metadata

# FTS5 can't be declared through SQLModel; plain external-content-free
# table keyed by memory id. Kept in sync by the store (delete + insert
# per write, the same shape `pattern_router._upsert_fts` uses).
_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
    "USING fts5(memory_id UNINDEXED, title, body, tags)"
)
_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_memories_status_tier "
    "ON memories(status, tier)",
    "CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope)",
    "CREATE INDEX IF NOT EXISTS idx_memories_source_trace "
    "ON memories(source_trace_id)",
    # One row per undirected pair+kind — the upsert key for edge harvest.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_edges_pair "
    "ON memory_edges(src_id, dst_id, kind)",
    "CREATE INDEX IF NOT EXISTS idx_memory_edges_dst ON memory_edges(dst_id)",
    "CREATE INDEX IF NOT EXISTS idx_memory_topic_members_memory "
    "ON memory_topic_members(memory_id)",
    # Reverse lookup for the recall-time topic boost: memories on a node.
    "CREATE INDEX IF NOT EXISTS idx_memory_auth_topics_node "
    "ON memory_authoritative_topics(topic_node_id)",
    # Per-topic exemplar lookup for route-time suppression / protection.
    "CREATE INDEX IF NOT EXISTS idx_topic_exemplars_topic "
    "ON topic_exemplars(topic_id)",
)

# Table renames for DBs that predate the negative→exemplar unification. A
# pre-existing `topic_negatives` table (negatives only) is renamed in place so
# its rows survive as `polarity = -1` exemplars once the column migration below
# backfills the default. Idempotent: skipped when the new table already exists.
# Must run *before* `create_all`, or it would create an empty new table and
# orphan the old rows.
# (old_table, new_table)
_RENAME_MIGRATIONS = (
    ("topic_negatives", "topic_exemplars"),
)

_memory_engine: Optional[Engine] = None
_memory_engine_url: Optional[str] = None
_MemorySessionFactory: Optional[sessionmaker] = None


def memory_db_path() -> str:
    """Resolved memory DB file path (settings override or project default)."""
    configured = settings.agent_memory.db_path
    if configured:
        return os.path.expanduser(str(configured))
    return str(settings.project_root / "db" / "regin_memory.db")


def _resolve_memory_url() -> str:
    return f"sqlite:///{memory_db_path()}"


# Additive column migrations for tables that predate a column. `create_all`
# only creates missing *tables*, never alters an existing one, so a column
# added to a model needs an explicit, idempotent ALTER for DBs in the wild.
# (table, column, type) — applied only when the column is absent.
_COLUMN_MIGRATIONS = (
    ("injection_events", "engaged", "INTEGER"),
    ("injection_events", "scored_at", "TEXT"),
    ("injection_events", "matched", "INTEGER"),
    ("injection_events", "query", "TEXT"),
    ("topic_injections", "query", "TEXT"),
    # Negative→exemplar unification: the renamed `topic_exemplars` table (and
    # any old-shaped row) gains the polarity/source axes. The DEFAULT clause
    # backfills pre-existing negatives to `polarity = -1`, `source = 'auto'`;
    # a fresh table already carries the columns from the model, so the ALTER is
    # skipped there.
    ("topic_exemplars", "polarity", "INTEGER NOT NULL DEFAULT -1"),
    ("topic_exemplars", "source", "TEXT NOT NULL DEFAULT 'auto'"),
    # The raw prompt behind each exemplar (inspect + per-row revert). Older
    # rows predate it and stay NULL — shown as "(query not recorded)".
    ("topic_exemplars", "query", "TEXT"),
)


def _apply_rename_migrations(conn) -> None:
    tables = {r[0] for r in conn.execute(sa_text(
        "SELECT name FROM sqlite_master WHERE type='table'"))}
    for old, new in _RENAME_MIGRATIONS:
        if old in tables and new not in tables:
            conn.execute(sa_text(f"ALTER TABLE {old} RENAME TO {new}"))
            tables.add(new)


def _apply_column_migrations(conn) -> None:
    for table, column, col_type in _COLUMN_MIGRATIONS:
        cols = {r[1] for r in conn.execute(
            sa_text(f"PRAGMA table_info({table})"))}
        if column not in cols:
            conn.execute(sa_text(
                f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))


def _init_memory_schema(engine: Engine) -> None:
    # Rename legacy tables before create_all, so it sees them as present and
    # doesn't create empty new ones that would orphan the existing rows.
    with engine.begin() as conn:
        _apply_rename_migrations(conn)
    memory_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa_text(_FTS_DDL))
        for ddl in _INDEX_DDL:
            conn.execute(sa_text(ddl))
        _apply_column_migrations(conn)


def get_memory_engine() -> Engine:
    """Process-wide memory engine, rebuilt when the resolved URL changes
    (tests monkeypatch `settings.agent_memory.db_path` per case)."""
    global _memory_engine, _memory_engine_url, _MemorySessionFactory
    url = _resolve_memory_url()
    if _memory_engine is None or _memory_engine_url != url:
        if _memory_engine is not None:
            _memory_engine.dispose()
        os.makedirs(os.path.dirname(memory_db_path()), exist_ok=True)
        _memory_engine = _build_engine(url)
        _memory_engine_url = url
        _init_memory_schema(_memory_engine)
        _MemorySessionFactory = sessionmaker(
            bind=_memory_engine, class_=Session,
            autoflush=False, expire_on_commit=False, future=True,
        )
    return _memory_engine


def MemorySessionLocal() -> Session:
    """New SQLModel session bound to the memory engine. Caller owns the
    lifecycle — use as a context manager for auto-close."""
    get_memory_engine()
    assert _MemorySessionFactory is not None  # for type checkers
    return _MemorySessionFactory()


def dispose_memory_engine() -> None:
    """Tear down the cached engine + pool. Primarily for tests."""
    global _memory_engine, _memory_engine_url, _MemorySessionFactory
    if _memory_engine is not None:
        _memory_engine.dispose()
    _memory_engine = None
    _memory_engine_url = None
    _MemorySessionFactory = None


__all__ = [
    "memory_db_path", "get_memory_engine",
    "MemorySessionLocal", "dispose_memory_engine",
]
