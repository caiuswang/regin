"""SQLAlchemy engine + `SessionLocal` factory.

Honours `settings.database_url` when set (MySQL in shared mode) and
falls back to the project-local SQLite file for standalone mode. The
engine is process-scoped and lazy — call `get_engine()` once per process
(or use `SessionLocal()` which does).

Pragmas for SQLite match the hand-tuned set applied by `get_connection`
below:
    journal_mode=WAL, synchronous=NORMAL, busy_timeout=5000, foreign_keys=ON.

`dispose_engine()` tears the cached engine down; useful in tests that
swap `settings.database_url` and want a fresh pool.
"""

from __future__ import annotations

import os
import re
import sqlite3
from typing import Optional

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session

from lib.settings import settings, _PROJECT_ROOT


# ── Canonical DB paths + raw-sqlite layer ─────────────────────────
#
# These lived in `lib/db.py` until the framework-led refactor folded
# the raw-sqlite helpers in here, next to the SQLAlchemy engine that
# derives its URL from the same DB_PATH. Tests monkeypatch
# `lib.orm.engine.DB_PATH` for per-test isolation.

DB_PATH: str = str(_PROJECT_ROOT / "db" / "regin.db")
SCHEMA_PATH: str = str(_PROJECT_ROOT / "db" / "schema.sql")

_TAG_SEED_BLOCK_RE = re.compile(
    r"(?ms)^-- Seed: .*?^INSERT OR IGNORE INTO tags \(name, category\) VALUES\s+.*?;\n?"
)


def load_schema_sql(*, include_tag_seeds: bool = False) -> str:
    """Read schema.sql, optionally stripping baked-in tag seed inserts."""
    with open(SCHEMA_PATH, 'r') as f:
        schema_sql = f.read()
    if include_tag_seeds:
        return schema_sql
    return _TAG_SEED_BLOCK_RE.sub("", schema_sql)


def get_connection() -> sqlite3.Connection:
    """Raw sqlite3 connection with row factory + the WAL pragmas this
    concurrent-writer/reader workload needs (hooks ingest spans while
    the Flask UI serves reads).

    Kept raw for the complex paginated trace reads (json_extract + CTEs)
    that don't translate cleanly to SQLModel. New code prefers
    `SessionLocal()`.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Initialize the database from schema.sql (used by `regin init`)."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    try:
        conn.executescript(load_schema_sql())
        conn.commit()
    finally:
        conn.close()


def db_exists() -> bool:
    """True if the DB file exists and has been initialised (repos table)."""
    if not os.path.exists(DB_PATH):
        return False
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='repos'"
        )
        return cur.fetchone()[0] > 0
    finally:
        conn.close()


_engine: Optional[Engine] = None
_engine_url: Optional[str] = None
_SessionFactory: Optional[sessionmaker] = None
_auth_engine: Optional[Engine] = None
_auth_engine_url: Optional[str] = None
_AuthSessionFactory: Optional[sessionmaker] = None


def _resolve_primary_url() -> str:
    """Resolve the URL for the *primary* data engine.

    The primary engine always talks to the project-local SQLite file
    used by the raw-sqlite layer (`get_connection`). This matches
    existing semantics: patterns, sync, rule triggers, spans,
    experiments, and — in standalone mode — auth/audit all live in
    that one file. `settings.database_url` is reserved for the separate
    *auth* engine in shared mode (introduced in Phase B.2).

    Reads the module-level `DB_PATH` so tests that monkey-patch
    `lib.orm.engine.DB_PATH` pick the same URL the raw-sqlite helpers
    resolve to.
    """
    return f"sqlite:///{DB_PATH}"


def _resolve_auth_url() -> str | None:
    """Resolve the URL for the auth/audit engine in shared mode.

    Returns None when mode=standalone (auth lives in the primary
    SQLite file). Rewrites a bare `mysql://` URL to `mysql+pymysql://`
    because PyMySQL is the declared driver — mysqlclient is not shipped.
    """
    if settings.mode != "shared":
        return None
    url = settings.database_url
    if not url:
        return None
    if url.startswith("mysql://"):
        url = "mysql+pymysql://" + url[len("mysql://"):]
    return url


def _build_engine(url: str) -> Engine:
    connect_args: dict = {}
    if url.startswith("sqlite"):
        # Same pragmas the raw helper applies per-connection. Wire them
        # as a SQLAlchemy event so every pool-checkout gets them too.
        connect_args["check_same_thread"] = False

    engine = create_engine(url, connect_args=connect_args, future=True)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _rec):
            cur = dbapi_conn.cursor()
            try:
                cur.execute("PRAGMA journal_mode = WAL")
                cur.execute("PRAGMA synchronous = NORMAL")
                cur.execute("PRAGMA busy_timeout = 5000")
                cur.execute("PRAGMA foreign_keys = ON")
            finally:
                cur.close()

    return engine


def get_engine() -> Engine:
    """Return the process-wide primary (data) engine, rebuilding if the
    resolved URL has changed since the last call.

    The rebuild-on-URL-change path matters for tests that monkey-patch
    `lib.orm.engine.DB_PATH` between cases — each test expects a fresh SQLite
    file. Production use resolves the same URL on every call, so the
    cache still wins.
    """
    global _engine, _engine_url, _SessionFactory
    url = _resolve_primary_url()
    if _engine is None or _engine_url != url:
        if _engine is not None:
            _engine.dispose()
        _engine = _build_engine(url)
        _engine_url = url
        # Use SQLModel's Session subclass so callers get `.exec(select(...))`
        # (typed row tuples) rather than SQLAlchemy's raw `.execute()`.
        _SessionFactory = sessionmaker(
            bind=_engine, class_=Session,
            autoflush=False, expire_on_commit=False, future=True,
        )
    return _engine


def SessionLocal() -> Session:
    """Return a new SQLModel Session bound to the cached engine.

    Caller owns the lifecycle — use as a context manager for auto-close.
    The factory name matches the FastAPI/SQLAlchemy idiom so a later
    FastAPI migration becomes `Depends(get_session)` with no changes to
    call sites.
    """
    get_engine()  # ensure _SessionFactory is populated
    assert _SessionFactory is not None  # for type checkers
    return _SessionFactory()


def get_auth_engine() -> Engine:
    """Engine for the auth/audit tables.

    - standalone mode: same as `get_engine()` — SQLite file holds every
      table in one place.
    - shared mode: a separate MySQL engine keyed off `settings.database_url`.

    In standalone mode the auth engine is just an alias for the primary
    engine (and its sessionmaker). Invalidates + rebuilds when the URL
    changes, matching the primary engine's test-friendly behaviour.
    """
    global _auth_engine, _auth_engine_url, _AuthSessionFactory
    url = _resolve_auth_url()

    if url is None:
        # Alias of primary engine — pick up any DB_PATH change.
        get_engine()  # ensures _SessionFactory is fresh for current URL
        _auth_engine = _engine
        _auth_engine_url = _engine_url
        _AuthSessionFactory = _SessionFactory
        return _auth_engine  # type: ignore[return-value]

    if _auth_engine is None or _auth_engine_url != url:
        if _auth_engine is not None and _auth_engine is not _engine:
            _auth_engine.dispose()
        _auth_engine = _build_engine(url)
        _auth_engine_url = url
        _AuthSessionFactory = sessionmaker(
            bind=_auth_engine, class_=Session,
            autoflush=False, expire_on_commit=False, future=True,
        )
    return _auth_engine


def AuthSessionLocal() -> Session:
    """New SQLModel session for auth/audit work.

    Routes to the MySQL engine in shared mode, otherwise to the primary
    SQLite engine. Callers don't need to know which — the User / AuditLog
    SQLModel classes declare their schema in a way both dialects support.
    """
    get_auth_engine()  # ensure factory is populated
    assert _AuthSessionFactory is not None
    return _AuthSessionFactory()


def dispose_engine() -> None:
    """Tear down the cached engines + pools. Primarily for tests."""
    global _engine, _engine_url, _SessionFactory
    global _auth_engine, _auth_engine_url, _AuthSessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_url = None
    _SessionFactory = None
    if _auth_engine is not None and _auth_engine is not _engine:
        _auth_engine.dispose()
    _auth_engine = None
    _auth_engine_url = None
    _AuthSessionFactory = None


__all__ = [
    "DB_PATH", "SCHEMA_PATH",
    "load_schema_sql", "get_connection", "init_db", "db_exists",
    "get_engine", "get_auth_engine",
    "SessionLocal", "AuthSessionLocal",
    "dispose_engine",
]
